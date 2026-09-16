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
