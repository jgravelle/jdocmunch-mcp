"""`python -m jdocmunch_mcp` must dispatch exactly like the console script.

The console script points at `jdocmunch_mcp.cli.main:main`, which keeps the MCP
SDK out of every process that is not `serve`. `python -m jdocmunch_mcp` is the
PostToolUse hook's spawn fallback (`hooks._self_invocation`) and the watch login
service's launcher, so if `__main__` imported `.server` instead it would load
the SDK on exactly those paths while the console script did not.

These run a REAL `-m` subprocess. An in-process import of `__main__` would not
show it: by then the test process may have loaded the SDK for another test.
"""

import os
import subprocess
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / "src")

# Top-level modules only the `serve` path has any business loading.
_SDK_TOP_LEVEL = ("mcp", "httpx", "anyio", "jsonschema")


def _run_module(*args: str, stdin: str = "") -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": SRC}
    return subprocess.run(
        [sys.executable, "-X", "importtime", "-m", "jdocmunch_mcp", *args],
        input=stdin, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, timeout=120,
    )


def _imported_top_level(stderr: str) -> set:
    """Top-level module names from `-X importtime` output.

    Each import line is `import time:  <self> | <cumulative> | <indent>name`.
    """
    names = set()
    for line in stderr.splitlines():
        if not line.startswith("import time:"):
            continue
        name = line.rsplit("|", 1)[-1].strip()
        if name and name != "imported":
            names.add(name.split(".")[0])
    return names


def test_dash_m_hook_pretooluse_does_not_load_the_mcp_sdk():
    r = _run_module("hook-pretooluse", stdin='{"tool_input": {"file_path": "x.py"}}')
    assert r.returncode == 0, r.stderr[-2000:]
    imported = _imported_top_level(r.stderr)
    assert "jdocmunch_mcp" in imported, "importtime output not parsed"
    leaked = sorted(set(_SDK_TOP_LEVEL) & imported)
    assert not leaked, (
        f"`python -m jdocmunch_mcp hook-pretooluse` imported {leaked}. "
        f"`__main__` must import `main` from `.cli.main`, not from `.server`."
    )


def test_dash_m_hook_posttooluse_does_not_load_the_mcp_sdk():
    r = _run_module("hook-posttooluse", stdin='{"tool_input": {"file_path": "x.py"}}')
    assert r.returncode == 0, r.stderr[-2000:]
    leaked = sorted(set(_SDK_TOP_LEVEL) & _imported_top_level(r.stderr))
    assert not leaked, f"`-m ... hook-posttooluse` imported {leaked}"


def test_dash_m_still_reaches_the_full_subcommand_table():
    """The other half: the fix must not cut `-m` down to a subset."""
    r = _run_module("--version")
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.strip(), "--version printed nothing"
    r = _run_module("--help")
    assert r.returncode == 0, r.stderr[-2000:]
    for command in ("serve", "hook-reindex", "watch"):
        assert command in r.stdout, f"{command!r} missing from `-m` --help"


def test_dash_m_main_is_the_console_script_main():
    src = (Path(SRC) / "jdocmunch_mcp" / "__main__.py").read_text(encoding="utf-8")
    assert "from .cli.main import main" in src
    assert "from .server" not in src
