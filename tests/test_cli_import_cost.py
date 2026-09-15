"""The CLI entry point must not drag the MCP SDK onto the hook path.

``pyproject``'s console script points at ``jdocmunch_mcp.cli.main:main``. It used
to point at ``server:main``, so every CLI and hook invocation imported the whole
MCP SDK before argparse had looked at the subcommand, on a path that never speaks
MCP.

These are ratchets, not measurements: they assert the SHAPE that makes the
saving possible, so a stray top-level import fails the suite instead of quietly
costing every hook fire a second. A timing assertion here would be flaky on a
loaded box and would not say what broke.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[1] / "src")

# Modules that only the `serve` path has any business loading.
_SDK_MODULES = ("mcp", "httpx", "jsonschema", "anyio")


def _run(code: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": SRC}
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True, text=True, env=env, timeout=120,
    )


@pytest.mark.parametrize("subcommand", ["hook-pretooluse", "hook-posttooluse"])
def test_hook_path_does_not_load_the_mcp_sdk(subcommand):
    """The ratchet. Proven non-vacuous by adding `import mcp.server` to
    cli/main.py's module scope and watching this fail, naming the leak."""
    payload = json.dumps({"tool_input": {"file_path": "nonexistent.md"}})
    r = _run(f"""
        import sys, io, json
        sys.stdin = io.StringIO({payload!r})
        import jdocmunch_mcp.cli.main as m
        try:
            m.main([{subcommand!r}])
        except SystemExit:
            pass
        leaked = [n for n in {_SDK_MODULES!r} if n in sys.modules]
        print("LEAKED=" + (",".join(leaked) or "NONE"))
    """)
    assert r.returncode == 0, r.stderr
    assert "LEAKED=NONE" in r.stdout, (
        f"{subcommand} pulled the MCP SDK onto the hook path: {r.stdout.strip()}. "
        f"Something gained a module-level import in jdocmunch_mcp/cli/main.py or "
        f"below it. Keep heavy imports inside the branch that needs them; the "
        f"`serve` branch is the only one that may import ..server."
    )


def test_importing_the_cli_module_alone_does_not_load_the_sdk():
    r = _run(f"""
        import sys
        import jdocmunch_mcp.cli.main  # noqa: F401
        leaked = [n for n in {_SDK_MODULES!r} if n in sys.modules]
        print("LEAKED=" + (",".join(leaked) or "NONE"))
    """)
    assert r.returncode == 0, r.stderr
    assert "LEAKED=NONE" in r.stdout, r.stdout


def test_asyncio_is_not_imported_at_cli_module_scope():
    """Only `serve` and `watch` use asyncio, so it must not load at import."""
    r = _run("""
        import sys
        import jdocmunch_mcp.cli.main  # noqa: F401
        print("ASYNCIO=" + ("YES" if "asyncio" in sys.modules else "NO"))
    """)
    assert r.returncode == 0, r.stderr
    assert "ASYNCIO=NO" in r.stdout, (
        "asyncio is back at module scope in cli/main.py. Import it inside the "
        "serve and watch branches instead."
    )


def test_server_still_exports_main():
    """1.x contract: ``from jdocmunch_mcp.server import main`` must keep working.

    A caller importing server has already paid for the SDK, so the re-export
    costs nothing new -- but removing it would break an existing entry point.
    """
    r = _run("""
        from jdocmunch_mcp.server import main, _force_utf8_stdio, _load_paths_from_arg
        print("OK", callable(main), callable(_force_utf8_stdio),
              callable(_load_paths_from_arg))
    """)
    assert r.returncode == 0, r.stderr
    assert "OK True True True" in r.stdout


def test_console_script_points_at_the_cli_module():
    """The pyproject entry point is the whole mechanism; if it drifts back to
    server:main every hook fire silently pays for the SDK again."""
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert 'jdocmunch-mcp = "jdocmunch_mcp.cli.main:main"' in pyproject
    assert 'jdocmunch_mcp.server:main' not in pyproject


def test_version_flag_still_works():
    """The lazy Action must behave exactly like argparse's built-in one."""
    r = _run("""
        import jdocmunch_mcp.cli.main as m
        try:
            m.main(["--version"])
        except SystemExit as e:
            print("EXIT", e.code)
    """)
    assert r.returncode == 0, r.stderr
    assert "jdocmunch-mcp" in r.stdout
    assert "EXIT 0" in r.stdout
