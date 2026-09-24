"""jdoc#143: the content loader resolved the content root on every section.

``_safe_content_path`` computed ``content_dir.resolve()`` inside the call, and
the per-section loader called it once per section with the same directory.
The reporter measured 179,486 ``realpath`` calls on one incremental run. The
root is now resolved once per loaded index and passed in. The candidate is
still resolved per call, since that is the symlink escape guard.

Reported by @LuigiNicaPRO (split from #140).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from jdocmunch_mcp.storage import doc_store as _doc_store
from jdocmunch_mcp.storage.doc_store import DocStore

_N_DOCS = 12


@pytest.fixture
def loaded(tmp_path):
    from jdocmunch_mcp.tools.index_local import index_local

    src = tmp_path / "docs"
    src.mkdir()
    for i in range(_N_DOCS):
        (src / f"doc{i}.md").write_text(
            f"# Doc {i}\n\n## Alpha\n\nalpha body {i}\n\n## Beta\n\nbeta body {i}\n",
            encoding="utf-8",
        )
    storage = tmp_path / "store"
    r = index_local(path=str(src), name="i143", use_ai_summaries=False,
                    use_embeddings=False, storage_path=str(storage))
    assert r.get("success"), r
    _doc_store._INDEX_CACHE.clear()
    store = DocStore(base_path=str(storage))
    owner, name = store._resolve_repo(r["repo"])
    index = store.load_index(owner, name)
    return store, owner, name, index


def _load_every_section(index) -> list:
    loader = index._content_loader
    return [
        loader(s["doc_path"], s["byte_start"], s["byte_end"])
        for s in index.sections if s.get("byte_end", 0) > s.get("byte_start", 0)
    ]


def test_loader_resolves_the_root_once_not_per_section(loaded, monkeypatch):
    store, owner, name, index = loaded
    root = store._content_dir(owner, name)
    root_resolves = []
    real_resolve = Path.resolve

    def counting_resolve(self, *a, **kw):
        if self == root:
            root_resolves.append(1)
        return real_resolve(self, *a, **kw)

    monkeypatch.setattr(Path, "resolve", counting_resolve)
    bodies = _load_every_section(index)
    assert len(bodies) >= 2 * _N_DOCS
    assert all(bodies)
    # Resolved at load_index time, which ran before the counter was installed.
    assert root_resolves == []


def test_loader_still_reads_correct_bytes(loaded):
    _store, _o, _n, index = loaded
    bodies = _load_every_section(index)
    assert any("alpha body 3" in b for b in bodies)
    assert any("beta body 7" in b for b in bodies)


def test_loader_returns_empty_for_missing_file(loaded):
    """The exists() probe was removed; open() raising must still read as ''."""
    store, owner, name, index = loaded
    (store._content_dir(owner, name) / "doc0.md").unlink()
    assert index._content_loader("doc0.md", 0, 10) == ""


@pytest.mark.parametrize("evil", ["../outside.md", "../../outside.md"])
def test_passed_root_still_refuses_traversal(tmp_path, evil):
    store = DocStore(base_path=str(tmp_path / "store"))
    content_dir = tmp_path / "store" / "local" / "r"
    content_dir.mkdir(parents=True)
    assert store._safe_content_path(content_dir, evil, content_dir.resolve()) is None


def test_passed_root_still_refuses_a_symlink_that_escapes(tmp_path):
    """The candidate's own resolve() is the guard, and it must survive."""
    outside = tmp_path / "secret.md"
    outside.write_text("secret", encoding="utf-8")
    content_dir = tmp_path / "store" / "local" / "r"
    content_dir.mkdir(parents=True)
    link = content_dir / "link.md"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this platform")
    store = DocStore(base_path=str(tmp_path / "store"))
    assert store._safe_content_path(content_dir, "link.md", content_dir.resolve()) is None
    assert store._safe_content_path(content_dir, "link.md") is None
