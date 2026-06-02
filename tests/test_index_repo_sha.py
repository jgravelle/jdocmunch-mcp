"""Commit-SHA handling for GitHub indexing."""

import importlib

import pytest

from jdocmunch_mcp.parser import parse_file
from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.tools.list_repos import list_repos
from jdocmunch_mcp.tools.search_sections import search_sections


@pytest.mark.asyncio
async def test_index_repo_fetches_tree_and_content_at_resolved_sha(tmp_path, monkeypatch):
    mod = importlib.import_module("jdocmunch_mcp.tools.index_repo")
    sha = "c" * 40
    refs = []

    async def fake_head(owner, repo, token=None, client=None, ref="HEAD"):
        assert (owner, repo, ref) == ("octo", "docs", "HEAD")
        return sha

    async def fake_tree(owner, repo, token=None, client=None, ref="HEAD"):
        refs.append(("tree", ref))
        return [{"type": "blob", "path": "README.md", "size": 64}]

    async def fake_gitignore(owner, repo, token=None, client=None, ref="HEAD"):
        refs.append(("gitignore", ref))
        return None

    async def fake_content(owner, repo, path, token=None, client=None, ref="HEAD"):
        refs.append(("content", ref, path))
        return "# README\n\nPinned content."

    monkeypatch.setattr(mod, "fetch_head_commit_sha", fake_head)
    monkeypatch.setattr(mod, "fetch_repo_tree", fake_tree)
    monkeypatch.setattr(mod, "fetch_gitignore", fake_gitignore)
    monkeypatch.setattr(mod, "fetch_file_content", fake_content)

    result = await mod.index_repo(
        "octo/docs",
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(tmp_path),
    )

    assert result["success"] is True
    assert result["head_sha"] == sha
    assert result["source_dirty"] is False
    assert result["sha_certified"] is True
    assert result["repo_at_sha"] == f"octo/docs@{sha}"
    assert refs == [
        ("tree", sha),
        ("gitignore", sha),
        ("content", sha, "README.md"),
    ]


@pytest.mark.asyncio
async def test_index_repo_fallback_to_head_is_not_certified(tmp_path, monkeypatch):
    mod = importlib.import_module("jdocmunch_mcp.tools.index_repo")
    refs = []

    async def fake_head(owner, repo, token=None, client=None, ref="HEAD"):
        return None

    async def fake_tree(owner, repo, token=None, client=None, ref="HEAD"):
        refs.append(("tree", ref))
        return [{"type": "blob", "path": "README.md", "size": 64}]

    async def fake_gitignore(owner, repo, token=None, client=None, ref="HEAD"):
        refs.append(("gitignore", ref))
        return None

    async def fake_content(owner, repo, path, token=None, client=None, ref="HEAD"):
        refs.append(("content", ref, path))
        return "# README\n\nUnpinned content."

    monkeypatch.setattr(mod, "fetch_head_commit_sha", fake_head)
    monkeypatch.setattr(mod, "fetch_repo_tree", fake_tree)
    monkeypatch.setattr(mod, "fetch_gitignore", fake_gitignore)
    monkeypatch.setattr(mod, "fetch_file_content", fake_content)

    result = await mod.index_repo(
        "octo/docs",
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(tmp_path),
    )

    assert result["success"] is True
    assert "head_sha" not in result
    assert result["source_dirty"] is False
    assert result["sha_certified"] is False
    assert "repo_at_sha" not in result
    assert refs == [
        ("tree", "HEAD"),
        ("gitignore", "HEAD"),
        ("content", "HEAD", "README.md"),
    ]


@pytest.mark.asyncio
async def test_index_repo_recovers_legacy_matching_sha_via_pinned_fetch(tmp_path, monkeypatch):
    mod = importlib.import_module("jdocmunch_mcp.tools.index_repo")
    sha = "d" * 40
    content = "# README\n\nPinned content."
    store = DocStore(base_path=str(tmp_path))
    store.save_index(
        "octo",
        "docs",
        parse_file(content, "README.md", "octo/docs"),
        {"README.md": content},
        {".md": 1},
        head_sha=sha,
    )
    refs = []

    async def fake_head(owner, repo, token=None, client=None, ref="HEAD"):
        return sha

    async def fake_tree(owner, repo, token=None, client=None, ref="HEAD"):
        refs.append(("tree", ref))
        return [{"type": "blob", "path": "README.md", "size": 64}]

    async def fake_gitignore(owner, repo, token=None, client=None, ref="HEAD"):
        refs.append(("gitignore", ref))
        return None

    async def fake_content(owner, repo, path, token=None, client=None, ref="HEAD"):
        refs.append(("content", ref, path))
        return content

    monkeypatch.setattr(mod, "fetch_head_commit_sha", fake_head)
    monkeypatch.setattr(mod, "fetch_repo_tree", fake_tree)
    monkeypatch.setattr(mod, "fetch_gitignore", fake_gitignore)
    monkeypatch.setattr(mod, "fetch_file_content", fake_content)

    result = await mod.index_repo(
        "octo/docs",
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(tmp_path),
    )

    assert result["success"] is True
    assert result["changed"] == 0
    assert result["head_sha"] == sha
    assert result["source_dirty"] is False
    assert result["sha_certified"] is True
    assert result["repo_at_sha"] == f"octo/docs@{sha}"
    assert refs == [
        ("tree", sha),
        ("gitignore", sha),
        ("content", sha, "README.md"),
    ]


@pytest.mark.asyncio
async def test_index_repo_custom_name_stores_under_override_and_keeps_source_identity(tmp_path, monkeypatch):
    mod = importlib.import_module("jdocmunch_mcp.tools.index_repo")
    sha = "e" * 40

    async def fake_head(owner, repo, token=None, client=None, ref="HEAD"):
        assert (owner, repo) == ("octo", "docs")
        return sha

    async def fake_tree(owner, repo, token=None, client=None, ref="HEAD"):
        assert (owner, repo, ref) == ("octo", "docs", sha)
        return [{"type": "blob", "path": "README.md", "size": 64}]

    async def fake_gitignore(owner, repo, token=None, client=None, ref="HEAD"):
        return None

    async def fake_content(owner, repo, path, token=None, client=None, ref="HEAD"):
        assert (owner, repo, path, ref) == ("octo", "docs", "README.md", sha)
        return "# Custom Docs\n\nHello custom content."

    monkeypatch.setattr(mod, "fetch_head_commit_sha", fake_head)
    monkeypatch.setattr(mod, "fetch_repo_tree", fake_tree)
    monkeypatch.setattr(mod, "fetch_gitignore", fake_gitignore)
    monkeypatch.setattr(mod, "fetch_file_content", fake_content)

    result = await mod.index_repo(
        "octo/docs",
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(tmp_path),
        name="docs_1.0",
    )

    assert result["success"] is True
    assert result["repo"] == "octo/docs_1.0"
    assert result["source_repo"] == "octo/docs"
    assert result["repo_at_sha"] == f"octo/docs_1.0@{sha}"
    assert result["source_repo_at_sha"] == f"octo/docs@{sha}"

    store = DocStore(base_path=str(tmp_path))
    assert store.load_index("octo", "docs") is None
    stored = store.load_index("octo", "docs_1.0")
    assert stored is not None
    assert stored.repo == "octo/docs_1.0"
    assert stored.source_repo == "octo/docs"
    assert stored.sections[0]["id"].startswith("octo/docs_1.0::README.md::")

    listed = list_repos(storage_path=str(tmp_path))
    assert [row["repo"] for row in listed["repos"]] == ["octo/docs_1.0"]
    assert listed["repos"][0]["source_repo"] == "octo/docs"
    assert listed["repos"][0]["source_repo_at_sha"] == f"octo/docs@{sha}"

    found = search_sections(
        repo="octo/docs_1.0",
        query="custom",
        storage_path=str(tmp_path),
    )
    assert found["repo"] == "octo/docs_1.0"
    assert found["result_count"] >= 1

    strict_found = search_sections(
        repo=f"octo/docs_1.0@{sha}",
        query="custom",
        storage_path=str(tmp_path),
    )
    assert strict_found["repo_at_sha"] == f"octo/docs_1.0@{sha}"
    assert strict_found["result_count"] >= 1


@pytest.mark.asyncio
async def test_index_repo_custom_name_fast_path_uses_override_storage(tmp_path, monkeypatch):
    mod = importlib.import_module("jdocmunch_mcp.tools.index_repo")
    sha = "f" * 40
    calls = {"tree": 0}

    async def fake_head(owner, repo, token=None, client=None, ref="HEAD"):
        return sha

    async def fake_tree(owner, repo, token=None, client=None, ref="HEAD"):
        calls["tree"] += 1
        if calls["tree"] > 1:
            raise AssertionError("custom-name fast path should skip tree fetch")
        return [{"type": "blob", "path": "README.md", "size": 64}]

    async def fake_gitignore(owner, repo, token=None, client=None, ref="HEAD"):
        return None

    async def fake_content(owner, repo, path, token=None, client=None, ref="HEAD"):
        return "# README\n\nStable content."

    monkeypatch.setattr(mod, "fetch_head_commit_sha", fake_head)
    monkeypatch.setattr(mod, "fetch_repo_tree", fake_tree)
    monkeypatch.setattr(mod, "fetch_gitignore", fake_gitignore)
    monkeypatch.setattr(mod, "fetch_file_content", fake_content)

    first = await mod.index_repo(
        "octo/docs",
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(tmp_path),
        name="docs_stable",
    )
    second = await mod.index_repo(
        "octo/docs",
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(tmp_path),
        name="docs_stable",
    )

    assert first["success"] is True
    assert second["success"] is True
    assert second["message"] == "No changes detected (HEAD SHA unchanged)"
    assert second["repo"] == "octo/docs_stable"
    assert second["source_repo"] == "octo/docs"
    assert second["repo_at_sha"] == f"octo/docs_stable@{sha}"
    assert second["source_repo_at_sha"] == f"octo/docs@{sha}"
    assert calls["tree"] == 1


@pytest.mark.asyncio
async def test_index_repo_custom_name_changed_file_incremental_uses_override_storage(tmp_path, monkeypatch):
    mod = importlib.import_module("jdocmunch_mcp.tools.index_repo")
    state = {"sha": "a" * 40, "body": "First content."}

    async def fake_head(owner, repo, token=None, client=None, ref="HEAD"):
        return state["sha"]

    async def fake_tree(owner, repo, token=None, client=None, ref="HEAD"):
        return [{"type": "blob", "path": "README.md", "size": 64}]

    async def fake_gitignore(owner, repo, token=None, client=None, ref="HEAD"):
        return None

    async def fake_content(owner, repo, path, token=None, client=None, ref="HEAD"):
        return f"# README\n\n{state['body']}"

    monkeypatch.setattr(mod, "fetch_head_commit_sha", fake_head)
    monkeypatch.setattr(mod, "fetch_repo_tree", fake_tree)
    monkeypatch.setattr(mod, "fetch_gitignore", fake_gitignore)
    monkeypatch.setattr(mod, "fetch_file_content", fake_content)

    first = await mod.index_repo(
        "octo/docs",
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(tmp_path),
        name="docs_changed",
    )
    state["sha"] = "b" * 40
    state["body"] = "Second content."
    second = await mod.index_repo(
        "octo/docs",
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(tmp_path),
        name="docs_changed",
    )

    assert first["success"] is True
    assert second["success"] is True
    assert second["repo"] == "octo/docs_changed"
    assert second["incremental"] is True
    assert second["changed"] == 1
    assert second["new"] == 0
    assert second["deleted"] == 0
    assert second["repo_at_sha"] == f"octo/docs_changed@{'b' * 40}"
    assert second["source_repo_at_sha"] == f"octo/docs@{'b' * 40}"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_name", ["foo@bar", "foo/bar", "", ".."])
async def test_index_repo_custom_name_rejects_unsafe_storage_names(tmp_path, monkeypatch, bad_name):
    mod = importlib.import_module("jdocmunch_mcp.tools.index_repo")

    async def fake_head(owner, repo, token=None, client=None, ref="HEAD"):
        raise AssertionError("invalid name should fail before network fetch")

    monkeypatch.setattr(mod, "fetch_head_commit_sha", fake_head)

    result = await mod.index_repo(
        "octo/docs",
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(tmp_path),
        name=bad_name,
    )

    assert result["success"] is False
    assert result["error"].startswith("Invalid name:")


@pytest.mark.asyncio
async def test_doc_index_repo_schema_exposes_name_override():
    srv = importlib.import_module("jdocmunch_mcp.server")
    tools = await srv.list_tools()
    tool = next(t for t in tools if t.name == "doc_index_repo")

    assert "name" in tool.inputSchema["properties"]
    assert tool.inputSchema["properties"]["name"]["type"] == "string"
    assert tool.inputSchema["required"] == ["url"]
