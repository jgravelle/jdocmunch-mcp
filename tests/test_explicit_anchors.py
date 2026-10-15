"""Explicit heading anchors: ``## Heading {#custom-id}`` (v1.147.0, #64 follow-on).

Two things are under test and they are separable:

1. The INDEXER honours the marker — the anchor becomes the section's slug, so the
   id stops being a function of the heading's words, and the marker no longer
   leaks into the id or the title.
2. The indexer and ``get_broken_links`` share ONE anchor namespace. They did not
   before: the checker reported ``doc.md#custom-id`` as a live target while
   ``get_section`` could not resolve it.

Fixtures pin ``use_embeddings=False`` and ``use_ai_summaries=False``. Leaving
either at its default reads whatever happens to be installed on the box, which
is a different program from the one CI runs.
"""

import pytest

from jdocmunch_mcp.parser.markdown_parser import parse_markdown
from jdocmunch_mcp.parser.sections import split_explicit_id
from jdocmunch_mcp.tools.get_broken_links import get_broken_links
from jdocmunch_mcp.tools.get_section import get_section
from jdocmunch_mcp.tools.index_local import index_local

REPO = "local/anchortest"
HEADING = (
    "Export FAILS when the cache volume is full -- "
    "the partial file is removed"
)
DERIVED = (
    "export-fails-when-the-cache-volume-is-full-"
    "the-partial-file-is-removed"
)


def _sections(body, doc_path="docs/guides/exporting.md"):
    return parse_markdown(body, doc_path, REPO)


def _by_title(sections, title):
    return next(s for s in sections if s.title == title)


# --------------------------------------------------------------------------
# split_explicit_id
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title,expected",
    [
        ("A heading {#ex-cache42}", ("A heading", "ex-cache42")),
        ("A heading", ("A heading", None)),
        # Lowercased: anchors match case-insensitively in every renderer.
        ("A heading {#Ex-CACHE42}", ("A heading", "ex-cache42")),
        # Trailing whitespace after the marker is still a marker.
        ("A heading {#ex-cache42}   ", ("A heading", "ex-cache42")),
    ],
)
def test_split_explicit_id(title, expected):
    assert split_explicit_id(title) == expected


def test_unsafe_id_strips_marker_but_yields_no_anchor():
    """An id a renderer would not emit must not become an anchor -- but the
    marker still comes off, so the slug is the clean text slug and never the
    polluted 'heading-text-custom-id' form (#64)."""
    text, explicit = split_explicit_id("A heading {#9nope}")
    assert explicit is None
    assert text == "A heading"


# --------------------------------------------------------------------------
# Indexing
# --------------------------------------------------------------------------


def test_anchor_replaces_the_slug_and_leaves_the_title_clean():
    secs = _sections(f"# Exporting\n\nintro\n\n## {HEADING} {{#ex-cache42}}\n\nbody\n")
    sec = _by_title(secs, HEADING)
    assert sec.anchor == "ex-cache42"
    assert sec.id.endswith("::ex-cache42#2")
    # The braces must reach neither the id nor the title.
    assert "{#" not in sec.id and "{#" not in sec.title
    assert "ex-cache42" not in sec.title


def test_anchor_is_the_whole_path_not_a_leaf():
    """The ancestor prefix exists only to disambiguate same-named headings.
    Inheriting it would put the H1's WORDS back into an id whose whole purpose
    is to stop depending on words, so renaming the H1 would still break the
    pointer."""
    secs = _sections(f"# Exporting\n\n## {HEADING} {{#ex-cache42}}\n\nbody\n")
    sec = _by_title(secs, HEADING)
    assert "exporting/" not in sec.id


def test_anchor_survives_a_heading_rewording():
    """The point of the feature: reword the heading, keep the id."""
    a = _by_title(_sections(f"# T\n\n## {HEADING} {{#ex-cache42}}\n\nbody\n"), HEADING)
    reworded = "Totally different words now"
    b = _by_title(_sections(f"# T\n\n## {reworded} {{#ex-cache42}}\n\nbody\n"), reworded)
    assert a.id == b.id


def test_derived_id_is_kept_as_an_alias():
    """A consumer holding the pre-anchor id must keep resolving after the
    anchor is added, otherwise adding one silently breaks existing pointers."""
    secs = _sections(f"# Exporting\n\n## {HEADING} {{#ex-cache42}}\n\nbody\n")
    sec = _by_title(secs, HEADING)
    # The id v1.146.0 gave it (marker slugified into the title), then the
    # marker-free heading-derived id.
    assert sec.alias_ids == [
        f"{REPO}::docs/guides/exporting.md::exporting/{DERIVED}-ex-cache42#2",
        f"{REPO}::docs/guides/exporting.md::exporting/{DERIVED}#2",
    ]


def test_unanchored_children_inherit_the_stable_prefix():
    secs = _sections(
        f"# Exporting\n\n## {HEADING} {{#ex-cache42}}\n\nbody\n\n### Child\n\nkid\n"
    )
    assert _by_title(secs, "Child").id.endswith("::ex-cache42/child#3")


def test_child_of_an_anchored_heading_keeps_both_earlier_ids():
    """v1.146.0 gave this child 'exporting/export-fails-ex-cache42/child#3'; the
    marker-free derivation is 'exporting/export-fails/child#3'. Both are written
    out as literals so the test does not lean on the code it checks."""
    body = (
        "# Exporting\n\n## Export fails {#ex-cache42}\n\nbody\n\n"
        "### Child\n\nkid\n\n#### Grandchild\n\ndeep\n"
    )
    secs = _sections(body)
    child = _by_title(secs, "Child")
    assert child.id.endswith("::ex-cache42/child#3")
    p = f"{REPO}::docs/guides/exporting.md::"
    assert child.alias_ids == [
        p + "exporting/export-fails-ex-cache42/child#3",
        p + "exporting/export-fails/child#3",
    ]
    grand = _by_title(secs, "Grandchild")
    assert p + "exporting/export-fails-ex-cache42/child/grandchild#4" in grand.alias_ids
    assert p + "exporting/export-fails/child/grandchild#4" in grand.alias_ids


def test_marker_heading_keeps_the_id_v1_146_0_gave_it():
    """At v1.146.0 the marker stayed in the title and was slugified, so
    '## Export fails {#ex-cache42}' was 'exporting/export-fails-ex-cache42#2'
    and an unsafe '{#has space}' was 'exporting/bad-has-space#2'."""
    secs = _sections(
        "# Exporting\n\n## Export fails {#ex-cache42}\n\nbody\n\n"
        "## Bad {#has space}\n\nbody\n"
    )
    p = f"{REPO}::docs/guides/exporting.md::"
    anchored = _by_title(secs, "Export fails")
    assert p + "exporting/export-fails-ex-cache42#2" in anchored.alias_ids
    assert p + "exporting/export-fails#2" in anchored.alias_ids
    bad = _by_title(secs, "Bad")
    assert bad.id == p + "exporting/bad#2"
    assert bad.alias_ids == [p + "exporting/bad-has-space#2"]


def test_unanchored_document_gets_no_alias_ids_key():
    secs = _sections("# Exporting\n\n## Plain heading\n\nbody\n")
    assert all("alias_ids" not in s.to_dict() for s in secs)


def test_setext_headings_take_anchors_too():
    secs = _sections("Exporting {#ex-top}\n===============\n\nbody\n")
    sec = _by_title(secs, "Exporting")
    assert sec.anchor == "ex-top"
    assert sec.id.endswith("::ex-top#1")


def test_unanchored_document_is_byte_for_byte_unchanged():
    """Regression guard: a corpus with no anchors must index exactly as before,
    or this feature is a silent id migration for every existing user."""
    secs = _sections("# Exporting\n\n## Plain heading\n\nbody\n\n### Deeper\n\nx\n")
    ids = [s.id for s in secs]
    assert ids == [
        f"{REPO}::docs/guides/exporting.md::exporting#0",
        f"{REPO}::docs/guides/exporting.md::exporting#1",
        f"{REPO}::docs/guides/exporting.md::exporting/plain-heading#2",
        f"{REPO}::docs/guides/exporting.md::exporting/plain-heading/deeper#3",
    ]
    assert all(s.anchor == "" and s.alias_ids == [] for s in secs)


def test_unsafe_anchor_does_not_pollute_the_id():
    secs = _sections("# T\n\n## Heading {#9nope}\n\nbody\n")
    sec = _by_title(secs, "Heading")
    assert sec.anchor == ""
    assert sec.id.endswith("::t/heading#2")


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


@pytest.fixture
def indexed(tmp_path):
    src = tmp_path / "src"
    store = tmp_path / "store"
    guides = src / "docs" / "guides"
    guides.mkdir(parents=True)
    (guides / "exporting.md").write_text(
        f"# Exporting\n\nintro\n\n## {HEADING} {{#ex-cache42}}\n\n"
        "The exporter removes the partial file when the volume fills.\n",
        encoding="utf-8",
    )
    (src / "guides.md").write_text(
        "# Index\n\n- Failed exports leave no file -> "
        "[ex](docs/guides/exporting.md#ex-cache42)\n",
        encoding="utf-8",
    )
    index_local(
        path=str(src),
        name="anchortest",
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(store),
    )
    return str(store)


@pytest.mark.parametrize(
    "ref",
    [
        "docs/guides/exporting.md#ex-cache42",   # full indexed path
        "exporting.md#ex-cache42",                  # relative suffix
        "./docs/guides/exporting.md#ex-cache42",  # leading ./
        "#ex-cache42",                                    # bare, unique here
    ],
)
def test_anchor_reference_resolves(indexed, ref):
    out = get_section(repo=REPO, section_id=ref, storage_path=indexed)
    assert out.get("section"), out.get("error")
    assert out["section"]["anchor"] == "ex-cache42"
    assert out["section"]["byte_end"] > out["section"]["byte_start"]


@pytest.mark.parametrize(
    "ref",
    [
        "docs/guides/exporting.md#EX-CACHE42",   # upper-case anchor
        "exporting.md#Ex-CaChe42",                  # mixed case
        "#EX-CACHE42",                                    # bare, upper-case
        "  #Ex-Cache42  ",                                # padded
    ],
)
def test_anchor_part_of_a_reference_is_case_insensitive(indexed, ref):
    """The heading side lowercases ({#Ex-CACHE42} -> ex-cache42); the reference
    side must too, or an id copied from a rendered page's URL fragment misses."""
    out = get_section(repo=REPO, section_id=ref, storage_path=indexed)
    assert out.get("section"), out.get("error")
    assert out["section"]["anchor"] == "ex-cache42"
    lower = get_section(repo=REPO, section_id=ref.strip().lower(), storage_path=indexed)
    assert lower["section"]["id"] == out["section"]["id"]


def test_document_part_of_a_reference_is_not_case_folded(indexed):
    """Only the ANCHOR is case-insensitive. A document path is a filesystem path
    and is matched as written, so USER_GUIDE says 'ids', not 'references'."""
    out = get_section(
        repo=REPO,
        section_id="docs/guides/EXPORTING.md#ex-cache42",
        storage_path=indexed,
    )
    assert not out.get("section")


def test_alias_id_still_resolves(indexed):
    alias = f"{REPO}::docs/guides/exporting.md::exporting/{DERIVED}#2"
    out = get_section(repo=REPO, section_id=alias, storage_path=indexed)
    assert out.get("section"), out.get("error")
    assert out["section"]["anchor"] == "ex-cache42"


def test_ids_from_before_the_feature_resolve_after_a_reindex(tmp_path):
    """The ids below are literals as v1.146.0 produced them. An index built
    then and re-indexed now must still answer to every one."""
    src = tmp_path / "src"
    store = tmp_path / "store"
    (src / "docs").mkdir(parents=True)
    (src / "docs" / "exporting.md").write_text(
        "# Exporting\n\n## Export fails {#ex-cache42}\n\nbody\n\n"
        "### Child\n\nkid\n\n## Bad {#has space}\n\nbody\n",
        encoding="utf-8",
    )
    index_local(
        path=str(src), name="anchortest", use_ai_summaries=False,
        use_embeddings=False, storage_path=str(store),
    )
    p = f"{REPO}::docs/exporting.md::"
    for old_id, title in [
        (p + "exporting/export-fails-ex-cache42#2", "Export fails"),
        (p + "exporting/export-fails-ex-cache42/child#3", "Child"),
        (p + "exporting/export-fails/child#3", "Child"),
        (p + "exporting/bad-has-space#2", "Bad"),
    ]:
        out = get_section(repo=REPO, section_id=old_id, storage_path=str(store))
        assert out.get("section"), (old_id, out.get("error"))
        assert out["section"]["title"] == title


@pytest.mark.parametrize(
    "ref",
    [
        "docs/guides/exporting.md#ex-cache42",
        f"{REPO}::docs/guides/exporting.md::exporting/{DERIVED}#2",  # alias id
    ],
)
def test_citation_names_the_stored_id_not_the_callers_reference(indexed, ref):
    from jdocmunch_mcp.tools.get_section_context import get_section_context

    stored = f"{REPO}::docs/guides/exporting.md::ex-cache42#2"
    out = get_section(repo=REPO, section_id=ref, storage_path=indexed)
    assert out["section"]["id"] == stored
    assert out["_meta"]["citation"]["section_id"] == stored
    ctx = get_section_context(repo=REPO, section_id=ref, storage_path=indexed)
    assert ctx["_meta"]["citation"]["section_id"] == stored


def test_unknown_anchor_is_a_miss_not_a_neighbour(indexed):
    out = get_section(
        repo=REPO,
        section_id="docs/guides/exporting.md#no-such-anchor",
        storage_path=indexed,
    )
    assert not out.get("section")


def test_ambiguous_anchor_promotes_no_single_section(tmp_path):
    """A guides index once had a pointer that silently borrowed its
    neighbour's target. So an ambiguous cross-document ref promotes NOTHING to
    `section` -- every match is returned under `sections` instead, and the
    caller is told."""
    src = tmp_path / "src"
    store = tmp_path / "store"
    guides = src / "docs" / "guides"
    guides.mkdir(parents=True)
    (guides / "a.md").write_text("# A\n\n## One {#dup}\n\nbody a\n", encoding="utf-8")
    (guides / "b.md").write_text("# B\n\n## Two {#dup}\n\nbody b\n", encoding="utf-8")
    index_local(
        path=str(src),
        name="anchordup",
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(store),
    )
    # Ambiguous: refuses.
    assert not get_section(
        repo="local/anchordup", section_id="#dup", storage_path=str(store)
    ).get("section")
    # Disambiguated by document: resolves.
    out = get_section(
        repo="local/anchordup",
        section_id="docs/guides/a.md#dup",
        storage_path=str(store),
    )
    assert out.get("section"), out.get("error")
    assert out["section"]["title"] == "One"


# --------------------------------------------------------------------------
# One namespace
# --------------------------------------------------------------------------


def test_link_to_an_anchor_is_not_reported_broken(indexed):
    """The split-brain fix. Before v1.147.0 the checker accepted {#id} while the
    indexer mangled it, so this link validated as live and get_section could not
    resolve it. Both halves must now agree."""
    out = get_broken_links(repo=REPO, storage_path=indexed)
    offenders = [
        b for b in out.get("broken_links", [])
        if "ex-cache42" in str(b)
    ]
    assert offenders == [], offenders


def test_legacy_index_without_anchor_field_still_validates():
    """An index built before v1.147.0 has no ``anchor`` field and still carries
    the marker verbatim in its title. It must keep validating without a reindex
    -- the 1.x contract forbids a forced one."""
    from jdocmunch_mcp.tools.get_broken_links import _build_rendered_anchors

    legacy = [{"doc_path": "t.md", "level": 2, "title": "Stable Heading {#stable-target}"}]
    assert "stable-target" in _build_rendered_anchors(legacy).get("t.md", set())

    current = [{"doc_path": "t.md", "level": 2, "title": "Stable Heading",
                "anchor": "stable-target"}]
    assert "stable-target" in _build_rendered_anchors(current).get("t.md", set())


def test_reference_more_qualified_than_the_index_resolves(tmp_path):
    """A common layout: guides.md sits at the repo root and points at
    'docs/guides/x.md#a', but the index is rooted at docs/guides so its
    doc_path is just 'x.md'. The pointer must still resolve."""
    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir()
    (src / "exporting.md").write_text(
        "# Exporting\n\n## Heading {#ex-cache42}\n\nbody\n", encoding="utf-8"
    )
    index_local(path=str(src), name="vcshape", use_ai_summaries=False,
                use_embeddings=False, storage_path=str(store))
    out = get_section(
        repo="local/vcshape",
        section_id="docs/guides/exporting.md#ex-cache42",
        storage_path=str(store),
    )
    assert out.get("section"), out.get("error")
    assert out["section"]["anchor"] == "ex-cache42"


def test_in_document_links_are_per_file_scoped(tmp_path):
    """Two files may each define AND link their own '#foo'. A fragment inside a
    document is relative to that document (HTML/Markdown semantics), so this is
    not a duplicate and nothing is broken.

    This pins the scoping boundary: the index-wide lookup used by the bare
    get_section(section_id='#foo') API form must never take over link checking,
    where a current document exists. A repo whose archive rule copies headings
    verbatim into '*-retired-*.md' produces identical ids across files routinely.
    """
    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir()
    for fname in ("a.md", "b.md"):
        (src / fname).write_text(
            f"# {fname}\n\nsee [here](#shared-id)\n\n## Target {{#shared-id}}\n\nbody\n",
            encoding="utf-8",
        )
    index_local(path=str(src), name="anchorscope", use_ai_summaries=False,
                use_embeddings=False, storage_path=str(store))

    out = get_broken_links(repo="local/anchorscope", storage_path=str(store))
    assert out.get("broken_links", []) == [], out.get("broken_links")

    # The bare API form has no current document, so it refuses the ambiguity...
    assert not get_section(repo="local/anchorscope", section_id="#shared-id",
                           storage_path=str(store)).get("section")
    # ...while qualifying by document resolves each one.
    for fname in ("a.md", "b.md"):
        got = get_section(repo="local/anchorscope", section_id=f"{fname}#shared-id",
                          storage_path=str(store))
        assert got.get("section"), got.get("error")
        assert got["section"]["doc_path"] == fname


@pytest.fixture
def dupes(tmp_path):
    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir()
    (src / "d.md").write_text(
        "# Doc\n\n## First heading {#foo}\n\nFIRST\n\n## Second heading {#foo}\n\nSECOND\n",
        encoding="utf-8",
    )
    (src / "e.md").write_text("# E\n\n## Other {#bar}\n\nX\n", encoding="utf-8")
    (src / "f.md").write_text("# F\n\n## Another {#bar}\n\nY\n", encoding="utf-8")
    index_local(path=str(src), name="dupdisclose", use_ai_summaries=False,
                use_embeddings=False, storage_path=str(store))
    return str(store)


def test_duplicate_within_one_document_returns_every_match_with_its_body(dupes):
    """We are not a browser. A browser picks one because it scrolls to one place;
    a JSON API has no such constraint, so both bodies come back in ONE call --
    not a pointer to a second call. `section` stays the first only so an existing
    consumer sees the shape it always saw."""
    out = get_section(repo="local/dupdisclose", section_id="d.md#foo",
                      storage_path=dupes)
    assert out["anchor_ambiguous"] is True
    assert out["anchor_scope"] == "document"
    assert out["anchor_match_count"] == 2
    assert [s["title"] for s in out["sections"]] == ["First heading", "Second heading"]
    # Both carry real content, so no follow-up call is needed.
    assert "FIRST" in out["sections"][0]["content"]
    assert "SECOND" in out["sections"][1]["content"]
    # Document order is author-controlled and meaningful, so first is a real
    # choice rather than an arbitrary one -- wire compatibility only.
    assert out["section"]["title"] == "First heading"


def test_duplicate_across_documents_returns_both_and_names_no_primary(dupes):
    """Across documents there is no ordering, so naming one "the" result would be
    arbitrary -- and an arbitrary pick read by a caller that skips the warning is
    the failure this design exists to refuse. Both are returned; neither is
    promoted to `section`."""
    out = get_section(repo="local/dupdisclose", section_id="#bar",
                      storage_path=dupes)
    assert not out.get("section")
    assert out["anchor_ambiguous"] is True
    assert out["anchor_scope"] == "index"
    assert out["anchor_match_count"] == 2
    assert sorted(s["doc_path"] for s in out["sections"]) == ["e.md", "f.md"]
    by_doc = {s["doc_path"]: s["content"] for s in out["sections"]}
    assert "X" in by_doc["e.md"] and "Y" in by_doc["f.md"]
    assert "Qualify the reference" in out["warning"]


def test_qualifying_an_ambiguous_reference_resolves_it_cleanly(dupes):
    out = get_section(repo="local/dupdisclose", section_id="e.md#bar",
                      storage_path=dupes)
    assert out["section"]["title"] == "Other"
    # No warning keys on the unambiguous path: an existing consumer sees the
    # response it always saw.
    assert "anchor_ambiguous" not in out
    assert "sections" not in out
    assert "warning" not in out


def test_unambiguous_anchor_response_is_unchanged(indexed):
    out = get_section(repo=REPO, section_id="docs/guides/exporting.md#ex-cache42",
                      storage_path=indexed)
    assert out.get("section")
    assert "anchor_ambiguous" not in out


# --------------------------------------------------------------------------
# Index-time disclosure
# --------------------------------------------------------------------------


def test_index_local_reports_duplicate_anchors(tmp_path):
    """Index time is when the author can still fix it cheaply. Finding out later,
    via a retrieval that returns two sections, is honest but arrives too late."""
    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir()
    (src / "d.md").write_text(
        "# Doc\n\n## First {#foo}\n\nA\n\n## Second {#foo}\n\nB\n", encoding="utf-8"
    )
    out = index_local(path=str(src), name="dupindex", use_ai_summaries=False,
                      use_embeddings=False, storage_path=str(store))
    dups = out["duplicate_anchors"]
    assert len(dups) == 1
    assert dups[0]["doc_path"] == "d.md"
    assert dups[0]["anchor"] == "foo"
    assert dups[0]["count"] == 2
    assert dups[0]["titles"] == ["First", "Second"]
    # Both ids are named, and both actually resolve.
    for sid in dups[0]["section_ids"]:
        assert get_section(repo="local/dupindex", section_id=sid,
                           storage_path=str(store)).get("section")


def test_index_local_reports_no_duplicates_when_there_are_none(tmp_path):
    """The key is present on a clean run. A signal that appears only when it fires is
    indistinguishable from one that was never computed."""
    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir()
    (src / "d.md").write_text(
        "# Doc\n\n## First {#foo}\n\nA\n\n## Second {#bar}\n\nB\n", encoding="utf-8"
    )
    out = index_local(path=str(src), name="nodupindex", use_ai_summaries=False,
                      use_embeddings=False, storage_path=str(store))
    assert out["duplicate_anchors"] == []


def test_same_anchor_in_two_documents_is_not_a_duplicate(tmp_path):
    """A fragment is relative to its own document, so the same id in two files is
    ordinary. Flagging it would be a signal that always fires in a repo whose
    archive rule copies headings verbatim."""
    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir()
    (src / "a.md").write_text("# A\n\n## X {#shared}\n\nA\n", encoding="utf-8")
    (src / "b.md").write_text("# B\n\n## Y {#shared}\n\nB\n", encoding="utf-8")
    out = index_local(path=str(src), name="crossdocindex", use_ai_summaries=False,
                      use_embeddings=False, storage_path=str(store))
    assert out["duplicate_anchors"] == []


def test_index_emits_a_human_readable_warning_line(tmp_path):
    """A list of dicts is something a caller must decide to inspect; a sentence
    is something a model relays and a person reads. Both ship."""
    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir()
    (src / "d.md").write_text(
        "# D\n\n## A {#foo}\n\n1\n\n## B {#foo}\n\n2\n", encoding="utf-8"
    )
    out = index_local(path=str(src), name="warnline", use_ai_summaries=False,
                      use_embeddings=False, storage_path=str(store))
    warning = out["duplicate_anchor_warning"]
    assert "foo" in warning and "d.md" in warning
    assert "{#id}" in warning  # says what to DO, not just that something is wrong


def test_warning_line_is_empty_when_clean(tmp_path):
    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir()
    (src / "d.md").write_text("# D\n\n## A {#foo}\n\n1\n", encoding="utf-8")
    out = index_local(path=str(src), name="warnclean", use_ai_summaries=False,
                      use_embeddings=False, storage_path=str(store))
    assert out["duplicate_anchor_warning"] == ""
    assert out["duplicate_anchors"] == []
