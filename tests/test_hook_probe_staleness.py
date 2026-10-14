"""The PreToolUse staleness gate decides by CONTENT, not by mtime alone.

``_index_state`` used to call a file stale whenever its mtime was newer than its
content mirror's. But indexing is keyed on a content hash, and an incremental
pass does not rewrite the mirror of a file whose hash did not change. So a
``touch``, a ``git stash pop`` or a revert to identical bytes left the source
permanently newer than a mirror that was still correct, and the hook told the
agent -- falsely, and for good -- that the index would return superseded content.

Some formats are mirrored as PREPROCESSED text (``.html`` becomes Markdown, and
so on), so a byte comparison against them is meaningless. For those the gate
never claims staleness.
"""

import os
import re
import time
from pathlib import Path

import pytest

from jdocmunch_mcp.cli import _hook_probe
from jdocmunch_mcp.cli._hook_probe import (
    _DOC_EXTENSIONS,
    _VERBATIM_MIRROR_EXTENSIONS,
    _index_state,
)

BODY = "# Title\n\n" + "word " * 700


def _index(tmp_path, monkeypatch, filename, content, *, binary=False):
    """Index one file for real and point the probe at the store."""
    from jdocmunch_mcp.tools.index_local import index_local
    src, store = tmp_path / "src", tmp_path / "store"
    src.mkdir(exist_ok=True)
    target = src / filename
    if binary:
        target.write_bytes(content)
    else:
        target.write_text(content, encoding="utf-8")
    index_local(path=str(src), name="stale-probe", use_ai_summaries=False,
                use_embeddings=False, storage_path=str(store))
    monkeypatch.setenv("DOC_INDEX_PATH", str(store))
    return target, src, store


def _reindex(src, store):
    from jdocmunch_mcp.tools.index_local import index_local
    index_local(path=str(src), name="stale-probe", use_ai_summaries=False,
                use_embeddings=False, storage_path=str(store))


def _forward(path, seconds=120):
    future = time.time() + seconds
    os.utime(path, (future, future))


def _mirror(store: Path, name: str) -> Path:
    found = [p for p in store.rglob(name) if p.is_file() and "_hooks" not in p.parts]
    assert found, f"no mirror of {name} under {store}"
    return found[0]


def test_touch_with_identical_bytes_is_fresh(tmp_path, monkeypatch):
    target, _, _ = _index(tmp_path, monkeypatch, "doc.md", BODY)
    _forward(target)
    state, _ = _index_state(str(target))
    assert state == "fresh", (
        "a source newer than its mirror but byte-identical to it is not stale: "
        "an incremental index does not rewrite the mirror of an unchanged file"
    )


def test_a_real_edit_is_stale(tmp_path, monkeypatch):
    target, _, _ = _index(tmp_path, monkeypatch, "doc.md", BODY)
    target.write_text(BODY + "\nan added line\n", encoding="utf-8")
    _forward(target)
    assert _index_state(str(target))[0] == "stale"


def test_a_same_size_edit_is_stale(tmp_path, monkeypatch):
    """The size is not a proxy for the content."""
    target, _, _ = _index(tmp_path, monkeypatch, "doc.md", BODY)
    target.write_text(BODY.replace("word", "ward"), encoding="utf-8")
    _forward(target)
    assert _index_state(str(target))[0] == "stale"


def test_a_changed_file_with_an_older_mtime_is_stale(tmp_path, monkeypatch):
    """The mtime is a pre-filter, not the verdict: a file can arrive changed with
    an mtime OLDER than its mirror's, and must not be steered at a stale index."""
    target, _, store = _index(tmp_path, monkeypatch, "doc.md", BODY)
    target.write_text(BODY + "\nan added line\n", encoding="utf-8")
    old = _mirror(store, "doc.md").stat().st_mtime - 3600
    os.utime(target, (old, old))
    assert target.stat().st_mtime < _mirror(store, "doc.md").stat().st_mtime
    assert _index_state(str(target))[0] == "stale"


def test_a_changed_file_with_a_preserved_mtime_is_stale(tmp_path, monkeypatch):
    """`cp -p`, `rsync -t` and a tar extract carry the ORIGINAL mtime over new
    content."""
    target, _, _ = _index(tmp_path, monkeypatch, "doc.md", BODY)
    original = target.stat().st_mtime_ns
    target.write_text(BODY + "\nan added line\n", encoding="utf-8")
    os.utime(target, ns=(original, original))
    assert _index_state(str(target))[0] == "stale"


def test_an_unchanged_file_is_fresh_without_reading_either_copy(tmp_path, monkeypatch):
    """The common case stays at two stats: same size, not newer, no read."""
    target, _, _ = _index(tmp_path, monkeypatch, "doc.md", BODY)
    opened = []
    real_open = open

    def spy(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", spy)
    state = _index_state(str(target))[0]
    monkeypatch.undo()
    assert state == "fresh"
    assert not [p for p in opened if not p.endswith(".summary.json")], opened


def test_an_edit_that_is_then_reverted_is_fresh_again(tmp_path, monkeypatch):
    target, _, _ = _index(tmp_path, monkeypatch, "doc.md", BODY)
    target.write_text(BODY + "\nchanged\n", encoding="utf-8")
    _forward(target)
    assert _index_state(str(target))[0] == "stale"
    target.write_text(BODY, encoding="utf-8")
    _forward(target, 240)
    assert _index_state(str(target))[0] == "fresh"


def test_an_incremental_pass_leaves_the_mirror_alone_so_the_premise_holds(tmp_path, monkeypatch):
    """The premise the gate rests on, measured rather than assumed: reindexing
    a touched file whose hash is unchanged does NOT rewrite its mirror."""
    target, src, store = _index(tmp_path, monkeypatch, "doc.md", BODY)
    mirror = _mirror(store, "doc.md")
    before = mirror.stat().st_mtime_ns
    _forward(target)
    _reindex(src, store)
    assert mirror.stat().st_mtime_ns == before, "premise broken: mirror rewritten"
    assert _index_state(str(target))[0] == "fresh"


def test_an_edit_then_a_reindex_is_fresh(tmp_path, monkeypatch):
    target, src, store = _index(tmp_path, monkeypatch, "doc.md", BODY)
    target.write_text(BODY + "\nanother line\n", encoding="utf-8")
    _forward(target)
    assert _index_state(str(target))[0] == "stale"
    _reindex(src, store)
    assert _index_state(str(target))[0] == "fresh"


def test_a_bom_does_not_make_an_untouched_file_stale(tmp_path, monkeypatch):
    """preprocess_content drops a leading BOM, so the mirror never has one."""
    content = b"\xef\xbb\xbf" + BODY.encode("utf-8")
    target, _, store = _index(tmp_path, monkeypatch, "bom.md", content, binary=True)
    assert not _mirror(store, "bom.md").read_bytes().startswith(b"\xef\xbb\xbf")
    _forward(target)
    assert _index_state(str(target))[0] == "fresh"


def test_invalid_utf8_does_not_make_an_untouched_file_stale(tmp_path, monkeypatch):
    """index_local reads with errors="replace", so the mirror holds U+FFFD."""
    content = BODY.encode("utf-8") + b"\n\x9d trailing\n"
    target, _, _ = _index(tmp_path, monkeypatch, "bad.md", content, binary=True)
    _forward(target)
    assert _index_state(str(target))[0] == "fresh"


@pytest.mark.parametrize("filename,content", [
    ("page.html", "<html><body><h1>Title</h1>" + "<p>word</p>" * 400 + "</body></html>"),
    ("data.json", '{"items": [' + ",".join('{"k": %d}' % i for i in range(400)) + "]}"),
    ("note.mdx", "import X from './x'\n\n# Title\n\n" + "word " * 700),
    ("conf.jsonc", '{\n  // c\n  "items": [' + ",".join(str(i) for i in range(600)) + "]\n}"),
], ids=["html", "json", "mdx", "jsonc"])
def test_a_preprocessed_format_never_claims_staleness(tmp_path, monkeypatch, filename, content):
    """These are mirrored as converted text. Their bytes never equal the
    source's, so a byte comparison would call EVERY newer one stale; the gate
    must say nothing instead -- neither steer nor accuse."""
    target, _, store = _index(tmp_path, monkeypatch, filename, content)
    assert _mirror(store, filename).read_bytes() != content.encode("utf-8"), (
        "premise: this format's mirror is not the source text"
    )
    assert _index_state(str(target))[0] == "fresh"
    _forward(target)
    state, repo = _index_state(str(target))
    assert state not in ("stale", "fresh"), state
    assert repo  # still known to be indexed; just not verifiable
    target.write_text(content + " ", encoding="utf-8")
    _forward(target, 240)
    assert _index_state(str(target))[0] not in ("stale", "fresh")


def test_the_unverified_state_emits_no_hint(tmp_path, monkeypatch, capsys):
    import io
    import json

    from jdocmunch_mcp.cli.hooks import run_pretooluse
    content = "<html><body><h1>Title</h1>" + "<p>word</p>" * 400 + "</body></html>"
    target, _, _ = _index(tmp_path, monkeypatch, "page.html", content)
    _forward(target)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"tool_input": {"file_path": str(target)}})))
    assert run_pretooluse() == 0
    assert capsys.readouterr().out == ""


def test_the_verbatim_set_matches_what_preprocess_content_leaves_alone():
    """Ties the set to the function that decides it, so a format that gains a
    transform (or loses one) fails here instead of drifting."""
    from jdocmunch_mcp.parser import preprocess_content
    sample = ("---\ntitle: t\n---\nimport X from 'y'\n\n# Title\n\n"
              "<b>x</b> {\"a\": 1}\nplain text here\n")
    for ext in sorted(_DOC_EXTENSIONS):
        unchanged = preprocess_content(sample, "f" + ext) == sample
        assert unchanged == (ext in _VERBATIM_MIRROR_EXTENSIONS), (
            f"{ext}: preprocess_content {'keeps' if unchanged else 'transforms'} "
            f"the text but _VERBATIM_MIRROR_EXTENSIONS says otherwise"
        )
    assert _VERBATIM_MIRROR_EXTENSIONS <= _DOC_EXTENSIONS


def test_a_sidecar_that_is_not_an_object_does_not_silence_the_others(tmp_path, monkeypatch):
    """One corrupt ``*.summary.json`` used to abort the scan with an
    AttributeError, hiding every healthy index on the box."""
    target, _, store = _index(tmp_path, monkeypatch, "doc.md", BODY)
    for i, junk in enumerate(("[]", "42", "null", '"text"')):
        d = store / f"aa-corrupt{i}"
        d.mkdir()
        (d / f"junk{i}.summary.json").write_text(junk, encoding="utf-8")
    state, repo = _index_state(str(target))
    assert state == "fresh" and repo


# --- the hint names things that exist ---------------------------------------

def _stale_hint(tmp_path, monkeypatch, capsys) -> str:
    import io
    import json

    from jdocmunch_mcp.cli.hooks import run_pretooluse
    target, _, _ = _index(tmp_path, monkeypatch, "doc.md", BODY)
    target.write_text(BODY + "\nedited\n", encoding="utf-8")
    _forward(target)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"tool_input": {"file_path": str(target)}})))
    assert run_pretooluse() == 0
    return json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]


def test_the_stale_hint_names_only_tools_and_commands_that_exist(tmp_path, monkeypatch, capsys):
    from jdocmunch_mcp.server import _all_tools
    hint = _stale_hint(tmp_path, monkeypatch, capsys)
    tools = {t.name for t in _all_tools()}
    named = set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", hint))
    assert named, "the hint names no tools at all"
    assert named <= tools, f"the hint names tools that do not exist: {sorted(named - tools)}"
    assert "index_local" in named
    # There is no `index_file` MCP tool -- only the CLI subcommand.
    assert "index_file" not in hint
    assert "jdocmunch-mcp index-file" in hint
    main_src = (Path(_hook_probe.__file__).parent / "main.py").read_text(encoding="utf-8")
    assert '"index-file"' in main_src, "the CLI subcommand the hint names is gone"


# --- an unexpected failure leaves a breadcrumb, without importing the reindexer

def test_an_unexpected_error_leaves_a_breadcrumb_when_logging_is_on(tmp_path, monkeypatch):
    import io
    import json

    store = tmp_path / "store"
    monkeypatch.setenv("DOC_INDEX_PATH", str(store))
    monkeypatch.setenv("JDOCMUNCH_HOOK_LOG", "1")
    big = tmp_path / "big.md"
    big.write_text(BODY, encoding="utf-8")

    def boom(_path):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(_hook_probe, "_index_state", boom)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"tool_input": {"file_path": str(big)}})))
    assert _hook_probe.run_pretooluse() == 0
    log = (store / "_hooks" / "reindex.log").read_text(encoding="utf-8")
    assert "run_pretooluse failed: RuntimeError: probe exploded" in log


def test_no_breadcrumb_and_no_heavy_import_when_logging_is_off(tmp_path, monkeypatch):
    import subprocess
    import sys
    code = (
        "import io, json, sys\n"
        "sys.stdin = io.StringIO(json.dumps({'tool_input': {'file_path': 'x.md'}}))\n"
        "from jdocmunch_mcp.cli import _hook_probe\n"
        "def boom(p):\n"
        "    raise RuntimeError('x')\n"
        "_hook_probe._index_state = boom\n"
        "import os\n"
        "open('x.md', 'w').write('word ' * 1000)\n"
        "rc = _hook_probe.run_pretooluse()\n"
        "print('RC=%d HOOKS=%s PATHLIB=%s' % (rc, 'jdocmunch_mcp.cli.hooks' in sys.modules,"
        " 'pathlib' in sys.modules))\n"
    )
    env = {**os.environ,
           "PYTHONPATH": str(Path(_hook_probe.__file__).resolve().parents[2]),
           "DOC_INDEX_PATH": str(tmp_path / "store")}
    env.pop("JDOCMUNCH_HOOK_LOG", None)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, cwd=str(tmp_path),
                       timeout=120)
    assert r.returncode == 0, r.stderr
    assert "RC=0 HOOKS=False" in r.stdout, r.stdout
    assert not (tmp_path / "store" / "_hooks").exists()
