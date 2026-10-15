"""The anchor response surfaces are bounded, and say so (v1.147.0).

On 1.x a list that ships unbounded cannot be capped later, so each cap arrives
with the keys that disclose it. Every test here builds PAST its cap using the
imported constant, then checks the totals against what was built (never
``len(list)``), the ``*_truncated`` flag, and the ``+N more`` in the warning. A
fixture at the cap pins the other side: nothing dropped, flag false.

The caps: ``ANCHOR_SECTIONS_CAP`` (bodies ``get_section`` reads for an ambiguous
reference), ``ANCHOR_CANDIDATES_CAP`` (``anchor_candidates``, every tool),
``DUPLICATE_ANCHORS_CAP`` (entries in ``duplicate_anchors``) and
``DUPLICATE_ANCHOR_IDS_CAP`` (ids and titles inside one entry).
"""

import pytest

from jdocmunch_mcp.parser.sections import (
    DUPLICATE_ANCHOR_IDS_CAP,
    DUPLICATE_ANCHORS_CAP,
    WARNING_NAMES_SHOWN,
)
from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.storage.doc_store import ANCHOR_CANDIDATES_CAP
from jdocmunch_mcp.tools.check_section_delete_safe import check_section_delete_safe
from jdocmunch_mcp.tools.get_section import ANCHOR_SECTIONS_CAP, get_section
from jdocmunch_mcp.tools.get_sections import get_sections
from jdocmunch_mcp.tools.index_local import index_local

# Past both response caps, so the fixture reaches every one of them.
OVER = max(ANCHOR_SECTIONS_CAP, ANCHOR_CANDIDATES_CAP) + 3


def _index(tmp_path, name, files):
    src, store = tmp_path / "src", tmp_path / "store"
    src.mkdir()
    for fname, text in files.items():
        (src / fname).write_text(text, encoding="utf-8")
    out = index_local(path=str(src), name=name, use_ai_summaries=False,
                      use_embeddings=False, storage_path=str(store))
    return out, str(store)


def _cross_doc_files(n):
    """``n`` documents that each carry ``## Intro {#intro}``."""
    return {f"doc{i:03d}.md": f"# Doc {i}\n\n## Intro {{#intro}}\n\nBody {i}\n"
            for i in range(n)}


def _same_doc_file(n, anchor="step"):
    """One document with ``{#step}`` written ``n`` times, titles Step 000..."""
    parts = ["# Dup\n"]
    parts += [f"## Step {i:03d} {{#{anchor}}}\n\nBody {i}\n" for i in range(n)]
    return {"dup.md": "\n".join(parts)}


def _more(total, listed):
    return f"(+{total - listed} more)"


@pytest.fixture
def spy_content(monkeypatch):
    """Count the section bodies read through the store."""
    calls = []
    real = DocStore.get_section_content

    def counting(self, owner, name, section_id, *a, **kw):
        calls.append(section_id)
        return real(self, owner, name, section_id, *a, **kw)

    monkeypatch.setattr(DocStore, "get_section_content", counting)
    return calls


# --------------------------------------------------------------------------
# get_section: bodies returned for an ambiguous reference
# --------------------------------------------------------------------------


def test_get_section_cross_document_returns_only_the_capped_bodies(tmp_path, spy_content):
    _, store = _index(tmp_path, "capx", _cross_doc_files(OVER))
    spy_content.clear()
    out = get_section(repo="local/capx", section_id="#intro", storage_path=store)

    assert "section" not in out
    assert len(out["sections"]) == ANCHOR_SECTIONS_CAP
    assert out["sections_truncated"] is True
    assert out["anchor_match_count"] == OVER
    assert out["anchor_scope"] == "index"
    # Bodies beyond the cap were never read, not read and sliced off.
    assert len(spy_content) == ANCHOR_SECTIONS_CAP
    # The bodies are the first matches, in document order.
    assert [s["doc_path"] for s in out["sections"]] == [
        f"doc{i:03d}.md" for i in range(ANCHOR_SECTIONS_CAP)
    ]
    # Matches past the cap are still reachable: their ids are in the candidates.
    assert len(out["anchor_candidates"]) == ANCHOR_CANDIDATES_CAP
    assert out["anchor_candidates_truncated"] is (OVER > ANCHOR_CANDIDATES_CAP)
    assert [c["id"] for c in out["anchor_candidates"][:ANCHOR_SECTIONS_CAP]] == [
        s["id"] for s in out["sections"]
    ]
    warning = out["warning"]
    assert f"{ANCHOR_SECTIONS_CAP} of {OVER}" in warning
    assert _more(OVER - ANCHOR_SECTIONS_CAP, WARNING_NAMES_SHOWN) in warning
    assert "all are returned" not in warning.lower()


def test_get_section_cross_document_at_the_cap_drops_nothing(tmp_path, spy_content):
    _, store = _index(tmp_path, "capxok", _cross_doc_files(ANCHOR_SECTIONS_CAP))
    spy_content.clear()
    out = get_section(repo="local/capxok", section_id="#intro", storage_path=store)

    assert len(out["sections"]) == ANCHOR_SECTIONS_CAP
    assert out["sections_truncated"] is False
    assert out["anchor_match_count"] == ANCHOR_SECTIONS_CAP
    assert len(out["anchor_candidates"]) == ANCHOR_SECTIONS_CAP
    assert out["anchor_candidates_truncated"] is False
    assert len(spy_content) == ANCHOR_SECTIONS_CAP
    assert "more)" not in out["warning"]
    assert "All are returned" in out["warning"]


def test_get_section_same_document_returns_only_the_capped_bodies(tmp_path, spy_content):
    _, store = _index(tmp_path, "capd", _same_doc_file(OVER))
    spy_content.clear()
    out = get_section(repo="local/capd", section_id="dup.md#step", storage_path=store)

    # `section` is the first match and counts toward the cap.
    assert out["section"]["title"] == "Step 000"
    assert [s["title"] for s in out["sections"]] == [
        f"Step {i:03d}" for i in range(ANCHOR_SECTIONS_CAP)
    ]
    assert out["sections_truncated"] is True
    assert out["anchor_match_count"] == OVER
    assert out["anchor_scope"] == "document"
    assert len(spy_content) == ANCHOR_SECTIONS_CAP
    assert len(out["anchor_candidates"]) == ANCHOR_CANDIDATES_CAP
    assert out["anchor_candidates_truncated"] is (OVER > ANCHOR_CANDIDATES_CAP)
    assert f"{ANCHOR_SECTIONS_CAP} of {OVER}" in out["warning"]
    assert _more(OVER - ANCHOR_SECTIONS_CAP, WARNING_NAMES_SHOWN) in out["warning"]


def test_get_section_same_document_at_the_cap_drops_nothing(tmp_path, spy_content):
    _, store = _index(tmp_path, "capdok", _same_doc_file(ANCHOR_SECTIONS_CAP))
    spy_content.clear()
    out = get_section(repo="local/capdok", section_id="dup.md#step", storage_path=store)

    assert len(out["sections"]) == ANCHOR_SECTIONS_CAP
    assert out["sections_truncated"] is False
    assert out["anchor_match_count"] == ANCHOR_SECTIONS_CAP
    assert out["anchor_candidates_truncated"] is False
    assert len(spy_content) == ANCHOR_SECTIONS_CAP
    assert "more)" not in out["warning"]


def test_get_section_candidates_cap_is_independent_of_the_body_cap(tmp_path):
    """Exactly ANCHOR_CANDIDATES_CAP matches: every candidate is listed and the
    candidates flag is false, while the bodies are still cut at their own cap."""
    _, store = _index(tmp_path, "capc", _cross_doc_files(ANCHOR_CANDIDATES_CAP))
    out = get_section(repo="local/capc", section_id="#intro", storage_path=store)

    assert out["anchor_match_count"] == ANCHOR_CANDIDATES_CAP
    assert len(out["anchor_candidates"]) == ANCHOR_CANDIDATES_CAP
    assert out["anchor_candidates_truncated"] is False
    assert len(out["sections"]) == min(ANCHOR_SECTIONS_CAP, ANCHOR_CANDIDATES_CAP)
    assert out["sections_truncated"] is (ANCHOR_CANDIDATES_CAP > ANCHOR_SECTIONS_CAP)


def test_get_section_unambiguous_reference_has_no_cap_keys(tmp_path):
    _, store = _index(tmp_path, "capu", {"a.md": "# A\n\n## Only {#only}\n\nX\n"})
    out = get_section(repo="local/capu", section_id="a.md#only", storage_path=store)
    assert out.get("section")
    for key in ("sections_truncated", "anchor_candidates", "anchor_candidates_truncated"):
        assert key not in out


# --------------------------------------------------------------------------
# anchor_disclosure / anchor_refusal: anchor_candidates
# --------------------------------------------------------------------------


def test_get_sections_ambiguous_entry_caps_its_candidates(tmp_path):
    _, store = _index(tmp_path, "capg", _cross_doc_files(OVER))
    out = get_sections(repo="local/capg", section_ids=["#intro"], storage_path=store)
    entry = out["sections"][0]

    assert entry["anchor_match_count"] == OVER
    assert len(entry["anchor_candidates"]) == ANCHOR_CANDIDATES_CAP
    assert entry["anchor_candidates_truncated"] is True
    # Ordered before the cut, so the kept ids are stable.
    assert [c["doc_path"] for c in entry["anchor_candidates"]] == [
        f"doc{i:03d}.md" for i in range(ANCHOR_CANDIDATES_CAP)
    ]
    for text in (entry["warning"], entry["error"], out["warning"]):
        assert _more(OVER, WARNING_NAMES_SHOWN) in text
        # Only the first few ids are spelled out, never the whole list.
        assert entry["anchor_candidates"][WARNING_NAMES_SHOWN]["id"] not in text


def test_get_sections_ambiguous_entry_at_the_cap_drops_nothing(tmp_path):
    _, store = _index(tmp_path, "capgok", _cross_doc_files(ANCHOR_CANDIDATES_CAP))
    entry = get_sections(repo="local/capgok", section_ids=["#intro"],
                         storage_path=store)["sections"][0]

    assert entry["anchor_match_count"] == ANCHOR_CANDIDATES_CAP
    assert len(entry["anchor_candidates"]) == ANCHOR_CANDIDATES_CAP
    assert entry["anchor_candidates_truncated"] is False


def test_get_sections_same_document_entry_caps_its_candidates(tmp_path):
    _, store = _index(tmp_path, "capgd", _same_doc_file(OVER))
    out = get_sections(repo="local/capgd", section_ids=["dup.md#step"],
                       storage_path=store)
    entry = out["sections"][0]

    # The first match is still served; only the disclosure is bounded.
    assert entry["section"]["title"] == "Step 000"
    assert entry["anchor_scope"] == "document"
    assert entry["anchor_match_count"] == OVER
    assert len(entry["anchor_candidates"]) == ANCHOR_CANDIDATES_CAP
    assert entry["anchor_candidates_truncated"] is True
    assert _more(OVER, WARNING_NAMES_SHOWN) in out["warning"]


@pytest.mark.parametrize("ref,scope,files", [
    ("dup.md#step", "document", _same_doc_file(OVER)),
    ("#intro", "index", _cross_doc_files(OVER)),
])
def test_refusal_caps_its_candidates(tmp_path, ref, scope, files):
    _, store = _index(tmp_path, f"capr{scope}", files)
    out = check_section_delete_safe(repo=f"local/capr{scope}", section_id=ref,
                                    storage_path=store)

    assert out["anchor_scope"] == scope
    assert out["anchor_match_count"] == OVER
    assert len(out["anchor_candidates"]) == ANCHOR_CANDIDATES_CAP
    assert out["anchor_candidates_truncated"] is True
    assert out["error"] == out["warning"]
    assert _more(OVER, WARNING_NAMES_SHOWN) in out["error"]
    if scope == "document":
        assert "nothing was analysed" in out["error"]
        assert f"written {OVER} times" in out["error"]


@pytest.mark.parametrize("ref,files", [
    ("dup.md#step", _same_doc_file(ANCHOR_CANDIDATES_CAP)),
    ("#intro", _cross_doc_files(ANCHOR_CANDIDATES_CAP)),
])
def test_refusal_at_the_cap_drops_nothing(tmp_path, ref, files):
    _, store = _index(tmp_path, "caprok", files)
    out = check_section_delete_safe(repo="local/caprok", section_id=ref,
                                    storage_path=store)

    assert out["anchor_match_count"] == ANCHOR_CANDIDATES_CAP
    assert len(out["anchor_candidates"]) == ANCHOR_CANDIDATES_CAP
    assert out["anchor_candidates_truncated"] is False
    assert _more(ANCHOR_CANDIDATES_CAP, WARNING_NAMES_SHOWN) in out["error"]


def test_refusal_with_few_candidates_names_them_all(tmp_path):
    n = WARNING_NAMES_SHOWN
    _, store = _index(tmp_path, "caprf", _same_doc_file(n))
    out = check_section_delete_safe(repo="local/caprf", section_id="dup.md#step",
                                    storage_path=store)
    assert "more)" not in out["error"]
    for cand in out["anchor_candidates"]:
        assert cand["id"] in out["error"]


# --------------------------------------------------------------------------
# duplicate_anchors on the indexing responses
# --------------------------------------------------------------------------


def _many_duplicate_anchors(n):
    """One document in which ``n`` different ids are each written twice."""
    parts = ["# Doc\n"]
    for i in range(n):
        parts.append(f"## First {i:03d} {{#anc{i:03d}}}\n\nA\n")
        parts.append(f"## Second {i:03d} {{#anc{i:03d}}}\n\nB\n")
    return {"d.md": "\n".join(parts)}


def test_duplicate_anchors_list_is_capped(tmp_path):
    total = DUPLICATE_ANCHORS_CAP + 3
    out, _ = _index(tmp_path, "capdup", _many_duplicate_anchors(total))

    assert len(out["duplicate_anchors"]) == DUPLICATE_ANCHORS_CAP
    assert out["duplicate_anchors_total"] == total
    assert out["duplicate_anchors_truncated"] is True
    # Sorted before the cut: the kept entries are the first by (doc_path, anchor).
    assert [d["anchor"] for d in out["duplicate_anchors"]] == [
        f"anc{i:03d}" for i in range(DUPLICATE_ANCHORS_CAP)
    ]
    # The sentence counts the uncapped report, and "+N more" is its remainder.
    assert f"{total} duplicate anchor id(s)" in out["duplicate_anchor_warning"]
    assert _more(total, 3) in out["duplicate_anchor_warning"]
    for entry in out["duplicate_anchors"]:
        assert entry["count"] == 2
        assert entry["section_ids_truncated"] is False


def test_duplicate_anchors_list_at_the_cap_drops_nothing(tmp_path):
    out, _ = _index(tmp_path, "capdupok", _many_duplicate_anchors(DUPLICATE_ANCHORS_CAP))

    assert len(out["duplicate_anchors"]) == DUPLICATE_ANCHORS_CAP
    assert out["duplicate_anchors_total"] == DUPLICATE_ANCHORS_CAP
    assert out["duplicate_anchors_truncated"] is False
    assert f"{DUPLICATE_ANCHORS_CAP} duplicate anchor id(s)" in out["duplicate_anchor_warning"]


def test_one_duplicate_entry_caps_its_ids_and_titles(tmp_path):
    written = DUPLICATE_ANCHOR_IDS_CAP + 3
    out, _ = _index(tmp_path, "capids", _same_doc_file(written))

    assert out["duplicate_anchors_total"] == 1
    assert out["duplicate_anchors_truncated"] is False
    (entry,) = out["duplicate_anchors"]
    # `count` is the authority, not the length of the lists beside it.
    assert entry["count"] == written
    assert len(entry["section_ids"]) == DUPLICATE_ANCHOR_IDS_CAP
    assert len(entry["titles"]) == DUPLICATE_ANCHOR_IDS_CAP
    assert entry["section_ids_truncated"] is True
    assert entry["titles"] == [f"Step {i:03d}" for i in range(DUPLICATE_ANCHOR_IDS_CAP)]
    assert f"step x{written} in dup.md" in out["duplicate_anchor_warning"]


def test_one_duplicate_entry_at_the_cap_drops_nothing(tmp_path):
    out, _ = _index(tmp_path, "capidsok", _same_doc_file(DUPLICATE_ANCHOR_IDS_CAP))

    (entry,) = out["duplicate_anchors"]
    assert entry["count"] == DUPLICATE_ANCHOR_IDS_CAP
    assert len(entry["section_ids"]) == DUPLICATE_ANCHOR_IDS_CAP
    assert len(entry["titles"]) == DUPLICATE_ANCHOR_IDS_CAP
    assert entry["section_ids_truncated"] is False


def test_clean_index_carries_the_cap_keys_too(tmp_path):
    out, _ = _index(tmp_path, "capclean", {"a.md": "# A\n\n## One {#one}\n\nX\n"})
    assert out["duplicate_anchors"] == []
    assert out["duplicate_anchors_total"] == 0
    assert out["duplicate_anchors_truncated"] is False
    assert out["duplicate_anchor_warning"] == ""
