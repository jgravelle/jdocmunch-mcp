"""jdoc#176: get_toc, get_toc_tree and list_docs are paged and bounded.

They returned the whole index in one response: 14.0 MB for `get_toc` on
36,000 sections. Now `limit` / `offset` page them, every response says how many
items exist and whether more follow, and a response over
``JDOCMUNCH_RESPONSE_MAX_BYTES`` is REFUSED with the parameters that narrow it.

⚠ Totals are checked against the number of documents and sections BUILT, never
against ``len()`` of the returned list. A count derived from the list it
describes agrees with it by construction.
"""

import asyncio
import json

import pytest

from jdocmunch_mcp.server import _all_tools, call_tool
from jdocmunch_mcp.tools._response_bounds import (
    DEFAULT_RESPONSE_MAX_BYTES,
    RESPONSE_MAX_BYTES_ENV,
    response_max_bytes,
)
from jdocmunch_mcp.tools.get_toc import get_toc
from jdocmunch_mcp.tools.get_toc_tree import get_toc_tree
from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.list_docs import list_docs

DOCS = 40
SECTIONS_PER_DOC = 1 + 1 + 4  # file root + H1 + four H2s
REPO = "local/r176"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv(RESPONSE_MAX_BYTES_ENV, raising=False)
    corpus = tmp_path / "corpus"
    for i in range(DOCS):
        d = corpus / f"area{i % 4}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"page-{i:02}.md").write_text(
            f"# Page {i}\n\nIntro.\n\n"
            + "".join(f"## Topic {k} of {i}\n\nBody.\n\n" for k in range(4)),
            encoding="utf-8",
        )
    path = str(tmp_path / "store")
    index_local(path=str(corpus), name="r176", storage_path=path,
                use_ai_summaries=False, use_embeddings=False)
    return path


def _bytes(result):
    return len(json.dumps(result, separators=(",", ":")).encode("utf-8"))


# --- the fixture really has the shape the totals are checked against --------

def test_fixture_section_count_is_what_the_tests_assume(store):
    out = get_toc(repo=REPO, storage_path=store)
    assert out["sections_total"] == DOCS * SECTIONS_PER_DOC


# --- every response carries the disclosure keys, paged or not -----------------

@pytest.mark.parametrize("fn,unit,total", [
    (get_toc, "sections", DOCS * SECTIONS_PER_DOC),
    (get_toc_tree, "documents", DOCS),
    (list_docs, "docs", DOCS),
])
def test_an_unpaged_call_says_it_is_complete(store, fn, unit, total):
    out = fn(repo=REPO, storage_path=store)
    assert out[f"{unit}_total"] == total
    assert out[f"{unit}_truncated"] is False
    assert out["next_offset"] is None


@pytest.mark.parametrize("fn,unit,listkey,total", [
    (get_toc, "sections", "sections", DOCS * SECTIONS_PER_DOC),
    (get_toc_tree, "documents", "documents", DOCS),
    (list_docs, "docs", "docs", DOCS),
])
def test_paging_returns_every_item_exactly_once(store, fn, unit, listkey, total):
    key = "id" if listkey == "sections" else "doc_path"
    seen, offset, pages = [], 0, 0
    while offset is not None:
        out = fn(repo=REPO, storage_path=store, limit=7, offset=offset)
        assert out[f"{unit}_total"] == total
        assert len(out[listkey]) <= 7
        assert out[f"{unit}_truncated"] is (out["next_offset"] is not None)
        seen += [item[key] for item in out[listkey]]
        offset = out["next_offset"]
        pages += 1
    assert len(seen) == total
    assert len(set(seen)) == total
    assert pages == -(-total // 7)


def test_toc_tree_never_splits_a_document(store):
    out = get_toc_tree(repo=REPO, storage_path=store, limit=3)
    for doc in out["documents"]:
        h1 = doc["sections"][0]
        # root -> H1 -> four H2s, all present on the page that holds the doc
        assert len(h1["children"]) == 1
        assert len(h1["children"][0]["children"]) == 4


def test_path_glob_total_is_after_the_filter(store):
    out = get_toc(repo=REPO, storage_path=store, path_glob="area1/*", limit=5)
    assert out["sections_total"] == (DOCS // 4) * SECTIONS_PER_DOC
    assert out["sections_truncated"] is True


def test_list_docs_path_glob_keeps_whole_index_totals(store):
    out = list_docs(repo=REPO, storage_path=store, path_glob="area1/*", limit=3)
    assert out["docs_total"] == DOCS // 4
    assert out["doc_count"] == 3
    # These two always described the whole index, and still do.
    assert out["total_section_count"] == DOCS * SECTIONS_PER_DOC
    # Comparable with doc_count, so it is counted over the page.
    assert out["_meta"]["docs_with_mtime"] == 3


@pytest.mark.parametrize("kwargs,needle", [
    ({"limit": 0}, "limit"), ({"limit": -3}, "limit"), ({"limit": "5"}, "limit"),
    ({"limit": True}, "limit"), ({"offset": -1}, "offset"), ({"offset": 1.5}, "offset"),
])
def test_bad_paging_arguments_are_errors(store, kwargs, needle):
    out = get_toc(repo=REPO, storage_path=store, **kwargs)
    assert needle in out["error"]


# --- the ceiling ---------------------------------------------------------------

@pytest.mark.parametrize("fn,unit,listkey", [
    (get_toc, "sections", "sections"),
    (get_toc_tree, "documents", "documents"),
    (list_docs, "docs", "docs"),
])
def test_over_the_ceiling_is_refused_not_truncated(store, monkeypatch, fn, unit, listkey):
    full = fn(repo=REPO, storage_path=store)
    ceiling = _bytes(full) // 3
    monkeypatch.setenv(RESPONSE_MAX_BYTES_ENV, str(ceiling))
    out = fn(repo=REPO, storage_path=store)
    assert "error" in out
    assert listkey not in out  # a refusal, never a shortened list
    # Not an exact match: `_meta.latency_ms` can gain a digit between calls.
    assert abs(out["response_bytes"] - _bytes(full)) <= 8
    assert out["response_bytes"] > ceiling
    assert out["response_max_bytes"] == ceiling
    assert out[f"{unit}_total"] == full[f"{unit}_total"]
    assert "path_glob" in out["narrow_with"] and "limit" in out["narrow_with"]
    # One retry at the suggested limit fits.
    retry = fn(repo=REPO, storage_path=store, limit=out["suggested_limit"])
    assert "error" not in retry
    assert _bytes(retry) <= ceiling


def test_a_paged_call_is_still_bounded(store, monkeypatch):
    monkeypatch.setenv(RESPONSE_MAX_BYTES_ENV, "2000")
    out = get_toc(repo=REPO, storage_path=store, limit=DOCS * SECTIONS_PER_DOC)
    assert "error" in out


def test_zero_turns_the_ceiling_off(store, monkeypatch):
    monkeypatch.setenv(RESPONSE_MAX_BYTES_ENV, "0")
    out = get_toc(repo=REPO, storage_path=store)
    assert len(out["sections"]) == DOCS * SECTIONS_PER_DOC


def test_the_ceiling_is_read_at_call_time(store, monkeypatch):
    assert "error" not in get_toc(repo=REPO, storage_path=store)
    monkeypatch.setenv(RESPONSE_MAX_BYTES_ENV, "500")
    assert "error" in get_toc(repo=REPO, storage_path=store)


@pytest.mark.parametrize("raw", ["", "  ", "abc", "-5", "1.5"])
def test_an_unusable_setting_keeps_the_default_ceiling(monkeypatch, raw):
    # A typo must not silently remove the bound.
    monkeypatch.setenv(RESPONSE_MAX_BYTES_ENV, raw)
    assert response_max_bytes() == DEFAULT_RESPONSE_MAX_BYTES


def test_default_ceiling_is_one_mebibyte(monkeypatch):
    monkeypatch.delenv(RESPONSE_MAX_BYTES_ENV, raising=False)
    assert response_max_bytes() == 1024 * 1024


# --- through the server ----------------------------------------------------------

@pytest.mark.parametrize("tool", ["get_toc", "get_toc_tree", "list_docs"])
def test_schema_declares_the_paging_parameters(tool):
    props = next(t for t in _all_tools() if t.name == tool).inputSchema["properties"]
    assert {"path_glob", "limit", "offset"} <= set(props)


@pytest.mark.parametrize("tool,listkey", [
    ("get_toc", "sections"), ("get_toc_tree", "documents"), ("list_docs", "docs"),
])
def test_dispatch_passes_limit_and_offset(store, monkeypatch, tool, listkey):
    monkeypatch.setenv("DOC_INDEX_PATH", store)
    text = asyncio.run(call_tool(tool, {"repo": REPO, "limit": 2, "offset": 1}))[0].text
    out = json.loads(text)
    assert len(out[listkey]) == 2
    assert out["next_offset"] == 3
