"""No in-server subprocess may inherit the MCP server's stdin.

Ported from jcodemunch-mcp (jcm#392, @rknighton). In a stdio MCP server, stdin
IS the live JSON-RPC channel. A child launched without `stdin=` inherits it,
blocks reading a pipe nobody will write to, and on Windows the Git wrapper's
child can outlive the `timeout=` that kills the immediate process, leaving the
parent blocked in the follow-up `communicate()` well past its deadline.

⚠ This is ported as a GUARD, not as a fix. jdoc's three server-path git probes
in `tools/_git.py` already pass `stdin=subprocess.DEVNULL`, and two of them
already carry the "prevents stdio-server deadlock" comment. That is exactly the
state jcm was in before jcm#392: the convention was universal and enforced by
nothing, so a call site added later silently opted out and reintroduced the
hang. The defect this file prevents is a FUTURE one; there is no current
offender, and `test_guard_is_not_vacuous` is what keeps that fact honest.
"""

from __future__ import annotations

import ast
import pathlib

SRC_ROOT = pathlib.Path(__file__).resolve().parents[1] / "src" / "jdocmunch_mcp"

_SPAWNERS = {"run", "Popen", "call", "check_call", "check_output"}

# Modules that own their own process, where stdin is a terminal rather than the
# MCP JSON-RPC channel. Inheriting it there is correct: an installer may prompt.
# Exempt BY NAME, never by directory prefix, so a new server-path module cannot
# inherit an exemption by accident.
_NOT_SERVER_PATH = {
    "cli/hooks.py",
    "cli/init.py",
    "service_installer.py",
}

# Server-path modules whose child speaks a protocol over stdin, so `PIPE` is
# the correct value rather than an oversight. ⚠ This is the escape hatch this
# file's own docstring names — an explicit list with a reason, not a loosened
# rule. The hazard DEVNULL prevents is a child blocking on a pipe nobody
# writes to; a child the parent is actively writing to and killing on a
# timeout is the opposite case.
#
# ⚠ Entries here are still bound by every other rule: the module must pass
# `stdin=` explicitly, and it must not inherit fd 0.
_INTENTIONAL_STDIN_PIPE = {
    "embeddings/worker.py": (
        "jdoc#118: the sentence-transformers child is driven over a private "
        "NDJSON pipe on its stdin, and the parent bounds and kills it."
    ),
    "watch.py": (
        "jdoc#189: each watch batch's job is written to the child's stdin and "
        "the pipe is closed by communicate(); the child never reads fd 0 of "
        "the watcher."
    ),
}


def _spawn_sites() -> list[tuple[str, int, bool]]:
    """(relative_path, lineno, passes_stdin) for every subprocess spawn in src."""
    sites: list[tuple[str, int, bool]] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(SRC_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
                and func.attr in _SPAWNERS
            ):
                kwargs = {kw.arg for kw in node.keywords}
                sites.append((rel, node.lineno, "stdin" in kwargs))
    return sites


def _server_path_sites() -> list[tuple[str, int, bool]]:
    return [s for s in _spawn_sites() if s[0] not in _NOT_SERVER_PATH]


def test_guard_is_not_vacuous():
    """The walker must actually find server-path spawns.

    Without this, the guard below passes trivially if the walker breaks, if the
    package is renamed, or if every spawn moves behind a helper it cannot see.
    A structural test that cannot fail is worse than no test: it reads as
    coverage and provides none.
    """
    sites = _server_path_sites()
    assert sites, (
        "no server-path subprocess spawns found in src/jdocmunch_mcp. Either the "
        "AST walker stopped matching, or the spawns moved. Fix the walker rather "
        "than deleting this file."
    )


def test_no_server_path_subprocess_inherits_stdin():
    """Every spawn outside the exempt CLI modules must pass stdin=."""
    offenders = [
        f"{rel}:{lineno}" for rel, lineno, has_stdin in _server_path_sites() if not has_stdin
    ]
    assert not offenders, (
        "these run inside the MCP server process, where stdin is the live "
        "JSON-RPC channel, and must pass stdin=subprocess.DEVNULL "
        f"(jcm#392): {offenders}"
    )


def test_server_path_stdin_is_devnull():
    """`stdin=` alone isn't enough: PIPE would reintroduce a blocking child.

    DEVNULL is the only correct value except in the modules named in
    `_INTENTIONAL_STDIN_PIPE`, which drive a child over stdin on purpose.
    Add a module there, with its reason, rather than loosening this.
    """
    wrong: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(SRC_ROOT).as_posix()
        if rel in _NOT_SERVER_PATH or rel in _INTENTIONAL_STDIN_PIPE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
                and func.attr in _SPAWNERS
            ):
                continue
            for kw in node.keywords:
                if kw.arg != "stdin":
                    continue
                value = kw.value
                if not (isinstance(value, ast.Attribute) and value.attr == "DEVNULL"):
                    wrong.append(f"{rel}:{node.lineno}")
    assert not wrong, f"server-path stdin must be subprocess.DEVNULL: {wrong}"


def test_exemption_list_has_no_dead_entries():
    """A stale exemption is a hole nobody can see.

    If an exempt module stops spawning processes, drop it from the list rather
    than leaving standing cover for a future call site at the same path.
    """
    spawning = {rel for rel, _, _ in _spawn_sites()}
    dead = (_NOT_SERVER_PATH | set(_INTENTIONAL_STDIN_PIPE)) - spawning
    assert not dead, f"exemption entries no longer spawn any process: {sorted(dead)}"


def test_intentional_pipe_modules_really_pass_pipe():
    """The narrow exemption must not become cover for a missing `stdin=`.

    A module on that list is excused from DEVNULL and from nothing else. If its
    spawn stops passing `stdin=subprocess.PIPE`, the entry is describing a call
    site that no longer exists and the real one is unguarded.
    """
    seen: dict[str, list[str]] = {rel: [] for rel in _INTENTIONAL_STDIN_PIPE}
    for path in sorted(SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(SRC_ROOT).as_posix()
        if rel not in _INTENTIONAL_STDIN_PIPE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
                and func.attr in _SPAWNERS
            ):
                continue
            for kw in node.keywords:
                if kw.arg == "stdin" and isinstance(kw.value, ast.Attribute):
                    seen[rel].append(kw.value.attr)
    for rel, values in seen.items():
        assert "PIPE" in values, (
            f"{rel} is exempted as an intentional stdin=PIPE site but passes "
            f"{values or 'nothing'}; drop the exemption or fix the call"
        )
