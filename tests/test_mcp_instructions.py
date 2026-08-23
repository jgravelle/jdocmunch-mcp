"""The MCP `instructions` string sent in the initialize response.

Until this change we sent none: the single transport called
`server.create_initialization_options()` bare, so the field went out empty and
nothing anywhere said so. That is invisible in a normal session and expensive
in a DEFERRED one: a host over its schema budget ships tool NAMES only and
withholds the JSONSchemas, so an agent sees 64 bare strings and none of
the descriptions. `instructions` travels on a separate track from the tool list
and arrives whole either way.

The property under test is the one that rots: **what the string advertises is
what the server will dispatch.** A tool named here that we do not serve is
worse than saying nothing, because it sends the agent to a name that does not
exist.

Ported from jcodemunch-mcp v1.108.292. A setting fixed in one repo of a suite
is fixed in one repo.
"""

from __future__ import annotations

import ast
import inspect
import re

from jdocmunch_mcp import server as server_mod
from jdocmunch_mcp.server import (
    _MCP_INSTRUCTIONS_MAX_CHARS,
    _instruction_tool_names,
    _mcp_instructions,
    _tool_search_query,
)

_PREFIX = "mcp__jdocmunch__"

# Tool names appear in the prose as bare identifiers. Anything matching this
# that is not a real tool is either a typo or a tool we dropped.
_NAME_RE = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b")

# Lowercase snake_case words in the prose that are deliberately not tool names.
_NOT_TOOL_NAMES = {"doc_file"}


def _served_names() -> set:
    """Every tool name this server will dispatch."""
    src = inspect.getsource(server_mod)
    return set(re.findall(r'name="([a-z][a-z0-9_]+)"', src))


def test_instructions_say_something_within_budget():
    text = _mcp_instructions()
    assert text.strip(), "empty instructions"
    assert len(text) <= _MCP_INSTRUCTIONS_MAX_CHARS, (
        f"{len(text)} chars exceeds the {_MCP_INSTRUCTIONS_MAX_CHARS} budget. "
        "Trim the prose; nothing proves a longer string survives un-truncated."
    )


def test_every_tool_named_is_a_tool_we_dispatch():
    """The whole point. Prose naming a tool we do not serve is a wrong turn."""
    text = _mcp_instructions()
    served = _served_names()
    mentioned = {
        m for m in _NAME_RE.findall(text.replace(_PREFIX, ""))
        if m not in _NOT_TOOL_NAMES
    }
    unknown = {m for m in mentioned if m not in served}
    assert not unknown, (
        f"instructions name {sorted(unknown)}, which the server does not "
        "dispatch. Either the tool moved or the prose is stale."
    )


def test_named_tools_are_the_ones_the_lookup_loads():
    names = _instruction_tool_names()
    assert names, "names no tools at all"
    query = _tool_search_query()
    assert query.startswith("select:")
    loaded = query[len("select:"):].split(",")
    assert loaded == [_PREFIX + n for n in names], (
        "the ToolSearch query and the bullet list disagree. An agent following "
        "the query would load a different set than the bullets tell it to use."
    )
    assert query in _mcp_instructions()


def test_initialization_options_carry_the_instructions():
    opts = server_mod._initialization_options()
    if "instructions" not in type(opts).model_fields:  # pragma: no cover - old SDK
        return
    assert opts.instructions == _mcp_instructions()


def test_server_info_reports_our_version_not_the_sdks():
    """`Server(name)` with no `version=` makes the SDK report its OWN version in
    `serverInfo`, so every host that displays a server version displayed the mcp
    package number.

    ⚠ Green here does NOT prove the wire carries a real version.
    `__version__` falls back to "unknown" when distribution metadata is absent,
    which is every `PYTHONPATH=src` run. That fallback is deliberate: "unknown"
    is an honest could-not-establish, where the SDK's number was a confident
    answer about a different package.
    """
    from importlib.metadata import version as _dist_version

    from jdocmunch_mcp import __version__

    opts = server_mod._initialization_options()
    assert opts.server_version == __version__
    assert opts.server_version != _dist_version("mcp")


def test_every_server_run_passes_our_initialization_options():
    """A bare `create_initialization_options()` sends an empty `instructions`
    and nothing fails at runtime.

    ⚠ Asserts on the CALL, not on the helper: a test that only checked
    `_initialization_options()` would pass against a tree where the transport
    was rewired back.
    """
    tree = ast.parse(inspect.getsource(server_mod))
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "run"):
            continue
        if not (isinstance(fn.value, ast.Name) and fn.value.id == "server"):
            continue
        sites.append(node)

    assert sites, "no server.run() call found at all"
    for site in sites:
        args = site.args
        assert len(args) >= 3, f"server.run() at line {site.lineno} takes no options"
        opts = args[2]
        assert isinstance(opts, ast.Call), f"line {site.lineno}: options is not a call"
        assert isinstance(opts.func, ast.Name) and opts.func.id == "_initialization_options", (
            f"server.run() at line {site.lineno} does not pass "
            "_initialization_options(); its handshake sends no instructions."
        )
