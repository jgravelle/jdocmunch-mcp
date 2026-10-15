"""Anchor references through the tools that are not get_section (v1.147.0).

Every tool taking a caller-supplied id resolves ``doc.md#anchor`` references via
``DocIndex.get_section``. Only ``get_section`` used to say when the reference was
ambiguous; the others served the first duplicate silently and turned a
cross-document ambiguity into a plain "not found". These tests pin the shared
disclosure on each of them.
"""

import pytest

from jdocmunch_mcp.tools.check_section_delete_safe import check_section_delete_safe
from jdocmunch_mcp.tools.describe_section import describe_section
from jdocmunch_mcp.tools.get_related_sections import get_related_sections
from jdocmunch_mcp.tools.get_section_blast_radius import get_section_blast_radius
from jdocmunch_mcp.tools.get_section_context import get_section_context
from jdocmunch_mcp.tools.get_section_descendants import get_section_descendants
from jdocmunch_mcp.tools.get_section_diff import get_section_diff
from jdocmunch_mcp.tools.get_section_excerpt import get_section_excerpt
from jdocmunch_mcp.tools.get_section_excerpts import get_section_excerpts
from jdocmunch_mcp.tools.get_section_path import get_section_path
from jdocmunch_mcp.tools.get_section_summaries import get_section_summaries
from jdocmunch_mcp.tools.get_section_summary import get_section_summary
from jdocmunch_mcp.tools.get_sections import get_sections
from jdocmunch_mcp.tools.get_tutorial_path import get_tutorial_path
from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.section_neighbors import section_neighbors

REPO = "local/sibdisclose"
KEYS = {"anchor_ambiguous", "anchor_scope", "anchor_match_count",
        "anchor_candidates"}


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    root = tmp_path_factory.mktemp("sib")
    src, st = root / "src", root / "store"
    src.mkdir()
    (src / "d.md").write_text(
        "# Doc\n\n## First {#foo}\n\nFIRST\n\n## Second {#foo}\n\nSECOND\n\n"
        "## Solo {#solo}\n\nSOLO\n",
        encoding="utf-8",
    )
    (src / "e.md").write_text("# E\n\n## Other {#bar}\n\nX\n", encoding="utf-8")
    (src / "f.md").write_text("# F\n\n## Another {#bar}\n\nY\n", encoding="utf-8")
    # A parent with children and a sibling, so a lookup that loses the parent's
    # stored id visibly loses its children, neighbours and relations.
    (src / "g.md").write_text(
        "# G\n\n## Parent {#par}\n\nP\n\n### Kid A\n\nA\n\n### Kid B\n\nB\n\n"
        "## Sibling\n\nS\n",
        encoding="utf-8",
    )
    # Something that links to the anchor, and a numbered pair for a tutorial chain.
    (src / "h.md").write_text("# H\n\nSee [it](g.md#par).\n", encoding="utf-8")
    (src / "01-intro.md").write_text("# Intro {#intro}\n\nStart.\n", encoding="utf-8")
    (src / "02-next.md").write_text("# Next {#nxt}\n\nThen.\n", encoding="utf-8")
    index_local(path=str(src), name="sibdisclose", use_ai_summaries=False,
                use_embeddings=False, storage_path=str(st))
    return str(st)


def _concrete_id(store, ref="d.md#solo"):
    out = get_sections(repo=REPO, section_ids=[ref], storage_path=store)
    return out["sections"][0]["section"]["id"]


def _single(tool, ref, store):
    return tool(repo=REPO, section_id=ref, storage_path=store)


def _first(tool, ref, store):
    out = tool(repo=REPO, section_ids=[ref], storage_path=store)
    return out, out["sections"][0]


SINGLE = [get_section_context, get_section_excerpt]
BATCH = [get_sections, get_section_excerpts, get_section_summaries]


@pytest.mark.parametrize("tool", SINGLE)
def test_single_within_document_duplicate_discloses_and_serves_first(tool, store):
    out = _single(tool, "d.md#foo", store)
    assert out["section"]["title"] == "First"
    assert out["anchor_ambiguous"] is True
    assert out["anchor_scope"] == "document"
    assert out["anchor_match_count"] == 2
    assert [c["title"] for c in out["anchor_candidates"]] == ["First", "Second"]
    assert "Ambiguous" in out["warning"]


@pytest.mark.parametrize("tool", SINGLE)
def test_single_cross_document_ambiguity_is_an_ambiguity_error(tool, store):
    out = _single(tool, "#bar", store)
    assert "section" not in out
    assert "ambiguous" in out["error"].lower()
    assert "not found" not in out["error"].lower()
    assert out["anchor_scope"] == "index"
    assert sorted(c["doc_path"] for c in out["anchor_candidates"]) == ["e.md", "f.md"]
    assert out["warning"]


@pytest.mark.parametrize("tool", BATCH)
def test_batch_within_document_duplicate_discloses_on_entry_and_top_level(tool, store):
    out, entry = _first(tool, "d.md#foo", store)
    assert KEYS <= set(entry)
    assert entry["anchor_scope"] == "document"
    assert entry["anchor_match_count"] == 2
    assert "error" not in entry
    assert out["anchor_ambiguous"] is True
    assert "Ambiguous" in out["warning"]


@pytest.mark.parametrize("tool", BATCH)
def test_batch_cross_document_ambiguity_is_a_per_id_ambiguity_error(tool, store):
    out, entry = _first(tool, "#bar", store)
    assert "ambiguous" in entry["error"].lower()
    assert entry["anchor_scope"] == "index"
    assert sorted(c["doc_path"] for c in entry["anchor_candidates"]) == ["e.md", "f.md"]
    assert out["anchor_ambiguous"] is True and out["warning"]


@pytest.mark.parametrize("tool", SINGLE)
def test_single_unambiguous_anchor_and_concrete_id_add_no_keys(tool, store):
    for ref in ("d.md#solo", "e.md#bar", _concrete_id(store)):
        out = _single(tool, ref, store)
        assert out.get("section"), (ref, out)
        assert not (KEYS & set(out)) and "warning" not in out


@pytest.mark.parametrize("tool", BATCH)
def test_batch_unambiguous_anchor_and_concrete_id_add_no_keys(tool, store):
    for ref in ("d.md#solo", "e.md#bar", _concrete_id(store)):
        out, entry = _first(tool, ref, store)
        assert entry.get("section"), (ref, entry)
        assert not (KEYS & set(entry)) and "warning" not in entry
        assert "anchor_ambiguous" not in out and "warning" not in out


@pytest.mark.parametrize("tool", SINGLE)
def test_single_plain_miss_stays_a_plain_miss(tool, store):
    out = _single(tool, "d.md#nope", store)
    assert out["error"].startswith("Section not found")
    assert not (KEYS & set(out))


@pytest.mark.parametrize("tool", BATCH)
def test_batch_mixed_ids_flag_only_the_ambiguous_one(tool, store):
    out = tool(repo=REPO, section_ids=["d.md#solo", "d.md#foo"], storage_path=store)
    assert "anchor_ambiguous" not in out["sections"][0]
    assert out["sections"][1]["anchor_ambiguous"] is True
    assert out["anchor_ambiguous"] is True


# --- the tools that only read a section by id (v1.147.0) ---------------------
# Same disclosure, but each of these also walks the tree, a relation graph or a
# tutorial chain from the section it resolved, so the equivalence test below is
# what proves the walk starts from the STORED id rather than the caller's string.

TOP = [
    describe_section, section_neighbors, get_section_path,
    get_section_descendants, get_section_summary, get_section_diff,
    get_related_sections, get_tutorial_path,
]
SAFETY = [check_section_delete_safe, get_section_blast_radius]


def _ids(tool):
    return tool.__name__


@pytest.mark.parametrize("tool", TOP, ids=_ids)
def test_top_within_document_duplicate_discloses_and_serves_first(tool, store):
    out = _single(tool, "d.md#foo", store)
    assert "error" not in out, out
    assert out["anchor_ambiguous"] is True
    assert out["anchor_scope"] == "document"
    assert out["anchor_match_count"] == 2
    assert [c["title"] for c in out["anchor_candidates"]] == ["First", "Second"]
    assert "Ambiguous" in out["warning"]


@pytest.mark.parametrize("tool", TOP, ids=_ids)
def test_top_cross_document_ambiguity_is_an_ambiguity_error(tool, store):
    out = _single(tool, "#bar", store)
    assert "ambiguous" in out["error"].lower()
    assert "not found" not in out["error"].lower()
    assert out["anchor_scope"] == "index"
    assert sorted(c["doc_path"] for c in out["anchor_candidates"]) == ["e.md", "f.md"]
    assert out["warning"]


@pytest.mark.parametrize("tool", TOP, ids=_ids)
def test_top_unambiguous_anchor_and_concrete_id_add_no_keys(tool, store):
    for ref in ("d.md#solo", "e.md#bar", _concrete_id(store)):
        out = _single(tool, ref, store)
        assert "error" not in out, (ref, out)
        assert not (KEYS & set(out)) and "warning" not in out


@pytest.mark.parametrize("tool", TOP, ids=_ids)
def test_top_plain_miss_stays_a_plain_miss(tool, store):
    out = _single(tool, "d.md#nope", store)
    assert out["error"].startswith("Section not found")
    assert not (KEYS & set(out))


def _body(out):
    """A response minus what legitimately differs between a reference and an id."""
    out = dict(out)
    out.pop("_meta", None)
    out.pop("section_id", None)
    if isinstance(out.get("start"), dict):
        out["start"] = {k: v for k, v in out["start"].items() if k != "section_id"}
    return out


@pytest.mark.parametrize("tool", TOP, ids=_ids)
def test_top_anchor_reference_answers_exactly_as_its_stored_id(tool, store):
    concrete = _concrete_id(store, "g.md#par")
    by_ref = _single(tool, "g.md#par", store)
    by_id = _single(tool, concrete, store)
    assert "error" not in by_ref, by_ref
    assert _body(by_ref) == _body(by_id)


def test_the_equivalence_fixture_has_something_to_lose(store):
    # The test above is vacuous for a tool whose answer is empty either way.
    concrete = _concrete_id(store, "g.md#par")
    assert get_section_descendants(
        repo=REPO, section_id=concrete, storage_path=store)["descendant_count"] == 2
    assert section_neighbors(
        repo=REPO, section_id=concrete, storage_path=store)["child_count"] == 2
    rel = get_related_sections(
        repo=REPO, section_id=concrete, mode="structural", storage_path=store)
    assert rel["structural"]
    assert describe_section(
        repo=REPO, section_id=concrete, storage_path=store)["neighbors"]["next"]


@pytest.mark.parametrize("sidecar", [True, False], ids=["sidecar", "on_demand"])
def test_related_reads_the_stored_id_for_a_reference_to_a_parent(
        sidecar, store, monkeypatch):
    # Two lookups key on the id: the persisted relation sidecar and the
    # on-demand graph. Each is exercised on its own, because the sidecar
    # answers first and would hide a raw-ref bug in the second.
    if not sidecar:
        monkeypatch.setattr(
            "jdocmunch_mcp.tools.get_related_sections._persisted_lookup",
            lambda *a, **k: None)
    concrete = _concrete_id(store, "g.md#par")
    by_ref = get_related_sections(
        repo=REPO, section_id="g.md#par", mode="structural", storage_path=store)
    by_id = get_related_sections(
        repo=REPO, section_id=concrete, mode="structural", storage_path=store)
    assert by_id["_meta"]["source"] == ("sidecar" if sidecar else "on_demand")
    assert by_ref["_meta"]["source"] == by_id["_meta"]["source"]
    assert by_ref["structural"] == by_id["structural"] != []


# --- get_section_context include_related (v1.147.0) --------------------------

def test_context_include_related_reads_the_stored_id_for_a_reference(store):
    concrete = _concrete_id(store, "g.md#par")
    by_ref = get_section_context(
        repo=REPO, section_id="g.md#par", include_related=True, storage_path=store)
    by_id = get_section_context(
        repo=REPO, section_id=concrete, include_related=True, storage_path=store)
    assert by_id["related"], "fixture must produce related sections"
    assert by_ref["related"] == by_id["related"]
    assert by_ref["_meta"]["related_count"] == by_id["_meta"]["related_count"]


# --- the deletion preflight and the blast radius REFUSE (v1.147.0) -----------

def _result(out):
    return out["result"]


@pytest.mark.parametrize("tool", SAFETY, ids=_ids)
def test_safety_within_document_duplicate_is_refused_not_analysed(tool, store):
    out = _single(tool, "d.md#foo", store)
    assert "result" not in out
    assert "ambiguous" in out["error"].lower()
    assert out["error"] == out["warning"]
    assert "first match was served" not in out["warning"]
    assert out["anchor_ambiguous"] is True
    assert out["anchor_scope"] == "document"
    assert out["anchor_match_count"] == 2
    assert [c["title"] for c in out["anchor_candidates"]] == ["First", "Second"]


@pytest.mark.parametrize("tool", SAFETY, ids=_ids)
def test_safety_cross_document_ambiguity_is_refused_naming_candidates(tool, store):
    out = _single(tool, "#bar", store)
    assert "result" not in out
    assert "ambiguous" in out["error"].lower()
    assert "not found" not in out["error"].lower()
    assert out["anchor_scope"] == "index"
    assert sorted(c["doc_path"] for c in out["anchor_candidates"]) == ["e.md", "f.md"]


@pytest.mark.parametrize("tool", SAFETY, ids=_ids)
def test_safety_each_duplicate_is_still_analysable_by_its_own_id(tool, store):
    for c in _single(tool, "d.md#foo", store)["anchor_candidates"]:
        out = _single(tool, c["id"], store)
        assert "error" not in out, out
        assert out["result"]


@pytest.mark.parametrize("tool", SAFETY, ids=_ids)
def test_safety_unambiguous_anchor_and_concrete_id_add_no_keys(tool, store):
    for ref in ("d.md#solo", "e.md#bar", _concrete_id(store)):
        out = _single(tool, ref, store)
        assert "error" not in out, (ref, out)
        assert not (KEYS & set(out)) and not (KEYS & set(_result(out)))
        assert "warning" not in out and "warning" not in _result(out)


@pytest.mark.parametrize("tool", SAFETY, ids=_ids)
def test_safety_plain_miss_stays_a_plain_miss(tool, store):
    out = _single(tool, "d.md#nope", store)
    assert out["error"].startswith("Section not found")
    assert not (KEYS & set(out))


def _safety_body(out):
    res = dict(_result(out))
    res.pop("section_id", None)
    res.pop("resolved_section_id", None)
    if isinstance(res.get("target"), dict):
        res["target"] = {k: v for k, v in res["target"].items()
                         if k not in ("section_id", "resolved_section_id")}
    return res


@pytest.mark.parametrize("tool", SAFETY, ids=_ids)
def test_safety_anchor_reference_is_analysed_as_its_stored_id(tool, store):
    # g.md#par is linked to from h.md by anchor; the reference has no
    # `owner::doc::slug#level` shape, so a slug parsed from the RAW string is
    # empty and the anchor-link channel silently drops out.
    concrete = _concrete_id(store, "g.md#par")
    by_ref = _single(tool, "g.md#par", store)
    by_id = _single(tool, concrete, store)
    assert "error" not in by_ref, by_ref
    assert _safety_body(by_ref) == _safety_body(by_id)
    res = _result(by_id)
    found = (res["evidence"]["anchor_backlink_count"] if "evidence" in res
             else res["summary"]["anchor_refs"])
    assert found >= 1, "fixture must carry an anchored link into the target"
    holder = lambda o: _result(o).get("target", _result(o))  # noqa: E731
    assert holder(by_ref)["resolved_section_id"] == concrete
    assert "resolved_section_id" not in holder(by_id)


def test_delete_preflight_sees_a_tutorial_chain_through_an_anchor_reference(store):
    # The chain is numbered by filename, so every section of 01-intro.md starts it.
    concrete = _concrete_id(store, "01-intro.md#intro")
    by_id = check_section_delete_safe(
        repo=REPO, section_id=concrete, storage_path=store)
    by_ref = check_section_delete_safe(
        repo=REPO, section_id="01-intro.md#intro", storage_path=store)
    assert _result(by_id)["verdict"] == "tutorial_path_blocking"
    assert _result(by_ref)["verdict"] == "tutorial_path_blocking"
    blast = get_section_blast_radius(
        repo=REPO, section_id="01-intro.md#intro", storage_path=store)
    assert _result(blast)["summary"]["tutorial_chains_broken"] == 1


# --- an upper-case reference discloses exactly as a lower-case one ----------
# Anchors match case-insensitively (USER_GUIDE), so the disclosure must not
# depend on how the caller spelled the id. A document PATH is not case-folded.

ALL_SINGLE = SINGLE + TOP + SAFETY


@pytest.mark.parametrize("tool", ALL_SINGLE, ids=_ids)
def test_uppercase_reference_discloses_the_same_as_lowercase(tool, store):
    for lower, upper in (("d.md#foo", "d.md#FOO"), ("#bar", "#BaR")):
        a, b = _single(tool, lower, store), _single(tool, upper, store)
        assert b.get("anchor_ambiguous") is True, (upper, b)
        for key in ("anchor_scope", "anchor_match_count", "anchor_candidates"):
            assert b[key] == a[key], (tool.__name__, upper, key)
        assert ("error" in a) == ("error" in b)


@pytest.mark.parametrize("tool", BATCH, ids=_ids)
def test_uppercase_reference_discloses_on_batch_tools(tool, store):
    for lower, upper in (("d.md#foo", "d.md#FOO"), ("#bar", "#BaR")):
        _, a = _first(tool, lower, store)
        out, b = _first(tool, upper, store)
        assert b["anchor_ambiguous"] is True and out["anchor_ambiguous"] is True
        assert b["anchor_candidates"] == a["anchor_candidates"]


@pytest.mark.parametrize("tool", ALL_SINGLE, ids=_ids)
def test_uppercase_unambiguous_reference_resolves_with_no_keys(tool, store):
    out = _single(tool, "d.md#SOLO", store)
    assert "error" not in out, out
    assert not (KEYS & set(out))


# --- tutorial chains ----------------------------------------------------------
# The earlier equivalence test ran on g.md, which has no chain, so for
# get_tutorial_path it compared an empty chain with an empty chain. This one
# uses the numbered pair, which does produce one.

def test_tutorial_chain_by_reference_equals_chain_by_stored_id(store):
    concrete = _concrete_id(store, "01-intro.md#intro")
    by_ref = get_tutorial_path(
        repo=REPO, section_id="01-intro.md#intro", storage_path=store)
    by_id = get_tutorial_path(repo=REPO, section_id=concrete, storage_path=store)
    assert [c["doc_path"] for c in by_id["chain"]] == ["01-intro.md", "02-next.md"]
    assert by_ref["chain"] == by_id["chain"]
    assert by_ref["strategy"] == by_id["strategy"] == "ordered_filename"


def test_tutorial_chain_members_are_document_roots_which_carry_no_anchor(store):
    """TRIPWIRE, not a behaviour test. A chain is made of each document's
    level-0 root section, and an explicit `{#id}` attaches to a heading, never to
    that root. So a `doc.md#anchor` reference can never name a chain member, and
    `check_section_delete_safe`'s mid-chain comparison (`stored_id in chain_ids`)
    cannot be reached with an anchored target: no test can tell it from the raw
    reference. If chain members ever become anchorable, that comparison becomes
    live and needs a biting test; this one fails to say so."""
    out = get_tutorial_path(
        repo=REPO, section_id="01-intro.md#intro", storage_path=store)
    assert len(out["chain"]) == 2
    stored = get_sections(
        repo=REPO, section_ids=[c["section_id"] for c in out["chain"]],
        storage_path=store)["sections"]
    for entry in stored:
        sec = entry["section"]
        assert sec["level"] == 0 and not sec.get("anchor"), sec
