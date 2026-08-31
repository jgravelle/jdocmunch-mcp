"""Measure what each JDOCMUNCH_TOOL_PROFILE tier actually costs on the wire.

Regenerate:

    PYTHONPATH=src python benchmarks/tool_surface/measure_tiers.py

Writes `tier_weights.json` beside this file. Never hand-type a number from it
into prose - quote the artifact, or regenerate.

⚠⚠ Weights are read LIVE from `server._build_tools_list`, the same function
`list_tools` returns, and weighed with `server._schema_weight`, the same
estimator the published `tool_surface` receipt uses. jcodemunch's first tier
measurement filtered the raw catalog by the tier bundle instead and was wrong
by three tools in every tier: it priced a surface no client is ever sent. This
script therefore cannot drift from the surface it measures, and it deliberately
defines no weigher and no tier filter of its own.

⚠ The counts are payload size. They are NOT a per-request saving - see
`jdocmunch_mcp.schema_basis` for the time basis that belongs to every one of
these numbers.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from jdocmunch_mcp.schema_basis import SCHEMA_TOKENS_BASIS, SCHEMA_TOKENS_BASIS_NOTE
from jdocmunch_mcp.server import _PROFILE_TIERS, _build_tools_list, _schema_weight

OUT = Path(__file__).with_name("tier_weights.json")


def measure() -> dict:
    # ⚠ A disabled_tools list in the ambient environment would price a surface
    # specific to this box, not the tier.
    os.environ.pop("JDOCMUNCH_DISABLED_TOOLS", None)

    tiers: dict[str, dict] = {}
    for profile in _PROFILE_TIERS:
        tools = _build_tools_list(profile_override=profile)
        weights = {t.name: _schema_weight(t) for t in tools}
        tiers[profile] = {
            "tools": len(tools),
            "schema_tokens": sum(weights.values()),
            "tool_names": sorted(weights),
        }

    full = tiers["full"]
    for profile, row in tiers.items():
        dropped = set(full["tool_names"]) - set(row["tool_names"])
        row["tools_dropped_vs_full"] = len(dropped)
        row["dropped_tool_names"] = sorted(dropped)
        row["schema_tokens_avoided_vs_full"] = full["schema_tokens"] - row["schema_tokens"]
        row["payload_reduction_vs_full_pct"] = (
            round(100.0 * row["schema_tokens_avoided_vs_full"] / full["schema_tokens"], 2)
            if full["schema_tokens"]
            else 0.0
        )

    return {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "estimator": "bytes/4 over {name, description, inputSchema}, compact separators",
        "source": "jdocmunch_mcp.server._build_tools_list + _schema_weight (live)",
        "schema_tokens_basis": SCHEMA_TOKENS_BASIS,
        "schema_tokens_basis_note": SCHEMA_TOKENS_BASIS_NOTE,
        "tiers": tiers,
    }


def main() -> None:
    data = measure()
    OUT.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    for profile, row in data["tiers"].items():
        print(
            f"{profile:9s} {row['tools']:3d} tools  "
            f"{row['schema_tokens']:6d} tokens  "
            f"-{row['payload_reduction_vs_full_pct']:5.2f}% vs full "
            f"({row['tools_dropped_vs_full']} tools dropped)"
        )
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
