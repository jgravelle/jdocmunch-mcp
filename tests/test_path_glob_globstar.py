"""path_glob: ``**/`` also matches zero directories.

The tool schemas give ``api/**/*.md`` as the example glob. Plain ``fnmatch``
still requires the ``/`` after ``**``, so that example missed ``api/auth.md``
and ``**/*.md`` missed every root-level document.
"""

from __future__ import annotations

import pytest

from jdocmunch_mcp.storage.doc_store import path_glob_matches
from jdocmunch_mcp.tools.count_sections import count_sections
from jdocmunch_mcp.tools.get_toc import get_toc
from jdocmunch_mcp.tools.get_toc_tree import get_toc_tree
from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.search_sections import search_sections


@pytest.mark.parametrize(
    "doc_path,path_glob,expected",
    [
        ("api/auth.md", "api/**/*.md", True),
        ("api/v2/auth.md", "api/**/*.md", True),
        ("guide.md", "**/*.md", True),
        ("sub/other.md", "**/*.md", True),
        ("api/v2/x/auth.md", "api/**/x/**/*.md", True),
        ("api/x/auth.md", "api/**/x/**/*.md", True),
        ("guide/auth.md", "api/**/*.md", False),
        ("api/auth.txt", "api/**/*.md", False),
        # Unchanged fnmatch behavior: ``*`` still crosses ``/``.
        ("api/v2/auth.md", "api/*", True),
        ("reference/config.md", "reference/*", True),
    ],
)
def test_path_glob_matches(doc_path, path_glob, expected):
    assert path_glob_matches(doc_path, path_glob) is expected


def _index(tmp_path):
    repo = tmp_path / "docs"
    (repo / "api" / "v2").mkdir(parents=True)
    (repo / "readme.md").write_text("# Readme\n\nInstallation steps.\n", encoding="utf-8")
    (repo / "api" / "auth.md").write_text("# Auth\n\nInstallation of tokens.\n", encoding="utf-8")
    (repo / "api" / "v2" / "users.md").write_text("# Users\n\nInstallation of users.\n", encoding="utf-8")
    index_local(
        path=str(repo), name="globstar",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )


def test_get_toc_globstar_includes_direct_children(tmp_path):
    _index(tmp_path)
    out = get_toc(repo="globstar", path_glob="api/**/*.md", storage_path=str(tmp_path))
    assert {s["doc_path"] for s in out["sections"]} == {"api/auth.md", "api/v2/users.md"}


def test_get_toc_leading_globstar_includes_root_docs(tmp_path):
    _index(tmp_path)
    out = get_toc(repo="globstar", path_glob="**/*.md", storage_path=str(tmp_path))
    assert {s["doc_path"] for s in out["sections"]} == {
        "readme.md", "api/auth.md", "api/v2/users.md"}


def test_get_toc_tree_globstar_includes_direct_children(tmp_path):
    _index(tmp_path)
    out = get_toc_tree(repo="globstar", path_glob="api/**/*.md", storage_path=str(tmp_path))
    assert {d["doc_path"] for d in out["documents"]} == {"api/auth.md", "api/v2/users.md"}


def test_count_sections_globstar_includes_direct_children(tmp_path):
    _index(tmp_path)
    toc = get_toc(repo="globstar", storage_path=str(tmp_path))
    api_sections = [s for s in toc["sections"] if s["doc_path"].startswith("api/")]
    out = count_sections(repo="globstar", path_glob="api/**/*.md", storage_path=str(tmp_path))
    assert api_sections and out["count"] == len(api_sections)


def test_search_sections_globstar_includes_direct_children(tmp_path):
    _index(tmp_path)
    out = search_sections(
        repo="globstar", query="installation", path_glob="api/**/*.md",
        semantic=False, storage_path=str(tmp_path),
    )
    assert {r["doc_path"] for r in out["results"]} == {"api/auth.md", "api/v2/users.md"}
