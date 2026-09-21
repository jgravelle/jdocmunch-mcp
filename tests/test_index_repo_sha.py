"""Commit-SHA handling for GitHub indexing."""

import importlib
import json

import pytest

from jdocmunch_mcp.parser import parse_file
from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.tools.list_repos import list_repos
from jdocmunch_mcp.tools.search_sections import search_sections

def _patch_head(monkeypatch, mod, fake_head):
    """jdoc#135: the request moved from `fetch_head_commit_sha` to
    `fetch_head_commit`, which returns (sha, date) in one call.

    ⚠ Patching only the old name leaves the real HTTP call live and the test
    passes or fails for the wrong reason. Both names are stubbed here, and the
    existing sha-returning fakes are reused unchanged by adapting them.
    """
    async def fake_commit(*a, **kw):
        return await fake_head(*a, **kw), "2026-09-20T13:15:39+00:00"
    monkeypatch.setattr(mod, "fetch_head_commit_sha", fake_head)
    monkeypatch.setattr(mod, "fetch_head_commit", fake_commit)



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

    _patch_head(monkeypatch, mod, fake_head)
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
    assert result["source_repo"] == "octo/docs"
    assert result["source_repo_at_sha"] == f"octo/docs@{sha}"
    assert refs == [
        ("tree", sha),
        ("gitignore", sha),
        ("content", sha, "README.md"),
    ]

    listed = list_repos(storage_path=str(tmp_path))
    assert listed["repos"][0]["source_repo"] == "octo/docs"
    assert listed["repos"][0]["source_repo_at_sha"] == f"octo/docs@{sha}"


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

    _patch_head(monkeypatch, mod, fake_head)
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
async def test_index_repo_ref_fetches_tree_and_content_at_resolved_sha(tmp_path, monkeypatch):
    mod = importlib.import_module("jdocmunch_mcp.tools.index_repo")
    sha = "4" * 40
    refs = []

    async def fake_head(owner, repo, token=None, client=None, ref="HEAD"):
        assert (owner, repo, ref) == ("octo", "docs", "v1.2.3")
        return sha

    async def fake_tree(owner, repo, token=None, client=None, ref="HEAD"):
        refs.append(("tree", ref))
        return [{"type": "blob", "path": "README.md", "size": 64}]

    async def fake_gitignore(owner, repo, token=None, client=None, ref="HEAD"):
        refs.append(("gitignore", ref))
        return None

    async def fake_content(owner, repo, path, token=None, client=None, ref="HEAD"):
        refs.append(("content", ref, path))
        return "# README\n\nVersioned content."

    _patch_head(monkeypatch, mod, fake_head)
    monkeypatch.setattr(mod, "fetch_repo_tree", fake_tree)
    monkeypatch.setattr(mod, "fetch_gitignore", fake_gitignore)
    monkeypatch.setattr(mod, "fetch_file_content", fake_content)

    result = await mod.index_repo(
        "octo/docs",
        ref="v1.2.3",
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(tmp_path),
    )

    assert result["success"] is True
    assert result["head_sha"] == sha
    assert result["sha_certified"] is True
    assert result["repo_at_sha"] == f"octo/docs@{sha}"
    assert result["source_repo_at_sha"] == f"octo/docs@{sha}"
    assert refs == [
        ("tree", sha),
        ("gitignore", sha),
        ("content", sha, "README.md"),
    ]


@pytest.mark.asyncio
async def test_index_repo_ref_composes_with_custom_name(tmp_path, monkeypatch):
    mod = importlib.import_module("jdocmunch_mcp.tools.index_repo")
    sha = "5" * 40

    async def fake_head(owner, repo, token=None, client=None, ref="HEAD"):
        assert (owner, repo, ref) == ("octo", "docs", "v1.2.3")
        return sha

    async def fake_tree(owner, repo, token=None, client=None, ref="HEAD"):
        assert ref == sha
        return [{"type": "blob", "path": "README.md", "size": 64}]

    async def fake_gitignore(owner, repo, token=None, client=None, ref="HEAD"):
        return None

    async def fake_content(owner, repo, path, token=None, client=None, ref="HEAD"):
        assert ref == sha
        return "# README\n\nNamed versioned content."

    _patch_head(monkeypatch, mod, fake_head)
    monkeypatch.setattr(mod, "fetch_repo_tree", fake_tree)
    monkeypatch.setattr(mod, "fetch_gitignore", fake_gitignore)
    monkeypatch.setattr(mod, "fetch_file_content", fake_content)

    result = await mod.index_repo(
        "octo/docs",
        ref="v1.2.3",
        name="docs_v1",
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(tmp_path),
    )

    assert result["success"] is True
    assert result["repo"] == "octo/docs_v1"
    assert result["source_repo"] == "octo/docs"
    assert result["repo_at_sha"] == f"octo/docs_v1@{sha}"
    assert result["source_repo_at_sha"] == f"octo/docs@{sha}"


@pytest.mark.asyncio
async def test_index_repo_explicit_unknown_ref_fails_without_head_fallback(tmp_path, monkeypatch):
    mod = importlib.import_module("jdocmunch_mcp.tools.index_repo")

    async def fake_head(owner, repo, token=None, client=None, ref="HEAD"):
        assert ref == "missing-tag"
        return None

    async def fake_tree(owner, repo, token=None, client=None, ref="HEAD"):
        raise AssertionError("explicit missing ref should not fetch tree/content")

    _patch_head(monkeypatch, mod, fake_head)
    monkeypatch.setattr(mod, "fetch_repo_tree", fake_tree)

    result = await mod.index_repo(
        "octo/docs",
        ref="missing-tag",
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(tmp_path),
    )

    assert result == {"success": False, "error": "GitHub ref could not be resolved: octo/docs@missing-tag"}


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_ref", ["", "   ", 123, [], {}])
async def test_index_repo_rejects_invalid_ref_before_network_fetch(tmp_path, monkeypatch, bad_ref):
    mod = importlib.import_module("jdocmunch_mcp.tools.index_repo")

    async def fake_head(owner, repo, token=None, client=None, ref="HEAD"):
        raise AssertionError("invalid ref should fail before network fetch")

    _patch_head(monkeypatch, mod, fake_head)

    result = await mod.index_repo(
        "octo/docs",
        ref=bad_ref,
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(tmp_path),
    )

    assert result["success"] is False
    assert result["error"].startswith("Invalid ref:")


@pytest.mark.asyncio
async def test_fetch_head_commit_sha_url_encodes_ref_path_segment():
    mod = importlib.import_module("jdocmunch_mcp.tools.index_repo")
    sha = "6" * 40
    seen = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"sha": sha}

    class FakeClient:
        async def get(self, url, headers=None):
            seen["url"] = url
            seen["headers"] = headers
            return FakeResponse()

    result = await mod.fetch_head_commit_sha(
        "octo",
        "docs",
        client=FakeClient(),
        ref="release/1.x",
    )

    assert result == sha
    assert seen["url"].endswith("/repos/octo/docs/commits/release%2F1.x")


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

    _patch_head(monkeypatch, mod, fake_head)
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
async def test_index_repo_fast_path_backfills_legacy_source_repo_metadata(tmp_path, monkeypatch):
    mod = importlib.import_module("jdocmunch_mcp.tools.index_repo")
    sha = "1" * 40
    content = "# README\n\nLegacy certified content."
    store = DocStore(base_path=str(tmp_path))
    store.save_index(
        "octo",
        "docs",
        parse_file(content, "README.md", "octo/docs"),
        {"README.md": content},
        {".md": 1},
        head_sha=sha,
        sha_certified=True,
    )

    async def fake_head(owner, repo, token=None, client=None, ref="HEAD"):
        return sha

    async def fake_tree(owner, repo, token=None, client=None, ref="HEAD"):
        raise AssertionError("matching certified legacy index should stay on SHA fast path")

    _patch_head(monkeypatch, mod, fake_head)
    monkeypatch.setattr(mod, "fetch_repo_tree", fake_tree)

    result = await mod.index_repo(
        "octo/docs",
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(tmp_path),
    )

    assert result["success"] is True
    assert result["message"] == "No changes detected (HEAD SHA unchanged)"
    assert result["source_repo"] == "octo/docs"
    assert result["source_repo_at_sha"] == f"octo/docs@{sha}"

    listed = list_repos(storage_path=str(tmp_path))
    assert listed["repos"][0]["source_repo"] == "octo/docs"
    assert listed["repos"][0]["source_repo_at_sha"] == f"octo/docs@{sha}"


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

    _patch_head(monkeypatch, mod, fake_head)
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

    _patch_head(monkeypatch, mod, fake_head)
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
async def test_index_repo_ref_fast_path_uses_requested_ref(tmp_path, monkeypatch):
    mod = importlib.import_module("jdocmunch_mcp.tools.index_repo")
    sha = "7" * 40
    content = "# README\n\nCertified ref content."
    store = DocStore(base_path=str(tmp_path))
    store.save_index(
        "octo",
        "docs_v1",
        parse_file(content, "README.md", "octo/docs_v1"),
        {"README.md": content},
        {".md": 1},
        head_sha=sha,
        sha_certified=True,
        source_repo="octo/docs",
    )

    async def fake_head(owner, repo, token=None, client=None, ref="HEAD"):
        assert (owner, repo, ref) == ("octo", "docs", "v1.2.3")
        return sha

    async def fake_tree(owner, repo, token=None, client=None, ref="HEAD"):
        raise AssertionError("matching certified ref index should stay on SHA fast path")

    _patch_head(monkeypatch, mod, fake_head)
    monkeypatch.setattr(mod, "fetch_repo_tree", fake_tree)

    result = await mod.index_repo(
        "octo/docs",
        ref="v1.2.3",
        name="docs_v1",
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(tmp_path),
    )

    assert result["success"] is True
    assert result["message"] == "No changes detected (resolved ref SHA unchanged)"
    assert result["repo"] == "octo/docs_v1"
    assert result["repo_at_sha"] == f"octo/docs_v1@{sha}"
    assert result["source_repo_at_sha"] == f"octo/docs@{sha}"


@pytest.mark.asyncio
async def test_index_repo_custom_name_same_storage_different_source_does_not_fast_path(tmp_path, monkeypatch):
    mod = importlib.import_module("jdocmunch_mcp.tools.index_repo")
    sha = "2" * 40
    tree_repos = []

    async def fake_head(owner, repo, token=None, client=None, ref="HEAD"):
        return sha

    async def fake_tree(owner, repo, token=None, client=None, ref="HEAD"):
        tree_repos.append(repo)
        return [{"type": "blob", "path": "README.md", "size": 64}]

    async def fake_gitignore(owner, repo, token=None, client=None, ref="HEAD"):
        return None

    async def fake_content(owner, repo, path, token=None, client=None, ref="HEAD"):
        return f"# README\n\nContent from {repo}."

    _patch_head(monkeypatch, mod, fake_head)
    monkeypatch.setattr(mod, "fetch_repo_tree", fake_tree)
    monkeypatch.setattr(mod, "fetch_gitignore", fake_gitignore)
    monkeypatch.setattr(mod, "fetch_file_content", fake_content)

    first = await mod.index_repo(
        "octo/docs",
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(tmp_path),
        name="shared",
    )
    second = await mod.index_repo(
        "octo/other",
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(tmp_path),
        name="shared",
    )

    assert first["success"] is True
    assert second["success"] is True
    assert second["repo"] == "octo/shared"
    assert second["source_repo"] == "octo/other"
    assert second["source_repo_at_sha"] == f"octo/other@{sha}"
    assert tree_repos == ["docs", "other"]

    store = DocStore(base_path=str(tmp_path))
    stored = store.load_index("octo", "shared")
    assert stored is not None
    assert stored.source_repo == "octo/other"
    assert "Content from other." in (tmp_path / "octo" / "shared" / "README.md").read_text()


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

    _patch_head(monkeypatch, mod, fake_head)
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

    store = DocStore(base_path=str(tmp_path))
    assert store.load_index("octo", "docs") is None
    stored = store.load_index("octo", "docs_changed")
    assert stored is not None
    assert "Second content." in (tmp_path / "octo" / "docs_changed" / "README.md").read_text()


@pytest.mark.asyncio
async def test_index_repo_moved_ref_with_unchanged_docs_updates_sha_metadata(tmp_path, monkeypatch):
    mod = importlib.import_module("jdocmunch_mcp.tools.index_repo")
    state = {"sha": "8" * 40}
    tree_refs = []

    async def fake_head(owner, repo, token=None, client=None, ref="HEAD"):
        assert ref == "release/1.x"
        return state["sha"]

    async def fake_tree(owner, repo, token=None, client=None, ref="HEAD"):
        tree_refs.append(ref)
        return [{"type": "blob", "path": "README.md", "size": 64}]

    async def fake_gitignore(owner, repo, token=None, client=None, ref="HEAD"):
        return None

    async def fake_content(owner, repo, path, token=None, client=None, ref="HEAD"):
        return "# README\n\nBranch content unchanged."

    _patch_head(monkeypatch, mod, fake_head)
    monkeypatch.setattr(mod, "fetch_repo_tree", fake_tree)
    monkeypatch.setattr(mod, "fetch_gitignore", fake_gitignore)
    monkeypatch.setattr(mod, "fetch_file_content", fake_content)

    first = await mod.index_repo(
        "octo/docs",
        ref="release/1.x",
        name="docs_release",
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(tmp_path),
    )
    state["sha"] = "9" * 40
    second = await mod.index_repo(
        "octo/docs",
        ref="release/1.x",
        name="docs_release",
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(tmp_path),
    )

    assert first["success"] is True
    assert second["success"] is True
    assert second["message"] == "No changes detected"
    assert second["changed"] == 0
    assert second["new"] == 0
    assert second["deleted"] == 0
    assert second["head_sha"] == "9" * 40
    assert second["repo_at_sha"] == f"octo/docs_release@{'9' * 40}"
    assert second["source_repo_at_sha"] == f"octo/docs@{'9' * 40}"
    assert tree_refs == ["8" * 40, "9" * 40]

    store = DocStore(base_path=str(tmp_path))
    stored = store.load_index("octo", "docs_release")
    assert stored is not None
    assert stored.head_sha == "9" * 40


@pytest.mark.asyncio
async def test_index_repo_custom_name_fallback_to_head_is_not_certified(tmp_path, monkeypatch):
    mod = importlib.import_module("jdocmunch_mcp.tools.index_repo")

    async def fake_head(owner, repo, token=None, client=None, ref="HEAD"):
        return None

    async def fake_tree(owner, repo, token=None, client=None, ref="HEAD"):
        return [{"type": "blob", "path": "README.md", "size": 64}]

    async def fake_gitignore(owner, repo, token=None, client=None, ref="HEAD"):
        return None

    async def fake_content(owner, repo, path, token=None, client=None, ref="HEAD"):
        return "# README\n\nUncertified custom content."

    _patch_head(monkeypatch, mod, fake_head)
    monkeypatch.setattr(mod, "fetch_repo_tree", fake_tree)
    monkeypatch.setattr(mod, "fetch_gitignore", fake_gitignore)
    monkeypatch.setattr(mod, "fetch_file_content", fake_content)

    result = await mod.index_repo(
        "octo/docs",
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(tmp_path),
        name="docs_uncertified",
    )

    assert result["success"] is True
    assert result["repo"] == "octo/docs_uncertified"
    assert result["source_repo"] == "octo/docs"
    assert result["sha_certified"] is False
    assert "repo_at_sha" not in result
    assert "source_repo_at_sha" not in result

    store = DocStore(base_path=str(tmp_path))
    stored = store.load_index("octo", "docs_uncertified")
    assert stored is not None
    assert stored.source_repo == "octo/docs"
    assert stored.sha_certified is False

    strict = search_sections(
        repo=f"octo/docs_uncertified@{'3' * 40}",
        query="uncertified",
        storage_path=str(tmp_path),
    )
    assert strict["error"] == f"Repo not found: octo/docs_uncertified@{'3' * 40}"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_name", ["foo@bar", "foo/bar", "foo\\bar", "", "..", 123, ["x"], {"x": "y"}])
async def test_index_repo_custom_name_rejects_unsafe_storage_names(tmp_path, monkeypatch, bad_name):
    mod = importlib.import_module("jdocmunch_mcp.tools.index_repo")

    async def fake_head(owner, repo, token=None, client=None, ref="HEAD"):
        raise AssertionError("invalid name should fail before network fetch")

    _patch_head(monkeypatch, mod, fake_head)

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
    assert "ref" in tool.inputSchema["properties"]
    assert tool.inputSchema["properties"]["ref"]["type"] == "string"
    assert tool.inputSchema["required"] == ["url"]


@pytest.mark.asyncio
async def test_doc_index_repo_call_tool_passes_name_override(monkeypatch):
    srv = importlib.import_module("jdocmunch_mcp.server")
    seen = {}

    async def fake_index_repo(**kwargs):
        seen.update(kwargs)
        return {"success": True}

    monkeypatch.setattr(srv, "index_repo", fake_index_repo)

    response = await srv.call_tool(
        "doc_index_repo",
        {
            "url": "octo/docs",
            "ref": "v1.2.3",
            "name": "docs_v1",
            "use_ai_summaries": False,
            "use_embeddings": False,
            "incremental": False,
        },
    )

    assert json.loads(response[0].text)["success"] is True
    assert seen["url"] == "octo/docs"
    assert seen["ref"] == "v1.2.3"
    assert seen["name"] == "docs_v1"
    assert seen["use_ai_summaries"] is False
    assert seen["use_embeddings"] is False
    assert seen["incremental"] is False
