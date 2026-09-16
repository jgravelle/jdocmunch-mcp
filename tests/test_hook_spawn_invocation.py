"""The PostToolUse hook must spawn its reindex worker by a path it can reach.

A hook runs under Claude Code's minimal hook shell, whose PATH carries neither
``~/.local/bin`` nor a user venv nor a pipx shim -- which is the whole reason
``init._hook_invocation`` resolves an ABSOLUTE path before writing the hook
command into settings.json (#39). The hook itself therefore starts fine and its
BARE-NAME child spawn does not, on exactly the installs that needed the
absolute path. The failure is silent and permanent: the index quietly stops
tracking edits and every symptom points somewhere other than here.

Ported from jcodemunch-mcp, which hit this first.

Two properties are pinned, and the second is a security property, not a
robustness one:

  * the spawn prefix is never the bare name ``jdocmunch-mcp``;
  * a RELATIVE ``sys.argv[0]`` is NEVER executed. A relative path is
    re-resolved against the hook's cwd -- the checked-out, untrusted repo the
    agent is editing -- so a file named ``jdocmunch-mcp`` committed by a
    hostile contributor would otherwise be what we run. ``os.path.isabs`` is
    the check that stops it and it is load-bearing.
"""

import ast
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from jdocmunch_mcp.cli import hooks


# ---------------------------------------------------------------------------
# _self_invocation: which argv0 shapes are trusted
# ---------------------------------------------------------------------------

class TestSelfInvocation:
    """Unit coverage for the argv-prefix resolver."""

    _FALLBACK = [sys.executable, "-m", "jdocmunch_mcp"]

    def test_absolute_entry_point_is_preferred(self, tmp_path):
        exe = tmp_path / "jdocmunch-mcp"
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        with mock.patch.object(sys, "argv", [str(exe), "hook-posttooluse"]):
            assert hooks._self_invocation() == [str(exe)]

    def test_absolute_windows_exe_is_preferred(self, tmp_path):
        # The console script is `jdocmunch-mcp.exe` on Windows and was
        # `jdocmunch-mcp-script.py` under legacy setuptools; both must ride on
        # the same startswith check rather than needing an exact match.
        exe = tmp_path / "jdocmunch-mcp.exe"
        exe.write_bytes(b"MZ")
        with mock.patch.object(sys, "argv", [str(exe)]):
            assert hooks._self_invocation() == [str(exe)]

    def test_relative_argv0_is_never_executed(self, tmp_path, monkeypatch):
        """THE security case: a plant in the edited repo must not be run.

        The hook's cwd is the repository the agent is editing. A relative
        argv0 resolves against it, so this file -- which a pull request could
        add -- would become the thing the hook executes on every Edit.
        """
        plant = tmp_path / "jdocmunch-mcp"
        plant.write_text("#!/bin/sh\necho pwned\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        for relative in ("jdocmunch-mcp", "./jdocmunch-mcp", "../{}/jdocmunch-mcp".format(tmp_path.name)):
            with mock.patch.object(sys, "argv", [relative, "hook-posttooluse"]):
                resolved = hooks._self_invocation()
            assert resolved == self._FALLBACK, (
                f"relative argv0 {relative!r} was accepted; os.path.isabs "
                f"is the guard and it did not hold"
            )
            # Belt and braces: the plant must not appear anywhere in the argv.
            assert not any(str(plant) in part or part.endswith("jdocmunch-mcp") for part in resolved)

    def test_absolute_but_missing_falls_back(self, tmp_path):
        # A settings.json written by a since-uninstalled venv points at a path
        # that no longer exists; spawning it would raise where the fallback
        # simply works.
        ghost = tmp_path / "gone" / "jdocmunch-mcp"
        with mock.patch.object(sys, "argv", [str(ghost)]):
            assert hooks._self_invocation() == self._FALLBACK

    def test_unrelated_argv0_falls_back(self, tmp_path):
        # Under pytest, a test runner, or `python -m jdocmunch_mcp`, argv0 is
        # not this entry point at all. Re-executing it would be wrong.
        other = tmp_path / "pytest"
        other.write_text("", encoding="utf-8")
        with mock.patch.object(sys, "argv", [str(other)]):
            assert hooks._self_invocation() == self._FALLBACK

    def test_empty_argv0_falls_back(self):
        # Embedded interpreters leave argv0 empty; `or ""` must absorb a None
        # rather than raising inside a hook.
        for argv in ([""], [None]):
            with mock.patch.object(sys, "argv", argv):
                assert hooks._self_invocation() == self._FALLBACK


# ---------------------------------------------------------------------------
# End to end through the hook handler
# ---------------------------------------------------------------------------

class TestPostToolUseSpawnArgv:

    def _run(self, payload: dict, tmp_path) -> int:
        with mock.patch.dict(os.environ, {"DOC_INDEX_PATH": str(tmp_path)}):
            with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))):
                return hooks.run_posttooluse()

    def test_spawn_never_uses_the_bare_name(self, tmp_path):
        doc = tmp_path / "README.md"
        doc.write_text("hello", encoding="utf-8")
        with mock.patch("subprocess.Popen") as popen:
            assert self._run({"tool_input": {"file_path": str(doc)}}, tmp_path) == 0
        argv = popen.call_args[0][0]
        assert argv[0] != "jdocmunch-mcp", (
            "the hook handed a bare name to a hook shell whose PATH does not "
            "carry pipx/pip --user/venv entry points"
        )
        assert argv[-2:] == ["hook-reindex", str(doc.resolve())]

    def test_spawn_argv_is_executable_as_written(self, tmp_path):
        """argv[0] must be something a spawn can actually find.

        Either an absolute path that exists, or the running interpreter -- not
        a name whose resolution depends on a PATH we do not control.
        """
        doc = tmp_path / "README.md"
        doc.write_text("hello", encoding="utf-8")
        with mock.patch("subprocess.Popen") as popen:
            self._run({"tool_input": {"file_path": str(doc)}}, tmp_path)
        argv0 = popen.call_args[0][0][0]
        assert os.path.isabs(argv0) and os.path.exists(argv0), argv0

    def test_spawn_failure_leaves_a_breadcrumb_when_logging_is_on(self, tmp_path):
        # A permanently failing spawn used to be invisible. The breadcrumb is
        # opt-in, so the default remains as silent as it was.
        doc = tmp_path / "README.md"
        doc.write_text("hello", encoding="utf-8")
        env = {"DOC_INDEX_PATH": str(tmp_path), "JDOCMUNCH_HOOK_LOG": "1"}
        with mock.patch.dict(os.environ, env):
            with mock.patch("subprocess.Popen", side_effect=FileNotFoundError("nope")):
                with mock.patch("sys.stdin", io.StringIO(json.dumps({"tool_input": {"file_path": str(doc)}}))):
                    assert hooks.run_posttooluse() == 0
        log = tmp_path / "_hooks" / "reindex.log"
        assert log.is_file(), "spawn failure recorded nothing"
        assert "spawn failed" in log.read_text(encoding="utf-8")

    def test_a_failed_spawn_does_not_roll_back_the_debounce_stamp(self, tmp_path):
        """Deliberate: a PATH/exec failure is permanent, so retrying sooner buys
        nothing, and the one transient failure -- spawn refused under memory
        pressure -- is the condition the throttle exists to stop us worsening.

        Pinned so a "tidy" rollback in the except clause turns this red.
        """
        doc = tmp_path / "README.md"
        doc.write_text("hello", encoding="utf-8")
        env = {"DOC_INDEX_PATH": str(tmp_path),
               "JDOCMUNCH_HOOK_DEBOUNCE_SECONDS": "3600"}
        with mock.patch.dict(os.environ, env):
            with mock.patch("subprocess.Popen", side_effect=OSError("refused")):
                with mock.patch("sys.stdin", io.StringIO(json.dumps({"tool_input": {"file_path": str(doc)}}))):
                    assert hooks.run_posttooluse() == 0
            stamps = list((tmp_path / "_hooks" / "debounce").iterdir())
            assert len(stamps) == 1, "the failed spawn left no debounce stamp"
            # ...so the very next edit inside the window is coalesced.
            assert hooks._should_reindex(str(doc.resolve())) is False

    def test_spawn_failure_is_silent_by_default(self, tmp_path):
        doc = tmp_path / "README.md"
        doc.write_text("hello", encoding="utf-8")
        env = {"DOC_INDEX_PATH": str(tmp_path)}
        env.pop("JDOCMUNCH_HOOK_LOG", None)
        with mock.patch.dict(os.environ, env):
            os.environ.pop("JDOCMUNCH_HOOK_LOG", None)
            with mock.patch("subprocess.Popen", side_effect=OSError("nope")):
                with mock.patch("sys.stdin", io.StringIO(json.dumps({"tool_input": {"file_path": str(doc)}}))):
                    assert hooks.run_posttooluse() == 0
        assert not (tmp_path / "_hooks" / "reindex.log").exists()


# ---------------------------------------------------------------------------
# The fallback has to be real
# ---------------------------------------------------------------------------

class TestFallbackIsReachable:
    """`python -m jdocmunch_mcp` must reach the same subcommand table.

    A fallback that is merely plausible is worse than none: it converts a
    loud failure into the same silent one, one layer down. `__main__`
    dispatches through `cli.main:main` for the watch service; this pins that
    the reindex worker is reachable the same way.
    """

    def test_main_module_exists(self):
        import importlib
        assert importlib.import_module("jdocmunch_mcp.__main__") is not None

    def test_module_entry_point_lists_hook_reindex(self):
        proc = subprocess.run(
            [sys.executable, "-m", "jdocmunch_mcp", "--help"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr
        assert "hook-reindex" in proc.stdout

    def test_module_entry_point_dispatches_hook_reindex(self, tmp_path):
        # A non-doc extension makes the worker return at its extension check
        # before loading any index, so this proves DISPATCH (an unknown
        # subcommand would exit 2 from argparse) without paying for a reindex.
        proc = subprocess.run(
            [sys.executable, "-m", "jdocmunch_mcp", "hook-reindex", str(tmp_path / "app.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120,
            env={**os.environ, "DOC_INDEX_PATH": str(tmp_path)},
        )
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)


# ---------------------------------------------------------------------------
# Ratchet
# ---------------------------------------------------------------------------

_HOOKS_SRC = Path(hooks.__file__)

# Calls that start a process. `subprocess.X` and a bare `X` imported from it,
# and every `os.exec*` / `os.spawn*`.
_SUBPROCESS_SPAWNERS = {"Popen", "run", "call", "check_call", "check_output"}
_BARE_NAME = "jdocmunch-mcp"


def _is_spawn_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.value.id == "subprocess" and func.attr in _SUBPROCESS_SPAWNERS:
            return True
        if func.value.id == "os" and func.attr.startswith(("exec", "spawn")):
            return True
    return isinstance(func, ast.Name) and func.id in _SUBPROCESS_SPAWNERS


def _names_the_bare_command(node: ast.AST) -> bool:
    """Does this expression contain the literal command name anywhere?

    A string literal that IS the name, or a shell-style string that starts with
    it; f-string pieces are visited too, so `f"jdocmunch-mcp {x}"` counts.
    """
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            if sub.value == _BARE_NAME or sub.value.startswith(_BARE_NAME + " "):
                return True
    return False


def _spawn_calls(source: str) -> list:
    """Every process-starting call in `source`, as (lineno, call node)."""
    return sorted(
        {n.lineno: n for n in ast.walk(ast.parse(source))
         if isinstance(n, ast.Call) and _is_spawn_call(n)}.items()
    )


def _bare_name_spawns(source: str) -> list:
    """Line numbers of spawns whose arguments name the bare command.

    "Name" means the literal appears in the arguments in any shape: a list
    literal, a concatenation, an f-string, or a variable bound IN THE SAME
    FUNCTION to an expression containing it. The old check looked only at a
    `Popen` whose first argument was a list literal starting with the name, so
    `["jdocmunch-mcp"] + rest`, `cmd = [...]; Popen(cmd)` and
    `subprocess.run([...])` all passed it.
    """
    tree = ast.parse(source)
    scopes = [tree] + [n for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    offenders = set()
    for scope in scopes:
        bound = set()
        for stmt in ast.walk(scope):
            if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                value = stmt.value
                if value is not None and _names_the_bare_command(value):
                    targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                    bound.update(t.id for t in targets if isinstance(t, ast.Name))
        for call in (n for n in ast.walk(scope) if isinstance(n, ast.Call) and _is_spawn_call(n)):
            args = list(call.args) + [k.value for k in call.keywords]
            if any(_names_the_bare_command(a) for a in args):
                offenders.add(call.lineno)
            elif bound and any(isinstance(n, ast.Name) and n.id in bound
                               for a in args for n in ast.walk(a)):
                offenders.add(call.lineno)
    return sorted(offenders)


class TestNoBareNameSpawn:

    def test_at_least_one_spawn_is_present(self):
        # Without this the ratchet below passes by finding nothing -- a green
        # ratchet and an absent ratchet look identical.
        assert _spawn_calls(_HOOKS_SRC.read_text(encoding="utf-8")), (
            "no process-starting call found in cli/hooks.py to check"
        )

    def test_no_spawn_in_the_hook_module_names_the_bare_command(self):
        """Every spawn's argv must start with a RESOLVED prefix.

        A literal command name is a PATH lookup by another name, and the hook
        shell's PATH is the thing that does not work.
        """
        offenders = _bare_name_spawns(_HOOKS_SRC.read_text(encoding="utf-8"))
        assert not offenders, (
            "cli/hooks.py spawns a child by literal name at line(s) "
            + ", ".join(map(str, offenders))
            + " -- use _self_invocation() so the child is reachable from a "
              "hook shell's minimal PATH"
        )

    @pytest.mark.parametrize("source", [
        'import subprocess\nsubprocess.Popen(["jdocmunch-mcp", "hook-reindex", p])',
        'import subprocess\nsubprocess.Popen(["jdocmunch-mcp"] + rest)',
        'import subprocess\nsubprocess.Popen(("jdocmunch-mcp",) + tuple(rest))',
        'import subprocess\ndef f():\n    cmd = ["jdocmunch-mcp", "x"]\n    subprocess.Popen(cmd)',
        'import subprocess\ndef f():\n    cmd = ["jdocmunch-mcp"]\n    cmd += ["x"]\n    subprocess.Popen(cmd + [p])',
        'import subprocess\nsubprocess.run(["jdocmunch-mcp", "x"])',
        'import subprocess\nsubprocess.check_output(["jdocmunch-mcp", "x"])',
        'import subprocess\nsubprocess.call("jdocmunch-mcp hook-reindex x", shell=True)',
        'import subprocess\nsubprocess.Popen(f"jdocmunch-mcp hook-reindex {p}", shell=True)',
        'from subprocess import Popen\nPopen(["jdocmunch-mcp"])',
        'import os\nos.execv("/bin/x", ["jdocmunch-mcp", "x"])',
        'import os\nos.spawnvp(os.P_NOWAIT, "jdocmunch-mcp", ["jdocmunch-mcp"])',
        'import subprocess\nsubprocess.Popen(args=["jdocmunch-mcp"])',
    ])
    def test_the_guard_flags_every_shape_of_the_defect(self, source):
        """Run against the defect put back: each spelling of the bare name."""
        assert _bare_name_spawns(source), source

    @pytest.mark.parametrize("source", [
        'import subprocess, sys\nsubprocess.Popen([sys.executable, "-m", "jdocmunch_mcp", "hook-reindex", p])',
        'import subprocess\nsubprocess.Popen(_self_invocation() + ["hook-reindex", p])',
        'import subprocess\ndef f():\n    cmd = [sys.executable, "-m", "jdocmunch_mcp"]\n    subprocess.Popen(cmd + [p])',
        # The name appears, but not as a command being spawned.
        'import subprocess\nbase = "jdocmunch-mcp"\nsubprocess.Popen([sys.executable, "-c", "pass"])',
        'import os\nos.path.basename(p).startswith("jdocmunch-mcp")',
    ])
    def test_the_guard_does_not_flag_the_correct_shape(self, source):
        assert not _bare_name_spawns(source), source

    def test_self_invocation_keeps_its_isabs_guard(self):
        """The security check must not be simplified away.

        `exists()` alone is not equivalent: a planted file in the edited repo
        exists. Only `isabs` separates "the path init wrote" from "a name
        resolved against untrusted cwd".
        """
        src = _HOOKS_SRC.read_text(encoding="utf-8")
        start = src.index("def _self_invocation")
        end = src.index("def run_posttooluse")
        body = src[start:end]
        assert "os.path.isabs(" in body, (
            "_self_invocation lost its isabs guard; a relative argv0 would be "
            "re-resolved against the untrusted repo the hook is editing"
        )


@pytest.mark.parametrize("bad", ["jdocmunch-mcp", "./jdocmunch-mcp"])
def test_relative_names_are_rejected_parametrized(bad, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "jdocmunch-mcp").write_text("", encoding="utf-8")
    with mock.patch.object(sys, "argv", [bad]):
        assert hooks._self_invocation() == [sys.executable, "-m", "jdocmunch_mcp"]
