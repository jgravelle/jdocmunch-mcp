"""The `hook-pretooluse` fast path must be indistinguishable from the slow one.

`cli/main.py` dispatches a bare `hook-pretooluse` before building the argparse
tree and before `verify_package_integrity()`, because Claude Code fires that
subcommand as a fresh process on every `Read` and the process does very little
work. The saving is only legitimate if the two paths are observably the same,
so this file compares them as real subprocesses -- exit code, stdout bytes and
stderr bytes -- across the payload shapes `tests/test_hooks.py` pins that do not
crash, plus the file-size and targeted-read conditions.

⚠ The comparison is BYTES, not "contains search_sections". The hint text is
what reaches the model, and a difference of one character is a changed hook.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[1] / "src")
MAIN_PY = Path(SRC) / "jdocmunch_mcp" / "cli" / "main.py"

# The two arms. FAST goes through `main(["hook-pretooluse"])`, which is what the
# console script runs; SLOW calls the function the argparse branch dispatches to.
_FAST = """
    import io, sys
    sys.stdin = io.StringIO(PAYLOAD)
    import jdocmunch_mcp.cli.main as m
    try:
        m.main(["hook-pretooluse"])
    except SystemExit as e:
        sys.exit(e.code or 0)
"""

_SLOW = """
    import io, sys
    sys.stdin = io.StringIO(PAYLOAD)
    from jdocmunch_mcp.cli.hooks import run_pretooluse
    sys.exit(run_pretooluse())
"""


def _run(arm: str, payload: str, store: str | None) -> tuple:
    env = {**os.environ, "PYTHONPATH": SRC}
    if store is None:
        env.pop("DOC_INDEX_PATH", None)
    else:
        env["DOC_INDEX_PATH"] = store
    code = f"PAYLOAD = {payload!r}\n" + textwrap.dedent(arm)
    p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       env=env, timeout=120)
    return p.returncode, p.stdout, p.stderr


def _assert_identical(payload: str, store: str | None):
    fast = _run(_FAST, payload, store)
    slow = _run(_SLOW, payload, store)
    assert fast == slow, (
        f"fast and slow hook-pretooluse paths diverged.\n"
        f"  payload: {payload}\n"
        f"  fast:    rc={fast[0]} out={fast[1]!r} err={fast[2]!r}\n"
        f"  slow:    rc={slow[0]} out={slow[1]!r} err={slow[2]!r}"
    )
    assert fast[0] == 0, f"a hook must exit 0; got {fast[0]} / {fast[2]!r}"
    return fast


# --- payload shapes, including the five malformed ones -----------------------

_SHAPES = [
    json.dumps({"tool_input": {"file_path": "app.py"}}),
    json.dumps({"tool_input": {"file_path": "/nonexistent/doc.md"}}),
    json.dumps({"tool_input": {}}),
    "not json",
    "",
]


@pytest.mark.parametrize("payload", _SHAPES, ids=range(len(_SHAPES)))
def test_identical_on_every_payload_shape(payload, tmp_path):
    _assert_identical(payload, str(tmp_path / "empty-store"))


# --- and on the file-based conditions, against real files -------------------

@pytest.fixture(scope="module")
def files(tmp_path_factory):
    root = tmp_path_factory.mktemp("fastpath")
    big = root / "big.md"
    big.write_text("# Big\n\n" + "x " * 3000, encoding="utf-8")
    small = root / "small.md"
    small.write_text("hi", encoding="utf-8")
    return big, small


def test_identical_and_hints_on_a_large_doc_file(files, tmp_path):
    big, _ = files
    _, out, _ = _assert_identical(
        json.dumps({"tool_input": {"file_path": str(big)}}), str(tmp_path))
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert ctx.startswith("jDocMunch hint: this is a ") and "search_sections" in ctx


def test_identical_and_silent_for_a_small_file(files, tmp_path):
    _, small = files
    _, out, _ = _assert_identical(
        json.dumps({"tool_input": {"file_path": str(small)}}), str(tmp_path))
    assert out == b""


def test_identical_for_a_targeted_read(files, tmp_path):
    big, _ = files
    _, out, _ = _assert_identical(json.dumps(
        {"tool_input": {"file_path": str(big), "offset": 10, "limit": 5}}), str(tmp_path))
    assert out == b""


# --- the two dispatch sites cannot drift apart -------------------------------

def test_both_dispatch_sites_run_the_same_two_lines():
    """The fast branch and the argparse branch must dispatch identically.

    They are two call sites for one function, which is exactly the shape that
    drifts. Compare them as source rather than trusting that they match -- a
    divergence here is how the byte-identity above would start lying.
    """
    src = MAIN_PY.read_text(encoding="utf-8")
    dispatch = "from ..cli.hooks import run_pretooluse\n        sys.exit(run_pretooluse())"
    assert src.count(dispatch) == 2, (
        "cli/main.py no longer has exactly two identical `hook-pretooluse` "
        "dispatch sites (the fast path above argparse, and the argparse "
        "branch). If one changed, the other must change with it -- or the "
        "byte-identity tests in this file are comparing the fast path against "
        "something the CLI no longer runs."
    )


def test_the_fast_path_is_what_actually_ran():
    """Byte-identity would also pass if the fast path were never taken.

    `importlib.metadata` is reachable on this subcommand only through
    `verify_package_integrity()`, which only the argparse path runs.
    """
    env = {**os.environ, "PYTHONPATH": SRC}
    p = subprocess.run([sys.executable, "-c", textwrap.dedent("""
        import io, sys
        sys.stdin = io.StringIO('{"tool_input": {"file_path": "x.py"}}')
        import jdocmunch_mcp.cli.main as m
        try:
            m.main(["hook-pretooluse"])
        except SystemExit:
            pass
        print("METADATA=" + ("YES" if "importlib.metadata" in sys.modules else "NO"))
    """)], capture_output=True, text=True, encoding="utf-8", errors="replace",
       env=env, timeout=120)
    assert p.returncode == 0, p.stderr
    assert "METADATA=NO" in p.stdout, (
        "the bare `hook-pretooluse` invocation went through argparse and the "
        "integrity check, so every test above compared the slow path with "
        "itself."
    )


def test_hook_posttooluse_keeps_the_integrity_check_and_the_fast_path_skips_it():
    """The fast path skips `verify_package_integrity()` on purpose, and only
    there: `hook-posttooluse` -- the hook that executes something -- still runs
    it. Pinned so a later "fast path for posttooluse too" turns this red."""
    for subcommand, expected in (("hook-posttooluse", 1), ("hook-pretooluse", 0)):
        env = {**os.environ, "PYTHONPATH": SRC}
        p = subprocess.run([sys.executable, "-c", textwrap.dedent(f"""
            import io, sys
            sys.stdin = io.StringIO("{{}}")
            import jdocmunch_mcp.security as security
            calls = []
            security.verify_package_integrity = lambda: calls.append(1)
            import jdocmunch_mcp.cli.main as m
            try:
                m.main([{subcommand!r}])
            except SystemExit:
                pass
            print("CHECK_RAN=%d" % len(calls))
        """)], capture_output=True, text=True, encoding="utf-8", errors="replace",
           env=env, timeout=120)
        assert p.returncode == 0, p.stderr
        assert f"CHECK_RAN={expected}" in p.stdout, (subcommand, p.stdout)
