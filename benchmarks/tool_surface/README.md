# Tool-surface tier weights

What each `JDOCMUNCH_TOOL_PROFILE` tier costs on the wire. Measured
2026-08-30; regenerate with:

```bash
PYTHONPATH=src python benchmarks/tool_surface/measure_tiers.py
```

Numbers live in `tier_weights.json`. **Quote the artifact, never a figure typed
into prose** — the two diverge silently, and this file is prose.

## Estimator

`bytes/4` over each tool's `{name, description, inputSchema}` serialized with
compact separators. It is the same scale the published `tool_surface` receipt
reports (`get_session_stats`), because the script imports the server's own
`_schema_weight` rather than defining a second one.

⚠⚠ **The tier lists are read live from `server._build_tools_list`, the function
`list_tools` itself returns.** jcodemunch's first tier measurement filtered the
raw catalog by the tier bundle instead, and was wrong by three tools in every
tier: it kept a hidden tool set and dropped force-included ones, pricing a
surface no client is ever sent. `_ALWAYS_PRESENT_TOOLS` (`jdocmunch_guide`) and
`JDOCMUNCH_DISABLED_TOOLS` both change what arrives, and only the builder knows
about them.

## What the tiers do (2026-08-30, 64 tools / 13,252 tokens at `full`)

| profile | tools | schema tokens | vs `full` |
|---------|------:|--------------:|----------:|
| `core` | 15 | 5,025 | **−62.08%** (49 tools dropped) |
| `standard` | 56 | 12,008 | **−9.39%** (8 tools dropped) |
| `full` | 64 | 13,252 | — |

**`core` is a real lever. `standard` is close to none.** It drops eight tools —
`analyze_perf`, `check_embedding_drift`, `find_endpoint`,
`find_operations_using_schema`, `get_schema_graph`, `get_session_stats`,
`list_endpoints_by_tag`, `tune_weights` — for 9.39% of the payload, and gives
up the whole OpenAPI query surface to do it. jcodemunch measured its own
`standard` at 9 of 91 tools and 6.7%; the shape is the same in both servers.

⚠ **`standard` stays, documented rather than deleted.** Removing it would break
an existing `JDOCMUNCH_TOOL_PROFILE=standard` config, which the 1.x contract
forbids, and the tier is a coherent choice for a caller that wants the doc tools
without the OpenAPI and telemetry surfaces. What it is not is a token lever: a
setting that implies a saving it does not deliver is the same defect class as an
unstated basis. **If you are choosing a tier to shrink the payload, choose
`core`.**

## The basis these counts carry

⚠⚠ **They are payload size, not a per-request saving.** The tool-schema block
is stable across requests, so it is paid at full rate roughly once per cache
lifetime and at cache-read rates (~0.1x) thereafter — jcodemunch-mcp's
`benchmarks/codex_surface/` measured 86% of baseline input cached. Reading
`schema_tokens_avoided` as "N tokens in every request" overstates the cost
impact by roughly an order of magnitude. The artifact stamps
`schema_tokens_basis` for exactly this reason; so does the shipped receipt. See
`src/jdocmunch_mcp/schema_basis.py`.
