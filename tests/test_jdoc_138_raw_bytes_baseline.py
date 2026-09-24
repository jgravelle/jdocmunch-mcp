"""jdoc#138: the token-savings baseline of the metadata tools read 0 after a reload.

``search_sections``, ``get_toc``, ``get_toc_tree`` and ``get_document_outline``
summed section ``content`` to get the bytes a caller would otherwise have read.
``Section.to_dict`` does not persist ``content`` for byte-addressed sections, so
on any index loaded from disk that sum was 0 and ``tokens_saved`` was floored to
0 on every call. The baseline now comes from the cached files.

Reported by @sdjrdriver.
"""

from __future__ import annotations

import pytest

from jdocmunch_mcp.storage import doc_store as _doc_store
from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.storage.token_tracker import estimate_savings

_BODY = "To configure the widget you must set the `widget_timeout` option. " * 40
_OTHER = "Background prose about widgets. " * 40


@pytest.fixture
def indexed(tmp_path, monkeypatch):
    """Six documents in one subfolder, two in another, indexed and then
    RELOADED from disk, the way a fresh server process sees them."""
    monkeypatch.setenv("JDOCMUNCH_SHARE_SAVINGS", "0")
    from jdocmunch_mcp.tools.index_local import index_local

    src = tmp_path / "docs"
    (src / "guide").mkdir(parents=True)
    (src / "api").mkdir()
    for i in range(6):
        (src / "guide" / f"doc{i}.md").write_text(
            f"# Doc {i}\n\n## Configuring the widget\n\n{_BODY}\n\n"
            f"## Other section {i}\n\n{_OTHER}\n",
            encoding="utf-8",
        )
    for i in range(2):
        (src / "api" / f"ref{i}.md").write_text(
            f"# Ref {i}\n\n## Endpoint\n\n{_OTHER}\n", encoding="utf-8",
        )
    storage = tmp_path / "store"
    r = index_local(
        path=str(src), name="i138", use_ai_summaries=False,
        use_embeddings=False, storage_path=str(storage),
    )
    assert r.get("success"), r
    repo = r["repo"]

    _doc_store._INDEX_CACHE.clear()
    store = DocStore(base_path=str(storage))
    owner, name = store._resolve_repo(repo)
    index = store.load_index(owner, name)
    # The precondition the defect needs. If this ever fails, content has
    # started round-tripping and the old code would have passed too.
    assert index.sections
    assert not any(s.get("content") for s in index.sections)

    def size(doc_path):
        return store._safe_content_path(store._content_dir(owner, name), doc_path).stat().st_size

    return repo, str(storage), store, owner, name, size


def test_search_sections_saves_tokens_after_reload(indexed):
    from jdocmunch_mcp.tools.search_sections import search_sections
    repo, storage, *_ = indexed
    out = search_sections(repo=repo, query="configure widget timeout",
                          storage_path=storage)
    assert out["results"]
    assert out["_meta"]["tokens_saved"] > 0


def test_get_toc_saves_tokens_after_reload(indexed):
    from jdocmunch_mcp.tools.get_toc import get_toc
    repo, storage, *_ = indexed
    assert get_toc(repo=repo, storage_path=storage)["_meta"]["tokens_saved"] > 0


def test_get_toc_tree_saves_tokens_after_reload(indexed):
    from jdocmunch_mcp.tools.get_toc_tree import get_toc_tree
    repo, storage, *_ = indexed
    assert get_toc_tree(repo=repo, storage_path=storage)["_meta"]["tokens_saved"] > 0


def test_get_document_outline_saves_tokens_after_reload(indexed):
    from jdocmunch_mcp.tools.get_document_outline import get_document_outline
    repo, storage, *_ = indexed
    out = get_document_outline(repo=repo, doc_path="guide/doc0.md", storage_path=storage)
    assert out["_meta"]["tokens_saved"] > 0


def test_outline_baseline_is_the_document_file_size(indexed):
    """Pins the VALUE, not just > 0: the baseline is exactly the cached file."""
    from jdocmunch_mcp.tools.get_document_outline import get_document_outline
    repo, storage, _store, _o, _n, size = indexed
    out = get_document_outline(repo=repo, doc_path="guide/doc0.md", storage_path=storage)
    response_bytes = sum(len(str(o).encode("utf-8")) for o in out["sections"])
    assert out["_meta"]["tokens_saved"] == estimate_savings(size("guide/doc0.md"), response_bytes)


def test_raw_doc_bytes_counts_each_document_once(indexed):
    _repo, _storage, store, owner, name, size = indexed
    one = size("guide/doc0.md")
    assert store.raw_doc_bytes(owner, name, ["guide/doc0.md"] * 5) == one
    assert store.raw_doc_bytes(owner, name, ["guide/doc0.md", "guide/doc1.md"]) == (
        one + size("guide/doc1.md")
    )


def test_raw_doc_bytes_ignores_missing_empty_and_escaping_paths(indexed):
    _repo, _storage, store, owner, name, _size = indexed
    assert store.raw_doc_bytes(owner, name, ["", None, "nope.md", "../../outside.md"]) == 0


def test_toc_baseline_is_scoped_to_path_glob(indexed):
    """A glob narrows the baseline to the files the caller would have read,
    never the whole index."""
    from jdocmunch_mcp.tools.get_toc import get_toc
    repo, storage, _store, _o, _n, size = indexed
    out = get_toc(repo=repo, path_glob="api/*", storage_path=storage)
    response_bytes = sum(len(str(t).encode("utf-8")) for t in out["sections"])
    raw = size("api/ref0.md") + size("api/ref1.md")
    assert out["_meta"]["tokens_saved"] == estimate_savings(raw, response_bytes)
