"""watch — keep every locally-indexed doc repo fresh on any on-disk change.

jDocMunch's index freshness otherwise rides the PostToolUse hook, which only
fires when the *agent* edits a doc file. Docs changed outside the agent (a git
pull, an editor, a build step, a teammate) go stale until the agent happens to
touch that file again. This watcher closes that gap the same way jCodeMunch's
``watch-all`` daemon does, scoped to documentation file types.

Design: registry-driven discovery (read the same doc indexes jDocMunch already
maintains) rather than polling the storage dir from outside, and the existing
incremental ``index_local`` refresh path (subset ``paths=`` semantics, jdoc#31)
so an edit/add/delete is applied to the owning index without a full reindex.

Public surface:

    discover_local_doc_repos(storage_path=None) -> list[tuple[str, str]]
        (source_root, repo_name) for every locally-indexed doc repo whose
        source_root still exists on disk. GitHub indexes (no local source_root)
        are skipped — there's nothing on-disk to watch.

    async watch_docs(...)
        Long-running coroutine: watches every discovered doc root, filters to
        documentation extensions, coalesces bursts, and refreshes the owning
        index incrementally. Rediscovers on an interval so repos indexed while
        it runs are picked up; shuts down cleanly on SIGINT/SIGTERM.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import stat
import sys
from contextlib import aclosing
from pathlib import Path
from typing import IO, Optional

logger = logging.getLogger(__name__)

DEFAULT_DEBOUNCE_MS = 1000
DEFAULT_REDISCOVER_INTERVAL_S = 30.0
# After an awatch error the next attempt waits this long, doubling per
# consecutive error up to the rediscovery interval. Without a wait, an error
# that recurs at once (a root awatch cannot set up) restarts the loop as fast
# as the watcher can be rebuilt: one core, for as long as it runs.
ERROR_RETRY_INITIAL_S = 1.0
ERROR_RETRY_MAX_DOUBLINGS = 16
# Poll interval used ONLY when watchfiles falls back to polling (it auto-enables
# polling under WSL, where inotify is unreliable across the boundary). Mirrors
# jcodemunch's WSL CPU fix (jcm #356): raise it to cut idle CPU on many-repo
# hosts; ignored when native FS events are in use.
DEFAULT_WATCH_POLL_DELAY_MS = 1000


def doc_storage_path_default() -> str:
    from .storage.paths import default_root  # jdoc#146
    return str(default_root())


def _doc_extensions() -> set[str]:
    """Documentation extensions worth watching (lowercased).

    Reuses the single source of truth already used by the PostToolUse reindex
    hook so the watcher and the hook agree on what counts as a doc file.
    """
    from .cli.hooks import _DOC_EXTENSIONS
    exts = {e.lower() for e in _DOC_EXTENSIONS}
    # Office documents (.pdf/.docx/...) are watchable only when the optional
    # markitdown extra is installed; without it index_local would skip them.
    from .parser.office import OFFICE_EXTENSIONS, office_available
    if office_available():
        exts |= OFFICE_EXTENSIONS
    return exts


def discover_local_doc_repos(storage_path: Optional[str] = None) -> "list[tuple[str, str]]":
    """Return (source_root, repo_name) for every locally-indexed doc repo.

    GitHub indexes (empty ``source_root``) and indexes whose ``source_root`` no
    longer exists on disk are skipped — the latter protects the watcher from
    blowing up when a repo was deleted out from under its index.
    """
    from .tools.list_repos import list_repos

    out: "list[tuple[str, str]]" = []
    seen: set[str] = set()
    try:
        result = list_repos(storage_path=storage_path)
    except Exception:
        logger.warning("discover: list_repos failed", exc_info=True)
        return out
    for row in result.get("repos", []):
        src = (row.get("source_root") or "").strip()
        if not src:
            continue  # GitHub index — nothing on-disk to watch
        repo = row.get("repo") or row.get("name")
        if not repo:
            continue
        try:
            path = Path(src).expanduser()
            if not path.is_dir():
                continue
            resolved = str(path.resolve())
        except OSError:
            logger.debug("Unreachable source_root: %s", src, exc_info=True)
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append((resolved, str(repo)))
    return sorted(out)


# ── environment helpers ─────────────────────────────────────────────────────


def _watch_poll_delay_ms() -> int:
    for var in ("JDOCMUNCH_WATCH_POLL_DELAY_MS", "WATCHFILES_POLL_DELAY_MS"):
        raw = os.environ.get(var)
        if raw:
            try:
                v = int(raw)
                if v > 0:
                    return v
            except (TypeError, ValueError):
                pass
    return DEFAULT_WATCH_POLL_DELAY_MS


def _force_polling_default() -> bool:
    """Whether watchfiles will poll when the caller passes ``force_polling=None``.

    The authority is watchfiles' own ``_default_force_polling``, a PRIVATE name
    that a release may rename. The fallback restates its documented rule:
    ``WATCHFILES_FORCE_POLLING`` when set (any value but
    ``false``/``disable``/``disabled`` means poll), else WSL detection. Same
    shape as jcodemunch's helper of the same name.
    """
    try:
        from watchfiles.main import _default_force_polling
    except ImportError:
        env_var = os.getenv("WATCHFILES_FORCE_POLLING")
        if env_var:
            return env_var.lower() not in {"false", "disable", "disabled"}
        return _is_wsl()
    return bool(_default_force_polling(None))


def _native_recursion_is_safe(force_polling: bool) -> bool:
    """True where a recursive watch does not walk through directory links.

    jdoc#154: on Linux (inotify) and under polling, watchfiles walks the tree
    to register it and follows directory links while it does, before any
    filter runs. Measured on inotify with watchfiles 1.3.0: a root of 2
    directories holding one link to ``/usr/share`` held 2,476 watches.
    watchfiles does not expose notify's ``follow_symlinks``, so there the
    watcher hands it an enumerated set of real directories instead. macOS
    FSEvents and Windows ReadDirectoryChangesW take one recursive watch per
    root and do no such walk.
    """
    return sys.platform != "linux" and not force_polling


def _watch_directories(roots) -> "dict[str, tuple[int, int]]":
    """Every real directory under ``roots``, mapped to its (device, inode).

    Directory links are never entered, which is what `discover_doc_files`
    does with its default ``followlinks=False``.

    ⚠ That is the ONLY rule applied here, on purpose. Discovery also prunes
    dot-directories, SKIP_PATTERNS and ignored paths, but a directory that
    discovery reads and this skips is a directory whose edits are never seen.
    Pruning less than discovery costs watches; pruning more loses updates.

    The identity lets a caller see a directory REPLACED at the same path,
    which needs a new native watch.
    """
    directories: "dict[str, tuple[int, int]]" = {}
    for root in roots:
        for current, dirs, _files in os.walk(root, followlinks=False):
            try:
                st = os.stat(current, follow_symlinks=False)
            except OSError:
                dirs[:] = []
                continue
            if not stat.S_ISDIR(st.st_mode):
                dirs[:] = []  # a link, not a real directory
                continue
            directories[current] = (st.st_dev, st.st_ino)
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(current, d))]
    return directories


def _doc_files_in(directories, doc_exts: set[str]) -> "list[str]":
    """Doc-extension entries directly inside each of ``directories``."""
    found: "list[str]" = []
    for directory in directories:
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if os.path.splitext(entry.name)[1].lower() not in doc_exts:
                        continue
                    if not entry.is_dir(follow_symlinks=False):
                        found.append(entry.path)
        except OSError:
            continue
    return found


def _is_wsl() -> bool:
    if sys.platform != "linux":
        return False
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False


def _watcher_output(msg: str, *, quiet: bool = False, log_file_handle: Optional[IO] = None) -> None:
    if quiet:
        return
    handle = log_file_handle or sys.stderr
    try:
        print(msg, file=handle, flush=True)
    except Exception:  # pragma: no cover - never let a log write kill the daemon
        pass


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    def _request_stop() -> None:
        if not stop.is_set():
            stop.set()

    if sys.platform == "win32":
        # add_signal_handler is unsupported on the Windows ProactorEventLoop;
        # Ctrl-C surfaces as KeyboardInterrupt/CancelledError out of asyncio.run.
        return
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except (NotImplementedError, RuntimeError):
            logger.debug("Could not install handler for %s", sig, exc_info=True)


# ── change routing ──────────────────────────────────────────────────────────


def _is_topology_change(change, path: str, watched_dirs) -> bool:
    """True when a directory under an enumerated watch was added or removed."""
    if getattr(change, "name", "") not in ("added", "deleted"):
        return False
    if path in watched_dirs:
        return True
    return os.path.isdir(path) and not os.path.islink(path)


def _make_watch_filter(doc_exts: set[str], storage_path: str, watched: Optional[dict] = None):
    """Filter to documentation files outside our own storage tree.

    ``watched`` (jdoc#154) is a holder the loop fills with the enumerated
    directory set, under ``"dirs"``. While it is set, a directory being added
    or removed also passes: a non-recursive watch has to be told about a new
    directory, and that event is the only prompt notice of one.
    """
    storage_abs = os.path.normcase(os.path.abspath(storage_path)) if storage_path else None

    def _filter(_change, path: str) -> bool:
        ext = os.path.splitext(path)[1].lower()
        if ext not in doc_exts:
            dirs = watched.get("dirs") if watched is not None else None
            if dirs is None or not _is_topology_change(_change, path, dirs):
                return False
        if storage_abs:
            ap = os.path.normcase(os.path.abspath(path))
            if ap == storage_abs or ap.startswith(storage_abs + os.sep):
                return False  # never re-index our own storage tree
        return True

    return _filter


def _corpus_filters(root: str, name: str, storage_path: str):
    """Return (gitignore_spec, extra_spec) for a watched root, or (None, None).

    jdoc#115: the watcher must not re-admit a file that full discovery excluded.
    ⚠ This belongs HERE and deliberately NOT in `index_local`'s `paths=` branch.
    A caller naming a file explicitly bypassing `.gitignore` is intentional and
    documented (SPEC.md, the 1.61.0 changelog); a human asking for a specific
    generated file should get it. The watcher is not that caller — it
    manufactures the path list from filesystem events, so the bypass fires for
    files nobody asked for. Same split jcodemunch uses for CACHEDIR.TAG:
    explicit paths opt past the rules, the watcher fast path applies them.

    jdoc#116's stored `corpus_shape_patterns` are applied for the same reason:
    a watcher that reinstates a pattern-excluded file is that defect wearing a
    different hat.
    """
    from .tools.index_local import _load_gitignore

    gitignore_spec = None
    extra_spec = None
    try:
        gitignore_spec = _load_gitignore(Path(root))
    except Exception:
        logger.debug("watch: .gitignore load failed for %s", root, exc_info=True)
    try:
        import pathspec
        from .storage.doc_store import DocStore

        idx = DocStore(base_path=storage_path).load_index("local", name)
        patterns = list(getattr(idx, "corpus_shape_patterns", None) or []) if idx else []
        if patterns:
            extra_spec = pathspec.PathSpec.from_lines("gitignore", patterns)
    except Exception:
        logger.debug("watch: shape-pattern load failed for %s", name, exc_info=True)
    return gitignore_spec, extra_spec


def _excluded_from_corpus(abs_path: str, root: str, gitignore_spec, extra_spec) -> bool:
    """True when this path is outside the indexed corpus by the root's rules."""
    if gitignore_spec is None and extra_spec is None:
        return False
    try:
        rel = os.path.relpath(abs_path, root).replace(os.sep, "/")
    except ValueError:
        return False  # different drive; containment already checked upstream
    if rel.startswith(".."):
        return False
    for spec in (gitignore_spec, extra_spec):
        if spec is not None and spec.match_file(rel):
            return True
    return False


def _owning_root(path: str, roots_map: "dict[str, str]") -> Optional[str]:
    """Return the watched root that contains ``path`` (longest match wins)."""
    best: Optional[str] = None
    pc = os.path.normcase(path)
    for root in roots_map:
        rc = os.path.normcase(root)
        if pc == rc or pc.startswith(rc + os.sep):
            if best is None or len(root) > len(best):
                best = root
    return best


async def _handle_changes(
    changes,
    roots_map: "dict[str, str]",
    storage_path: str,
    use_ai_summaries: bool,
    quiet: bool,
    log_file_handle: Optional[IO],
) -> None:
    from .tools.index_local import index_local

    by_root: "dict[str, set[str]]" = {}
    for _change, path in changes:
        try:
            ap = str(Path(path).resolve()) if os.path.exists(path) else os.path.abspath(path)
        except OSError:
            ap = os.path.abspath(path)
        root = _owning_root(ap, roots_map)
        if root is None:
            continue
        by_root.setdefault(root, set()).add(ap)

    for root, paths in by_root.items():
        name = roots_map[root]
        # jdoc#115: drop events for files the indexed corpus excludes, BEFORE
        # they reach the paths= bypass. Filtering here rather than suppressing
        # the call's effects afterwards also keeps the log honest: a batch of
        # only-ignored edits must not report "re-indexed 1 file(s)".
        gitignore_spec, extra_spec = _corpus_filters(root, name, storage_path)
        kept = sorted(
            p for p in paths
            if not _excluded_from_corpus(p, root, gitignore_spec, extra_spec)
        )
        if not kept:
            dropped = len(paths)
            logger.debug(
                "watch: %d change(s) in %s ignored by the corpus rules", dropped, name
            )
            continue
        paths = kept
        try:
            # Subset refresh (jdoc#31): only the changed paths are re-indexed;
            # unlisted docs are never pruned, a listed-but-deleted file deletes.
            await asyncio.to_thread(
                index_local,
                path=root,
                name=name,
                paths=sorted(paths),
                storage_path=storage_path,
                use_ai_summaries=use_ai_summaries,
                incremental=True,
            )
            _watcher_output(
                f"jdocmunch-mcp watch: re-indexed {len(paths)} file(s) in {name}",
                quiet=quiet, log_file_handle=log_file_handle,
            )
        except Exception:
            logger.warning("reindex failed for %s", name, exc_info=True)


def _error_retry_delay(consecutive_errors: int, ceiling_s: float) -> float:
    """Seconds to wait before the next awatch attempt in an error streak.

    The exponent is capped: `float * 2 ** 1024` raises OverflowError, which
    from inside the loop's `except` handler would end the watcher on the
    1,025th consecutive error.
    """
    doublings = min(max(consecutive_errors, 1) - 1, ERROR_RETRY_MAX_DOUBLINGS)
    return min(ceiling_s, ERROR_RETRY_INITIAL_S * 2 ** doublings)


# ── main loop ───────────────────────────────────────────────────────────────


async def watch_docs(
    *,
    storage_path: Optional[str] = None,
    debounce_ms: int = DEFAULT_DEBOUNCE_MS,
    rediscover_interval_s: float = DEFAULT_REDISCOVER_INTERVAL_S,
    use_ai_summaries: bool = True,
    quiet: bool = False,
    log_file_handle: Optional[IO] = None,
) -> None:
    """Watch every locally-indexed doc repo; rediscover on an interval.

    Repos added to the registry while running are picked up on the next
    rediscovery pass. Repos whose source_root disappears are dropped.
    """
    try:
        from watchfiles import Change, awatch
    except ImportError:
        _watcher_output(
            "jdocmunch-mcp watch: the 'watchfiles' package is required. "
            "Upgrade jdocmunch-mcp (>=1.98.0) or run `pip install watchfiles`.",
            quiet=False, log_file_handle=log_file_handle,
        )
        raise SystemExit(1)

    storage_path = storage_path or doc_storage_path_default()
    doc_exts = _doc_extensions()
    watched: dict = {"dirs": None}
    watch_filter = _make_watch_filter(doc_exts, storage_path, watched)
    poll_delay = _watch_poll_delay_ms()
    force_polling = _force_polling_default()
    recursive = _native_recursion_is_safe(force_polling)
    # jdoc#154, enumerated mode only: the last directory set whose watches
    # were armed and caught up, and the roots it covered.
    armed_census: "Optional[dict[str, tuple[int, int]]]" = None
    armed_roots: "set[str]" = set()

    stop_event = asyncio.Event()
    try:
        loop = asyncio.get_running_loop()
        _install_signal_handlers(loop, stop_event)
    except RuntimeError:
        pass

    if _is_wsl():
        _watcher_output(
            "jdocmunch-mcp watch: WSL detected -> watchfiles is polling "
            f"(every {poll_delay}ms). To cut CPU: raise "
            "JDOCMUNCH_WATCH_POLL_DELAY_MS, or for repos on the Linux filesystem "
            "set WATCHFILES_FORCE_POLLING=false to use native inotify.",
            quiet=quiet, log_file_handle=log_file_handle,
        )

    roots_map: "dict[str, str]" = dict(discover_local_doc_repos(storage_path))
    if roots_map:
        _watcher_output(
            f"jdocmunch-mcp watch: watching {len(roots_map)} doc repo(s). It stays "
            "running and re-indexes on every doc file change. Press Ctrl+C to stop. "
            "To run it in the background as a login service instead: "
            "jdocmunch-mcp watch-install",
            quiet=quiet, log_file_handle=log_file_handle,
        )
    else:
        _watcher_output(
            "jdocmunch-mcp watch: no locally-indexed doc repos found yet. Waiting; "
            "index one with `jdocmunch-mcp index-local --path <dir>` and it'll be "
            "picked up on the next discovery pass.",
            quiet=quiet, log_file_handle=log_file_handle,
        )

    consecutive_errors = 0
    while not stop_event.is_set():
        if not roots_map:
            # Nothing to watch yet — poll discovery until a repo appears or stop.
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=rediscover_interval_s)
                break
            except asyncio.TimeoutError:
                roots_map = dict(discover_local_doc_repos(storage_path))
                if roots_map:
                    _watcher_output(
                        f"jdocmunch-mcp watch: now watching {len(roots_map)} doc repo(s).",
                        quiet=quiet, log_file_handle=log_file_handle,
                    )
                continue

        roots = sorted(roots_map)
        cycle_stop = asyncio.Event()
        discovery_changed = False
        new_map: "dict[str, str]" = {}

        async def _monitor() -> None:
            # awatch takes a fixed path set; when discovery changes we stop this
            # cycle (setting cycle_stop) and restart awatch over the new roots.
            nonlocal discovery_changed, new_map
            while True:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=rediscover_interval_s)
                    cycle_stop.set()  # global stop requested
                    return
                except asyncio.TimeoutError:
                    try:
                        current = dict(discover_local_doc_repos(storage_path))
                    except Exception:
                        logger.warning("rediscover pass failed", exc_info=True)
                        continue
                    if current != roots_map:
                        discovery_changed = True
                        new_map = current
                        cycle_stop.set()
                        return
                    if census is not None:
                        # Safety net for a directory event that never came.
                        if await asyncio.to_thread(_watch_directories, roots) != census:
                            cycle_stop.set()
                            return

        census: "Optional[dict[str, tuple[int, int]]]" = None
        monitor_task = asyncio.create_task(_monitor())
        retry_delay = 0.0
        try:
            watch_paths: list = roots
            enumerated_kwargs: dict = {}
            if not recursive:
                census = await asyncio.to_thread(_watch_directories, roots)
                if not census:
                    raise FileNotFoundError("no watchable directory under any root")
                watch_paths = list(census)
                # A yield within a second even when nothing changed: the first
                # one is the signal that the watches are armed.
                enumerated_kwargs = {"rust_timeout": 1000, "yield_on_timeout": True}
            watched["dirs"] = census
            armed = False
            stream = awatch(
                *watch_paths,
                watch_filter=watch_filter,
                debounce=debounce_ms,
                stop_event=cycle_stop,
                poll_delay_ms=poll_delay,
                force_polling=force_polling,
                recursive=recursive,
                # Every root shares this one watcher: without this, one
                # directory it cannot read under ANY root ends the watch of
                # every repo.
                ignore_permission_denied=True,
                **enumerated_kwargs,
            )
            async with aclosing(stream):
                async for changes in stream:
                    rearm = False
                    if census is not None:
                        if not armed:
                            # A directory that is new since the last armed set
                            # had no watch until now, so a file written into it
                            # in between raised no event. Read those once.
                            # ⚠ Only after the first yield: a scan before the
                            # watches exist leaves the same gap after itself.
                            armed = True
                            if armed_census is not None:
                                fresh = [
                                    d for d, ident in census.items()
                                    if armed_census.get(d) != ident
                                    and _owning_root(d, armed_roots) is not None
                                ]
                                missed = await asyncio.to_thread(_doc_files_in, fresh, doc_exts)
                                changes = set(changes) | {
                                    (Change.added, p) for p in missed
                                    if watch_filter(Change.added, p)
                                }
                            armed_census, armed_roots = census, set(roots)
                        if any(_is_topology_change(c, p, census) for c, p in changes):
                            rearm = await asyncio.to_thread(_watch_directories, roots) != census
                    doc_changes = {
                        (c, p) for c, p in changes
                        if os.path.splitext(p)[1].lower() in doc_exts
                    }
                    if doc_changes:
                        await _handle_changes(
                            doc_changes, roots_map, storage_path,
                            use_ai_summaries, quiet, log_file_handle,
                        )
                    if rearm:
                        break  # the next cycle enumerates again and re-arms
        except (KeyboardInterrupt, asyncio.CancelledError):
            stop_event.set()
        except Exception as exc:
            consecutive_errors += 1
            retry_delay = _error_retry_delay(consecutive_errors, rediscover_interval_s)
            logger.warning("awatch loop error (%d in a row); retrying in %.0fs",
                           consecutive_errors, retry_delay, exc_info=consecutive_errors == 1)
            if consecutive_errors == 1:
                _watcher_output(
                    f"jdocmunch-mcp watch: watching failed ({type(exc).__name__}: {exc}); "
                    f"retrying with backoff up to {rediscover_interval_s:.0f}s.",
                    quiet=quiet, log_file_handle=log_file_handle,
                )
        else:
            consecutive_errors = 0
        finally:
            monitor_task.cancel()
            try:
                await monitor_task
            except (asyncio.CancelledError, Exception):
                pass

        if retry_delay and not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=retry_delay)
            except asyncio.TimeoutError:
                pass
            if not stop_event.is_set() and not discovery_changed:
                # A cycle that fails at once cancels _monitor before its first
                # pass, so during an error streak this is the only rediscovery.
                # Without it a root that vanished is retried forever.
                try:
                    current = dict(discover_local_doc_repos(storage_path))
                except Exception:
                    logger.warning("rediscover pass failed", exc_info=True)
                else:
                    if current != roots_map:
                        discovery_changed = True
                        new_map = current

        if discovery_changed and not stop_event.is_set():
            roots_map = new_map
            if roots_map:
                _watcher_output(
                    f"jdocmunch-mcp watch: repo set changed -> now watching {len(roots_map)}.",
                    quiet=quiet, log_file_handle=log_file_handle,
                )
