"""v1.146.0 — a posting-index build checks each document's path once, and the
verdict ends with the build.

The loader ``DocStore.load_index`` injects is long-lived: its index sits in the
process-wide ``_INDEX_CACHE`` and every later tool call gets the same object.
So the speed claim and the security claim are tested separately, and the
security half is tested through that cache, not through a fresh ``DocStore``:

1. **Speed.** Inside ``_loader.sweep()``, one ``_safe_content_path`` call per
   document, however many sections it has.
2. **Lifetime.** Outside a sweep, every read resolves afresh, so a document
   replaced by a symlink out of the content mirror after a cold search is
   refused on the next read -- including through the cached index.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from jdocmunch_mcp.retrieval.prune import PostingIndex
from jdocmunch_mcp.storage import doc_store as _ds
from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.search_sections import search_sections

DOCS = 12
HEADINGS = 6


@pytest.fixture
def store(tmp_path):
    src = tmp_path / "docs"
    src.mkdir()
    for d in range(DOCS):
        body = [f"# Page {d}\n"]
        for h in range(HEADINGS):
            body.append(f"\n## Part {d}-{h}\n\nlantern quartz prose {d} {h}.\n")
        (src / f"page-{d:02d}.md").write_text("".join(body), encoding="utf-8")
    storage = tmp_path / "store"
    r = index_local(path=str(src), name="sweep", storage_path=str(storage),
                    use_ai_summaries=False, use_embeddings=False)
    assert r.get("success") is True, r
    _ds._INDEX_CACHE.clear()
    yield storage
    _ds._INDEX_CACHE.clear()


@pytest.fixture
def resolve_calls(monkeypatch):
    calls: list = []
    orig = DocStore._safe_content_path

    def counted(self, content_dir, relative_path, resolved_root=None):
        calls.append(relative_path)
        return orig(self, content_dir, relative_path, resolved_root)

    monkeypatch.setattr(DocStore, "_safe_content_path", counted)
    return calls


def _loaded(storage):
    return DocStore(base_path=str(storage)).load_index("local", "sweep")


def _with_body(index):
    return [s for s in index.sections if s.get("byte_end", 0) > s.get("byte_start", 0)]


def test_a_build_checks_each_document_once(store, resolve_calls):
    index = _loaded(store)
    sections = _with_body(index)
    docs = {s["doc_path"] for s in sections}
    assert len(sections) > 2 * len(docs), "the fixture must have many sections per document"

    resolve_calls.clear()
    PostingIndex.build(index.sections, content_loader=index._content_loader)
    assert sorted(resolve_calls) == sorted(docs)


def test_the_build_reads_the_same_bodies_as_unscoped_reads(store):
    index = _loaded(store)
    loader = index._content_loader
    scoped = PostingIndex.build(index.sections, content_loader=loader).postings
    unscoped = PostingIndex._build(index.sections, content_loader=loader).postings
    assert scoped == unscoped
    assert "lantern" in scoped


def test_outside_a_sweep_every_read_resolves(store, resolve_calls):
    index = _loaded(store)
    loader = index._content_loader
    sec = _with_body(index)[0]
    resolve_calls.clear()
    for _ in range(3):
        assert loader(sec["doc_path"], sec["byte_start"], sec["byte_end"])
    assert len(resolve_calls) == 3


def test_the_verdicts_end_with_the_sweep(store, resolve_calls):
    index = _loaded(store)
    loader = index._content_loader
    sec = _with_body(index)[0]
    with loader.sweep():
        loader(sec["doc_path"], sec["byte_start"], sec["byte_end"])
        loader(sec["doc_path"], sec["byte_start"], sec["byte_end"])
    assert len(resolve_calls) == 1
    loader(sec["doc_path"], sec["byte_start"], sec["byte_end"])
    assert len(resolve_calls) == 2


def test_the_verdicts_end_even_when_the_sweep_raises(store, resolve_calls):
    index = _loaded(store)
    loader = index._content_loader
    sec = _with_body(index)[0]
    with pytest.raises(RuntimeError):
        with loader.sweep():
            loader(sec["doc_path"], sec["byte_start"], sec["byte_end"])
            raise RuntimeError("mid-sweep failure")
    loader(sec["doc_path"], sec["byte_start"], sec["byte_end"])
    assert len(resolve_calls) == 2


def test_a_nested_sweep_keeps_the_outer_memo(store, resolve_calls):
    index = _loaded(store)
    loader = index._content_loader
    sec = _with_body(index)[0]
    with loader.sweep():
        loader(sec["doc_path"], sec["byte_start"], sec["byte_end"])
        with loader.sweep():
            loader(sec["doc_path"], sec["byte_start"], sec["byte_end"])
        loader(sec["doc_path"], sec["byte_start"], sec["byte_end"])
    assert len(resolve_calls) == 1


def test_one_threads_sweep_does_not_answer_for_another(store, resolve_calls):
    index = _loaded(store)
    loader = index._content_loader
    sec = _with_body(index)[0]
    args = (sec["doc_path"], sec["byte_start"], sec["byte_end"])
    with loader.sweep():
        loader(*args)
        worker = threading.Thread(target=lambda: [loader(*args), loader(*args)])
        worker.start()
        worker.join()
    assert len(resolve_calls) == 3


def test_a_document_swapped_for_an_outbound_symlink_is_refused_through_the_cached_index(
    store, tmp_path
):
    """⚠⚠ The escape the candidate check exists to catch, reached the way a
    long-lived server reaches it: a cold search builds the posting index
    (one sweep), the mirrored file is then replaced by a symlink pointing out
    of the content mirror, and a LATER tool call reads through the same cached
    index. That read must be refused."""
    found = search_sections(repo="local/sweep", query="lantern", storage_path=str(store),
                            semantic=False)
    assert found.get("results"), found

    index = _loaded(store)
    assert getattr(index, "_posting_index", None) is not None, (
        "the search did not build the posting index, so no sweep ran and this "
        "test could not tell a kept memo from a dropped one"
    )
    sec = _with_body(index)[0]
    content_dir = Path(store) / "local" / "sweep"
    mirrored = content_dir / sec["doc_path"]
    assert mirrored.is_file()

    outside = tmp_path / "outside.md"
    outside.write_text("SECRET " * 200, encoding="utf-8")
    mirrored.unlink()
    try:
        os.symlink(outside, mirrored)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - no symlink privilege
        pytest.skip(f"cannot create a symlink here: {exc}")

    again = _loaded(store)
    assert again is index, "the second load must come from _INDEX_CACHE for this test to mean anything"
    text = again._content_loader(sec["doc_path"], sec["byte_start"], sec["byte_end"])
    assert "SECRET" not in text
    assert text == ""


def test_a_document_already_escaping_is_refused_for_every_section_of_a_build(
    store, tmp_path
):
    """The memo keeps REFUSALS too. A mirrored document that is already a
    symlink out of the mirror when a build starts must contribute none of its
    target's terms, from any of its sections, and the build must still index
    every other document."""
    index = _loaded(store)
    sections = _with_body(index)
    victim = sections[0]["doc_path"]
    victim_sections = [s for s in sections if s["doc_path"] == victim]
    assert len(victim_sections) > 2

    content_dir = Path(store) / "local" / "sweep"
    outside = tmp_path / "outside.md"
    outside.write_text("zircon " * 400, encoding="utf-8")
    (content_dir / victim).unlink()
    try:
        os.symlink(outside, content_dir / victim)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - no symlink privilege
        pytest.skip(f"cannot create a symlink here: {exc}")

    loader = index._content_loader
    postings = PostingIndex.build(index.sections, content_loader=loader).postings
    assert "zircon" not in postings
    assert "lantern" in postings
    with loader.sweep():
        for s in victim_sections:
            assert loader(s["doc_path"], s["byte_start"], s["byte_end"]) == ""
