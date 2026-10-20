"""Tests for the jdoc#78 doc watcher (`watch` daemon + watch-install service)."""
import asyncio
import os

import pytest

from jdocmunch_mcp import watch as watch_mod
from jdocmunch_mcp import service_installer
from jdocmunch_mcp.tools.get_watch_status import get_watch_status


def _make_index(tmp_path):
    """Index a small local doc folder and return (docs_dir, storage_path)."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# Guide\n\nHello world.\n", encoding="utf-8")
    storage = tmp_path / "store"
    from jdocmunch_mcp.tools.index_local import index_local
    result = index_local(
        path=str(docs),
        storage_path=str(storage),
        use_ai_summaries=False,
    )
    assert result.get("success") or result.get("sections") or result.get("repo"), result
    return str(docs), str(storage)


class TestDiscovery:
    def test_discovers_local_doc_repo(self, tmp_path):
        docs, storage = _make_index(tmp_path)
        found = watch_mod.discover_local_doc_repos(storage_path=storage)
        assert len(found) == 1
        root, name = found[0]
        assert os.path.normcase(root) == os.path.normcase(os.path.realpath(docs))
        assert name  # a durable repo handle

    def test_skips_missing_source_root(self, tmp_path, monkeypatch):
        # A row with a source_root that no longer exists must be dropped.
        fake = {"repos": [{"repo": "local/gone", "source_root": str(tmp_path / "nope")}]}
        monkeypatch.setattr(
            "jdocmunch_mcp.tools.list_repos.list_repos",
            lambda storage_path=None: fake,
        )
        assert watch_mod.discover_local_doc_repos(storage_path="x") == []

    def test_skips_github_index_without_source_root(self, tmp_path, monkeypatch):
        fake = {"repos": [{"repo": "octo/docs", "source_root": ""}]}
        monkeypatch.setattr(
            "jdocmunch_mcp.tools.list_repos.list_repos",
            lambda storage_path=None: fake,
        )
        assert watch_mod.discover_local_doc_repos(storage_path="x") == []


class TestWatchFilter:
    def test_accepts_doc_extensions_rejects_others(self, tmp_path):
        exts = watch_mod._doc_extensions()
        f = watch_mod._make_watch_filter(exts, str(tmp_path / "store"))
        assert f(None, "/repo/readme.md") is True
        assert f(None, "/repo/guide.RST") is True  # case-insensitive
        assert f(None, "/repo/main.py") is False
        assert f(None, "/repo/image.png") is False

    def test_rejects_storage_tree(self, tmp_path):
        exts = watch_mod._doc_extensions()
        store = str(tmp_path / "store")
        f = watch_mod._make_watch_filter(exts, store)
        inside = os.path.join(store, "local", "x.md")
        assert f(None, inside) is False


class TestOwningRoot:
    def test_longest_prefix_wins(self):
        roots = {"/a": "ra", "/a/b": "rab"}
        assert watch_mod._owning_root(os.path.normpath("/a/b/c.md"), roots) == "/a/b"
        assert watch_mod._owning_root(os.path.normpath("/a/x.md"), roots) == "/a"
        assert watch_mod._owning_root(os.path.normpath("/other/x.md"), roots) is None


class TestHandleChanges:
    @pytest.mark.asyncio
    async def test_groups_and_reindexes_by_owning_root(self, tmp_path, monkeypatch):
        calls = []

        def fake_index_local(**kwargs):
            calls.append(kwargs)
            return {"success": True}

        monkeypatch.setattr(
            "jdocmunch_mcp.tools.index_local.index_local", fake_index_local
        )
        # jdoc#189: batches run in a child process by default, where this stub
        # can't reach. Grouping is the subject here, so use the in-process path.
        monkeypatch.setenv("JDOCMUNCH_WATCH_INPROCESS", "1")

        root = str(tmp_path)
        roots_map = {root: "local/mydocs"}
        changes = {
            (1, os.path.join(root, "a.md")),
            (2, os.path.join(root, "sub", "b.md")),
        }
        await watch_mod._handle_changes(
            changes, roots_map, "store", False, True, None
        )
        assert len(calls) == 1
        kw = calls[0]
        assert kw["path"] == root
        assert kw["name"] == "local/mydocs"
        assert kw["incremental"] is True
        assert len(kw["paths"]) == 2

    @pytest.mark.asyncio
    async def test_ignores_changes_outside_watched_roots(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "jdocmunch_mcp.tools.index_local.index_local",
            lambda **kw: calls.append(kw),
        )
        roots_map = {str(tmp_path / "watched"): "local/x"}
        changes = {(1, str(tmp_path / "elsewhere" / "z.md"))}
        await watch_mod._handle_changes(changes, roots_map, "s", False, True, None)
        assert calls == []


class TestServiceInstaller:
    def test_exec_cmd_invokes_watch(self):
        cmd = service_installer._exec_cmd()
        assert cmd[1:] == ["-m", "jdocmunch_mcp", "watch"]

    def test_names(self):
        assert service_installer.SERVICE_NAME == "jdocmunch-watch"
        assert service_installer.LAUNCHD_LABEL.endswith("jdocmunch-watch")

    def test_status_does_not_raise(self):
        # status probing must never raise regardless of host state.
        out = service_installer.service_status()
        assert "active" in out or "platform" in out


class TestGetWatchStatus:
    def test_reports_repo_coverage(self, tmp_path):
        docs, storage = _make_index(tmp_path)
        out = get_watch_status(storage_path=storage)
        assert out["local_repo_count"] == 1
        assert out["watchable_repo_count"] == 1
        assert out["repos"][0]["watchable"] is True
        assert "service" in out and "installed_active" in out["service"]

    def test_honest_empty(self, tmp_path):
        out = get_watch_status(storage_path=str(tmp_path / "empty"))
        assert out["local_repo_count"] == 0
        assert out["repos"] == []


class TestToolRegistration:
    @pytest.mark.asyncio
    async def test_get_watch_status_is_registered_read_only(self):
        from jdocmunch_mcp import server
        tools = await server.list_tools()
        names = {t.name for t in tools}
        assert "get_watch_status" in names
        assert "get_watch_status" not in server._NON_READONLY_TOOLS


class TestCLI:
    def test_watch_status_cli_prints_json(self, tmp_path, monkeypatch, capsys):
        docs, storage = _make_index(tmp_path)
        monkeypatch.setenv("DOC_INDEX_PATH", storage)
        from jdocmunch_mcp.server import main
        main(["watch-status"])
        out = capsys.readouterr().out
        import json
        payload = json.loads(out)
        assert payload["local_repo_count"] == 1
