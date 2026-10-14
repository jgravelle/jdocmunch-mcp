"""#129: the PreToolUse hint must travel on the one channel the model receives.

From 1.66.3 through 1.139.1 ``run_pretooluse`` printed its steering hint to
stderr and exited 0. Claude Code sends an exit-0 hook's stderr to the debug
log only; the model never saw it, so registering the hook cost an interpreter
start per matching Read and changed nothing. The only model-facing route for
an exit-0 PreToolUse hook is ``hookSpecificOutput.additionalContext`` on
stdout (plain stdout is fed back only on prompt/session events; top-level
``systemMessage`` surfaces to the user).

The behavioural tests pin the JSON shape. The ratchet reads the source, so it
fails if someone restores ``file=sys.stderr`` inside ``run_pretooluse`` while
the JSON tests still pass because a second print was added beside it.
"""

from __future__ import annotations

import ast
import inspect
import io
import json
from unittest import mock

import pytest

from jdocmunch_mcp.cli import hooks
from jdocmunch_mcp.cli.hooks import run_pretooluse


def _run(payload: dict) -> int:
    with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))):
        return run_pretooluse()


@pytest.fixture
def big(tmp_path, monkeypatch):
    """A large doc file that is indexed and current.

    The hint is gated on the file being in an index that is current for it;
    an unindexed file is correctly silent, so a bare file would test nothing.
    """
    from jdocmunch_mcp.tools.index_local import index_local
    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir()
    p = src / "big.md"
    p.write_text("x" * 5000)
    index_local(path=str(src), name="hintprobe", use_ai_summaries=False,
                use_embeddings=False, storage_path=str(store))
    monkeypatch.setenv("DOC_INDEX_PATH", str(store))
    return p


class TestHintChannel:
    def test_hint_is_additional_context_json_on_stdout(self, big, capsys):
        assert _run({"tool_input": {"file_path": str(big)}}) == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        out = payload["hookSpecificOutput"]
        assert out["hookEventName"] == "PreToolUse"
        assert out["additionalContext"].startswith("jDocMunch hint: this is a 5,000-byte doc file,")
        assert "search_sections" in out["additionalContext"]

    def test_nothing_on_stderr(self, big, capsys):
        _run({"tool_input": {"file_path": str(big)}})
        assert capsys.readouterr().err == ""

    def test_no_permission_decision_so_the_read_is_still_allowed(self, big, capsys):
        """Advisory only: a hard deny breaks Read-before-Edit."""
        _run({"tool_input": {"file_path": str(big)}})
        payload = json.loads(capsys.readouterr().out)
        assert "permissionDecision" not in payload["hookSpecificOutput"]
        assert "decision" not in payload
        assert "systemMessage" not in payload

    def test_small_file_emits_nothing(self, tmp_path, capsys):
        p = tmp_path / "small.md"
        p.write_text("hi")
        _run({"tool_input": {"file_path": str(p)}})
        assert capsys.readouterr().out == ""


class TestRatchet:
    def test_run_pretooluse_never_writes_to_stderr(self):
        """Source-level guard: no ``print(..., file=sys.stderr)`` in run_pretooluse.

        Proven non-vacuous by restoring the 1.139.1 print and watching it fire.
        """
        src = inspect.getsource(run_pretooluse)
        tree = ast.parse(src)
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "print":
                for kw in node.keywords:
                    if kw.arg == "file" and ast.unparse(kw.value) == "sys.stderr":
                        offenders.append(node.lineno)
        assert not offenders, f"run_pretooluse prints to stderr at source lines {offenders} (#129)"

    def test_emitter_matches_jcodemunch_shape(self):
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            assert hooks._emit_additional_context("PreToolUse", "hi") == 0
        assert json.loads(buf.getvalue()) == {
            "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "hi"}
        }
