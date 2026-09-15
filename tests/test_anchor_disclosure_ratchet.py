"""Every tool that takes a section id tells the truth about an ambiguous anchor.

USER_GUIDE promises: "Any tool that takes a `section_id` or `section_ids` also
accepts a `doc.md#anchor` reference ... If the reference is ambiguous the
response says so". ``test_anchor_disclosure_siblings.py`` pins the tools that
existed when the disclosure was written, by NAME. A tool added later that takes
a section id and resolves it silently would pass every test in that file.

So this guard derives the set instead of listing it:

* the tools are every entry of ``server._all_tools()`` whose ``inputSchema``
  declares a top-level ``section_id`` or ``section_ids``;
* each is called through ``server.call_tool`` -- the production dispatch -- so
  the name-to-function mapping is the server's own, and a tool whose schema
  claims the parameter but whose dispatch drops it fails here too;
* the check is behavioural: an ambiguous reference must come back flagged, with
  its candidates, and a stored id or an unambiguous reference must not.

A tool that genuinely cannot disclose goes in ``EXEMPT`` with a reason. It is
empty on purpose: add a tool WITH its reason, never loosen the rule.

⚠ Not covered by this set, deliberately: ``get_doc_pr_risk_profile`` takes
``changed_sections`` (ids computed from a diff, so always stored ids) and
``finalize_handoff`` takes ``evidence_refs``. Neither declares a top-level
``section_id`` / ``section_ids``, which is the shape USER_GUIDE promises.
"""

import json

import pytest

from jdocmunch_mcp import server
from jdocmunch_mcp.tools.get_sections import get_sections
from jdocmunch_mcp.tools.index_local import index_local

REPO = "local/ratchet"
ID_PARAMS = ("section_id", "section_ids")
SCHEMA_PHRASE = "`doc.md#anchor`"

# tool name -> reason it cannot disclose. Empty on purpose.
EXEMPT: dict = {}

# tool name -> extra arguments its schema requires beyond `repo` and the id.
# Every selected tool today needs nothing else; a NEW one that does is filled
# generically from its schema's `required` list (see _fill), and only a value
# that generic fill cannot guess belongs here.
ARG_SHIMS: dict = {}


def _selected():
    """{tool name: (Tool, the id parameter name)} derived from the schema."""
    out = {}
    for tool in server._all_tools():
        props = (tool.inputSchema or {}).get("properties", {})
        for param in ID_PARAMS:
            if param in props:
                out[tool.name] = (tool, param)
    return out


SELECTED = _selected()
TOOL_NAMES = sorted(n for n in SELECTED if n not in EXEMPT)


def _fill(tool, args):
    """Add a minimal valid value for every other required argument."""
    props = tool.inputSchema.get("properties", {})
    for name in tool.inputSchema.get("required", []):
        if name in args:
            continue
        spec = props.get(name, {})
        if "enum" in spec:
            args[name] = spec["enum"][0]
        elif spec.get("type") == "integer":
            args[name] = 1
        elif spec.get("type") == "number":
            args[name] = 1.0
        elif spec.get("type") == "boolean":
            args[name] = False
        elif spec.get("type") == "array":
            args[name] = []
        elif spec.get("type") == "object":
            args[name] = {}
        else:
            args[name] = "x"
    return args


async def _call(name, ref):
    tool, param = SELECTED[name]
    arguments = {"repo": REPO, param: [ref] if param == "section_ids" else ref}
    arguments.update(ARG_SHIMS.get(name, {}))
    result = await server.call_tool(name, _fill(tool, arguments))
    assert len(result) == 1
    return json.loads(result[0].text)


def _carrier(name, out):
    """The dict that carries the disclosure: the per-id entry for a batch tool."""
    if SELECTED[name][1] == "section_ids":
        assert out.get("sections"), out
        return out["sections"][0]
    return out


def _candidates(carrier):
    # get_section predates the shared helper and lists every match, bodies
    # included, under `sections`; the rest list {id, doc_path, title} under
    # `anchor_candidates`.
    return carrier.get("anchor_candidates") or carrier.get("sections") or []


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    root = tmp_path_factory.mktemp("ratchet")
    src, st = root / "src", root / "store"
    src.mkdir()
    (src / "d.md").write_text(
        "# Doc\n\n## First {#foo}\n\nFIRST\n\n## Second {#foo}\n\nSECOND\n\n"
        "## Solo {#solo}\n\nSOLO\n",
        encoding="utf-8",
    )
    (src / "e.md").write_text("# E\n\n## Other {#bar}\n\nX\n", encoding="utf-8")
    (src / "f.md").write_text("# F\n\n## Another {#bar}\n\nY\n", encoding="utf-8")
    out = index_local(path=str(src), name="ratchet", use_ai_summaries=False,
                      use_embeddings=False, storage_path=str(st))
    assert out["success"], out
    return str(st)


@pytest.fixture
def served(store, monkeypatch):
    monkeypatch.setenv("DOC_INDEX_PATH", store)
    return store


# --------------------------------------------------------------------------
# The set is derived, and the derivation selects what it should
# --------------------------------------------------------------------------


def test_the_derivation_selects_id_tools_and_only_id_tools():
    assert {"get_section", "get_sections", "check_section_delete_safe"} <= set(SELECTED)
    # Tools that take a repo, a query or a document path are not section-id tools.
    for name in ("search_sections", "get_toc", "get_document_outline",
                 "doc_list_repos", "index_local", "get_doc_pr_risk_profile"):
        assert name not in SELECTED, name
    # And the rule is the schema's, not a name pattern.
    for tool in server._all_tools():
        has = any(p in (tool.inputSchema or {}).get("properties", {}) for p in ID_PARAMS)
        assert (tool.name in SELECTED) is has, tool.name


def test_every_exemption_names_a_real_tool_and_a_reason():
    for name, reason in EXEMPT.items():
        assert name in SELECTED, f"{name} is exempt but takes no section id"
        assert reason.strip(), f"{name} is exempt with no reason"


# --------------------------------------------------------------------------
# The schema makes the promise
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_schema_description_offers_the_anchor_reference(name):
    tool, param = SELECTED[name]
    desc = tool.inputSchema["properties"][param].get("description", "")
    assert SCHEMA_PHRASE in desc, f"{name}.{param} does not mention {SCHEMA_PHRASE}"
    assert "anchor_ambiguous" in desc, f"{name}.{param} does not say how ambiguity shows"


# --------------------------------------------------------------------------
# The behaviour keeps it
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", TOOL_NAMES)
async def test_within_document_duplicate_is_disclosed(name, served):
    out = await _call(name, "d.md#foo")
    carrier = _carrier(name, out)
    assert carrier.get("anchor_ambiguous") is True, (name, carrier)
    assert carrier.get("anchor_scope") == "document", (name, carrier)
    assert carrier.get("anchor_match_count") == 2, (name, carrier)
    assert len(_candidates(carrier)) == 2, (name, carrier)
    assert carrier.get("warning") or out.get("warning"), (name, out)
    if carrier is not out:
        assert out.get("anchor_ambiguous") is True, f"{name}: no top-level flag"
    # A refusing tool says so in `error`; a serving one must not report a miss.
    if "error" in carrier:
        assert "ambiguous" in carrier["error"].lower(), (name, carrier["error"])


@pytest.mark.parametrize("name", TOOL_NAMES)
async def test_cross_document_ambiguity_is_disclosed_not_a_plain_miss(name, served):
    out = await _call(name, "#bar")
    carrier = _carrier(name, out)
    assert carrier.get("anchor_ambiguous") is True, (name, carrier)
    assert carrier.get("anchor_scope") == "index", (name, carrier)
    assert carrier.get("anchor_match_count") == 2, (name, carrier)
    cands = _candidates(carrier)
    assert sorted(c["doc_path"] for c in cands) == ["e.md", "f.md"], (name, carrier)
    if "error" in carrier:
        assert "ambiguous" in carrier["error"].lower(), (name, carrier["error"])
        assert "not found" not in carrier["error"].lower(), (name, carrier["error"])
    if carrier is not out:
        assert out.get("anchor_ambiguous") is True, f"{name}: no top-level flag"


@pytest.mark.parametrize("name", TOOL_NAMES)
async def test_unambiguous_references_and_stored_ids_add_no_disclosure(name, served):
    """The positive control: a guard that flags everything proves nothing."""
    stored = get_sections(
        repo=REPO, section_ids=["d.md#solo"], storage_path=served
    )["sections"][0]["section"]["id"]
    for ref in ("d.md#solo", "e.md#bar", stored):
        out = await _call(name, ref)
        carrier = _carrier(name, out)
        assert not any(k.startswith("anchor_") for k in carrier), (name, ref, carrier)
        assert not any(k.startswith("anchor_") for k in out), (name, ref, out)
        assert "ambiguous" not in json.dumps(out).lower(), (name, ref, out)
