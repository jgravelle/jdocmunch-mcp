"""jdoc#140: an incremental index parsed the embeddings sidecar three times.

``load`` in the embed pass, the rescan in ``append_entries`` (via
``_ensure_sidecar_from_sections``) and ``stored_hashes`` (the #107 coverage
report) each read the whole file. The reporter measured 3.3 s apiece on a
279 MB sidecar. The keys are now remembered per sidecar and trusted only while
the file's (size, mtime_ns) match, so the later readers reuse them. A change
made by anything else still forces a real read, which is what keeps the #107
coverage signal reporting what is on disk.

Reported by @LuigiNicaPRO.
"""

from __future__ import annotations

import os
import pathlib
import traceback

import pytest

from jdocmunch_mcp.embeddings import cache as emb_cache
from jdocmunch_mcp.embeddings import provider as emb_provider

DIM = 8
OWNER, NAME = "local", "corpus"


@pytest.fixture(autouse=True)
def _clear_memo():
    emb_cache._KEY_MEMO.clear()
    yield
    emb_cache._KEY_MEMO.clear()


def _write(tmp_path, keys):
    emb_cache.write(str(tmp_path), OWNER, NAME, provider="fake", model="m", dim=DIM,
                    entries=[(k, [1.0] * DIM) for k in keys])


def _path(tmp_path):
    return emb_cache._cache_path(str(tmp_path), OWNER, NAME)


@pytest.fixture
def no_sidecar_reads(monkeypatch):
    """Make any read-mode open of a sidecar fail the test."""
    real = pathlib.Path.open

    def guarded(self, mode="r", *a, **kw):
        if str(self).endswith(".embeddings.jsonl") and "r" in mode and "+" not in mode:
            raise AssertionError(f"sidecar was re-read: {self}")
        return real(self, mode, *a, **kw)

    return lambda: monkeypatch.setattr(pathlib.Path, "open", guarded)


def test_stored_hashes_reuses_keys_from_load(tmp_path, no_sidecar_reads):
    _write(tmp_path, ["a#pv1", "b#pv1"])
    emb_cache._KEY_MEMO.clear()
    emb_cache.load(str(tmp_path), OWNER, NAME, provider="fake", model="m", dim=DIM)
    no_sidecar_reads()
    assert emb_cache.stored_hashes(str(tmp_path), OWNER, NAME) == {"a", "b"}


def test_append_entries_skips_its_rescan_and_stays_correct(tmp_path, no_sidecar_reads):
    _write(tmp_path, ["a#pv1"])
    no_sidecar_reads()
    n = emb_cache.append_entries(str(tmp_path), OWNER, NAME,
                                 entries=[("a#pv1", [2.0] * DIM), ("b#pv1", [2.0] * DIM)],
                                 identity_if_new=("x", "x", None))
    assert n == 1, "an existing key was appended again"
    assert emb_cache.stored_hashes(str(tmp_path), OWNER, NAME) == {"a", "b"}


def test_append_rows_keeps_the_memo_current(tmp_path, no_sidecar_reads):
    _write(tmp_path, ["a#pv1"])
    no_sidecar_reads()
    emb_cache.append_rows(str(tmp_path), OWNER, NAME, [("c#pv1", [1.0] * DIM)])
    assert emb_cache.stored_hashes(str(tmp_path), OWNER, NAME) == {"a", "c"}


def test_an_outside_append_is_seen(tmp_path):
    """The #107 guarantee: coverage reports the disk, not the memo."""
    _write(tmp_path, ["a#pv1"])
    with _path(tmp_path).open("a", encoding="utf-8") as fh:
        fh.write('{"hash": "z#pv1", "vector": [1.0]}\n')
    assert emb_cache.stored_hashes(str(tmp_path), OWNER, NAME) == {"a", "z"}


def test_an_outside_same_size_rewrite_is_seen(tmp_path):
    """Size alone would miss this; the mtime catches it."""
    _write(tmp_path, ["a#pv1"])
    p = _path(tmp_path)
    before = p.stat()
    p.write_text(p.read_text(encoding="utf-8").replace("a#pv1", "q#pv1"), encoding="utf-8")
    os.utime(p, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
    assert p.stat().st_size == before.st_size
    assert emb_cache.stored_hashes(str(tmp_path), OWNER, NAME) == {"q"}


def test_a_deleted_sidecar_is_seen(tmp_path):
    _write(tmp_path, ["a#pv1"])
    emb_cache.purge(str(tmp_path), OWNER, NAME)
    assert emb_cache.stored_hashes(str(tmp_path), OWNER, NAME) == set()


def test_a_mismatched_load_records_nothing(tmp_path):
    _write(tmp_path, ["a#pv1"])
    emb_cache._KEY_MEMO.clear()
    assert emb_cache.load(str(tmp_path), OWNER, NAME, provider="other", model="m", dim=DIM) == {}
    assert emb_cache._KEY_MEMO == {}


# --- end to end ------------------------------------------------------------

class _Fake:
    def embed_texts(self, texts, task_type=None):
        return [[1.0] * DIM for _ in texts]


def test_incremental_index_parses_the_sidecar_once(tmp_path, monkeypatch):
    monkeypatch.setenv("JDOCMUNCH_SHARE_SAVINGS", "0")
    monkeypatch.setattr(emb_provider, "_get_provider", lambda: _Fake())
    monkeypatch.setattr(emb_provider, "get_provider_name", lambda: "fake")
    monkeypatch.setattr(emb_provider, "_provider_identity", lambda n: ("fake-model", DIM))
    from jdocmunch_mcp.tools.index_local import index_local

    src = tmp_path / "docs"
    src.mkdir()
    for i in range(30):
        (src / f"d{i}.md").write_text(f"# D{i}\n\n## A\n\nalpha {i}\n\n## B\n\nbeta {i}\n",
                                      encoding="utf-8")
    store = str(tmp_path / "store")
    kw = dict(path=str(src), name="c140", use_ai_summaries=False,
              use_embeddings=True, storage_path=store)
    assert index_local(**kw).get("success")
    (src / "d3.md").write_text("# D3\n\n## A\n\nchanged\n", encoding="utf-8")

    full_reads = []
    real = pathlib.Path.open

    def spy(self, mode="r", *a, **k):
        if str(self).endswith(".embeddings.jsonl") and mode == "r":
            caller = traceback.extract_stack()[-2].name
            if caller != "identity":  # reads the header line only
                full_reads.append(caller)
        return real(self, mode, *a, **k)

    monkeypatch.setattr(pathlib.Path, "open", spy)
    r = index_local(**kw)
    assert r.get("success") and r.get("changed") == 1
    assert r.get("embedding_coverage") == 1.0
    assert full_reads == ["load"], full_reads
