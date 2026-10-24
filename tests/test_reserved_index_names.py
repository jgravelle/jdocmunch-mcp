"""#196: an index name may not end in a sidecar suffix.

An index's primary file is `<name>.json`. A name ending in `.summary` or
`.related` would put that file on top of another index's sidecar, which either
crashed the next incremental load or overwrote the sidecar silently.
"""

import asyncio
import json
from pathlib import Path

import pytest

from jdocmunch_mcp.storage.doc_store import (
    INDEX_OWNED_SIDECAR_SUFFIXES,
    RESERVED_INDEX_NAME_SUFFIXES,
    reserved_index_name_suffix,
)
from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.index_repo import index_repo


def _folder(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir()
    (d / "a.md").write_text("# Title\n\nHello.\n", encoding="utf-8")
    return d


def _index(folder: Path, store: Path, name=None) -> dict:
    return index_local(
        str(folder),
        name=name,
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(store),
    )


def test_every_json_sidecar_suffix_is_reserved():
    # Derived from the sidecar list, so a new .json sidecar is reserved too.
    for s in INDEX_OWNED_SIDECAR_SUFFIXES:
        if s.endswith(".json"):
            assert s[: -len(".json")] in RESERVED_INDEX_NAME_SUFFIXES


def test_jsonl_sidecars_are_not_reserved():
    # `<name>.json` can never equal a `.jsonl` path, so these names stay legal.
    assert reserved_index_name_suffix("X.embeddings") is None
    assert reserved_index_name_suffix("X.postings") is None


def test_ordinary_names_are_not_reserved():
    for n in ("X", "summary", "my.docs", "X.summaryish", "X-related"):
        assert reserved_index_name_suffix(n) is None, n


def test_reserved_check_ignores_case():
    assert reserved_index_name_suffix("X.SUMMARY") == ".summary"


def test_folder_named_like_a_sidecar_is_refused_before_reading(tmp_path):
    """The reporter's first repro: the default name comes from the folder."""
    store = tmp_path / "idx"
    assert _index(_folder(tmp_path, "X"), store)["success"]
    summary = store / "local" / "X.summary.json"
    before = summary.read_bytes()

    r = _index(_folder(tmp_path, "X.summary"), store)

    assert r["success"] is False
    assert r["error"].startswith("Invalid name: 'X.summary'")
    assert "derived from the folder name" in r["error"]
    assert summary.read_bytes() == before


@pytest.mark.parametrize("suffix", RESERVED_INDEX_NAME_SUFFIXES)
def test_explicit_reserved_name_leaves_the_sidecar_untouched(tmp_path, suffix):
    """The reporter's second repro: name=X.related overwrote X's sidecar."""
    store = tmp_path / "idx"
    assert _index(_folder(tmp_path, "X"), store)["success"]
    target = store / "local" / f"X{suffix}.json"
    before = target.read_bytes() if target.exists() else None

    r = _index(_folder(tmp_path, "other"), store, name=f"X{suffix}")

    assert r["success"] is False
    assert "reserves for index sidecar files" in r["error"]
    assert "derived" not in r["error"]
    after = target.read_bytes() if target.exists() else None
    assert after == before
    if after is not None:
        assert "owner" not in json.loads(after)


def test_index_repo_refuses_a_reserved_name_before_any_request(tmp_path, monkeypatch):
    import jdocmunch_mcp.tools.index_repo as mod

    async def _no_network(*a, **k):
        raise AssertionError("no request may be made for a refused name")

    monkeypatch.setattr(mod, "fetch_head_commit_sha", _no_network)
    r = asyncio.run(index_repo(
        "octo/docs", use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), name="docs.related",
    ))
    assert r["success"] is False
    assert r["error"].startswith("Invalid name: 'docs.related'")


def test_index_repo_names_the_repository_when_it_is_the_default(tmp_path, monkeypatch):
    import jdocmunch_mcp.tools.index_repo as mod

    async def _no_network(*a, **k):
        raise AssertionError("no request may be made for a refused name")

    monkeypatch.setattr(mod, "fetch_head_commit_sha", _no_network)
    r = asyncio.run(index_repo(
        "octo/docs.terms", use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path),
    ))
    assert r["success"] is False
    assert "(the repository name)" in r["error"]
