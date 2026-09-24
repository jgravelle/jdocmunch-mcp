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
import sys
from pathlib import Path
from typing import IO, Optional

logger = logging.getLogger(__name__)

DEFAULT_DEBOUNCE_MS = 1000
DEFAULT_REDISCOVER_INTERVAL_S = 30.0
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


def _make_watch_filter(doc_exts: set[str], storage_path: str):
    storage_abs = os.path.normcase(os.path.abspath(storage_path)) if storage_path else None

    def _filter(_change, path: str) -> bool:
        ext = os.path.splitext(path)[1].lower()
        if ext not in doc_exts:
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
        from watchfiles import awatch
    except ImportError:
        _watcher_output(
            "jdocmunch-mcp watch: the 'watchfiles' package is required. "
            "Upgrade jdocmunch-mcp (>=1.98.0) or run `pip install watchfiles`.",
            quiet=False, log_file_handle=log_file_handle,
        )
        raise SystemExit(1)

    storage_path = storage_path or doc_storage_path_default()
    doc_exts = _doc_extensions()
    watch_filter = _make_watch_filter(doc_exts, storage_path)
    poll_delay = _watch_poll_delay_ms()

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

        monitor_task = asyncio.create_task(_monitor())
        try:
            async for changes in awatch(
                *roots,
                watch_filter=watch_filter,
                debounce=debounce_ms,
                stop_event=cycle_stop,
                poll_delay_ms=poll_delay,
            ):
                await _handle_changes(
                    changes, roots_map, storage_path,
                    use_ai_summaries, quiet, log_file_handle,
                )
        except (KeyboardInterrupt, asyncio.CancelledError):
            stop_event.set()
        except Exception:
            logger.warning("awatch loop error", exc_info=True)
        finally:
            monitor_task.cancel()
            try:
                await monitor_task
            except (asyncio.CancelledError, Exception):
                pass

        if discovery_changed and not stop_event.is_set():
            roots_map = new_map
            if roots_map:
                _watcher_output(
                    f"jdocmunch-mcp watch: repo set changed -> now watching {len(roots_map)}.",
                    quiet=quiet, log_file_handle=log_file_handle,
                )
