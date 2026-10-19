"""v1.85.0 — jdoc#65 + jdoc#66 (both reported by @mmashwani).

#65: the JSON-producing CLI commands (`index-file`, `index-local`) computed
their result — including embedding-provider initialization — before printing
JSON, with no stdout guard. The serve path already redirects provider chatter to
stderr (jdoc#19); the CLI paths now do the same so stdout stays parseable JSON.

#66: `hook-precompact` listed every indexed doc repo plus absolute source roots
and ignored the `cwd` hint. The snapshot is now cwd-scoped, capped + summarized,
and hides absolute source roots by default (opt back in with
JDOCMUNCH_HOOK_INCLUDE_SOURCE_ROOTS=1).
"""

from __future__ import annotations

import io
import json
from unittest import mock

import pytest


def _repos(*items):
    return {"repos": list(items), "count": len(items)}


# --------------------------------------------------------------------------- #
# jdoc#65 — JSON CLI commands guard stdout during provider init                #
# --------------------------------------------------------------------------- #

class TestCliStdoutGuard:
    def test_index_file_keeps_stdout_clean_json(self, capsys):
        from jdocmunch_mcp.server import main

        def fake_cli(file, name=None, ocr=None):
            # Simulate sentence-transformers / HF Hub chatter to stdout.
            print("Warning: unauthenticated HF Hub request; Loading weights 100%")
            return {"success": True, "file": file, "sections": 1}

        with mock.patch("jdocmunch_mcp.tools.index_file.index_file_cli", side_effect=fake_cli):
            with pytest.raises(SystemExit) as exc:
                main(["index-file", "doc.md"])
        assert exc.value.code == 0

        captured = capsys.readouterr()
        payload = json.loads(captured.out)  # must parse — no chatter on stdout
        assert payload["success"] is True
        assert "HF Hub" not in captured.out
        assert "HF Hub" in captured.err

    def test_index_local_keeps_stdout_clean_json(self, capsys, tmp_path):
        from jdocmunch_mcp.server import main

        def fake_local(path, name=None, paths=None, incremental=True, **kwargs):
            print("Loading weights: 100%|##########|")
            return {"success": True, "files_indexed": 0}

        with mock.patch("jdocmunch_mcp.tools.index_local.index_local", side_effect=fake_local):
            main(["index-local", "--path", str(tmp_path)])

        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["success"] is True
        assert "Loading weights" not in captured.out
        assert "Loading weights" in captured.err


# --------------------------------------------------------------------------- #
# jdoc#66 — PreCompact snapshot is focused + path-safe                         #
# --------------------------------------------------------------------------- #

class TestPrecompactSnapshotScoping:
    def test_scopes_to_cwd_and_summarizes_rest(self, tmp_path):
        from jdocmunch_mcp.cli.hooks import _build_snapshot
        a = tmp_path / "proj-alpha"; a.mkdir()
        b = tmp_path / "proj-beta"; b.mkdir()
        repos = _repos(
            {"name": "alpha-docs", "section_count": 10, "doc_count": 2, "source_root": str(a)},
            {"name": "beta-docs", "section_count": 20, "doc_count": 3, "source_root": str(b)},
        )
        with mock.patch("jdocmunch_mcp.tools.list_repos.list_repos", return_value=repos):
            snap = _build_snapshot(cwd=str(a / "subdir"))

        assert "Current workspace doc indexes" in snap
        assert "alpha-docs" in snap
        assert "beta-docs" not in snap
        assert "1 omitted" in snap
        assert "doc_list_repos" in snap
        # path safety: absolute source root not leaked by default
        assert str(a) not in snap

    def test_no_cwd_match_falls_back_to_inventory(self, tmp_path):
        from jdocmunch_mcp.cli.hooks import _build_snapshot
        a = tmp_path / "a"; a.mkdir()
        repos = _repos({"name": "x-docs", "section_count": 1, "doc_count": 1, "source_root": str(a)})
        with mock.patch("jdocmunch_mcp.tools.list_repos.list_repos", return_value=repos):
            snap = _build_snapshot(cwd=str(tmp_path / "unrelated"))
        assert "Indexed doc repos:" in snap
        assert "Current workspace" not in snap
        assert "x-docs" in snap

    def test_source_roots_hidden_by_default(self, tmp_path, monkeypatch):
        from jdocmunch_mcp.cli.hooks import _build_snapshot
        monkeypatch.delenv("JDOCMUNCH_HOOK_INCLUDE_SOURCE_ROOTS", raising=False)
        secret = tmp_path / "secret-path"
        repos = _repos({"name": "d", "section_count": 3, "doc_count": 1, "source_root": str(secret)})
        with mock.patch("jdocmunch_mcp.tools.list_repos.list_repos", return_value=repos):
            snap = _build_snapshot(cwd=None)
        assert "secret-path" not in snap

    def test_source_roots_shown_with_optin(self, tmp_path, monkeypatch):
        from jdocmunch_mcp.cli.hooks import _build_snapshot
        monkeypatch.setenv("JDOCMUNCH_HOOK_INCLUDE_SOURCE_ROOTS", "1")
        shown = tmp_path / "shown-path"
        repos = _repos({"name": "d", "section_count": 3, "doc_count": 1, "source_root": str(shown)})
        with mock.patch("jdocmunch_mcp.tools.list_repos.list_repos", return_value=repos):
            snap = _build_snapshot(cwd=None)
        assert "shown-path" in snap

    def test_caps_to_three_and_summarizes(self):
        from jdocmunch_mcp.cli.hooks import _build_snapshot
        repos = _repos(*[
            {"name": f"repo{i}", "section_count": i, "doc_count": i} for i in range(6)
        ])
        with mock.patch("jdocmunch_mcp.tools.list_repos.list_repos", return_value=repos):
            snap = _build_snapshot(cwd=None)
        assert snap.count("- **repo") == 3
        assert "3 omitted" in snap

    def test_run_sessionstart_threads_cwd(self, tmp_path, capsys):
        """Was run_precompact until #131 moved the snapshot to SessionStart."""
        from jdocmunch_mcp.cli.hooks import run_sessionstart
        a = tmp_path / "proj"; a.mkdir()
        repos = _repos(
            {"name": "matched", "section_count": 5, "doc_count": 2, "source_root": str(a)},
            {"name": "unmatched", "section_count": 9, "doc_count": 3, "source_root": str(tmp_path / "elsewhere")},
        )
        stdin = json.dumps({"cwd": str(a), "source": "compact"})
        with mock.patch("sys.stdin", io.StringIO(stdin)):
            with mock.patch("jdocmunch_mcp.tools.list_repos.list_repos", return_value=repos):
                assert run_sessionstart() == 0
        msg = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
        assert "matched" in msg
        assert "unmatched" not in msg

    def test_invalid_stdin_never_blocks(self):
        from jdocmunch_mcp.cli.hooks import run_precompact
        with mock.patch("sys.stdin", io.StringIO("not json")):
            assert run_precompact() == 0
