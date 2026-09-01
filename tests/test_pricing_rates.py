"""Value pins for `token_tracker.PRICING`.

Only reference to this table before 2026-09-01 was a key-PRESENCE check
(`tests/test_storage.py`), so `claude_sonnet` carried $3.00 — a rate written
for a price increase scheduled on 2026-09-01 that Anthropic then cancelled.
Sonnet 5 has never been $3.00; $3.00 is the superseded Sonnet 4.6's rate.

⚠ The literals below are RESTATED from the source page, not imported from the
module. A pin that reads the value it checks asserts nothing.

Source, read 2026-09-01:
https://platform.claude.com/docs/en/about-claude/pricing — Model pricing table,
"Base Input Tokens" column. Claude Opus 5 $5/MTok, Claude Sonnet 5 $2/MTok
(the note there records that the scheduled increase to $3 "will not occur"),
Claude Haiku 4.5 $1/MTok.

⚠ `gpt5_latest` is NOT an Anthropic model and no source was consulted for it.
It is pinned at the value the table shipped with so a drift is visible, not
because $10.00 was verified.
"""

from jdocmunch_mcp.storage.token_tracker import PRICING, cost_avoided

EXPECTED_USD_PER_MTOK = {
    "claude_opus": 5.00,
    "claude_sonnet": 2.00,
    "claude_haiku": 1.00,
    "gpt5_latest": 10.00,  # unverified — see module docstring
}


def test_pricing_keys_are_exactly_the_four_published_ones():
    # The keys are emitted verbatim in every `cost_avoided` block, so adding or
    # renaming one is a wire change on 1.x, not an implementation detail.
    assert set(PRICING) == set(EXPECTED_USD_PER_MTOK)


def test_every_rate_matches_the_published_per_mtok_price():
    for key, usd_per_mtok in EXPECTED_USD_PER_MTOK.items():
        assert PRICING[key] == usd_per_mtok / 1_000_000, (
            f"{key} drifted from ${usd_per_mtok:.2f}/1M input tokens"
        )


def test_sonnet_is_not_the_superseded_4_6_rate():
    # The specific defect: Sonnet 4.6's $3.00 standing in for Sonnet 5's $2.00.
    assert PRICING["claude_sonnet"] != 3.00 / 1_000_000


def test_cost_avoided_values_follow_the_pinned_rates():
    # Guards the rendered numbers as well as the table, at the rounding the
    # tool responses actually publish.
    ca = cost_avoided(1_000_000, 2_000_000)
    for key, usd_per_mtok in EXPECTED_USD_PER_MTOK.items():
        assert ca["cost_avoided"][key] == round(usd_per_mtok, 4)
        assert ca["total_cost_avoided"][key] == round(usd_per_mtok * 2, 4)
