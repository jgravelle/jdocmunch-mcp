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
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=120,
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


# ---------------------------------------------------------------------------
# The PreToolUse fast path (cli/main.py) and its stdlib-only leaf.
#
# Claude Code fires `hook-pretooluse` as a fresh process on EVERY `Read`, and
# that process does very little work. The preamble it used to run cost far more
# than the work: `verify_package_integrity()` (which calls
# `packages_distributions()` over every installed distribution and caches
# nothing), `importlib.metadata` for a `__version__` the hook never reads, and
# the argparse tree. `benchmarks/hook_path/measure.py` measures them.
#
# These are ratchets on the SHAPE that makes that saving possible, like the
# ones above. A timing assertion would be flaky and would not say what broke.
# ---------------------------------------------------------------------------

# Every one of these is a module the Read path must never pull in. `pathlib`,
# `hashlib` and `subprocess` belong to the PostToolUse reindexer;
# `importlib.metadata` is the one that pins BOTH the lazy package `__version__`
# and the skipped integrity check, because each needs it and nothing else on
# this path does.
_HOT_PATH_FORBIDDEN = ("pathlib", "hashlib", "subprocess", "importlib.metadata")


def test_pretooluse_fast_path_loads_none_of_the_expensive_modules():
    payload = json.dumps({"tool_input": {"file_path": "nonexistent.md"}})
    r = _run(f"""
        import sys, io
        sys.stdin = io.StringIO({payload!r})
        import jdocmunch_mcp.cli.main as m
        try:
            m.main(["hook-pretooluse"])
        except SystemExit:
            pass
        leaked = [n for n in {_HOT_PATH_FORBIDDEN!r} if n in sys.modules]
        print("LEAKED=" + (",".join(leaked) or "NONE"))
    """)
    assert r.returncode == 0, r.stderr
    assert "LEAKED=NONE" in r.stdout, (
        f"the hook-pretooluse fast path pulled in {r.stdout.strip()}. "
        f"`importlib.metadata` means either the package __init__ resolved "
        f"__version__ eagerly again or the fast path started running "
        f"verify_package_integrity(); the other three mean something gained a "
        f"module-level import in cli/main.py, cli/hooks.py or cli/_hook_probe.py."
    )


def _module_scope_imports(path: Path) -> set:
    """Top-level import names in one file, including a `try:` import probe."""
    import ast
    names: set = set()

    def visit(nodes):
        for node in nodes:
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names.add((node.module or "").split(".")[0])
            elif isinstance(node, ast.Try):        # the fcntl/msvcrt probes
                visit(node.body)

    visit(ast.parse(path.read_text(encoding="utf-8")).body)
    return names


def test_hook_probe_leaf_imports_only_json_os_sys():
    """The leaf is the whole mechanism: it is what `cli/hooks.py` re-exports
    from, so it is the one module guaranteed to load on every Read.

    Proven non-vacuous by adding `import pathlib` to it and watching this name
    the offender."""
    leaf = Path(SRC) / "jdocmunch_mcp" / "cli" / "_hook_probe.py"
    imported = _module_scope_imports(leaf)
    assert imported == {"json", "os", "sys"}, (
        f"cli/_hook_probe.py imports {sorted(imported)} at module scope. It may "
        f"import json, os and sys and nothing else -- every Read pays for this "
        f"module. Put what you need inside the function that needs it, or in "
        f"cli/hooks.py if it belongs to the PostToolUse reindexer."
    )


def test_hooks_module_keeps_the_reindexer_imports_out_of_module_scope():
    """`cli/hooks.py` is what the fast path imports -- so that patching
    `jdocmunch_mcp.cli.hooks.run_pretooluse` keeps working, and so that there
    is one definition of it -- which puts its module scope on the Read path."""
    src = Path(SRC) / "jdocmunch_mcp" / "cli" / "hooks.py"
    offenders = sorted(
        _module_scope_imports(src) & {"pathlib", "hashlib", "subprocess"}
    )
    assert not offenders, (
        f"cli/hooks.py imports {offenders} at module scope again. They belong "
        f"to the PostToolUse reindexer; import them inside the functions that "
        f"use them so a Read does not pay for them."
    )


def test_package_version_is_resolved_lazily():
    """`__version__` still reads correctly, and reading it is what costs."""
    r = _run("""
        import sys
        import jdocmunch_mcp
        print("BEFORE=" + ("YES" if "importlib.metadata" in sys.modules else "NO"))
        v = jdocmunch_mcp.__version__
        print("AFTER=" + ("YES" if "importlib.metadata" in sys.modules else "NO"))
        print("VALUE_OK=" + ("YES" if isinstance(v, str) and v else "NO"))
        from jdocmunch_mcp import __version__ as v2
        print("FROM_IMPORT_OK=" + ("YES" if v2 == v else "NO"))
    """)
    assert r.returncode == 0, r.stderr
    assert "BEFORE=NO" in r.stdout, (
        "importing jdocmunch_mcp resolved __version__ eagerly again; that put "
        "importlib.metadata back on every hook fire."
    )
    assert "AFTER=YES" in r.stdout
    assert "VALUE_OK=YES" in r.stdout
    assert "FROM_IMPORT_OK=YES" in r.stdout


@pytest.mark.parametrize("argv", [
    ["hook-pretooluse", "--help"],
    ["hook-pretooluse", "extra"],
])
def test_only_the_bare_subcommand_takes_the_fast_path(argv):
    """The guard is an exact one-argument match, so every other spelling still
    reaches argparse and behaves exactly as it did.

    `importlib.metadata` is reachable only through `verify_package_integrity()`,
    which only the slow path runs -- so its presence IS the proof argparse was
    used."""
    r = _run(f"""
        import sys, io
        sys.stdin = io.StringIO("{{}}")
        import jdocmunch_mcp.cli.main as m
        try:
            m.main({argv!r})
        except SystemExit as e:
            print("EXIT", e.code)
        print("SLOW_PATH=" + ("YES" if "importlib.metadata" in sys.modules else "NO"))
    """)
    assert r.returncode == 0, r.stderr
    assert "SLOW_PATH=YES" in r.stdout, r.stdout
