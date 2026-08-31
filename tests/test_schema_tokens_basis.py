"""The published schema-token counts carry their basis, and there is one weigher.

Ported from jcodemunch-mcp v1.108.312 (`tests/test_tier_switch_cost.py`). Only
the basis half transferred: jcm's release also refuses a mid-session tool-tier
switch that cannot repay the prompt cache it invalidates, and **that defect
cannot occur here** - `JDOCMUNCH_TOOL_PROFILE` is read at startup and there is
no runtime switch, so there is no invalidation to price.
`test_no_runtime_tier_switch_without_pricing` is the ratchet that keeps that
true, rather than a port of the gate.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from jdocmunch_mcp import server as server_mod

SRC = Path(server_mod.__file__).parent
REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# 1. The published counts carry their basis
# --------------------------------------------------------------------------- #

def test_every_schema_token_figure_ships_with_its_basis(monkeypatch):
    """A bare `schema_tokens_avoided` has no TIME basis, and a reader supplies
    the wrong one: per request. The tool-schema block is stable, so it is paid
    at full rate roughly once per cache lifetime and at cache-read rates
    thereafter - jcodemunch measured 86% of baseline input cached
    (`benchmarks/codex_surface/README.md`).

    Asserted as CO-PRESENCE, not as an exact string: any consumer reading a
    count also receives the basis. The wording is allowed to improve.
    """
    from jdocmunch_mcp.schema_basis import SCHEMA_TOKENS_BASIS

    monkeypatch.delenv("JDOCMUNCH_TOOL_PROFILE", raising=False)
    stats = server_mod._tool_surface_stats(top_n=3)
    counts = [k for k in stats if k.startswith("schema_tokens_") and "basis" not in k]
    assert counts, "no schema token figures found - this test asserts nothing"
    assert stats["schema_tokens_basis"] == SCHEMA_TOKENS_BASIS
    note = stats["schema_tokens_basis_note"].lower()
    assert "cache" in note
    assert "not a per-request saving" in note


def test_the_count_is_not_silently_discounted(monkeypatch):
    """The fix is a LABEL, never a scaled number. A count quietly multiplied by
    the cache-read rate answers neither the payload question nor the cost
    question, and nothing on the wire would show it had happened."""
    monkeypatch.delenv("JDOCMUNCH_TOOL_PROFILE", raising=False)
    monkeypatch.delenv("JDOCMUNCH_DISABLED_TOOLS", raising=False)
    stats = server_mod._tool_surface_stats(top_n=3)
    catalog = sum(server_mod._schema_weight(t) for t in server_mod._all_tools())
    visible = sum(server_mod._schema_weight(t) for t in server_mod._build_tools_list())
    assert stats["schema_tokens_catalog"] == catalog
    assert stats["schema_tokens_visible"] == visible
    assert stats["schema_tokens_avoided"] == max(0, catalog - visible)


def test_the_basis_constants_live_in_exactly_one_module():
    """jcm deliberately did not inline the strings at the call site: a second
    copy that agrees today is what makes a later divergence invisible."""
    hits = sorted(
        p.name for p in SRC.rglob("*.py")
        if "SCHEMA_TOKENS_BASIS =" in p.read_text(encoding="utf-8")
    )
    assert hits == ["schema_basis.py"], f"basis constant defined in {hits}"


# --------------------------------------------------------------------------- #
# 2. There is ONE schema weigher
# --------------------------------------------------------------------------- #

def _weigher_defs() -> list[str]:
    """Every function under src/ + benchmarks/ that turns a Tool into a count."""
    found: list[str] = []
    for root in (SRC, REPO / "benchmarks"):
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "inputSchema" not in text:
                continue
            try:
                tree = ast.parse(text)
            except SyntaxError:  # pragma: no cover - not our files
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                body = ast.get_source_segment(text, node) or ""
                if "inputSchema" in body and "// 4" in body:
                    found.append(f"{path.name}:{node.name}")
    return sorted(found)


def test_there_is_exactly_one_schema_weigher():
    """Two weighers that agree today are what make a later divergence
    invisible. The estimator sets a published number's scale, so a second copy
    is a second answer to the same question."""
    assert _weigher_defs() == ["server.py:_schema_weight"], _weigher_defs()


def test_the_benchmark_reads_the_published_surface_not_a_local_filter():
    """jcm's first attempt filtered the raw catalog by the tier bundle and was
    wrong by THREE TOOLS IN EVERY TIER - it priced a surface no client
    receives. The benchmark must route through the builder `list_tools` uses,
    and must not weigh tools with an estimator of its own."""
    bench = REPO / "benchmarks" / "tool_surface" / "measure_tiers.py"
    assert bench.exists(), "the tier-weight benchmark is not committed"
    text = bench.read_text(encoding="utf-8")
    assert "_build_tools_list" in text, "benchmark does not use the published builder"
    assert "_schema_weight" in text, "benchmark does not use the published weigher"


def test_list_tools_and_the_meter_share_one_builder():
    """The surface the meter prices is the surface the client is sent."""
    src = inspect.getsource(server_mod)
    handler = src.split("async def list_tools", 1)[1][:400]
    assert "_build_tools_list()" in handler, "list_tools builds its own list"


# --------------------------------------------------------------------------- #
# 3. The control: the tiers are distinct and ordered
# --------------------------------------------------------------------------- #

def test_tier_weights_are_distinct_and_ordered(monkeypatch):
    """THE CONTROL. Without it every assertion above is satisfied by a weigher
    that returns the same number for every tier."""
    monkeypatch.delenv("JDOCMUNCH_DISABLED_TOOLS", raising=False)
    weights = {
        p: sum(
            server_mod._schema_weight(t)
            for t in server_mod._build_tools_list(profile_override=p)
        )
        for p in ("core", "standard", "full")
    }
    assert weights["core"] < weights["standard"] < weights["full"], weights


def test_profile_override_does_not_leak_into_the_environment(monkeypatch):
    """Pricing a tier must not switch to it - the meter answers a question
    about a surface, it does not mutate the session's."""
    monkeypatch.setenv("JDOCMUNCH_TOOL_PROFILE", "full")
    server_mod._build_tools_list(profile_override="core")
    assert server_mod._get_tool_profile() == "full"


# --------------------------------------------------------------------------- #
# 4. The ratchet: no runtime tier switch arrives unpriced
# --------------------------------------------------------------------------- #

_SWITCH_MARKERS = (
    "send_tool_list_changed",
    "notifications/tools/list_changed",
    "tools_list_changed",
)


def test_no_runtime_tier_switch_without_pricing():
    """Verified 2026-08-30: jdoc has NO runtime tool-tier switch, so jcm's
    breakeven gate is machinery for a mechanism we do not have. The day someone
    adds one, the cached prefix - the schema block AND every turn behind it -
    starts being invalidated mid-session, and jcm's 1.108.311 defect arrives
    here. This fails then, not a year later.

    Proven non-vacuous by temporarily adding `send_tool_list_changed` to
    server.py and watching it fire.
    """
    offenders = [
        path.name for path in SRC.rglob("*.py")
        if any(m in path.read_text(encoding="utf-8") for m in _SWITCH_MARKERS)
    ]
    if not offenders:
        return
    priced = any(
        "breakeven_requests" in p.read_text(encoding="utf-8")
        for p in SRC.rglob("*.py")
    )
    assert priced, (
        f"{offenders} notify a tool-list change with no pricing helper in src/. "
        "A mid-session tool-list change invalidates the whole cached prefix; "
        "port jcodemunch-mcp's tier_switch_cost.breakeven_requests first."
    )
