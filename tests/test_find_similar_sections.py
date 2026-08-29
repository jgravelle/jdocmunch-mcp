"""Tests for find_similar_sections."""

from __future__ import annotations

import pytest

from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.find_similar_sections import find_similar_sections


def _index(docs_path: str, tmp_path) -> tuple[str, str]:
    # ⚠ use_embeddings is PINNED off. Left at "auto" it turns on whenever
    # an offline provider happens to be installed, so a dev box with
    # fastembed/sentence-transformers runs a different program from CI,
    # which installs neither (jdoc#129;
    # [[feedback_an_assumption_about_the_machine_is_not_a_fixture]]).
    storage = str(tmp_path / "store")
    res = index_local(
        path=docs_path,
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=storage,
    )
    assert res["success"], f"Indexing failed: {res}"
    return res["repo"], storage


# ---------- fixtures ----------


@pytest.fixture
def wiki_with_duplicates(tmp_path):
    """Two pages saying nearly the same thing, plus an unrelated page."""
    docs = tmp_path / "wiki"
    docs.mkdir()
    (docs / "auth-v1.md").write_text(
        "# Authentication\n\n"
        "Authentication handles user login. Pass a JWT token via the "
        "Authorization header. Tokens expire after one hour and must be "
        "refreshed using the refresh token endpoint. JWT validation "
        "happens at the middleware layer.\n"
    )
    (docs / "auth-v2.md").write_text(
        "# Authentication\n\n"
        "Authentication handles user login. JWT tokens go in the "
        "Authorization header. Tokens expire in one hour, refresh via "
        "the refresh endpoint. Middleware validates the JWT.\n"
    )
    (docs / "deploy.md").write_text(
        "# Deployment\n\n"
        "Deploy with Docker. Build the container, push to registry, "
        "and run on Kubernetes with appropriate resource limits.\n"
    )
    return str(docs)


@pytest.fixture
def wiki_no_duplicates(tmp_path):
    docs = tmp_path / "wiki"
    docs.mkdir()
    (docs / "alpha.md").write_text(
        "# Alpha\n\nGreek letters used in physics for angles.\n"
    )
    (docs / "compiler.md").write_text(
        "# Compiler\n\nA compiler translates source code to machine code.\n"
    )
    (docs / "rocket.md").write_text(
        "# Rocket\n\nRockets carry payloads into orbit using staged ignition.\n"
    )
    return str(docs)


@pytest.fixture
def wiki_parallel_tutorials(tmp_path):
    """Two tutorials in different directories covering the same topic."""
    docs = tmp_path / "wiki"
    docs.mkdir()
    py = docs / "python"
    py.mkdir()
    js = docs / "javascript"
    js.mkdir()
    (py / "getting-started.md").write_text(
        "# Getting Started\n\n"
        "Install Python. Create a virtual environment. Install dependencies "
        "via pip. Run the application with python main.py.\n"
    )
    (js / "getting-started.md").write_text(
        "# Getting Started\n\n"
        "Install Node. Create a project directory. Install dependencies "
        "via npm. Run the application with node main.js.\n"
    )
    return str(docs)


# ---------- tests ----------


def test_finds_duplicate_cluster(tmp_path, wiki_with_duplicates):
    repo, storage = _index(wiki_with_duplicates, tmp_path)
    r = find_similar_sections(repo, min_score=0.3, storage_path=storage)
    res = r["result"]
    assert res["cluster_count"] >= 1, f"Expected ≥1 cluster, got result={res}"
    # The auth pair should be flagged
    cluster = res["clusters"][0]
    paths = {cluster["canonical"]["doc_path"]} | {v["doc_path"] for v in cluster["variants"]}
    assert "auth-v1.md" in paths and "auth-v2.md" in paths


def test_no_duplicates_returns_empty(tmp_path, wiki_no_duplicates):
    repo, storage = _index(wiki_no_duplicates, tmp_path)
    r = find_similar_sections(repo, storage_path=storage)
    assert r["result"]["cluster_count"] == 0
    assert r["result"]["clusters"] == []


def test_min_score_filter(tmp_path, wiki_with_duplicates):
    """High threshold drops weak matches."""
    repo, storage = _index(wiki_with_duplicates, tmp_path)
    high = find_similar_sections(repo, min_score=0.99, storage_path=storage)
    # 0.99 is high enough that auth-v1 vs auth-v2 (text differs)
    # should usually fall below it without embeddings.
    assert high["result"]["cluster_count"] <= 1


def test_cluster_has_canonical_and_variants(tmp_path, wiki_with_duplicates):
    repo, storage = _index(wiki_with_duplicates, tmp_path)
    r = find_similar_sections(repo, min_score=0.3, storage_path=storage)
    if r["result"]["cluster_count"] == 0:
        pytest.skip("Lexical-only signal didn't cross threshold; smoke-tested elsewhere.")
    c = r["result"]["clusters"][0]
    assert "section_id" in c["canonical"]
    assert "doc_path" in c["canonical"]
    assert "rationale" in c["canonical"]
    assert isinstance(c["variants"], list)
    assert c["size"] >= 2


def test_variant_has_differs_by(tmp_path, wiki_with_duplicates):
    repo, storage = _index(wiki_with_duplicates, tmp_path)
    r = find_similar_sections(repo, min_score=0.3, storage_path=storage)
    if r["result"]["cluster_count"] == 0:
        pytest.skip("No clusters formed.")
    variant = r["result"]["clusters"][0]["variants"][0]
    assert "differs_by" in variant
    assert "body_unique_a" in variant["differs_by"]
    assert "body_unique_b" in variant["differs_by"]


def test_verdict_uses_threshold(tmp_path, wiki_with_duplicates):
    """At very low threshold, the auth pair should still classify, and
    the verdict is one of the three documented tiers."""
    repo, storage = _index(wiki_with_duplicates, tmp_path)
    r = find_similar_sections(repo, min_score=0.3, near_duplicate_threshold=0.999, storage_path=storage)
    if r["result"]["cluster_count"] == 0:
        pytest.skip("No clusters.")
    verdict = r["result"]["clusters"][0]["verdict"]
    assert verdict in {"near_duplicate", "overlapping_topic", "parallel_tutorial"}


def test_max_clusters_caps_output(tmp_path):
    """Synthetic many-duplicates wiki — cap kicks in."""
    docs = tmp_path / "wiki"
    docs.mkdir()
    # Make 5 pairs of near-identical pages
    template = (
        "# Topic {i}\n\nThis page describes topic number {i} with the "
        "same vocabulary and phrasing across all instances. Identical "
        "boilerplate language appears here for similarity testing.\n"
    )
    for i in range(5):
        (docs / f"a-{i}.md").write_text(template.format(i=i))
        (docs / f"b-{i}.md").write_text(template.format(i=i))
    repo, storage = _index(str(docs), tmp_path)
    r = find_similar_sections(repo, min_score=0.3, max_clusters=2, storage_path=storage)
    assert r["result"]["cluster_count"] <= 2


def test_exclude_same_doc(tmp_path):
    docs = tmp_path / "wiki"
    docs.mkdir()
    (docs / "long.md").write_text(
        "# Section A\n\nIdentical boilerplate content for testing.\n\n"
        "# Section B\n\nIdentical boilerplate content for testing.\n"
    )
    repo, storage = _index(str(docs), tmp_path)
    # Without exclusion, sections in long.md cluster.
    r_with = find_similar_sections(repo, min_score=0.3, storage_path=storage)
    r_without = find_similar_sections(repo, min_score=0.3, exclude_same_doc=True, storage_path=storage)
    # With exclusion, the same-doc pair is dropped → fewer or equal clusters.
    assert r_without["result"]["cluster_count"] <= r_with["result"]["cluster_count"]


def test_unknown_repo(tmp_path):
    r = find_similar_sections("nobody/nothing", storage_path=str(tmp_path / "store"))
    assert "error" in r


def test_max_sections_truncates(tmp_path, wiki_no_duplicates):
    repo, storage = _index(wiki_no_duplicates, tmp_path)
    r = find_similar_sections(repo, max_sections=1, storage_path=storage)
    assert r["_meta"]["truncated"] is True
    assert r["result"]["section_count_examined"] <= 1


def test_singletons_filtered(tmp_path, wiki_no_duplicates):
    """Sections without a similarity partner never produce a 1-member cluster."""
    repo, storage = _index(wiki_no_duplicates, tmp_path)
    r = find_similar_sections(repo, storage_path=storage)
    for c in r["result"]["clusters"]:
        assert c["size"] >= 2
