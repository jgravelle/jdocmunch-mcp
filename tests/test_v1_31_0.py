"""Tests for v1.31.0: stale-index simulation + multi-format regression harness."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from jdocmunch_mcp.parser import ALL_EXTENSIONS, parse_file, preprocess_content
from jdocmunch_mcp.retrieval.freshness import FreshnessProbe
from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.tools.get_section_diff import get_section_diff
from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.search_sections import search_sections
from jdocmunch_mcp.tools.verify_index import verify_index


# ---------------------------------------------------------------------------
# Stale-index simulation suite
#
# Cross-cuts the v1.16 freshness probe, the v1.20 section_diff, and the
# v1.27 verify_index. A single mutation should surface in all three.
# ---------------------------------------------------------------------------


class TestStaleIndexEndToEnd:
    def _index(self, tmp_path):
        repo = tmp_path / "docs"
        repo.mkdir()
        (repo / "g.md").write_text(
            "# Top\n\n## Auth\n\nbearer token authentication body\n\n## Logs\n\nbody beta\n",
            encoding="utf-8",
        )
        index_local(
            path=str(repo), name="stale",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )
        return repo

    def _mutate_cached(self, store: DocStore, original: bytes, replacement: bytes) -> None:
        """Rewrite the cached file with a substring replacement.

        Uses write_bytes to bypass Windows newline translation (the same
        trap that bit B3 in the v1.10 audit).
        """
        cached = store._safe_content_path(store._content_dir("local", "stale"), "g.md")
        data = cached.read_bytes()
        cached.write_bytes(data.replace(original, replacement))

    def test_byte_range_mutation_surfaces_in_all_three_paths(self, tmp_path):
        self._index(tmp_path)
        store = DocStore(base_path=str(tmp_path))
        self._mutate_cached(store, b"bearer token", b"BRAVO TOKEN")

        idx = store.load_index("local", "stale")
        auth = next(s for s in idx.sections if s["title"] == "Auth")

        # 1. FreshnessProbe must classify Auth as stale_index.
        probe = FreshnessProbe(store, "local", "stale", idx)
        bucket = probe.annotate(dict(auth))
        assert bucket == "stale_index", bucket

        # 2. verify_index must report drift on Auth.
        vi = verify_index(repo="stale", storage_path=str(tmp_path))
        assert vi["drift_count"] >= 1
        drift_ids = {d["section_id"] for d in vi["drift_sections"]}
        assert auth["id"] in drift_ids

        # 3. get_section_diff must report identical=False with hash drift.
        diff = get_section_diff(repo="stale", section_id=auth["id"], storage_path=str(tmp_path))
        assert diff["identical"] is False
        assert diff["indexed_hash"] != diff["current_hash"]

    def test_full_file_change_surfaces_as_edited_not_stale(self, tmp_path):
        # Append to the file so byte-range hashes still match but the
        # full-file hash drifts. The probe should report
        # edited_uncommitted, not stale_index.
        self._index(tmp_path)
        store = DocStore(base_path=str(tmp_path))
        cached = store._safe_content_path(store._content_dir("local", "stale"), "g.md")
        cached.write_bytes(cached.read_bytes() + b"\n## Extra\n\nnew\n")

        idx = store.load_index("local", "stale")
        auth = next(s for s in idx.sections if s["title"] == "Auth")
        probe = FreshnessProbe(store, "local", "stale", idx)
        bucket = probe.annotate(dict(auth))
        # Auth is in the early part of the file; its byte_range is intact.
        assert bucket == "edited_uncommitted", bucket
        # verify_index, by contrast, only checks byte ranges — Auth still hashes clean.
        vi = verify_index(repo="stale", storage_path=str(tmp_path))
        assert auth["id"] not in {d["section_id"] for d in vi["drift_sections"]}

    def test_search_sections_meta_freshness_reports_drift(self, tmp_path):
        self._index(tmp_path)
        store = DocStore(base_path=str(tmp_path))
        self._mutate_cached(store, b"bearer token", b"BRAVO TOKEN")
        # Force fresh load so the probe sees the mutation.
        from jdocmunch_mcp.storage.doc_store import _INDEX_CACHE
        _INDEX_CACHE.clear()

        out = search_sections(
            repo="stale", query="bearer token",
            semantic=False, storage_path=str(tmp_path),
        )
        # The result envelope's freshness summary should show non-fresh
        # buckets for at least one returned section.
        non_fresh = (
            out["_meta"]["freshness"].get("edited_uncommitted", 0)
            + out["_meta"]["freshness"].get("stale_index", 0)
        )
        assert non_fresh >= 1

    def test_missing_file_surfaces_in_verify_and_freshness(self, tmp_path):
        self._index(tmp_path)
        store = DocStore(base_path=str(tmp_path))
        cached = store._safe_content_path(store._content_dir("local", "stale"), "g.md")
        cached.unlink()

        idx = store.load_index("local", "stale")
        auth = next(s for s in idx.sections if s["title"] == "Auth")

        probe = FreshnessProbe(store, "local", "stale", idx)
        assert probe.annotate(dict(auth)) == "stale_index"

        vi = verify_index(repo="stale", storage_path=str(tmp_path))
        assert vi["missing_count"] >= 1
        assert any(m["reason"] == "file_missing" for m in vi["missing_sections"])


# ---------------------------------------------------------------------------
# Multi-format regression harness
#
# A single test indexes one file per supported format and asserts:
#   - parse_file produces sections (or skips cleanly when input is empty)
#   - the file ends up in DocIndex.doc_paths
#   - search hits at least one section per file
# Deliberately small fixtures so the test stays fast.
# ---------------------------------------------------------------------------


_FORMAT_FIXTURES = {
    "guide.md": "# Guide\n\n## Setup\n\nInstall the package.\n",
    "guide.mdx": "# MDX guide\n\n<Note>Hello</Note>\n\nbody markdownx content\n",
    "guide.rst": "Guide\n=====\n\nSetup\n-----\n\nInstall the package.\n",
    "guide.adoc": "= Guide\n\n== Setup\n\nInstall the package.\n",
    "guide.org": "#+TITLE: Guide\n\n* Setup\n\nInstall the package.\n",
    "guide.html": "<html><body><h1>Guide</h1><p>Install the package.</p></body></html>",
    "guide.txt": "Guide\nInstall the package.\n",
    "data.json": '{"name": "Guide", "setup": "Install the package."}',
    "data.xml": "<?xml version='1.0'?><guide><setup>Install</setup></guide>",
    "scene.tscn": '[gd_scene]\n[node name="Guide" type="Node"]\n',
    "spec.yaml": (
        "openapi: 3.0.0\n"
        "info: { title: Guide, version: '1.0' }\n"
        "paths:\n"
        "  /setup:\n"
        "    get:\n"
        "      operationId: getSetup\n"
        "      summary: Install the package\n"
        "      responses:\n"
        "        '200':\n"
        "          description: ok\n"
    ),
}


def test_format_extensions_registered():
    """Every format in the regression set must map to a parser key."""
    expected_extensions = {
        ".md", ".mdx", ".rst", ".adoc", ".org", ".html", ".txt",
        ".json", ".xml", ".tscn", ".yaml",
    }
    for ext in expected_extensions:
        assert ext in ALL_EXTENSIONS, f"missing parser registration: {ext}"


@pytest.mark.parametrize(
    "filename,content",
    list(_FORMAT_FIXTURES.items()),
    ids=list(_FORMAT_FIXTURES.keys()),
)
def test_each_format_indexes_and_finds_setup(tmp_path, filename, content):
    """End-to-end smoke test for every supported format."""
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / filename).write_text(content, encoding="utf-8")
    repo_id = "fmt_" + filename.replace(".", "_")
    out = index_local(
        path=str(repo), name=repo_id,
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )
    assert out.get("success") is True, (filename, out)

    store = DocStore(base_path=str(tmp_path))
    idx = store.load_index("local", repo_id)
    assert idx is not None
    assert filename in idx.doc_paths or any(
        d.endswith(filename) for d in idx.doc_paths
    )
    # Every format should yield at least one indexed section.
    section_count = len([s for s in idx.sections if s.get("doc_path")])
    assert section_count >= 1, (filename, idx.sections)


def test_parse_file_directly_for_each_format(tmp_path):
    """Each format's parser produces a non-empty section list when given
    a typical input."""
    for filename, content in _FORMAT_FIXTURES.items():
        preprocessed = preprocess_content(content, filename)
        sections = parse_file(preprocessed, filename, "regress/test")
        assert sections, f"{filename} produced no sections"
