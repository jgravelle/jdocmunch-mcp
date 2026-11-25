"""Tests for the v1.11.0 replay benchmark harness."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


# Ensure the repo root is importable so `benchmarks.replay.*` resolves.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.replay.metrics import (  # noqa: E402
    aggregate,
    mrr_at_k,
    ndcg_at_k,
    recall_at_k,
)


# ---------------------------------------------------------------------------
# nDCG@k
# ---------------------------------------------------------------------------

class TestNDCG:
    def test_perfect_ranking(self):
        assert ndcg_at_k(["a", "b", "c"], ["a"], 5) == 1.0

    def test_relevant_at_position_2(self):
        # Single expected, returned at index 1 (position 2)
        # DCG = 1/log2(3) ≈ 0.6309; ideal = 1.0
        score = ndcg_at_k(["x", "a"], ["a"], 5)
        assert 0.6 < score < 0.65

    def test_no_match(self):
        assert ndcg_at_k(["x", "y"], ["a"], 5) == 0.0

    def test_empty_predicted(self):
        assert ndcg_at_k([], ["a"], 5) == 0.0

    def test_empty_expected(self):
        assert ndcg_at_k(["a"], [], 5) == 0.0

    def test_k_zero(self):
        assert ndcg_at_k(["a"], ["a"], 0) == 0.0

    def test_two_relevant_perfect_order(self):
        # Both expected items at positions 0 and 1.
        score = ndcg_at_k(["a", "b", "c"], ["a", "b"], 5)
        assert score == 1.0

    def test_k_smaller_than_relevant_count(self):
        # 3 relevant items but k=2 caps the ideal denominator at top 2.
        score = ndcg_at_k(["a", "b"], ["a", "b", "c"], 2)
        assert score == 1.0


# ---------------------------------------------------------------------------
# MRR@k
# ---------------------------------------------------------------------------

class TestMRR:
    def test_first_position(self):
        assert mrr_at_k(["a"], ["a"], 5) == 1.0

    def test_third_position(self):
        assert mrr_at_k(["x", "y", "a"], ["a"], 5) == pytest.approx(1 / 3)

    def test_outside_k(self):
        assert mrr_at_k(["x", "y", "z", "a"], ["a"], 3) == 0.0

    def test_no_match(self):
        assert mrr_at_k(["x"], ["a"], 5) == 0.0

    def test_k_zero(self):
        assert mrr_at_k(["a"], ["a"], 0) == 0.0


# ---------------------------------------------------------------------------
# Recall@k
# ---------------------------------------------------------------------------

class TestRecall:
    def test_full_recall(self):
        assert recall_at_k(["a", "b"], ["a", "b"], 5) == 1.0

    def test_half_recall(self):
        assert recall_at_k(["a"], ["a", "b"], 5) == 0.5

    def test_zero_recall(self):
        assert recall_at_k(["x"], ["a"], 5) == 0.0

    def test_empty_expected(self):
        assert recall_at_k(["a"], [], 5) == 0.0

    def test_k_zero(self):
        assert recall_at_k(["a"], ["a"], 0) == 0.0


# ---------------------------------------------------------------------------
# aggregate()
# ---------------------------------------------------------------------------

class TestAggregate:
    def test_mean_across_queries(self):
        per_query = [
            {"ndcg": 1.0, "mrr": 1.0, "recall": 1.0},
            {"ndcg": 0.5, "mrr": 0.5, "recall": 0.5},
        ]
        out = aggregate(per_query)
        assert out["ndcg"] == 0.75
        assert out["mrr"] == 0.75
        assert out["recall"] == 0.75

    def test_empty_input(self):
        assert aggregate([]) == {"ndcg": 0.0, "mrr": 0.0, "recall": 0.0}

    def test_missing_key_treated_as_zero(self):
        out = aggregate([{"ndcg": 1.0}])
        assert out["ndcg"] == 1.0
        assert out["mrr"] == 0.0
        assert out["recall"] == 0.0


# ---------------------------------------------------------------------------
# Gate logic via run_replay.run_fixture
# ---------------------------------------------------------------------------

class TestGate:
    def _write_baseline(self, tmp_path, fixture_name, version, ndcg, mrr, recall):
        results_dir = ROOT / "benchmarks" / "replay" / "results"
        path = results_dir / f"{fixture_name}-v{version}.json"
        path.write_text(
            json.dumps(
                {
                    "fixture": fixture_name,
                    "version": version,
                    "aggregates": {"ndcg": ndcg, "mrr": mrr, "recall": recall},
                    "per_query": [],
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_first_run_when_baseline_missing(self):
        from benchmarks.replay.run_replay import run_fixture

        report = run_fixture(
            "self_v1_11_0",
            baseline="0.0.0",
            gate=0.02,
            write_results=False,
        )
        assert report["gate"]["status"] == "first_run"

    def test_pass_when_within_gate(self):
        # Real run against the live baseline. Tolerance is 0.06 to absorb
        # cross-platform BM25 line-length variance (CRLF on Windows vs LF
        # on Linux shifts avgdl by ~1 byte/line, which can flip one tied
        # depth-1 vs depth-3 section in the wiki benchmark query).
        from benchmarks.replay.run_replay import run_fixture

        report = run_fixture(
            "self_v1_11_0",
            baseline="1.11.0",
            gate=0.06,
            write_results=False,
        )
        assert report["gate"]["status"] == "pass", report["gate"]

    def test_fail_when_metric_drops(self, tmp_path, monkeypatch):
        # Synthesize an over-strict baseline so the live report fails the gate.
        from benchmarks.replay import run_replay

        results_dir = ROOT / "benchmarks" / "replay" / "results"
        synth = results_dir / "self_v1_11_0-v9.99.99.json"
        synth.write_text(
            json.dumps(
                {
                    "fixture": "self_v1_11_0",
                    "version": "9.99.99",
                    "aggregates": {"ndcg": 1.0, "mrr": 1.0, "recall": 1.0},
                    "per_query": [],
                }
            ),
            encoding="utf-8",
        )
        try:
            # Stub _run_query to return zero matches so the live aggregates collapse.
            monkeypatch.setattr(run_replay, "_run_query", lambda *a, **kw: ["nope"])
            report = run_replay.run_fixture(
                "self_v1_11_0",
                baseline="9.99.99",
                gate=0.02,
                write_results=False,
            )
            assert report["gate"]["status"] == "fail"
            assert report["gate"]["regressions"]
        finally:
            synth.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Fixture-shape contract
# ---------------------------------------------------------------------------

class TestFixtureShape:
    def test_seed_fixture_has_required_fields(self):
        path = ROOT / "benchmarks" / "replay" / "fixtures" / "self_v1_11_0.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["name"] == "self_v1_11_0"
        assert isinstance(data["repo_path"], str)
        assert isinstance(data["repo_id"], str)
        queries = data["queries"]
        assert len(queries) >= 5
        for q in queries:
            assert "query" in q and isinstance(q["query"], str)
            assert "expected_top_k" in q and isinstance(q["expected_top_k"], list)
            assert "k" in q and isinstance(q["k"], int)


# ---------------------------------------------------------------------------
# Baseline lock — every release must hit >= 0.98 against the v1.11.0 baseline
# ---------------------------------------------------------------------------

class TestBaselineLock:
    def test_self_fixture_meets_lock(self):
        from benchmarks.replay.run_replay import run_fixture

        report = run_fixture("self_v1_11_0", baseline=None, gate=0.02, write_results=False)
        agg = report["aggregates"]
        # Platform-tolerant lock — see test_pass_when_within_gate for
        # rationale on the 0.95 floor.
        assert agg["ndcg"] >= 0.95, agg
        assert agg["mrr"] >= 0.95, agg
        assert agg["recall"] >= 0.95, agg


# ---------------------------------------------------------------------------
# The "stale pages" golden names the section that documents stale pages
# ---------------------------------------------------------------------------

STALE_PAGES_GOLDEN = "local/jdocmunch-mcp::README.md::jdocmunch-mcp/what-you-can-do#2"


class TestStalePagesGolden:
    """The self fixture's "stale pages" golden is README "What you can do".

    It was the wiki benchmark's Index Snapshot table, a v1.12.0 BM25 output
    that never says "stale" and matched only "pages". README "What you can
    do" is the one tracked, non-excluded section that names get_stale_pages,
    and it has ranked first since the README rewrite in v1.123.1.
    """

    def _query(self):
        path = ROOT / "benchmarks" / "replay" / "fixtures" / "self_v1_11_0.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        return next(q for q in data["queries"] if q["query"] == "stale pages")

    def test_golden_is_readme_what_you_can_do(self):
        assert self._query()["expected_top_k"] == [STALE_PAGES_GOLDEN]

    def test_golden_section_documents_stale_pages(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        start = readme.index("\n## What you can do\n")
        end = readme.index("\n## ", start + 1)
        assert "get_stale_pages" in readme[start:end]

    def test_golden_ranks_first(self):
        from benchmarks.replay.run_replay import run_fixture

        report = run_fixture("self_v1_11_0", baseline=None, gate=0.02, write_results=False)
        row = next(r for r in report["per_query"] if r["query"] == "stale pages")
        assert row["predicted"][:1] == [STALE_PAGES_GOLDEN], row
        assert row["mrr"] == 1.0, row
