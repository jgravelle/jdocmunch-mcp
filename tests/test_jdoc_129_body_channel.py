"""jdoc#129 - find_similar_sections scored summaries and called them bodies.

The tool's description advertised a fusion of "title + body" lexical
Jaccard. The body channel read ``sec["summary"]``, and under
``index_local(use_ai_summaries=False)`` a summary IS the heading text.
So ``body_tokens == title_tokens``, ``0.70 * body + 0.30 * title`` was
one input counted twice, and any two sections sharing a heading name
scored exactly 1.0 and were reported ``near_duplicate``.

⚠ Every fixture here indexes with ``use_ai_summaries=False`` on purpose
- that is the condition that triggers it. A fixture built with AI
summaries exercises a different program.
"""

from __future__ import annotations

import ast
import io
import pathlib

import pytest

from jdocmunch_mcp.server import _all_tools
from jdocmunch_mcp.tools.find_similar_sections import (
    _differs_by,
    _pair_is_title_only,
    _section_tokens,
    _verdict,
    find_similar_sections,
)
from jdocmunch_mcp.tools.index_local import index_local


_SRC = pathlib.Path(__file__).resolve().parents[1] / (
    "src/jdocmunch_mcp/tools/find_similar_sections.py"
)


def _index(docs_path, tmp_path, use_embeddings=False) -> tuple[str, str]:
    """⚠⚠ ``use_embeddings`` is PINNED, never left at "auto".

    The reported defect is on the no-embeddings path (the reporter's
    corpus came back ``had_embeddings: False``), and "auto" enables
    embeddings whenever an offline provider happens to be installed. A
    dev box with fastembed/sentence-transformers would therefore run a
    different program from CI, which installs neither
    ([[feedback_an_assumption_about_the_machine_is_not_a_fixture]]).
    Measured: with "auto" on such a box the two empty-stub sections came
    back at cosine 1.0 and the guard under test never fired.
    """
    storage = str(tmp_path / "store")
    res = index_local(
        path=str(docs_path),
        use_ai_summaries=False,
        use_embeddings=use_embeddings,
        storage_path=storage,
    )
    assert res["success"], f"Indexing failed: {res}"
    return res["repo"], storage


def _clusters_of(res: dict) -> list[dict]:
    return res["result"]["clusters"]


def _cluster_titles(c: dict) -> set[str]:
    return {c["canonical"]["title"]} | {v["title"] for v in c["variants"]}


# ---------- fixtures ----------


@pytest.fixture
def shared_heading_only(tmp_path):
    """Two sections sharing a heading and sharing NOTHING else.

    This is the defect stated as a corpus: pre-fix both bodies tokenize
    to {architecture}, body_jac == title_jac == 1.0, score 1.0,
    near_duplicate.
    """
    docs = tmp_path / "wiki"
    docs.mkdir()
    (docs / "groq.md").write_text(
        "# Architecture\n\n"
        "Requests leave your application, traverse the Groq inference "
        "endpoint, and return through a streaming websocket bridge. "
        "Latency budgets are enforced per hop by the scheduler.\n",
        encoding="utf-8",
    )
    (docs / "storage.md").write_text(
        "# Architecture\n\n"
        "Version note: this page describes how the on-disk monolith is "
        "sharded across sidecar files, and which loader rehydrates each "
        "one lazily when a query first touches it.\n",
        encoding="utf-8",
    )
    return docs


@pytest.fixture
def real_duplicates_different_headings(tmp_path):
    """Genuinely near-duplicate bodies under DIFFERENT headings.

    Non-vacuity: without this, a fix that simply clusters nothing passes
    the shared-heading test.
    """
    docs = tmp_path / "wiki"
    docs.mkdir()
    body = (
        "Install the package with pip, create a virtual environment, "
        "export the API token, then run the migration command before "
        "starting the worker process under supervision.\n"
    )
    (docs / "setup.md").write_text("# Initial Setup\n\n" + body, encoding="utf-8")
    (docs / "onboarding.md").write_text(
        "# Getting Onboard\n\n" + body, encoding="utf-8"
    )
    return docs


@pytest.fixture
def empty_stubs_beside_real_sections(tmp_path):
    """Two EMPTY stubs under a heading that real sections also use.

    ⚠ This is the transitive hole an "all pairs were title_only" rule
    misses: union-find merges the two stubs (title_only, score 1.0) into
    the cluster holding the two real sections, so the cluster contains a
    body-bearing pair and takes its max_score from the pair that read
    nothing.
    """
    docs = tmp_path / "wiki"
    docs.mkdir()
    (docs / "alpha.md").write_text(
        "# Architecture\n\n"
        "Alpha routes every inbound request through a queue, then fans "
        "it out to a pool of stateless workers holding no session.\n",
        encoding="utf-8",
    )
    (docs / "beta.md").write_text(
        "# Architecture\n\n"
        "Beta persists documents as immutable segments and compacts "
        "them on a timer, keeping a manifest of live segment ranges.\n",
        encoding="utf-8",
    )
    (docs / "stub-one.md").write_text("# Architecture\n\n", encoding="utf-8")
    (docs / "stub-two.md").write_text("# Architecture\n\n", encoding="utf-8")
    return docs


# ---------- the defect, stated as a property ----------


class TestSharedHeadingIsNotADuplicate:
    def test_shared_heading_alone_is_never_near_duplicate(
        self, tmp_path, shared_heading_only
    ):
        repo, storage = _index(shared_heading_only, tmp_path)
        res = find_similar_sections(repo, min_score=0.3, storage_path=storage)
        for c in _clusters_of(res):
            if _cluster_titles(c) == {"Architecture"}:
                assert c["verdict"] != "near_duplicate", (
                    "Two sections sharing only a heading were reported as "
                    f"duplicates: {c}"
                )

    def test_shared_heading_alone_does_not_score_one(
        self, tmp_path, shared_heading_only
    ):
        """Pre-fix this pair scored EXACTLY 1.0 on every corpus."""
        repo, storage = _index(shared_heading_only, tmp_path)
        res = find_similar_sections(repo, min_score=0.3, storage_path=storage)
        for c in _clusters_of(res):
            if _cluster_titles(c) == {"Architecture"}:
                assert c["evidence_max_score"] < 0.92, (
                    "Unrelated bodies under one heading produced a "
                    f"near-duplicate evidence score: {c}"
                )

    def test_body_channel_reports_distinct_tokens(
        self, tmp_path, shared_heading_only
    ):
        """⚠ The tell the tool could not see.

        Pre-fix EVERY variant came back with body_unique_a == [] AND
        body_unique_b == []. "No unique content on either side" is
        indistinguishable from "I did not read either side."
        """
        repo, storage = _index(shared_heading_only, tmp_path)
        res = find_similar_sections(repo, min_score=0.1, storage_path=storage)
        checked = 0
        for c in _clusters_of(res):
            if _cluster_titles(c) != {"Architecture"}:
                continue
            for v in c["variants"]:
                d = v["differs_by"]
                assert d["body_unique_a"] or d["body_unique_b"], (
                    "Two sections with different byte ranges reported no "
                    f"unique body tokens on either side: {d}"
                )
                checked += 1
        assert checked, "Fixture produced no Architecture variant to check"


class TestRealDuplicatesStillCluster:
    """Non-vacuity: 'cluster nothing' must not pass the suite."""

    def test_identical_bodies_under_different_headings_cluster(
        self, tmp_path, real_duplicates_different_headings
    ):
        repo, storage = _index(real_duplicates_different_headings, tmp_path)
        res = find_similar_sections(repo, min_score=0.3, storage_path=storage)
        clusters = _clusters_of(res)
        assert clusters, "Genuinely duplicated bodies produced no cluster"
        c = clusters[0]
        paths = {c["canonical"]["doc_path"]} | {
            v["doc_path"] for v in c["variants"]
        }
        assert {"setup.md", "onboarding.md"} <= paths

    def test_that_cluster_is_body_signalled(
        self, tmp_path, real_duplicates_different_headings
    ):
        repo, storage = _index(real_duplicates_different_headings, tmp_path)
        res = find_similar_sections(repo, min_score=0.3, storage_path=storage)
        c = _clusters_of(res)[0]
        assert c["signal"] == "body"
        assert c["evidence_max_score"] >= 0.3
        for v in c["variants"]:
            assert v["signal"] == "body"
            assert v["differs_by"]["body_signal"] == "body"


# ---------- the degenerate-signal guard, asserted directly ----------


class TestDegenerateSignalGuard:
    def test_section_tokens_flags_title_only(self):
        sec = {"title": "Architecture", "summary": "Architecture"}
        _, _, source = _section_tokens(sec, "")
        assert source == "title_only"

    def test_section_tokens_reports_content_source(self):
        sec = {"title": "Architecture", "summary": "Architecture"}
        _, _, source = _section_tokens(
            sec, "Sharded segments compacted on a timer."
        )
        assert source == "content"

    def test_section_tokens_reports_summary_fallback(self):
        """An unreadable byte range falls back to summary and SAYS so."""
        sec = {"title": "Architecture", "summary": "Queues fan out to workers."}
        _, _, source = _section_tokens(sec, "")
        assert source == "summary"

    def test_pair_is_title_only_needs_both_sides(self):
        a = {"body_source": "title_only"}
        b = {"body_source": "title_only"}
        assert _pair_is_title_only(a, b) is True
        assert _pair_is_title_only(a, {"body_source": "content"}) is False
        assert _pair_is_title_only({"body_source": "content"}, b) is False

    def test_verdict_refuses_near_duplicate_when_title_only(self):
        assert _verdict(1.0, 0.92, {"a"}, title_only=False) == "near_duplicate"
        assert _verdict(1.0, 0.92, {"a"}, title_only=True) != "near_duplicate"

    def test_differs_by_carries_the_body_signal(self):
        d = _differs_by(
            set(), set(), {"architecture"}, {"architecture"}, "title_only"
        )
        assert d["body_unique_a"] == [] and d["body_unique_b"] == []
        assert d["body_signal"] == "title_only", (
            "An empty body diff must say whether it compared anything"
        )


class TestTransitiveTitleOnlyHole:
    """⚠ An 'all pairs were title_only' cap does NOT close this."""

    def test_empty_stubs_do_not_lift_a_cluster_to_near_duplicate(
        self, tmp_path, empty_stubs_beside_real_sections
    ):
        repo, storage = _index(empty_stubs_beside_real_sections, tmp_path)
        res = find_similar_sections(repo, min_score=0.3, storage_path=storage)
        for c in _clusters_of(res):
            if _cluster_titles(c) != {"Architecture"}:
                continue
            assert c["verdict"] != "near_duplicate", (
                "Two empty stubs lifted a cluster of unrelated sections to "
                f"near_duplicate: {c}"
            )

    def test_verdict_never_rests_on_a_title_only_pair(
        self, tmp_path, empty_stubs_beside_real_sections
    ):
        """The reported max_score may come from a title_only pair; the
        VERDICT must come from evidence_max_score."""
        repo, storage = _index(empty_stubs_beside_real_sections, tmp_path)
        res = find_similar_sections(repo, min_score=0.3, storage_path=storage)
        for c in _clusters_of(res):
            if c["verdict"] == "near_duplicate":
                assert c["evidence_max_score"] >= 0.92, (
                    f"near_duplicate minted on weak evidence: {c}"
                )

    def test_meta_counts_the_degenerate_sections(
        self, tmp_path, empty_stubs_beside_real_sections
    ):
        repo, storage = _index(empty_stubs_beside_real_sections, tmp_path)
        res = find_similar_sections(repo, min_score=0.3, storage_path=storage)
        sources = res["_meta"]["body_sources"]
        assert sources.get("title_only", 0) >= 2, (
            f"The two empty stubs were not disclosed as title_only: {sources}"
        )
        assert sources.get("content", 0) >= 2


# ---------- the description must be true ----------


class TestDescriptionBindsToBehaviour:
    """⚠ A description advertising a channel the code does not have is
    product surface that lies. These bind the two together."""

    def _tool(self):
        return next(
            t for t in _all_tools() if t.name == "find_similar_sections"
        )

    def test_description_does_not_promise_a_bare_body_channel(self):
        desc = self._tool().description
        assert "title + body lexical Jaccard" not in desc, (
            "This is the pre-fix wording, which claimed a body channel "
            "that read summaries."
        )

    def test_description_names_the_real_source_and_the_refusal(self):
        desc = self._tool().description
        low = desc.lower()
        assert "body bytes" in low
        assert "title_only" in low, (
            "The refusal is wire-visible and must be documented"
        )

    def test_implementation_reads_bytes_not_summary(self):
        """The body text handed to _section_tokens comes from the index
        content loader, and `summary` survives ONLY as the documented
        fallback inside _section_tokens itself."""
        src = io.open(_SRC, encoding="utf-8").read()
        tree = ast.parse(src)
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef)
            and n.name == "find_similar_sections"
        )
        reads_summary = [
            n for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "get"
            and n.args
            and isinstance(n.args[0], ast.Constant)
            and n.args[0].value == "summary"
        ]
        assert not reads_summary, (
            "find_similar_sections reads `summary` directly again; the body "
            "channel must come from the content loader "
            f"(lines {[n.lineno for n in reads_summary]})"
        )
        assert "_ensure_content" in src, (
            "Nothing in this module reaches the byte-range content loader"
        )

    def test_module_docstring_states_the_measurement(self):
        src = io.open(_SRC, encoding="utf-8").read()
        assert "jdoc#129" in src


class TestEmbeddingsAreAnIndependentChannel:
    """⚠ The title_only refusal is scoped to the LEXICAL-ONLY path on
    purpose, and this pins it so nobody "simplifies" the scope away.

    When cosine is available the fusion has a channel that did not come
    from the title, so refusing would suppress genuine duplicates the
    embedding channel found. Two byte-identical stubs ARE duplicates.
    """

    def test_pair_with_cosine_is_not_refused(self, tmp_path,
                                             empty_stubs_beside_real_sections):
        repo, storage = _index(
            empty_stubs_beside_real_sections, tmp_path, use_embeddings=True
        )
        res = find_similar_sections(repo, min_score=0.3, storage_path=storage)
        if not res["result"]["had_embeddings"]:
            pytest.skip("No embedding provider available in this environment")
        assert res["_meta"]["title_only_pairs"] == 0, (
            "A pair scored with cosine must never be labelled title_only"
        )

    def test_lexical_only_run_does_flag_them(self, tmp_path,
                                             empty_stubs_beside_real_sections):
        """Non-vacuity for the test above: the same corpus, no cosine."""
        repo, storage = _index(empty_stubs_beside_real_sections, tmp_path)
        res = find_similar_sections(repo, min_score=0.3, storage_path=storage)
        assert res["result"]["had_embeddings"] is False
        assert res["_meta"]["title_only_pairs"] >= 1
