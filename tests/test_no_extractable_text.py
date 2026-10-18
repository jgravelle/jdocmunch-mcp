"""A scanned (image-only) PDF is named and left out, never indexed empty.

Measured 2026-10-18 on this repo: a PDF holding only an image of text converted
to ``''`` with no error. `index_local` reported it under `files`, kept
`coverage_complete: true`, gave no warning, and stored one empty root section,
so a search for its contents found nothing and nothing said why. jDocMunch has
no OCR (an optional OCR extra is a separate, unbuilt proposal), so the honest
answer is to say the document could not be read.
"""

from __future__ import annotations

import pytest

from jdocmunch_mcp.parser import office


class _Stub:
    def __init__(self):
        self.by_name = {}

    def convert(self, path):
        from pathlib import Path

        class _R:
            pass

        r = _R()
        r.text_content = self.by_name.get(Path(path).name, "")
        return r


@pytest.fixture
def stub(monkeypatch):
    office._reset_for_tests()
    s = _Stub()
    monkeypatch.setattr(office, "_AVAILABLE", True)
    monkeypatch.setattr(office, "_get_converter", lambda: s)
    monkeypatch.setenv("JDOCMUNCH_SHARE_SAVINGS", "0")
    yield s
    office._reset_for_tests()


def _corpus(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "note.md").write_text("# Note\n\nPlain text.\n", encoding="utf-8")
    (docs / "scanned.pdf").write_bytes(b"%PDF-1.4 image only")
    (docs / "real.pdf").write_bytes(b"%PDF-1.4 with text")
    return docs


def _index(docs, store, **kw):
    from jdocmunch_mcp.tools.index_local import index_local
    return index_local(path=str(docs), name="ntx", use_ai_summaries=False,
                       use_embeddings=False, storage_path=str(store), **kw)


@pytest.mark.parametrize("empty", ["", "   \n\n", "\f\f\n", "\n- \n"])
def test_a_document_with_no_readable_text_is_named_and_left_out(tmp_path, stub, empty):
    docs = _corpus(tmp_path)
    stub.by_name = {"scanned.pdf": empty, "real.pdf": "# Report\n\nQuarterly figures.\n"}
    r = _index(docs, tmp_path / "store")

    assert r["success"] is True
    assert "scanned.pdf" not in r["files"]
    assert {"note.md", "real.pdf"} <= set(r["files"])
    assert r["skip_counts"]["no_extractable_text"] == 1
    assert r["coverage_complete"] is False
    assert "scanned.pdf" in r["skipped_paths"]["no_extractable_text"]
    assert any("scanned.pdf" in w and "scanned or image-only" in w for w in r["warnings"])


def test_no_empty_section_is_stored_for_it(tmp_path, stub):
    from jdocmunch_mcp.storage.doc_store import DocStore
    docs = _corpus(tmp_path)
    stub.by_name = {"real.pdf": "# Report\n\nFigures.\n"}
    r = _index(docs, tmp_path / "store")
    store = DocStore(base_path=str(tmp_path / "store"))
    owner, name = store._resolve_repo(r["repo"])
    index = store.load_index(owner, name)
    assert "scanned.pdf" not in index.doc_paths
    assert not [s for s in index.sections if s.get("doc_path") == "scanned.pdf"]


def test_an_old_empty_entry_is_cleared_on_refresh(tmp_path, stub):
    """A scan indexed by an older version as an empty document goes away."""
    docs = _corpus(tmp_path)
    stub.by_name = {"scanned.pdf": "x1", "real.pdf": "# Report\n\nFigures.\n"}
    first = _index(docs, tmp_path / "store")
    assert "scanned.pdf" in first["files"]
    stub.by_name["scanned.pdf"] = ""
    (docs / "scanned.pdf").write_bytes(b"%PDF-1.4 image only, rescanned")
    second = _index(docs, tmp_path / "store")
    assert second["deleted"] == 1
    assert second["coverage_complete"] is False


def test_a_document_with_text_is_unchanged(tmp_path, stub):
    docs = _corpus(tmp_path)
    stub.by_name = {"scanned.pdf": "Page 1 of 1", "real.pdf": "# Report\n\nFigures.\n"}
    r = _index(docs, tmp_path / "store")
    assert "scanned.pdf" in r["files"]
    assert "no_extractable_text" not in (r.get("skip_counts") or {})


def test_index_file_skips_it_with_the_reason(tmp_path, stub):
    from jdocmunch_mcp.tools.index_file import index_file
    docs = _corpus(tmp_path)
    stub.by_name = {"scanned.pdf": "x", "real.pdf": "# Report\n\nFigures.\n"}
    _index(docs, tmp_path / "store")
    stub.by_name["scanned.pdf"] = ""
    (docs / "scanned.pdf").write_bytes(b"%PDF-1.4 now image only")
    out = index_file(file_path=str(docs / "scanned.pdf"), storage_path=str(tmp_path / "store"),
                     use_ai_summaries=False, name="ntx")
    assert out["skipped"] is True
    assert out["reason"] == "no_extractable_text"
    assert "scanned or image-only" in out["warning"]


@pytest.mark.parametrize("text,ok", [
    ("", False), ("  \n\t\f", False), ("- * #", False),
    ("a", True), ("7", True), ("Ü", True), ("日本", True),
])
def test_has_readable_text(text, ok):
    assert office.has_readable_text(text) is ok
