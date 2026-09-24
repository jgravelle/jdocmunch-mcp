"""jdoc#142 (part 1): an incremental index read every section body twice.

``incremental_save`` reads each kept section's content for the BM25 stats,
then ``index_local`` rebuilds the four derived sidecars (#117) and read every
body again. The stats pass now records what it read in a ``content_sink`` and
the rebuild reuses it, so each kept body is read once.

Reported by @LuigiNicaPRO (split from #140).
"""

from __future__ import annotations

import builtins
import json
from pathlib import Path

import pytest


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("JDOCMUNCH_SHARE_SAVINGS", "0")
    src = tmp_path / "docs"
    src.mkdir()
    # Past the size where the incremental path re-materializes everything
    # (the #125/#131 lesson), so untouched documents are genuinely kept.
    for i in range(60):
        (src / f"d{i}.md").write_text(
            f"# D{i}\n\n## Alpha\n\nalpha widget {i}\n\n## Beta\n\nbeta gadget {i}\n",
            encoding="utf-8",
        )
    store = tmp_path / "store"
    return src, store


def _index(src, store):
    from jdocmunch_mcp.tools.index_local import index_local
    r = index_local(path=str(src), name="c142", use_ai_summaries=False,
                    use_embeddings=False, storage_path=str(store))
    assert r.get("success"), r
    return r


def _count_body_reads(monkeypatch, content_dir: Path) -> dict:
    reads: dict = {}
    real = builtins.open

    def spy(f, *a, **k):
        mode = a[0] if a else k.get("mode", "r")
        if mode == "rb" and str(f).startswith(str(content_dir)):
            reads[str(f)] = reads.get(str(f), 0) + 1
        return real(f, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    return reads


def test_each_kept_body_is_read_once(corpus, monkeypatch):
    src, store = corpus
    _index(src, store)
    (src / "d3.md").write_text("# D3\n\n## Alpha\n\nchanged\n", encoding="utf-8")

    reads = _count_body_reads(monkeypatch, store / "local" / "c142")
    r = _index(src, store)
    monkeypatch.undo()

    assert r.get("changed") == 1
    kept_docs = 59
    total = sum(reads.values())
    # Each kept document has three sections with a body (title, Alpha, Beta).
    # Before the fix this was twice that.
    assert total <= kept_docs * 3, f"{total} body reads for {kept_docs} kept documents"


def test_sidecars_match_a_full_rebuild(corpus, tmp_path):
    """Reusing the stats pass's reads must not change what the rebuild writes."""
    src, store = corpus
    _index(src, store)
    (src / "d3.md").write_text("# D3\n\n## Alpha\n\nchanged widget\n", encoding="utf-8")
    _index(src, store)

    fresh = tmp_path / "fresh"
    _index(src, fresh)

    compared = 0
    for suffix in ("terms.json", "boilerplate.json", "related.json", "duplicates.json"):
        a = store / "local" / f"c142.{suffix}"
        b = fresh / "local" / f"c142.{suffix}"
        assert a.exists() and b.exists(), suffix
        ja = json.loads(a.read_text(encoding="utf-8"))
        jb = json.loads(b.read_text(encoding="utf-8"))
        for j in (ja, jb):
            if isinstance(j, dict):
                for k in [k for k in j if k.endswith("_at")]:
                    j.pop(k)
        assert ja == jb, f"{suffix} differs from a full rebuild"
        compared += 1
    assert compared == 4


def test_sink_is_optional_for_other_callers(corpus):
    """index_file and index_repo call incremental_save without a sink."""
    import inspect
    from jdocmunch_mcp.storage.doc_store import DocStore
    param = inspect.signature(DocStore.incremental_save).parameters["content_sink"]
    assert param.default is None
