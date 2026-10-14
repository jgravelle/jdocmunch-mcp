"""The PreToolUse Read interceptor, and nothing else.

⚠⚠ **MODULE-LEVEL IMPORTS HERE ARE ``json``, ``os`` AND ``sys``. THAT IS THE
WHOLE POINT OF THIS FILE.** Claude Code fires ``jdocmunch-mcp hook-pretooluse``
as a fresh process on EVERY ``Read``, and the work it then does is a JSON parse,
an extension test, a size test and a few stats. Everything else that process
pays is startup, so startup is the thing worth owning.

This module is NOT a second copy of ``cli/hooks.py``'s logic. It is where that
logic now LIVES; ``cli/hooks.py`` imports these names back out, so there is
exactly one ``_DOC_EXTENSIONS``, one ``_index_state`` and one
``run_pretooluse`` in the package. Two copies of the index-state gate would be
far worse than a duplicated extension list -- they would drift on the question
of whether to steer a reader at a STALE index, which is a correctness property,
not a cosmetic one.

⚠ ``os.path``, never ``pathlib``: importing ``pathlib`` has a cost of its own,
and everything wanted here -- join, dirname, isdir, stat, a directory scan -- is
trivial with ``os.path`` + ``os.scandir``. ``hashlib`` and ``subprocess``
belong to the PostToolUse reindex machinery and stay in ``cli/hooks.py``;
nothing on this path needs them.

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

_SUMMARY_SUFFIX = ".summary.json"

# Extensions whose content mirror is the source text itself (up to the two
# normalisations ``_compare_to_mirror`` replays). Everything else in
# ``_DOC_EXTENSIONS`` is mirrored as PREPROCESSED text: ``preprocess_content``
# turns .mdx, .ipynb, .html/.htm, .xml/.svg/.xhtml, .tscn/.tres and .jsonc into
# Markdown, and plain .json too (only an OpenAPI .json is kept raw, which cannot
# be told from the extension). A byte comparison against one of those would
# call EVERY edited-then-reindexed file stale, so for them the hook never claims
# staleness at all. ``tests/test_hook_probe_staleness.py`` ties this set to
# ``preprocess_content`` so the two cannot drift apart.
_VERBATIM_MIRROR_EXTENSIONS = frozenset({
    ".md", ".markdown",
    ".txt",
    ".rst",
    ".adoc", ".asciidoc", ".asc",
    ".yaml", ".yml",
})


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


def _doc_index_base() -> str:
    """Storage root, honouring DOC_INDEX_PATH (#37: every entry point, hooks too).

    Deliberately does NOT mkdir, unlike ``DocStore.__init__``. A Read hook is an
    observer; creating a directory as a side effect of reading a file is not its
    business, and on a box with no index at all it would manufacture one.

    Returns a plain string. ``os.path.expanduser("~")`` is what ``Path.home()``
    calls underneath, minus the ``pathlib`` import.
    """
    env_path = os.environ.get("DOC_INDEX_PATH")
    if env_path:
        return env_path
    return os.path.join(os.path.expanduser("~"), ".doc-index")


def _iter_summary_sidecars(base: str):
    """Yield every ``<base>/*/*.summary.json`` path, in directory order.

    The ``os.scandir`` equivalent of ``Path(base).glob("*/*.summary.json")``,
    including its ordering (``pathlib`` scans with ``os.scandir`` too and does
    not sort), so a tie between two candidate roots resolves the same way it
    always did.
    """
    try:
        with os.scandir(base) as entries:
            dirs = [e.path for e in entries if e.is_dir()]
    except OSError:
        return
    for d in dirs:
        try:
            with os.scandir(d) as entries:
                names = [e.name for e in entries]
        except OSError:
            continue
        for name in names:
            if name.endswith(_SUMMARY_SUFFIX):
                yield os.path.join(d, name), d, name[: -len(_SUMMARY_SUFFIX)]


def _relative_within(target: str, root: str):
    """``target`` expressed relative to ``root``, or ``None`` when outside it.

    The ``os.path`` equivalent of ``Path.relative_to`` for the one question
    asked here. ``os.path.normcase`` is what carries the Windows
    case-insensitivity that ``PureWindowsPath`` comparison has built in, and it
    is length-preserving, so the slice below lands on the original casing.
    ``os.path.relpath`` is NOT the same function: it answers with ``..``
    segments where this must answer "outside".
    """
    t = os.path.normcase(target)
    r = os.path.normcase(root)
    if t == r:
        return ""
    if not r.endswith(os.sep):
        r += os.sep
    if t.startswith(r):
        return target[len(r):]
    return None


def _compare_to_mirror(source: str, mirror: str) -> str:
    """Compare a source file with its content mirror: ``"same"``, ``"differs"``
    or ``"unknown"``.

    ``"differs"`` is returned ONLY when the bytes are known to differ. Anything
    that cannot be settled is ``"unknown"``, because the caller must treat that
    as silence: a wrong ``"differs"`` tells the agent an index is out of date
    when it is not -- permanently, since nothing the agent can do makes the
    mtime older again.

    Only the extensions in ``_VERBATIM_MIRROR_EXTENSIONS`` can be compared: any
    other format is mirrored as preprocessed text, so its bytes never equal the
    source's and a comparison would call every one of them different.

    Replays the two ways a verbatim mirror still differs from its source
    (``index_local`` reads with ``encoding="utf-8", errors="replace",
    newline=""``, and ``preprocess_content`` drops a leading BOM): a source with
    a BOM, or with bytes that are not valid UTF-8, is compared in that form.
    """
    ext = os.path.splitext(source)[1].lower()
    if ext not in _VERBATIM_MIRROR_EXTENSIONS:
        return "unknown"
    try:
        with open(source, "rb") as f:
            src = f.read()
        with open(mirror, "rb") as f:
            mir = f.read()
    except OSError:
        return "unknown"
    if src == mir:
        return "same"
    text = src.decode("utf-8", errors="replace")
    if text.startswith("\ufeff"):
        text = text[1:]
    return "same" if text.encode("utf-8") == mir else "differs"


def _index_state(file_path: str) -> tuple:
    """Return (state, repo) where state is 'fresh', 'stale', 'unverified' or
    'unindexed'. Only 'fresh' steers and only 'stale' reports; the other two are
    silent.

    Answers "would jDocMunch actually serve this file, and serve it CURRENT?"
    without importing the storage layer or parsing an index. Reads only the tiny
    ``<name>.summary.json`` sidecars (sub-KB, a handful per box) for their
    ``source_root``, then stats the file's mirror copy.

    Staleness is decided by CONTENT, with the mtime and the size as cheap
    pre-filters. The mirror is written at index time and does not inherit the
    source's mtime. A source that is not newer than its mirror AND has the
    mirror's size is fresh without reading either file: that is the common case
    and it costs two stats. Two things send a file to the content comparison:

      * A source that IS newer proves nothing on its own: indexing is keyed on a
        content hash, and an incremental pass does not rewrite the mirror of a
        file whose hash did not change, so a ``touch``, a ``git stash pop`` or a
        revert to identical bytes leaves the source permanently newer than a
        mirror that is still correct.
      * A source of a different SIZE, whatever its mtime. A changed file can
        arrive with an older or preserved mtime (``cp -p``, ``rsync -t``, a tar
        extract), and an mtime-only gate would steer the reader at the stale
        index. Only for the verbatim formats: a preprocessed format's mirror is
        never the source's size, so for those a size difference means nothing.

    Only a source whose bytes differ from its mirror is reported stale (see
    ``_compare_to_mirror``). A source that needs comparing but whose format is
    mirrored as preprocessed text cannot be, and is 'unverified': neither
    steered at nor reported on. ⚠ The bound that remains: an edit that keeps
    both the size and an older-or-equal mtime is not seen.

    Fails to 'unindexed' on any surprise. Steering a reader toward an index that
    cannot answer -- or answers with superseded content -- is worse than staying
    quiet, because the reader was already about to get the right bytes.
    """
    try:
        target = os.path.realpath(os.path.abspath(file_path))
        base = _doc_index_base()
        if not os.path.isdir(base):
            return ("unindexed", "")

        best = None  # (root_len, repo, sidecar_dir, name, rel)
        for sidecar, sidecar_dir, name in _iter_summary_sidecars(base):
            try:
                with open(sidecar, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, ValueError):
                continue
            # A sidecar whose JSON is not an object is skipped like an unreadable
            # one. Without this, ``.get`` raises AttributeError, which neither
            # except clause here catches, and ONE corrupt sidecar would abort the
            # scan and silence the hint for every healthy index on the box.
            if not isinstance(meta, dict):
                continue
            root = (meta.get("source_root") or "").strip()
            repo = meta.get("repo") or ""
            if not root or not repo:
                continue
            try:
                root_path = os.path.realpath(os.path.abspath(root))
            except (ValueError, OSError):
                continue
            rel = _relative_within(target, root_path)
            if rel is None:
                continue
            # Most specific root wins: indexes nest (a repo index can contain a
            # docs-subdirectory index), and the narrower one is the better answer.
            score = len(root_path)
            if best is None or score > best[0]:
                best = (score, repo, sidecar_dir, name, rel)

        if best is None:
            return ("unindexed", "")
        _, repo, sidecar_dir, name, rel = best

        mirror = os.path.join(sidecar_dir, name, rel) if rel else os.path.join(sidecar_dir, name)
        if not os.path.isfile(mirror):
            # In an indexed tree but never indexed itself -- unsupported format,
            # gitignored, skipped for size, or added since the last pass.
            return ("unindexed", repo)

        src_stat = os.stat(target)
        mir_stat = os.stat(mirror)
        newer = src_stat.st_mtime > mir_stat.st_mtime
        resized = (
            src_stat.st_size != mir_stat.st_size
            and os.path.splitext(target)[1].lower() in _VERBATIM_MIRROR_EXTENSIONS
        )
        if not newer and not resized:
            return ("fresh", repo)
        verdict = _compare_to_mirror(target, mirror)
        if verdict == "differs":
            return ("stale", repo)
        if verdict == "same":
            return ("fresh", repo)
        # Newer than its mirror (or resized), and not comparable: say nothing.
        return ("unverified", repo)
    except (OSError, ValueError):
        return ("unindexed", "")


def _emit_additional_context(event_name: str, text: str) -> int:
    """Emit model-facing ``additionalContext`` for an exit-0 hook.

    Same shape as jcodemunch-mcp's ``_common._emit_additional_context``:
    ``{"hookSpecificOutput": {"hookEventName": ..., "additionalContext": ...}}``
    on stdout, exit 0. Exit 2 would also reach the model via stderr, but it
    blocks the call, and a hard deny breaks Read-before-Edit.

    NOT stderr. Claude Code's contract: "Stderr from a hook that exits 0 goes to
    the debug log only, never the transcript, and Claude never sees it."
    ``cli/hooks.py`` re-exports this for its other events (SessionStart).
    """
    print(json.dumps({
        "hookSpecificOutput": {"hookEventName": event_name, "additionalContext": text},
    }))
    return 0


@_guarded
def run_pretooluse() -> int:
    """PreToolUse hook: steer Read on large doc files toward jDocMunch tools.

    Steers ONLY when the steer would actually help: the file is a supported doc
    format, it is big enough to be worth structured retrieval, it IS in an index,
    and that index is CURRENT for it. A stale or absent index makes the advice
    actively harmful -- the reader was about to get the right bytes from disk,
    and we would be sending them to superseded ones. An unindexed file gets
    silence; a stale one gets a report of the fact and no steer.

    The note goes out as JSON ``additionalContext`` on stdout. It used to be a
    stderr print, which Claude Code's contract sends to the debug log only --
    "never the transcript, and Claude never sees it" -- so the hint had never
    reached a user or the model from 1.66.3 through 1.139.1 (#129).

    Returns 0 in every case. ``_guarded`` swallows (and breadcrumbs) anything
    unexpected so a malformed payload can never block a Read: previously the try
    covered only ``json.load``, and five valid-JSON shapes (``tool_input`` as a
    string, a top-level null/list/number, ``file_path`` as a number) crashed with
    a traceback whose first line the transcript then showed as a hook error.
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

    # Targeted reads (offset/limit set) are likely pre-edit -- allow silently.
    if tool_input.get("offset") is not None or tool_input.get("limit") is not None:
        return 0

    try:
        size = os.path.getsize(file_path)
    except OSError:
        return 0
    if size < _MIN_SIZE_BYTES:
        return 0

    state, repo = _index_state(file_path)
    if state == "fresh":
        _emit_additional_context(
            "PreToolUse",
            f"jDocMunch hint: this is a {size:,}-byte doc file, indexed and "
            f"current in jDocMunch as {repo}. Prefer search_sections + get_section "
            f"for exploration -- they return whole sections by byte range "
            f"instead of the whole file. Use Read when you need exact line "
            f"numbers for Edit."
        )
    elif state == "stale":
        # Report, do not act. Reindexing from here would run whole-corpus
        # tokenization (compute_corpus_stats runs over every section with a
        # content loader on save) on a trigger far hotter than Edit -- the same
        # unbounded fan-out that "took down a 16 GB machine" per the jdoc#76
        # note in ``cli/hooks.py``. It also could not help THIS read, which is
        # already underway.
        #
        # So the hook states the fact and the agent decides: it can see the
        # task, and this costs nothing. Staying silent would discard a fact
        # already paid for.
        #
        # The remedies named below must be things that EXIST: there is no
        # ``index_file`` MCP tool, only the ``index-file`` CLI subcommand, and
        # ``index_local`` is the MCP tool that re-reads a folder.
        _emit_additional_context(
            "PreToolUse",
            f"jDocMunch hint: this {size:,}-byte doc file is indexed as {repo}, "
            f"but the index is OLDER than the file, so search_sections and "
            f"get_section would return superseded content. Read is correct "
            f"here. To refresh just this file, run `jdocmunch-mcp index-file "
            f"<path>`; to refresh the whole folder, call the `index_local` "
            f"tool. The `jdocmunch-mcp watch` daemon keeps indexes current "
            f"automatically."
        )
    # 'unindexed' says nothing: there is nothing to steer toward, and a
    # note on every doc file in every unindexed tree is pure noise.
    return 0
