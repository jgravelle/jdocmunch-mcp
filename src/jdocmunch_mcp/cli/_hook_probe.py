"""The PreToolUse Read interceptor, and nothing else.

⚠⚠ **MODULE-LEVEL IMPORTS HERE ARE ``json``, ``os`` AND ``sys``. THAT IS THE
WHOLE POINT OF THIS FILE.** Claude Code fires ``jdocmunch-mcp hook-pretooluse``
as a fresh process on EVERY ``Read``, and the work it then does is a JSON parse,
an extension test and a size test. Everything else that process pays is
startup, so startup is the thing worth owning.

This module is NOT a second copy of ``cli/hooks.py``'s logic. It is where that
logic now LIVES; ``cli/hooks.py`` imports these names back out, so there is
exactly one ``_DOC_EXTENSIONS`` and one ``run_pretooluse`` in the package.

⚠ ``os.path``, never ``pathlib``: importing ``pathlib`` has a cost of its own.
``hashlib`` and ``subprocess`` belong to the PostToolUse reindex machinery and
stay in ``cli/hooks.py``; nothing on this path needs them.

``tests/test_cli_import_cost.py`` ratchets both halves: this module's import
set, and the absence of every one of those modules from a ``hook-pretooluse``
process.
"""

import json
import os
import sys

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


def _breadcrumb(msg: str) -> None:
    """Leave the opt-in ``JDOCMUNCH_HOOK_LOG`` breadcrumb ``cli/hooks.py`` writes.

    The Read hook must not import the reindex machinery (``cli.hooks`` pulls in
    ``storage.paths`` and ``pathlib`` the moment it writes anything), so the
    environment switch is tested HERE with ``os`` alone and the real writer is
    imported only when logging is on. With the switch off -- the default -- this
    is one dict lookup and nothing is imported.
    """
    if os.environ.get("JDOCMUNCH_HOOK_LOG", "").strip().lower() not in {
        "1", "true", "yes", "on",
    }:
        return
    try:
        from .hooks import _breadcrumb as _write
        _write(msg)
    except Exception:
        pass


def _guarded(fn):
    """Make a ``run_*`` hook handler unable to raise.

    A hook's exit code is part of its contract: a non-zero exit is a
    non-blocking error whose first line of stderr Claude Code shows in the
    transcript, so a traceback from a handler reaches the user as
    ``Traceback (most recent call last):``. Valid JSON is not a valid payload --
    ``tool_input`` as a string, a top-level null/list/number and a numeric
    ``file_path`` are all valid JSON -- so every handler wraps its whole body, not
    only the ``json.load``. The failure is not silent: it is breadcrumbed.

    Hand-rolled rather than ``functools.wraps`` because this module's import set
    is ratcheted; ``__wrapped__`` is set so ``inspect.getsource`` still finds the
    handler. ``_hook_guarded`` is what the ratchet in
    ``tests/test_hook_payload_shapes.py`` looks for on every ``run_*`` function.
    """
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            _breadcrumb(f"{fn.__name__} failed: {type(exc).__name__}: {exc}")
            return 0

    wrapper.__name__ = fn.__name__
    wrapper.__qualname__ = fn.__qualname__
    wrapper.__doc__ = fn.__doc__
    wrapper.__module__ = fn.__module__
    wrapper.__wrapped__ = fn
    wrapper._hook_guarded = True
    return wrapper


@_guarded
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

    Returns exit code (always 0). ``_guarded`` swallows (and breadcrumbs)
    anything unexpected so a malformed payload can never block a Read:
    previously the try covered only ``json.load``, and five valid-JSON shapes
    (``tool_input`` as a string, a top-level null/list/number, ``file_path`` as
    a number) crashed with a traceback whose first line the transcript then
    showed as a hook error.
    """
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    tool_input = data.get("tool_input") if isinstance(data, dict) else None
    if not isinstance(tool_input, dict):
        return 0

    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not file_path:
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
