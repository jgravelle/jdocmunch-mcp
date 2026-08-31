"""The time basis stamped on every published schema-token figure.

A bare count of "tokens avoided" carries no time basis, and a reader supplies
the wrong one: PER REQUEST. The tool-schema block is serialised ahead of system
and messages and is stable across requests, so it is paid at full rate roughly
ONCE per cache lifetime and at cache-read rates (~0.1x) thereafter.
jcodemunch-mcp measured **86% of baseline input cached** (1,938,176 of 2,247,575
tokens, `benchmarks/codex_surface/README.md`) and says in its own words that any
framing of "N tokens in every request" is wrong -- *and that the repository said
exactly that before measuring*. Read per-request, the count overstates the cost
impact by roughly an order of magnitude, in the direction that flatters us.

⚠ The COUNT is not discounted, deliberately. It answers a real question -- how
much payload the surface carries -- and a silently scaled one answers neither
that nor the cost question. Same rule as `analyze_perf`'s raw `hit_rate`, kept
beside `hit_rate_basis` rather than replaced.

⚠⚠ These constants live here and are IMPORTED, never inlined at the call site.
A second copy that agrees today is what makes a later divergence invisible.

⚠ This module carries no cache-price arithmetic on purpose. jcodemunch-mcp
1.108.311 gates a mid-session tool-tier switch on whether it can repay the
prompt cache it invalidates; **that defect cannot occur here** --
`JDOCMUNCH_TOOL_PROFILE` is read at startup and there is no runtime switch, so
there is no invalidation to price. Porting the gate would be machinery for a
mechanism we do not have. `tests/test_schema_tokens_basis.py` ratchets the
absence instead, and names the module to port from if it ever changes.
"""
from __future__ import annotations

SCHEMA_TOKENS_BASIS = "one_time_at_full_rate_then_cache_read"

SCHEMA_TOKENS_BASIS_NOTE = (
    "The tool-schema block is stable across requests, so it is paid at full "
    "rate approximately once per cache lifetime and at cache-read rates "
    "(~0.1x) thereafter. This count is payload size, NOT a per-request saving; "
    "reading it as one overstates the cost impact by roughly an order of "
    "magnitude. Measured: jcodemunch-mcp benchmarks/codex_surface/ (86% of "
    "baseline input cached)."
)
