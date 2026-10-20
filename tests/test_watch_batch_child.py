"""jdoc#189 part 1: each watch batch runs in a child interpreter.

The batch's peak memory (a full monolith rewrite) is paid in a process that
exits, so the watcher stays near its baseline. Diagnosed and measured by
@DreDay4u in #189. These tests run the real child through
``python -m jdocmunch_mcp.watch_batch``.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

import pytest

from jdocmunch_mcp import watch as W
from jdocmunch_mcp.storage.doc_store import DocStore


def _fake_popen(code, out, err):
    class _P:
        def __init__(self, argv, **kw):
            self.args, self.returncode = argv, code

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def communicate(self, data):
            return out, err
    return _P


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    from jdocmunch_mcp.tools.index_local import index_local
    monkeypatch.setenv("JDOCMUNCH_SHARE_SAVINGS", "0")
    monkeypatch.delenv("JDOCMUNCH_WATCH_INPROCESS", raising=False)
    # The child must import this checkout, not an installed copy.
    src = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src")
    monkeypatch.setenv("PYTHONPATH", src + os.pathsep + os.environ.get("PYTHONPATH", ""))
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# Alpha\n\nFirst.\n", encoding="utf-8")
    store = tmp_path / "store"
    index_local(path=str(docs), name="wb", use_ai_summaries=False, use_embeddings=False,
                extra_ignore_patterns=["skip/**"], storage_path=str(store))
    return docs, str(store)


def test_a_batch_in_the_child_updates_the_index(corpus):
    docs, store = corpus
    (docs / "b.md").write_text("# Bravo\n\nZEBRAQUOKKA.\n", encoding="utf-8")
    result = W._reindex_batch(str(docs.resolve()), "wb", [str((docs / "b.md").resolve())],
                              store, False)
    assert result["success"] is True
    index = DocStore(base_path=store).load_index("local", "wb")
    assert "b.md" in index.doc_paths


def test_an_error_result_is_not_logged_as_a_reindex(corpus, tmp_path, caplog):
    _docs, store = corpus
    empty = tmp_path / "empty"
    empty.mkdir()
    said = []
    with caplog.at_level("WARNING", logger="jdocmunch_mcp.watch"):
        orig = W._watcher_output
        W._watcher_output = lambda msg, **kw: said.append(msg)
        try:
            asyncio.run(W._handle_changes({(1, str(empty / "x.md"))},
                                          {str(empty.resolve()): "nothing-here"},
                                          store, False, True, None))
        finally:
            W._watcher_output = orig
    assert said == []
    assert any("reindex failed for nothing-here" in r.message for r in caplog.records)


def test_the_child_exits_nonzero_on_an_error_result(corpus, tmp_path):
    _docs, store = corpus
    empty = tmp_path / "empty"
    empty.mkdir()
    job = {"root": str(empty), "name": "nothing-here", "paths": [str(empty / "x.md")],
           "storage_path": store, "use_ai_summaries": False}
    proc = subprocess.run([sys.executable, "-m", "jdocmunch_mcp.watch_batch"],
                          input=json.dumps(job), capture_output=True, text=True,
                          encoding="utf-8")
    assert proc.returncode == 1
    assert json.loads(proc.stdout.strip().splitlines()[-1])["success"] is False


def test_a_child_with_no_result_reads_as_a_failure(monkeypatch):
    monkeypatch.setattr(W.subprocess, "Popen", _fake_popen(3, "not json\n", "Traceback: boom"))
    result = W._reindex_batch_in_child({"root": "r", "name": "n", "paths": []})
    assert result["success"] is False and "boom" in result["error"]


def test_no_console_window_per_batch_on_windows(monkeypatch):
    seen = {}

    def fake_popen(argv, **kw):
        seen.update(kw, argv=argv)
        return _fake_popen(0, '{"success": true}\n', "")(argv, **kw)

    monkeypatch.setattr(W.sys, "platform", "win32")
    monkeypatch.setattr(W.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(W.subprocess, "Popen", fake_popen)
    W._reindex_batch_in_child({"root": "r", "name": "n", "paths": []})
    assert seen["creationflags"] == 0x08000000
    assert seen["argv"][1:] == ["-m", "jdocmunch_mcp.watch_batch"]


def test_inprocess_switch_keeps_the_old_path(corpus, monkeypatch):
    docs, store = corpus
    monkeypatch.setenv("JDOCMUNCH_WATCH_INPROCESS", "1")
    monkeypatch.setattr(W, "_reindex_batch_in_child", lambda job: pytest.fail("spawned a child"))
    (docs / "c.md").write_text("# Charlie\n\nThird.\n", encoding="utf-8")
    result = W._reindex_batch(str(docs.resolve()), "wb", [str((docs / "c.md").resolve())],
                              store, False)
    assert result["success"] is True


def test_shape_patterns_come_from_the_summary_not_the_monolith(corpus, monkeypatch):
    docs, store = corpus
    monkeypatch.setattr(DocStore, "load_index",
                        lambda *a, **k: pytest.fail("parsed the monolith"))
    _gi, extra = W._corpus_filters(str(docs), "wb", store)
    assert extra is not None and extra.match_file("skip/x.md")


def test_a_summary_without_the_key_falls_back_to_the_monolith(corpus):
    docs, store = corpus
    ds = DocStore(base_path=store)
    path = ds._summary_path("local", "wb")
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["corpus_shape_patterns"]
    path.write_text(json.dumps(data), encoding="utf-8")
    _gi, extra = W._corpus_filters(str(docs), "wb", store)
    assert extra is not None and extra.match_file("skip/x.md")
