"""jdoc#189 part 2: the process-wide index cache has a byte budget.

A parsed index holds roughly 2.5-3x its monolith's size, so the old
8-entry bound let a serve process keep gigabytes. Reported with measurements
by @DreDay4u in #189.
"""

from __future__ import annotations

import pytest

from jdocmunch_mcp.storage import doc_store as ds

MB = 1024 * 1024


@pytest.fixture
def cache(monkeypatch, tmp_path):
    ds._index_cache_clear()
    files = {}

    def make(name, megabytes):
        p = tmp_path / f"{name}.json"
        p.write_bytes(b"x" * int(megabytes * MB))
        files[name] = (str(p), 1)
        return files[name]

    yield make
    ds._index_cache_clear()


def _keys():
    return [k[0].rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for k in ds._INDEX_CACHE]


def test_lru_entries_leave_past_the_budget(cache, monkeypatch):
    monkeypatch.setenv("JDOCMUNCH_INDEX_CACHE_MAX_BYTES", str(10 * MB))
    a, b, c = cache("a", 1), cache("b", 1), cache("c", 1)   # 3 MB estimated each
    ds._index_cache_put(a, "A")
    ds._index_cache_put(b, "B")
    ds._index_cache_get(a)                                 # a is now most recent
    ds._index_cache_put(c, "C")                            # 9 MB: fits
    assert _keys() == ["b.json", "a.json", "c.json"]
    d = cache("d", 1)
    ds._index_cache_put(d, "D")                            # 12 MB: b (LRU) leaves
    assert _keys() == ["a.json", "c.json", "d.json"]
    assert ds._index_cache_bytes() == 9 * MB


def test_one_index_larger_than_the_budget_is_kept(cache, monkeypatch):
    monkeypatch.setenv("JDOCMUNCH_INDEX_CACHE_MAX_BYTES", str(2 * MB))
    small, huge = cache("small", 0.1), cache("huge", 5)
    ds._index_cache_put(small, "S")
    ds._index_cache_put(huge, "H")
    assert _keys() == ["huge.json"]
    assert ds._index_cache_get(huge) == "H"


@pytest.mark.parametrize("value", ["0", "-1"])
def test_zero_or_less_turns_the_byte_bound_off(cache, monkeypatch, value):
    monkeypatch.setenv("JDOCMUNCH_INDEX_CACHE_MAX_BYTES", value)
    for i in range(ds._INDEX_CACHE_MAXSIZE + 2):
        ds._index_cache_put(cache(f"f{i}", 1), i)
    assert len(ds._INDEX_CACHE) == ds._INDEX_CACHE_MAXSIZE   # count bound still holds


def test_the_budget_is_read_per_insert(cache, monkeypatch):
    monkeypatch.setenv("JDOCMUNCH_INDEX_CACHE_MAX_BYTES", str(100 * MB))
    a, b = cache("a", 1), cache("b", 1)
    ds._index_cache_put(a, "A")
    monkeypatch.setenv("JDOCMUNCH_INDEX_CACHE_MAX_BYTES", str(4 * MB))
    ds._index_cache_put(b, "B")
    assert _keys() == ["b.json"]


def test_a_direct_clear_cannot_skew_the_total(cache, monkeypatch):
    monkeypatch.setenv("JDOCMUNCH_INDEX_CACHE_MAX_BYTES", str(10 * MB))
    ds._index_cache_put(cache("a", 2), "A")
    ds._INDEX_CACHE.clear()                     # what 10+ test files do
    assert ds._index_cache_bytes() == 0
    ds._index_cache_put(cache("b", 3), "B")     # 9 MB; a stale 6 MB would evict it
    assert _keys() == ["b.json"]


def test_evicting_a_path_drops_its_estimate(cache, monkeypatch):
    a = cache("a", 1)
    ds._index_cache_put(a, "A")
    ds._evict_index_cache(a[0])
    assert ds._index_cache_bytes() == 0 and not ds._INDEX_CACHE_ESTIMATES


def test_a_real_load_is_estimated_from_its_monolith(tmp_path, monkeypatch):
    from jdocmunch_mcp.tools.index_local import index_local
    monkeypatch.setenv("JDOCMUNCH_SHARE_SAVINGS", "0")
    ds._index_cache_clear()
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\nText.\n", encoding="utf-8")
    index_local(path=str(docs), name="cb", use_ai_summaries=False, use_embeddings=False,
                storage_path=str(tmp_path / "store"))
    store = ds.DocStore(base_path=str(tmp_path / "store"))
    ds._index_cache_clear()
    store.load_index("local", "cb")
    import os
    path = str(store._index_path("local", "cb"))
    expected = os.path.getsize(path) * ds._INDEX_CACHE_FOOTPRINT_MULTIPLIER
    assert ds._index_cache_bytes() == expected
    ds._index_cache_clear()
