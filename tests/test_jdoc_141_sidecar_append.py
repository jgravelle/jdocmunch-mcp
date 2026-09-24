"""jdoc#141: an incremental embed pass rewrote the whole sidecar to add rows.

With a matching identity and ``prune=False``, ``embed_sections`` started
``entries`` from every cached row, added this pass's vectors, and handed the
lot to ``cache.write``, a full atomic rewrite. That is the same key set an
append produces. The reporter measured 5.6 s on a 279 MB sidecar per run. The
pass now appends in that case. The rewrite still runs, unchanged, on an
identity rotation (#109), on ``prune=True`` (#107), and when no sidecar or no
readable row exists yet.

Reported by @LuigiNicaPRO (split from #140).
"""

from __future__ import annotations

import pytest

from jdocmunch_mcp.embeddings import cache as emb_cache
from jdocmunch_mcp.embeddings import provider as emb_provider

import json

DIM = 8


class _FakeProvider:
    def embed_texts(self, texts, task_type=None):
        return [[float(len(t) % 7) + 1.0] * DIM for t in texts]


class _Section:
    def __init__(self, hash_):
        self.content_hash = hash_
        self.title = "T"
        self.content = "body"
        self.summary = ""
        self.embedding = None


@pytest.fixture
def fake_provider(monkeypatch):
    prov = _FakeProvider()
    monkeypatch.setattr(emb_provider, "_get_provider", lambda: prov)
    monkeypatch.setattr(emb_provider, "get_provider_name", lambda: "fake")
    monkeypatch.setattr(emb_provider, "_provider_identity", lambda n: ("fake-model", DIM))
    return prov


def _embed(sections, tmp_path, **kwargs):
    return emb_provider.embed_sections(
        sections, owner="local", name="corpus", storage_path=str(tmp_path), **kwargs
    )


def _read_sidecar(tmp_path):
    header, out = None, {}
    for raw in _path(tmp_path).read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        e = json.loads(raw)
        if e.get("_header") is True:
            header = e
            continue
        out[e["hash"].rsplit("#", 1)[0]] = e["vector"]
    return header, out


def _path(tmp_path):
    return emb_cache._cache_path(str(tmp_path), "local", "corpus")


def test_matching_identity_appends_and_keeps_existing_bytes(tmp_path, fake_provider):
    _embed([_Section(f"h{i:03d}") for i in range(40)], tmp_path)
    before = _path(tmp_path).read_bytes()

    _embed([_Section("h007"), _Section("new1"), _Section("new2")], tmp_path)

    after = _path(tmp_path).read_bytes()
    assert after.startswith(before), "existing rows were rewritten, not appended to"
    _, rows = _read_sidecar(tmp_path)
    assert len(rows) == 42
    # h007 was a cache hit with an identical vector: not written twice.
    assert after[len(before):].count(b'"hash"') == 2


def test_matching_identity_does_not_call_write(tmp_path, fake_provider, monkeypatch):
    _embed([_Section(f"h{i:03d}") for i in range(10)], tmp_path)
    calls = []
    monkeypatch.setattr(emb_cache, "write", lambda *a, **kw: calls.append(1))
    _embed([_Section("new1")], tmp_path)
    assert calls == []


def test_changed_vector_for_existing_key_is_appended_and_wins(tmp_path, fake_provider):
    _embed([_Section(f"h{i:03d}") for i in range(10)], tmp_path)
    s = _Section("h003")
    # Force a miss on an existing key with a different vector.
    key = emb_provider._embed_cache_key(s)
    cached = emb_cache.load(str(tmp_path), "local", "corpus",
                            provider="fake", model="fake-model", dim=DIM,
                            embed_chars=emb_provider._embed_chars())
    assert key in cached
    s.embedding = [9.0] * DIM
    emb_cache.append_rows(str(tmp_path), "local", "corpus", [(key, s.embedding)])
    reloaded = emb_cache.load(str(tmp_path), "local", "corpus",
                              provider="fake", model="fake-model", dim=DIM,
                              embed_chars=emb_provider._embed_chars())
    assert reloaded[key] == [9.0] * DIM


def test_torn_trailing_line_is_terminated_before_appending(tmp_path, fake_provider):
    _embed([_Section(f"h{i:03d}") for i in range(5)], tmp_path)
    p = _path(tmp_path)
    p.write_bytes(p.read_bytes() + b'{"hash": "torn", "vec')  # crash mid-row
    _embed([_Section("new1")], tmp_path)
    _, rows = _read_sidecar_tolerant(tmp_path)
    assert "new1" in rows, "the appended row was glued onto the torn fragment"


def _read_sidecar_tolerant(tmp_path):
    out = {}
    for raw in _path(tmp_path).read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if not e.get("_header"):
            out[e["hash"].rsplit("#", 1)[0]] = e["vector"]
    return None, out


def test_prune_still_rewrites(tmp_path, fake_provider):
    _embed([_Section(f"h{i:03d}") for i in range(10)], tmp_path)
    _embed([_Section("h001"), _Section("h002")], tmp_path, prune=True)
    _, rows = _read_sidecar(tmp_path)
    assert set(rows) == {"h001", "h002"}


def test_identity_rotation_still_rewrites(tmp_path, fake_provider, monkeypatch):
    _embed([_Section(f"h{i:03d}") for i in range(10)], tmp_path)
    monkeypatch.setattr(emb_provider, "_provider_identity", lambda n: ("model-b", DIM))
    _embed([_Section("h001")], tmp_path)
    header, rows = _read_sidecar(tmp_path)
    assert header["model"] == "model-b"
    assert set(rows) == {"h001"}


def test_first_pass_writes_a_header(tmp_path, fake_provider):
    _embed([_Section("a"), _Section("b")], tmp_path)
    header, rows = _read_sidecar(tmp_path)
    assert header and header["model"] == "fake-model"
    assert set(rows) == {"a", "b"}
