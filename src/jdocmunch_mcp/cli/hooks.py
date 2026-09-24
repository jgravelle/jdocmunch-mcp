"""Claude Code hook handlers for jDocMunch enforcement.

PreToolUse  -- intercept Read on large doc files, suggest jDocMunch tools.
PostToolUse -- auto-reindex after Edit/Write on doc files to keep the index fresh.
PreCompact  -- emit a session snapshot so doc orientation survives context compaction.

All read JSON from stdin and write JSON to stdout per the Claude Code hooks spec.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    import fcntl  # POSIX advisory file locks (cross-process)
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None
try:
    import msvcrt  # Windows byte-range file locks (cross-process)
except ImportError:  # pragma: no cover - non-Windows
    msvcrt = None

# Doc extensions that benefit from jDocMunch structured retrieval.
# Mirrors parser.ALL_EXTENSIONS.
_DOC_EXTENSIONS: set[str] = {
    ".md", ".markdown", ".mdx",
    ".txt",
    ".rst",
    ".adoc", ".asciidoc", ".asc",
    ".ipynb",
    ".html", ".htm",
    ".yaml", ".yml",
    ".json", ".jsonc",
    ".xml", ".svg", ".xhtml",
    ".tscn", ".tres",
}

# Minimum file size (bytes) to trigger the jDocMunch suggestion.
# Override with JDOCMUNCH_HOOK_MIN_SIZE env var.
_MIN_SIZE_BYTES = int(os.environ.get("JDOCMUNCH_HOOK_MIN_SIZE", "2048"))


# ---------------------------------------------------------------------------
# PostToolUse reindex throttling (jdoc#76)
#
# The PostToolUse hook spawns a background reindex per Edit/Write. Without any
# throttle, a burst of N edits fans out into N concurrent processes that each
# load the full index -- a memory pile-up that took down a 16 GB machine in the
# report. Three composable guards, each independently tunable:
#   * per-file debounce  -- coalesce rapid repeated edits to one file;
#   * global concurrency cap -- at most N reindexers loading an index at once
#     (the spawned `hook-reindex` worker acquires one of N slot locks BEFORE it
#     loads anything, and exits if none is free);
#   * breadcrumb log     -- opt-in one-liner so pile-ups/skips are observable
#     instead of silently discarded to DEVNULL.
# ---------------------------------------------------------------------------

def _hook_state_dir() -> Path:
    """Directory holding the reindex throttle state (debounce stamps + slot
    locks + optional log). Co-located with the doc-index storage root."""
    from ..storage.paths import default_root  # jdoc#146
    base = str(default_root())
    d = Path(base) / "_hooks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _debounce_seconds() -> float:
    """Debounce window; <=0 disables debouncing. Env JDOCMUNCH_HOOK_DEBOUNCE_SECONDS."""
    try:
        return float(os.environ.get("JDOCMUNCH_HOOK_DEBOUNCE_SECONDS", "3.0"))
    except (TypeError, ValueError):
        return 3.0


def _max_reindex() -> int:
    """Max concurrent reindex workers. Env JDOCMUNCH_HOOK_MAX_REINDEX (default 2)."""
    try:
        return max(1, int(os.environ.get("JDOCMUNCH_HOOK_MAX_REINDEX", "2")))
    except (TypeError, ValueError):
        return 2


def _hook_log_enabled() -> bool:
    return os.environ.get("JDOCMUNCH_HOOK_LOG", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _breadcrumb(msg: str) -> None:
    """Append one diagnostic line so reindex pile-ups/skips are observable.
    No-op unless JDOCMUNCH_HOOK_LOG is set (default: silent, as before)."""
    if not _hook_log_enabled():
        return
    try:
        with open(_hook_state_dir() / "reindex.log", "a", encoding="utf-8") as f:
            f.write(f"{int(time.time())} pid={os.getpid()} {msg}\n")
    except OSError:
        pass


def _should_reindex(resolved_path: str) -> bool:
    """Leading-edge debounce: True (and stamps the path) when no reindex was
    spawned for this file within the debounce window; False to coalesce a rapid
    repeat edit. Fail-open (True) on any state error -- never drop a reindex
    because the stamp couldn't be read."""
    window = _debounce_seconds()
    if window <= 0:
        return True
    try:
        key = hashlib.sha1(resolved_path.encode("utf-8")).hexdigest()
        stamp_dir = _hook_state_dir() / "debounce"
        stamp_dir.mkdir(parents=True, exist_ok=True)
        stamp = stamp_dir / key
        try:
            if (time.time() - stamp.stat().st_mtime) < window:
                return False
        except OSError:
            pass  # no stamp yet -> proceed
        stamp.write_text("", encoding="utf-8")  # (re)stamp mtime
    except OSError:
        return True
    return True


def _acquire_reindex_slot():
    """Grab one of ``_max_reindex()`` cross-process slot locks without blocking.

    Returns an open, locked fd on success; ``-1`` when no lock primitive is
    available (proceed uncapped); ``None`` when every slot is held (over the
    concurrency cap -- caller should skip). The lock is advisory and held for
    the worker's lifetime; the OS releases it on process exit even on a crash,
    so a killed reindexer never wedges a slot."""
    if fcntl is None and msvcrt is None:
        return -1
    state = _hook_state_dir()
    for i in range(_max_reindex()):
        lock_path = state / f"reindex.slot{i}.lock"
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        except OSError:
            continue
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return fd
        except OSError:
            os.close(fd)
            continue
    return None


def _release_reindex_slot(fd) -> None:
    if fd is None or fd == -1:
        return
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        else:
            try:
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def run_hook_reindex(path: str) -> int:
    """`hook-reindex <path>` worker spawned by the PostToolUse hook (jdoc#76).

    Acquires a concurrency slot BEFORE loading the index; if the cap is already
    saturated it records a breadcrumb and exits without loading anything (the
    next edit reindexes -- correctness holds). Otherwise it reindexes the single
    file and releases the slot. Always exits 0: a background reindex failure
    must never surface as a hook error."""
    try:
        resolved = str(Path(path).resolve())
    except (OSError, ValueError):
        return 0
    _, ext = os.path.splitext(resolved)
    if ext.lower() not in _DOC_EXTENSIONS:
        return 0

    fd = _acquire_reindex_slot()
    if fd is None:
        _breadcrumb(f"skip over-cap {resolved}")
        return 0
    try:
        _breadcrumb(f"reindex start {resolved}")
        from ..tools.index_file import index_file_cli
        index_file_cli(resolved)
        _breadcrumb(f"reindex done {resolved}")
    except Exception as exc:  # noqa: BLE001 - background best-effort
        _breadcrumb(f"reindex error {resolved}: {exc}")
    finally:
        _release_reindex_slot(fd)
    return 0


def run_pretooluse() -> int:
    """PreToolUse hook: intercept Read calls on large doc files.

    Reads hook JSON from stdin.  If the target is a doc file above the
    size threshold, emits a hint directing Claude to use jDocMunch tools
    instead, as ``hookSpecificOutput.additionalContext`` JSON on stdout.

    ⚠ That channel is the ONLY one that reaches the model from an exit-0
    PreToolUse hook (#129). stderr on exit 0 goes to the debug log; plain
    stdout is fed back only on UserPromptSubmit/SessionStart-class events;
    a top-level ``systemMessage`` surfaces to the user. The hint was written
    to stderr from 1.66.3 through 1.139.1 and was never received.

    Small files, non-doc files, and unreadable paths are silently allowed.

    Returns exit code (always 0 -- errors are swallowed to avoid blocking).
    """
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    file_path: str = data.get("tool_input", {}).get("file_path", "")
    if not file_path:
        return 0

    _, ext = os.path.splitext(file_path)
    if ext.lower() not in _DOC_EXTENSIONS:
        return 0

    try:
        size = os.path.getsize(file_path)
    except OSError:
        return 0

    if size < _MIN_SIZE_BYTES:
        return 0

    # Targeted reads (offset/limit set) are likely pre-edit -- allow silently.
    tool_input = data.get("tool_input", {})
    if tool_input.get("offset") is not None or tool_input.get("limit") is not None:
        return 0

    # Full-file exploratory read on a large doc file -- warn but allow.
    # Hard deny breaks the Edit workflow (Claude Code requires Read before Edit).
    return _emit_additional_context(
        "PreToolUse",
        f"jDocMunch hint: this is a {size:,}-byte doc file. "
        "Prefer search_sections + get_section for exploration. "
        "Use Read only when you need exact line numbers for Edit.",
    )


def _emit_additional_context(event_name: str, text: str) -> int:
    """Emit model-facing ``additionalContext`` for an exit-0 hook.

    Same shape as jcodemunch-mcp's ``_common._emit_additional_context``:
    ``{"hookSpecificOutput": {"hookEventName": ..., "additionalContext": ...}}``
    on stdout, exit 0. Exit 2 would also reach the model via stderr, but it
    blocks the call, and a hard deny breaks Read-before-Edit.
    """
    print(json.dumps({
        "hookSpecificOutput": {"hookEventName": event_name, "additionalContext": text},
    }))
    return 0


def run_posttooluse() -> int:
    """PostToolUse hook: auto-reindex doc files after Edit/Write.

    Reads hook JSON from stdin, extracts the file path, and spawns
    ``jdocmunch-mcp index-local --path <dir>`` as a fire-and-forget
    background process to keep the index fresh.

    Non-doc files are skipped.  Errors are swallowed silently.

    Returns exit code (always 0).
    """
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    file_path: str = data.get("tool_input", {}).get("file_path", "")
    if not file_path:
        return 0

    _, ext = os.path.splitext(file_path)
    if ext.lower() not in _DOC_EXTENSIONS:
        return 0

    resolved = str(Path(file_path).resolve())

    # jdoc#76: coalesce rapid repeat edits to the same file before spawning
    # anything, so a fast agent doesn't fan out a reindex per keystroke-batch.
    if not _should_reindex(resolved):
        _breadcrumb(f"skip debounce {resolved}")
        return 0

    # Fire-and-forget: spawn the throttled `hook-reindex` worker (not `index-file`
    # directly) so the concurrency cap is enforced BEFORE the worker loads the
    # index -- N edits no longer become N simultaneous full-index loads.
    try:
        kwargs: dict = dict(
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        subprocess.Popen(
            ["jdocmunch-mcp", "hook-reindex", resolved],
            **kwargs,
        )
    except (OSError, FileNotFoundError):
        pass  # jdocmunch-mcp not in PATH -- skip silently

    return 0


def run_precompact() -> int:
    """PreCompact hook: kept as a no-op for settings.json entries already installed.

    ⚠ PreCompact has NO exit-0 output channel (#131). It has no
    ``hookSpecificOutput.additionalContext``, and Claude Code discards a
    PreCompact hook's top-level ``systemMessage``. From 1.73.0 through 1.140.0
    this hook wrote the session snapshot into that field, and nobody received
    it. The snapshot now reaches the model through ``run_sessionstart`` on
    ``source=compact``, which fires right after the compaction this hook fires
    before.

    The subcommand stays registered: a 1.x ``settings.json`` written by an
    earlier ``init`` still names it, and an unknown subcommand would turn every
    compaction into a hook error. Re-run ``jdocmunch-mcp init`` to add the
    SessionStart entry beside it.

    Returns exit code (always 0 -- errors are swallowed to avoid blocking).
    """
    try:
        json.load(sys.stdin)  # Drain stdin so the caller never sees EPIPE.
    except (json.JSONDecodeError, ValueError):
        pass
    return 0


_SESSIONSTART_SOURCES = {
    "compact": "restored after compaction",
    "resume": "restored on resume",
    "fork": "carried into this fork",
}


def run_sessionstart() -> int:
    """SessionStart hook: re-inject the doc session snapshot after compaction.

    Reads hook JSON from stdin. On ``source`` compact / resume / fork, builds
    the same cwd-focused snapshot ``run_precompact`` used to build and emits it
    as ``hookSpecificOutput.additionalContext`` (#131), the one channel an
    exit-0 hook has to the model. Stays silent on startup / clear: a fresh
    session has no prior doc state worth restoring, and the snapshot would
    present unrelated repos as current focus.

    Returns exit code (always 0 -- errors are swallowed to avoid blocking).
    """
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0

    source = data.get("source")
    source = source.strip().lower() if isinstance(source, str) else ""
    label = _SESSIONSTART_SOURCES.get(source)
    if label is None:
        return 0

    try:
        snapshot = _build_snapshot(cwd=data.get("cwd"))
    except Exception:
        return 0
    if not snapshot.strip():
        return 0

    return _emit_additional_context(
        "SessionStart",
        f"## jDocMunch session state ({label})\n\n{snapshot}",
    )


def _hook_include_source_roots() -> bool:
    return os.environ.get("JDOCMUNCH_HOOK_INCLUDE_SOURCE_ROOTS", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _repo_matches_cwd(source_root: str, cwd: str) -> bool:
    """True when cwd and the repo's source_root are on the same path branch."""
    if not source_root or not cwd:
        return False
    try:
        c = os.path.normcase(os.path.abspath(cwd))
        s = os.path.normcase(os.path.abspath(source_root))
    except Exception:
        return False
    return c == s or c.startswith(s + os.sep) or s.startswith(c + os.sep)


def _build_snapshot(cwd: "str | None" = None) -> str:
    """Build a compact, path-safe session snapshot from indexed doc repos.

    A compaction hook is injected into agent context at a high-pressure moment,
    so it should preserve the most relevant orientation with the least unrelated
    corpus and path exposure (jdoc#66). When a `cwd` hint is available, repos on
    the same path branch are surfaced first and the rest are summarized as
    omitted. Absolute source roots are hidden by default; set
    `JDOCMUNCH_HOOK_INCLUDE_SOURCE_ROOTS=1` to restore them for local-only use.
    """
    from ..tools.list_repos import list_repos

    repos_result = list_repos()
    repos = repos_result.get("repos", [])

    if not repos:
        return ""

    relevant = [
        r for r in repos
        if cwd and _repo_matches_cwd(r.get("source_root", r.get("source", "")), cwd)
    ]
    cap = 3
    shown = (relevant or repos)[:cap]
    omitted = len(repos) - len(shown)
    include_roots = _hook_include_source_roots()

    lines = ["## jDocMunch Session Snapshot", ""]
    lines.append("Current workspace doc indexes:" if relevant else "Indexed doc repos:")
    for r in shown:
        name = r.get("repo_at_sha", r.get("name", r.get("repo", "?")))
        sections = r.get("section_count", r.get("sections", "?"))
        docs = r.get("doc_count", r.get("documents", "?"))
        line = f"- **{name}**: {docs} docs, {sections} sections"
        if include_roots:
            source = r.get("source_root", r.get("source", ""))
            if source:
                line += f" ({source})"
        lines.append(line)

    if omitted > 0:
        lines.append("")
        lines.append(
            f"Other indexed doc repos: {omitted} omitted. "
            "Use `doc_list_repos` if needed."
        )

    lines.append("")
    lines.append(
        "Use `search_sections` + `get_section` for doc navigation. "
        "Use `Read` only when you need exact line numbers for `Edit`."
    )
    return "\n".join(lines)
