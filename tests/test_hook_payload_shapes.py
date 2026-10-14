"""Every hook handler survives every payload shape, and the set cannot shrink.

Claude Code hands a hook a JSON document on stdin, and "valid JSON" is a much
larger set than "a payload". Five valid shapes -- ``tool_input`` as a string, a
top-level ``null`` / list / number, and ``file_path`` as a number -- crashed
``run_pretooluse`` with a traceback. A non-zero hook exit is a non-blocking error
whose FIRST LINE OF STDERR the transcript shows, so a crash reaches the user as
``Traceback (most recent call last):``.

⚠ The first fix guarded the handler that was reported and left
``run_posttooluse`` reading ``data.get("tool_input", {}).get("file_path", "")``,
which raises on the same shapes. A ratchet catches the shape it NAMES and
nothing adjacent, so this one does not name a handler: it ENUMERATES every
``run_*`` function in the hook modules, requires each to be wrapped in
``_guarded``, and runs each over every shape. A new handler is therefore in scope
the moment it is written.
"""

import inspect
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from jdocmunch_mcp.cli import _hook_probe, hooks

SRC = Path(__file__).resolve().parents[1] / "src"
MAIN_PY = SRC / "jdocmunch_mcp" / "cli" / "main.py"

# The five shapes that crashed, then shapes of the same family the first fix did
# not name. Raw strings, because several are not meant to be well-formed.
SHAPES = {
    "tool_input_is_a_string": json.dumps({"tool_input": "notadict"}),
    "top_level_null": json.dumps(None),
    "top_level_list": json.dumps([1, 2]),
    "top_level_number": json.dumps(42),
    "file_path_is_a_number": json.dumps({"tool_input": {"file_path": 123}}),
    "file_path_is_a_list": json.dumps({"tool_input": {"file_path": ["a.md"]}}),
    "file_path_has_a_nul_byte": json.dumps({"tool_input": {"file_path": "a\x00b.md"}}),
    "source_is_a_number": json.dumps({"source": 5, "cwd": 5}),
    "cwd_is_a_number": json.dumps({"source": "compact", "cwd": 5}),
    "nested_past_the_recursion_limit": "[" * 200000,
    "not_json": "not json",
    "empty": "",
}

# Handlers that take an ARGUMENT rather than reading a payload from stdin. A new
# `run_*` with a required parameter must be added here deliberately, so it is a
# decision and not an oversight.
ARGUMENT_HANDLERS = {"run_hook_reindex"}


def _handlers() -> dict:
    """Every ``run_*`` function reachable from the hook modules, by name."""
    found = {}
    for module in (hooks, _hook_probe):
        for name, obj in vars(module).items():
            if name.startswith("run_") and callable(obj):
                found[name] = obj
    return found


def _payload_handlers() -> dict:
    return {
        name: fn for name, fn in _handlers().items()
        if name not in ARGUMENT_HANDLERS
    }


def test_the_enumeration_finds_the_handlers_it_should():
    names = set(_handlers())
    # A floor, not the list: the point is that the list is DERIVED. If an import
    # refactor made `_handlers()` find nothing, every test below would pass
    # vacuously, which is what this assertion exists to prevent.
    assert {"run_pretooluse", "run_posttooluse", "run_precompact",
            "run_sessionstart", "run_hook_reindex"} <= names


def test_every_run_function_is_wrapped_in_guarded():
    unguarded = sorted(
        name for name, fn in _handlers().items()
        if not getattr(fn, "_hook_guarded", False)
    )
    assert not unguarded, (
        f"hook handlers without @_guarded: {unguarded}. A handler that raises "
        f"exits non-zero, and the transcript shows the traceback's first line."
    )


def test_a_run_function_that_takes_arguments_is_a_decision():
    """A new `run_*(x)` is not a stdin handler; it must be named in
    ARGUMENT_HANDLERS rather than silently skipped by the shape test."""
    for name, fn in _handlers().items():
        required = [
            p for p in inspect.signature(fn).parameters.values()
            if p.default is inspect.Parameter.empty
            and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        if required:
            assert name in ARGUMENT_HANDLERS, (
                f"{name} takes {[p.name for p in required]}; if it does not read "
                f"a payload from stdin, add it to ARGUMENT_HANDLERS."
            )
        else:
            assert name not in ARGUMENT_HANDLERS, f"{name} no longer takes an argument"


def test_every_handler_main_dispatches_to_is_enumerated_and_back():
    """cli/main.py's hook-* branches and the enumeration must agree both ways."""
    src = MAIN_PY.read_text(encoding="utf-8")
    dispatched = set(re.findall(r"from \.\.cli\.hooks import (run_\w+)", src))
    assert dispatched == set(_handlers()), (
        f"main.py dispatches {sorted(dispatched)} but the hook modules define "
        f"{sorted(_handlers())}"
    )


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """No real store, no real hook state, and a spawn that must never happen."""
    monkeypatch.setenv("DOC_INDEX_PATH", str(tmp_path / "store"))
    monkeypatch.delenv("JDOCMUNCH_HOOK_LOG", raising=False)
    import subprocess as sp
    spawned = []
    monkeypatch.setattr(sp, "Popen", lambda *a, **k: spawned.append(a) or None)
    return spawned


@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("handler", sorted(_payload_handlers()))
def test_every_handler_exits_zero_on_every_shape(handler, shape, isolated,
                                                 monkeypatch, capsys):
    fn = _payload_handlers()[handler]
    monkeypatch.setattr(sys, "stdin", io.StringIO(SHAPES[shape]))
    assert fn() == 0
    out, err = capsys.readouterr()
    assert "Traceback" not in err and "Traceback" not in out
    assert isolated == [], f"{handler} spawned a process for {shape}"


# The subprocess arm leaves out the 200,000-bracket document: it is the one shape
# whose cost is the size of the input, and the in-process arm above already
# proves the handler survives it (a RecursionError is an Exception, which the
# guard catches). Every other shape is cheap and is run through a real process.
@pytest.mark.parametrize("shape", sorted(s for s in SHAPES if s != "nested_past_the_recursion_limit"))
@pytest.mark.parametrize("subcommand", sorted(
    "hook-" + n[len("run_"):] for n in _payload_handlers()
))
def test_every_hook_subcommand_exits_zero_on_every_shape(subcommand, shape, tmp_path):
    """The same, through a real process: what Claude Code actually runs."""
    env = {**os.environ, "PYTHONPATH": str(SRC),
           "DOC_INDEX_PATH": str(tmp_path / "store")}
    env.pop("JDOCMUNCH_HOOK_LOG", None)
    r = subprocess.run(
        [sys.executable, "-m", "jdocmunch_mcp", subcommand],
        input=SHAPES[shape], capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, timeout=120,
    )
    assert r.returncode == 0, f"{subcommand} / {shape}: rc={r.returncode}\n{r.stderr[-1500:]}"
    assert "Traceback" not in r.stderr, r.stderr[-1500:]
