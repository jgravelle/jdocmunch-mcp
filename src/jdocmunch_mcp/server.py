"""MCP server for jdocmunch-mcp."""

import argparse
import asyncio
import json
import logging
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Optional


def _load_paths_from_arg(paths_from: str) -> tuple[Optional[list], Optional[str]]:
    """Read explicit paths from a file or stdin for the `index-local --paths-from` CLI flag.

    Returns ``(paths, None)`` on success or ``(None, error_message)`` on failure.
    Filters out empty lines and ``# ...`` comments. An empty list is treated as
    an error so the caller doesn't silently fall through to a full-tree index.
    """
    try:
        if paths_from == "-":
            raw = sys.stdin.read()
        else:
            raw = Path(paths_from).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return None, f"Cannot read --paths-from {paths_from!r}: {e}"
    paths_arg = [
        ln.strip()
        for ln in raw.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    if not paths_arg:
        return None, f"--paths-from {paths_from!r} contained no usable paths"
    return paths_arg, None

from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import Tool, ToolAnnotations, TextContent, Resource

from . import runtime_identity
from .tools.index_local import index_local
from .tools.index_repo import index_repo
from .tools.list_repos import list_repos
from .tools.list_docs import list_docs
from .tools.get_doc import get_doc
from .tools.get_index_overview import get_index_overview
from .tools.get_toc import get_toc
from .tools.get_toc_tree import get_toc_tree
from .tools.get_document_outline import get_document_outline
from .tools.search_sections import search_sections
from .tools.search_titles import search_titles
from .tools.count_sections import count_sections
from .tools.get_section import get_section
from .tools.get_sections import get_sections
from .tools.get_section_context import get_section_context
from .tools.section_neighbors import section_neighbors
from .tools.describe_section import describe_section
from .tools.get_section_summary import get_section_summary
from .tools.get_section_summaries import get_section_summaries
from .tools.get_orphan_sections import get_orphan_sections
from .tools.get_section_path import get_section_path
from .tools.get_section_excerpt import get_section_excerpt
from .tools.get_section_excerpts import get_section_excerpts
from .tools.get_section_descendants import get_section_descendants
from .tools.get_all_tags import get_all_tags
from .tools.get_all_roles import get_all_roles
from .tools.get_recent_changes import get_recent_changes
from .tools.delete_index import delete_index
from .tools.get_broken_links import get_broken_links
from .tools.get_doc_coverage import get_doc_coverage
from .tools.get_backlinks import get_backlinks
from .tools.get_stale_pages import get_stale_pages
from .tools.get_wiki_stats import get_wiki_stats
from .tools.get_watch_status import get_watch_status
from .tools.analyze_perf import analyze_perf
from .tools.get_session_stats import get_session_stats
from .tools.check_embedding_drift import check_embedding_drift
from .tools.find_code_examples import find_code_examples
from .tools.link_code_to_symbols import link_code_to_symbols
from .tools.resolve_related_code_repos import resolve_related_code_repos
from .tools.resolve_repo import doc_resolve_repo
from .tools.openapi_tools import (
    find_endpoint,
    list_endpoints_by_tag,
    find_operations_using_schema,
    get_schema_graph,
)
from .tools.glossary_tools import lookup_term, list_terms
from .tools.get_related_sections import get_related_sections
from .tools.get_section_diff import get_section_diff
from .tools.get_doc_health import get_doc_health
from .tools.doc_health_radar import doc_health_radar
from .tools.health_radar import diff_doc_health_radar
from .tools.get_doc_pr_risk_profile import get_doc_pr_risk_profile
from .tools.get_tutorial_path import get_tutorial_path
from .tools.get_undocumented_symbols import get_undocumented_symbols
from .tools.tune_weights import tune_weights
from .tools.repo_group_tools import list_repo_groups, define_repo_group
from .tools.verify_index import verify_index
from .tools.check_section_delete_safe import check_section_delete_safe
from .tools.get_section_blast_radius import get_section_blast_radius
from .tools.find_similar_sections import find_similar_sections


from . import __version__  # module scope: serverInfo needs it at construction

server = Server("jdocmunch-mcp", version=__version__)


# --- MCP `instructions` (initialize response) ------------------------------ #
# The one piece of jdocmunch prose that survives TOOL DEFERRAL. When a host has
# more tools than its schema budget allows it sends tool NAMES only and withholds
# the JSONSchemas until a ToolSearch-style lookup fetches them. We ship 64
# tools, so in a deferred session every tool description is invisible at exactly
# the moment steering matters most.
#
# The MCP spec delivers `instructions` on a separate track from the tool list, so
# it arrives whole even then. Two jobs, in this order:
#   1. Defuse the deferral tax. ONE lookup loads the whole working set for the
#      session, so the cost is a single round trip and not two calls per use.
#   2. Say what each tool is FOR as a decision rule, not a feature summary. In a
#      plain MCP client with no hooks and no skill listing, this string plus the
#      tool descriptions are the entire steering budget we get.
#
# Ported from jcodemunch-mcp v1.108.292. A setting fixed in one repo of a suite
# is fixed in one repo.
#
# ⚠ Budget: under _MCP_INSTRUCTIONS_MAX_CHARS. Nothing proves a longer one
# survives un-truncated, and observed sibling servers sit at 660-984.
# ⚠ Every tool named here must be a real dispatchable name;
# `tests/test_mcp_instructions.py` binds the prose to the catalog so this cannot
# rot into advertising a tool we do not serve.

_MCP_INSTRUCTIONS_MAX_CHARS = 1000

# Default host prefix for MCP tool names. The real prefix comes from whatever key
# the user wrote in their MCP config, so this is the common case, not a promise.
_MCP_TOOL_PREFIX = "mcp__jdocmunch__"

# Named in the order an agent should reach for them, most-used first.
_INSTRUCTION_TOOLS: tuple = (
    ("doc_resolve_repo", "are these docs indexed? Call it first."),
    ("search_sections", "the section that answers a question, ranked."),
    ("get_section", "one section by id; get_section_excerpt for a slice."),
    ("get_document_outline", "before opening any doc file."),
    ("get_section_context", "a section plus what surrounds it."),
    ("lookup_term", "what a project-specific word means here."),
)


def _instruction_tool_names() -> list:
    """Tool names named in the instructions, in reach-for order."""
    return [name for name, _ in _INSTRUCTION_TOOLS]


def _tool_search_query(prefix: str = _MCP_TOOL_PREFIX) -> str:
    """The `select:` argument that loads every named tool in one lookup."""
    return "select:" + ",".join(prefix + n for n in _instruction_tool_names())


def _mcp_instructions(prefix: str = _MCP_TOOL_PREFIX) -> str:
    """The `instructions` string for the initialize response."""
    lines = [
        "This repo's documentation is indexed by jdocmunch: every section, "
        "heading, link and term, addressable by id. Prefer these over "
        "Read/Grep/Glob for prose. One call usually replaces several reads.",
        "",
        "**If these tools are deferred (names shown, schemas withheld), load them "
        'in ONE lookup:** ToolSearch "%s". One round trip for the session, '
        "never one at a time." % _tool_search_query(prefix),
        "",
    ]
    lines += ["- %s: %s" % (name, why) for name, why in _INSTRUCTION_TOOLS]
    return "\n".join(lines)


def _initialization_options():
    """`create_initialization_options()` carrying our `instructions` string."""
    opts = server.create_initialization_options()
    if "instructions" not in type(opts).model_fields:
        # mcp SDK predates the field (we allow >=1.10.0). Nothing to say, and
        # nowhere to say it.
        # `logging.getLogger(__name__)`, NOT a module-level `logger`: this module
        # has none, and this line only runs on an SDK old enough to lack the
        # field, so a NameError here would surface on exactly the install least
        # able to diagnose it.
        logging.getLogger(__name__).debug(
            "InitializationOptions has no `instructions` field; skipping"
        )
        return opts
    return opts.model_copy(update={"instructions": _mcp_instructions()})



# --------------------------------------------------------------------------- #
# Tool profiles: tiered sets for controlling context budget.                  #
# Mirrors jcodemunch-mcp's _TOOL_TIER_* design (issue #297).                  #
# Config via JDOCMUNCH_TOOL_PROFILE ("core" | "standard" | "full"; default    #
# "full"). Config via JDOCMUNCH_DISABLED_TOOLS (comma-separated tool names).  #
# --------------------------------------------------------------------------- #
_TOOL_TIER_CORE: frozenset[str] = frozenset({
    # Indexing
    "index_local", "doc_index_repo",
    # Discovery
    "doc_list_repos", "doc_resolve_repo", "list_docs", "get_index_overview",
    # Document navigation
    "get_doc", "get_toc", "get_toc_tree",
    # Section retrieval
    "search_sections", "search_titles",
    "get_section", "get_sections", "get_section_context",
})

_TOOL_TIER_STANDARD: frozenset[str] = _TOOL_TIER_CORE | frozenset({
    # Discovery extras
    "count_sections", "get_all_tags", "get_all_roles",
    "list_terms", "lookup_term",
    "list_repo_groups", "define_repo_group",
    # Document navigation extras
    "get_document_outline", "get_tutorial_path",
    # Section retrieval extras
    "describe_section", "section_neighbors", "get_section_path",
    "get_section_excerpt", "get_section_excerpts",
    "get_section_descendants",
    "get_section_summary", "get_section_summaries",
    # Cross-references & graph
    "get_backlinks", "get_related_sections",
    "get_broken_links", "get_orphan_sections",
    "find_similar_sections", "get_section_diff",
    "get_section_blast_radius", "check_section_delete_safe",
    # Code linking
    "find_code_examples", "link_code_to_symbols",
    "get_undocumented_symbols", "resolve_related_code_repos",
    # Health & metrics
    "get_doc_coverage", "get_stale_pages", "get_wiki_stats",
    "get_recent_changes", "get_doc_health",
    "doc_health_radar", "diff_doc_health_radar",
    "get_doc_pr_risk_profile", "get_watch_status",
    # Utilities
    "verify_index", "delete_index", "finalize_handoff",
})

# full = everything (no filter applied)
_PROFILE_TIERS: dict[str, frozenset[str] | None] = {
    "core": _TOOL_TIER_CORE,
    "standard": _TOOL_TIER_STANDARD,
    "full": None,
}

# Tools that survive tier filtering (always visible in core/standard).
_ALWAYS_PRESENT_TOOLS: frozenset[str] = frozenset({"jdocmunch_guide"})

# Tools that ALSO survive disabled_tools. Empty: jdocmunch_guide is
# documentation, not a runtime control surface, so disabled_tools honors it.
# (Mirrors jcodemunch-mcp v1.108.8 issue #298 resolution.)
_UNDISABLEABLE_TOOLS: frozenset[str] = frozenset()

# Deprecated call-time aliases -> canonical tool name (#36). The dispatcher
# accepts these older spellings for backward compat, but they are never
# advertised; mapping them here lets the disabled-tools gate block every
# spelling when the canonical tool is disabled.
_ALIAS_TO_CANONICAL: dict[str, str] = {
    "index_repo": "doc_index_repo",
    "list_repos": "doc_list_repos",
}


def _get_tool_profile() -> str:
    """Return the effective tool_profile from env, defaulting to 'full'."""
    raw = os.environ.get("JDOCMUNCH_TOOL_PROFILE", "full").strip().lower()
    return raw if raw in _PROFILE_TIERS else "full"


def _get_disabled_tools() -> frozenset[str]:
    """Return the set of tool names disabled via JDOCMUNCH_DISABLED_TOOLS."""
    raw = os.environ.get("JDOCMUNCH_DISABLED_TOOLS", "").strip()
    if not raw:
        return frozenset()
    return frozenset(t.strip() for t in raw.split(",") if t.strip())


def _filter_tools(tools: list[Tool]) -> list[Tool]:
    """Apply tool_profile + disabled_tools filtering. Preserves order."""
    profile = _get_tool_profile()
    allowed = _PROFILE_TIERS.get(profile)
    if allowed is not None:
        tools = [t for t in tools if t.name in allowed or t.name in _ALWAYS_PRESENT_TOOLS]
    disabled = _get_disabled_tools()
    if disabled:
        effective_disabled = disabled - _UNDISABLEABLE_TOOLS
        if effective_disabled:
            tools = [t for t in tools if t.name not in effective_disabled]
    return tools


def _tool_surface_stats(top_n: int = 15) -> dict:
    """Schema token weight of the visible tool surface vs the full catalog.

    Suite parity with jcodemunch-mcp v1.108.153. Estimated at the meter's
    bytes/4 scale over the {name, description, inputSchema} serialization.
    Advisory receipt only — never blocks, nothing persisted. jDoc has no
    Counter surface, so the block carries `profile` but no `surface` key.
    """
    import json as _json

    def _weight(tool: Tool) -> int:
        payload = _json.dumps(
            {
                "name": tool.name,
                "description": tool.description or "",
                "inputSchema": tool.inputSchema or {},
            },
            separators=(",", ":"),
            default=str,
        )
        return max(1, len(payload.encode("utf-8")) // 4)

    catalog_tools = _all_tools()
    visible = {t.name: _weight(t) for t in _filter_tools(catalog_tools)}
    catalog = {t.name: _weight(t) for t in catalog_tools}
    visible_total = sum(visible.values())
    catalog_total = sum(catalog.values())
    heaviest = dict(sorted(visible.items(), key=lambda kv: -kv[1])[:top_n])
    return {
        "profile": _get_tool_profile(),
        "visible_tools": len(visible),
        "catalog_tools": len(catalog),
        "schema_tokens_visible": visible_total,
        "schema_tokens_catalog": catalog_total,
        "schema_tokens_avoided": max(0, catalog_total - visible_total),
        "heaviest_tools": heaviest,
        "estimator": "bytes/4",
    }


# --- MCP read-only annotations (suite parity with jcodemunch PR #361) --------
# MCP clients that gate execution (Claude Code plan mode) prompt for approval on
# every tool they cannot prove is read-only. jDoc is read-only by charter apart
# from the handful of tools that index / mutate / delete a doc index, so annotate
# each tool with ToolAnnotations(readOnlyHint=...): query tools run silently, the
# write-set still prompts. Any tool that can mutate persistent state (a doc
# index, repo group, tuning file, or drift canary) under ANY argument is
# non-read-only — biased conservative, since mislabeling a writer as read-only is
# the harmful direction.
_NON_READONLY_TOOLS: frozenset[str] = frozenset({
    "index_local",
    "doc_index_repo",
    "delete_index",
    "define_repo_group",      # create / replace / delete a repo group
    "tune_weights",           # inspect reads; set/reset writes the tuning file
    "check_embedding_drift",  # reports by default; force=true re-pins the canary
    "finalize_handoff",       # persists a session handoff record (jdocmunch.handoff/v1)
})


def _apply_readonly_annotations(tools: list[Tool]) -> list[Tool]:
    """Attach ToolAnnotations(readOnlyHint=...) to any tool lacking annotations.

    Read tools (readOnlyHint=True) run silently in Claude Code plan mode; the
    write-set (_NON_READONLY_TOOLS) is marked False so those still prompt. Tools
    that already carry annotations are left untouched. Returns a new list; input
    Tool objects are copied (model_copy) rather than mutated.
    """
    annotated: list[Tool] = []
    for tool in tools:
        if tool.annotations is None:
            tool = tool.model_copy(
                update={
                    "annotations": ToolAnnotations(
                        readOnlyHint=tool.name not in _NON_READONLY_TOOLS
                    )
                }
            )
        annotated.append(tool)
    return annotated


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available tools."""
    return _apply_readonly_annotations(_filter_tools(_all_tools()))


def _all_tools() -> list[Tool]:
    """Return the unfiltered list of every tool exposed by this server."""
    return [
        Tool(
            name="index_local",
            description="Index a local folder containing documentation files (.md, .txt, .rst; plus .pdf/.docx/.pptx/.epub when the optional [office] extra is installed — converted to Markdown locally). Parses by heading hierarchy into sections for efficient retrieval. An already-indexed source is recognized before storage is chosen: the established handle is reused (or refreshed), an explicit conflicting name returns a conflict instead of creating a duplicate index, and multiple equivalent legacy indexes return bounded ambiguity. Embeddings auto-enable when a provider is configured (GOOGLE_API_KEY, OPENAI_API_KEY, openai-compatible + JDOCMUNCH_OPENAI_COMPAT_URL + JDOCMUNCH_OPENAI_COMPAT_MODEL, or sentence-transformers).",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to local folder (absolute or relative, supports ~ for home directory)"
                    },
                    "use_ai_summaries": {
                        "type": "boolean",
                        "description": "Use AI to generate section summaries (requires ANTHROPIC_API_KEY or GOOGLE_API_KEY). When false, uses heading text.",
                        "default": True
                    },
                    "use_embeddings": {
                        "description": "Generate semantic embeddings for each section, enabling hybrid (BM25+semantic) search. true/false/\"auto\". \"auto\" (default) enables embeddings when an embedding provider is configured (GOOGLE_API_KEY, OPENAI_API_KEY, openai-compatible + JDOCMUNCH_OPENAI_COMPAT_URL + JDOCMUNCH_OPENAI_COMPAT_MODEL, or sentence-transformers installed).",
                        "default": "auto"
                    },
                    "extra_ignore_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional gitignore-style patterns to exclude from indexing"
                    },
                    "include_dot_dirs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "jdoc#113 - directory NAMES to index even though they start with a dot, e.g. [\".claude\"]. Dotted directories are skipped by default so a tool's dotfile cache cannot be ingested as documentation; .github is always indexed. Names only, not paths."
                    },
                    "follow_symlinks": {
                        "type": "boolean",
                        "description": "Whether to follow symlinks. Default false for security.",
                        "default": False
                    },
                    "max_files": {
                        "type": "integer",
                        "description": "Maximum number of doc files to index. Default 10000. When the cap is hit, the response includes `truncated: true`, `discovered: <total found>`, and `indexed: <max_files>` so the caller can detect data loss programmatically. Raise this for very large corpora.",
                        "default": 10_000
                    },
                    "sort_by": {
                        "type": "string",
                        "enum": ["newest", "walk_order"],
                        "description": "Order in which files are truncated when discovered > max_files. 'newest' (default) keeps the most recently-edited files so a fresh edit is always in the index. 'walk_order' preserves filesystem-walk order for deterministic reproducible builds. No effect when corpus fits under the cap.",
                        "default": "newest"
                    },
                    "name": {
                        "type": "string",
                        "description": "Optional repo identifier override. Use this when two folders share the same name (e.g. both named 'docs'). If omitted, the folder name is used. Example: 'requests-docs', 'flask-docs'."
                    },
                    "incremental": {
                        "type": "boolean",
                        "description": "When true (default), only re-index files that changed since the last index. Set to false to force a full re-index.",
                        "default": True
                    },
                    "autotune": {
                        "type": "boolean",
                        "description": "v1.29+ — when true, runs tune_weights against accumulated ranking events at the end of indexing. No-op when telemetry isn't enabled.",
                        "default": False
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of explicit paths to index. When provided, the directory walk is skipped; only these files (and the contents of any directories in the list) are indexed. Entries may be absolute or relative to `path`. Useful for batch-indexing exactly the files an agent already knows about — e.g. the doc files git just touched."
                    },
                    "worktree_mode": {
                        "type": "string",
                        "enum": ["reuse_equivalent", "branch_local"],
                        "description": "Linked-worktree behavior (jdoc#83). 'reuse_equivalent' (default) reuses a proven-fresh established index from another linked worktree instead of creating a duplicate; uncertain outcomes return a bounded decision with no write. 'branch_local' intentionally creates/refreshes an exact-path index for this worktree.",
                        "default": "reuse_equivalent"
                    },
                    "legacy_reconcile": {
                        "type": "string",
                        "enum": ["report", "apply"],
                        "description": "Part C.2 legacy reconciliation (jdoc#87). Requires an explicit name= selecting a pre-1.102 fieldless legacy index and a full refresh. 'report' proves whether it is an exact duplicate of its single modern peer (same verified identity, same clean certified commit, full path-and-hash coverage) without changing anything; 'apply' repeats the proof and retires the selected legacy handle — the only possible loser; the peer is never touched. Omitted: ordinary refresh, backfill-only, never retires."
                    }
                },
                "required": ["path"]
            }
        ),
        Tool(
            name="doc_index_repo",
            description="Index a GitHub repository's documentation. Fetches .md/.txt files, parses sections, and saves to local storage. Embeddings auto-enable when a provider is configured (GOOGLE_API_KEY, OPENAI_API_KEY, openai-compatible + JDOCMUNCH_OPENAI_COMPAT_URL + JDOCMUNCH_OPENAI_COMPAT_MODEL, or sentence-transformers). Indexes .md and .txt only; every other file in the repo is ignored.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "GitHub repository URL or owner/repo string"
                    },
                    "ref": {
                        "type": "string",
                        "description": "Optional GitHub branch, tag, or commit-ish to index. If omitted, HEAD is used. The ref is resolved to a commit SHA before fetching content; repo@sha remains the durable lookup handle."
                    },
                    "use_ai_summaries": {
                        "type": "boolean",
                        "description": "Use AI to generate section summaries.",
                        "default": True
                    },
                    "use_embeddings": {
                        "description": "Generate semantic embeddings for each section. true/false/\"auto\". \"auto\" (default) enables embeddings when an embedding provider is configured, including openai-compatible + JDOCMUNCH_OPENAI_COMPAT_URL + JDOCMUNCH_OPENAI_COMPAT_MODEL.",
                        "default": "auto"
                    },
                    "name": {
                        "type": "string",
                        "description": "Optional stored index name override. If omitted, the GitHub repo name is used. Must be a safe storage component: letters, numbers, dot, underscore, and hyphen only."
                    },
                    "incremental": {
                        "type": "boolean",
                        "description": "When true (default), skip all HTTP fetches if the selected GitHub ref's commit SHA is unchanged; otherwise only re-index changed files. Set to false to force a full re-index.",
                        "default": True
                    }
                },
                "required": ["url"]
            }
        ),
        Tool(
            name="doc_list_repos",
            description=(
                "List every indexed documentation repo with its identifier and storage location. Call it first to find out whether the docs you need are already indexed, and to get the repo id every other tool needs. Lists only indexes under the active storage_path, so an empty list means nothing is indexed there."
            ),
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="get_index_overview",
            description="v1.56+ — single-call repo snapshot: doc_count, section_count, total_byte_size, format_breakdown, top_tags, top_roles, indexed_at. Composition of v1.46/v1.50/v1.55 aggregations. Use for 'what is this repo at a glance?'. Counts come from the index, so they are only as fresh as the last index run.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier"
                    },
                    "top_n": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 5,
                        "description": "Top-N tags and roles to surface. 0 omits both lists; full distributions still available via get_all_tags / get_all_roles."
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="get_doc",
            description="v1.58+ — single-doc detail view. Pairs with list_docs (cross-doc inventory). Returns section list (handles), role_distribution, tag_distribution, byte_size, format, indexed_at for one doc. No content reads.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier"},
                    "doc_path": {"type": "string", "description": "Document path within the repo, e.g. 'api/auth.md'"}
                },
                "required": ["repo", "doc_path"]
            }
        ),
        Tool(
            name="list_docs",
            description="v1.55+ — flat per-doc inventory of an indexed repo: doc_path, section_count, format, byte_size for each indexed document. Lighter than get_toc_tree (which returns full section trees per doc). Sorted by doc_path. Inventory only; it returns no section titles and no content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier"
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="get_toc",
            description="Get a flat table of contents for all sections in a repo, sorted by document order. Content is excluded — use get_section to retrieve content. Scope with path_glob; for a SINGLE document use get_document_outline (this tool has no doc_path parameter).",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "path_glob": {
                        "type": "string",
                        "description": "v1.36+ — fnmatch glob restricting results to matching doc_paths (e.g. 'api/**/*.md', 'reference/*'). Default: no filter."
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="get_toc_tree",
            description="Get a nested table of contents tree per document. Shows parent/child heading relationships. Content is excluded.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "path_glob": {
                        "type": "string",
                        "description": "v1.36+ — fnmatch glob restricting results to matching doc_paths. Default: no filter."
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="get_document_outline",
            description="Get the section hierarchy for a single document file, without content. Headings only, no content. Read a section with get_section.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier"
                    },
                    "doc_path": {
                        "type": "string",
                        "description": "Path to the document within the repository (e.g., 'README.md')"
                    }
                },
                "required": ["repo", "doc_path"]
            }
        ),
        Tool(
            name="search_sections",
            description="Search sections by relevance. Hybrid (BM25 lexical + semantic embedding) fusion when the index was built with use_embeddings=true; falls back to lexical-only otherwise. Returns summaries only — use get_section for full content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier"
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query"
                    },
                    "doc_path": {
                        "type": "string",
                        "description": "Optional: limit search to a specific document"
                    },
                    "path_glob": {
                        "type": "string",
                        "description": "v1.36+ — fnmatch glob restricting results to matching doc_paths (e.g. 'api/**/*.md'). Stacks with doc_path."
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return",
                        "default": 10
                    },
                    "semantic": {
                        "type": "boolean",
                        "description": "null/omit (auto — hybrid when embeddings exist), true (force hybrid), false (force lexical-only). Zero performance cost when the index has no embeddings."
                    },
                    "semantic_only": {
                        "type": "boolean",
                        "description": "Skip lexical scoring; rank purely by embedding cosine similarity.",
                        "default": False
                    },
                    "semantic_weight": {
                        "type": "number",
                        "description": "Weight (0.0–1.0) of semantic component in hybrid fusion. Lexical gets 1 - weight. Omit to use this repo's tuned weight, else 0.5; a value you pass is always honoured. _meta.semantic_weight and _meta.semantic_weight_source report which applied. Paraphrased queries usually want 0.7–0.95: RRF (k=60) structurally penalises an answer that is strong in only one channel, so 0.5 can rank below a section that is mediocre in both."
                    },
                    "role": {
                        "type": "string",
                        "description": "Optional v1.19+ role filter. Values: concept, tutorial, how_to, reference, api, example, troubleshooting, changelog, faq, other."
                    },
                    "profile": {
                        "type": "string",
                        "enum": ["install", "debug", "explain", "api"],
                        "description": "v1.32+ — task-aware retrieval profile. install/debug/explain/api each boost a small role set so matching sections rank ahead. Explicit role= overrides."
                    },
                    "dedupe": {
                        "type": "boolean",
                        "default": False,
                        "description": "v1.34+ — collapse near-duplicate sections to a single representative based on the v1.34 cluster sidecar. _meta.deduped reports suppressed member ids."
                    },
                    "min_answerability": {
                        "type": "number",
                        "description": "v1.42+ — drop results whose v1.33 _answerability score is below this threshold (0–1). _meta.quality_filtered reports drop count."
                    },
                    "min_quotability": {
                        "type": "number",
                        "description": "v1.42+ — drop results whose v1.33 _quotability score is below this threshold (0–1). Stacks with min_answerability."
                    },
                    "min_level": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "v1.44+ — restrict to sections at heading level >= this. Inclusive."
                    },
                    "max_level": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "v1.44+ — restrict to sections at heading level <= this. Inclusive. Stacks with min_level."
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "v1.45+ — restrict to sections whose Section.tags contains every listed tag (AND semantics). Case-insensitive."
                    },
                    "exclude_tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "v1.51+ — drop sections whose Section.tags contains ANY listed tag (negative ANY-match). Stacks with `tags`. Case-insensitive."
                    },
                    "roles": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "v1.52+ — restrict to sections whose metadata.role matches ANY listed role (positive OR-match). Differs from singular `role` (which is exact). Case-insensitive."
                    },
                    "exclude_roles": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "v1.52+ — drop sections whose metadata.role matches ANY listed role. Case-insensitive. Stacks with `roles` (the result must match an included role and not match any excluded role)."
                    },
                    "min_byte_length": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "v1.53+ — drop sections shorter than this many bytes (byte_end - byte_start). Use to filter out stubs / one-liners."
                    },
                    "max_byte_length": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "v1.53+ — drop sections longer than this many bytes. Use to filter out oversized dumps. Stacks with min_byte_length."
                    },
                    "repo_group": {
                        "type": "string",
                        "description": "v1.26+ — fan out across the named repo group (defined via define_repo_group). When set, the per-repo `repo` arg is ignored; results from each member repo are fused via RRF."
                    },
                    "compact": {
                        "type": "boolean",
                        "default": False,
                        "description": "v1.121+ — drop per-row fields a caller can't act on (repo, parent_id, children, byte_start/byte_end, content_hash, inline_code, references; plus empty tags and a summary identical to the title). Per-row _freshness is kept only when it isn't 'fresh'. ~40% fewer bytes per result. Default off — the full row is unchanged."
                    },
                    "fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "v1.121+ — explicit per-row field whitelist (e.g. ['id','title','doc_path','_score']). Wins over compact. `id` is always returned."
                    },
                    "snippet_bytes": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 0,
                        "description": "v1.121+ — inline the first N bytes of each section's body as `snippet` so a confident top hit needs no get_section round-trip. UTF-8 safe (never splits a codepoint); `snippet_truncated: true` marks a cut section. 0 = off."
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="count_sections",
            description="v1.59+ — count sections matching the same filter set as search_sections (path_glob, role/roles/exclude_roles, tags/exclude_tags, min/max_level, min/max_byte_length) but skip ranking. Use for UI counters or 'does anything match?' probes. Returns the count only, never the matching sections.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "doc_path": {"type": "string"},
                    "path_glob": {"type": "string"},
                    "role": {"type": "string"},
                    "roles": {"type": "array", "items": {"type": "string"}},
                    "exclude_roles": {"type": "array", "items": {"type": "string"}},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "exclude_tags": {"type": "array", "items": {"type": "string"}},
                    "min_level": {"type": "integer", "minimum": 0},
                    "max_level": {"type": "integer", "minimum": 0},
                    "min_byte_length": {"type": "integer", "minimum": 0},
                    "max_byte_length": {"type": "integer", "minimum": 0}
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="search_titles",
            description="v1.57+ — fast title-only token-overlap match. Different from search_sections (full hybrid retrieval). Use for navigation: 'find the section whose heading text matches X'. Handle-only output ({id, title, level, doc_path, _score}); no content reads, no embeddings.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier"
                    },
                    "query": {
                        "type": "string",
                        "description": "Heading text to match against"
                    },
                    "max_results": {
                        "type": "integer",
                        "default": 10,
                        "minimum": 1
                    }
                },
                "required": ["repo", "query"]
            }
        ),
        Tool(
            name="get_section",
            description="Retrieve the full content of a specific section using byte-range reads. Use after identifying section IDs via search_sections or get_toc. Returns this section's own bytes; nested child sections are not included.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier"
                    },
                    "section_id": {
                        "type": "string",
                        "description": "Section ID from get_toc, search_sections, or get_document_outline"
                    },
                    "verify": {
                        "type": "boolean",
                        "description": "Verify content hash matches stored hash (detects source drift)",
                        "default": False
                    },
                    "strip_boilerplate": {
                        "type": "boolean",
                        "description": "v1.24+ — when true, suppress repeated cross-section fragments (footers, nav, license headers) before returning content.",
                        "default": False
                    },
                    "compress_code": {
                        "type": "boolean",
                        "description": "v1.35+ — when true, drop blank lines and full-line comments inside fenced code blocks before returning. _meta.code_compressed_bytes reports bytes saved.",
                        "default": False
                    }
                },
                "required": ["repo", "section_id"]
            }
        ),
        Tool(
            name="get_sections",
            description="Batch content retrieval for multiple sections in one call. Content only for the ids you pass; unknown ids come back as per-id errors, not a failed call.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier"
                    },
                    "section_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of section IDs to retrieve"
                    },
                    "verify": {
                        "type": "boolean",
                        "description": "Verify content hashes",
                        "default": False
                    },
                    "strip_boilerplate": {
                        "type": "boolean",
                        "description": "v1.24+ — strip repeated cross-section fragments per section before returning.",
                        "default": False
                    },
                    "compress_code": {
                        "type": "boolean",
                        "description": "v1.35+ — drop blank lines and full-line comments inside fenced code blocks before returning. _meta.code_compressed_bytes reports total bytes saved.",
                        "default": False
                    }
                },
                "required": ["repo", "section_ids"]
            }
        ),
        Tool(
            name="get_section_context",
            description="Retrieve a section with its full hierarchy context: ancestor headings (root → parent) for orientation, the target section's content, and immediate child summaries. Prevents 'section too thin' without falling back to whole-file reads.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "section_id": {
                        "type": "string",
                        "description": "Target section ID from get_toc, search_sections, etc."
                    },
                    "max_tokens": {
                        "type": "integer",
                        "description": "Approximate token budget for the target section's content (bytes/4 estimate). Ancestors and child summaries are always included.",
                        "default": 2000
                    },
                    "include_children": {
                        "type": "boolean",
                        "description": "Include immediate child section summaries (no content reads). Default true.",
                        "default": True
                    },
                    "include_related": {
                        "type": "boolean",
                        "description": "v1.20+ adaptive context: append structural + semantic neighbor summaries.",
                        "default": False
                    },
                    "strip_boilerplate": {
                        "type": "boolean",
                        "description": "v1.24+ — strip repeated cross-section fragments before returning the target section content.",
                        "default": False
                    }
                },
                "required": ["repo", "section_id"]
            }
        ),
        Tool(
            name="describe_section",
            description="v1.54+ — consolidated handle bundle: full metadata + ancestor breadcrumb + prev/next/parent/first_child neighbors for one section in a single call. Saves three round-trips vs calling get_section_summary + get_section_path + section_neighbors separately. No content reads.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier"
                    },
                    "section_id": {
                        "type": "string",
                        "description": "Target section ID"
                    }
                },
                "required": ["repo", "section_id"]
            }
        ),
        Tool(
            name="section_neighbors",
            description="v1.37+ — return prev/next siblings (in document order), parent, and first child for a section. Handles only (id, title, level, doc_path) — no content. Use for fast sequential navigation without re-querying search_sections.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "section_id": {
                        "type": "string",
                        "description": "Target section ID from get_toc, search_sections, etc."
                    }
                },
                "required": ["repo", "section_id"]
            }
        ),
        Tool(
            name="get_recent_changes",
            description="v1.47+ — list sections that have drifted from index state (edited_uncommitted or stale_index buckets via the v1.16 FreshnessProbe). By default compares the index against the cached raw-content mirror, NOT live workspace files; pass live_source=true to read the live files under the index's source_root. _meta.drift_layer reports which layer ran. Pre-flight check before deciding whether to re-index. Handle-only — no content reads.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier"
                    },
                    "include_stale": {
                        "type": "boolean",
                        "default": True,
                        "description": "Include sections in stale_index bucket (byte range no longer hashes the same)."
                    },
                    "include_edited": {
                        "type": "boolean",
                        "default": True,
                        "description": "Include sections in edited_uncommitted bucket (file changed but this section's range still matches)."
                    },
                    "live_source": {
                        "type": "boolean",
                        "default": False,
                        "description": "Read the live workspace files under the index's source_root instead of the cached mirror. Falls back to the cached mirror (drift_layer='cached_mirror', live_source_available=false) when no usable source_root is recorded."
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="get_all_roles",
            description="v1.50+ — list every distinct role classification across the repo with per-role section counts and id samples. Companion to v1.46 get_all_tags. Sections without metadata.role are bucketed under 'unknown'. Use to discover what roles exist before constructing a `role=` or `profile=` query.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier"
                    },
                    "sample_size": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 3,
                        "description": "How many section_ids to surface per role. 0 omits samples."
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="get_all_tags",
            description="v1.46+ — list every unique #hashtag across the repo with per-tag section counts. Companion to the v1.45 `tags` filter on search_sections — use this to discover what tag namespaces exist before constructing a tag-filtered query. Lowercase-normalized. Aggregates the tags the index stored on each section, so a tag added since the last index is missing.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier"
                    },
                    "min_section_count": {
                        "type": "integer",
                        "minimum": 1,
                        "default": 1,
                        "description": "Drop tags appearing in fewer than this many sections (filter out typos)."
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="get_section_descendants",
            description="v1.43+ — return every descendant of a section (BFS over parent_id) in document order with depth offset. Pairs with get_section_path (ancestors). Optional max_depth caps the walk; max_depth=1 returns immediate children only. Handles only — no content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier"
                    },
                    "section_id": {
                        "type": "string",
                        "description": "Target section. Its descendants are returned; target itself is not included."
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Optional cap on traversal depth. None = full subtree. 1 = immediate children only.",
                        "minimum": 0
                    }
                },
                "required": ["repo", "section_id"]
            }
        ),
        Tool(
            name="get_section_excerpts",
            description="v1.49+ — batch counterpart to get_section_excerpt. Resolves N previews in one call against a single index load. Per-id errors reported in-line. _meta.tokens_saved aggregates byte savings across the batch. Previews are truncated by design; read full content with get_sections.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier"
                    },
                    "section_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of section IDs. Order preserved; each entry carries `requested_id` for correlation."
                    },
                    "max_bytes": {
                        "type": "integer",
                        "default": 500,
                        "description": "Per-section soft cap in UTF-8 bytes."
                    }
                },
                "required": ["repo", "section_ids"]
            }
        ),
        Tool(
            name="get_section_excerpt",
            description="v1.41+ — return a short content preview (default 500 bytes) for one section. Trimmed to last newline before the cap so it ends on a paragraph boundary. Use to peek at content before paying for a full get_section read. _meta.tokens_saved reports the byte-savings vs full content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "section_id": {
                        "type": "string",
                        "description": "Target section ID"
                    },
                    "max_bytes": {
                        "type": "integer",
                        "description": "Soft cap on excerpt size in UTF-8 bytes. Default 500.",
                        "default": 500
                    }
                },
                "required": ["repo", "section_id"]
            }
        ),
        Tool(
            name="get_section_path",
            description="v1.40+ — return the breadcrumb chain (root → ... → target) for a section_id. Walks parent_id upward; cycle-protected. Handles only ({id, title, level, doc_path}) per step plus depth.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "section_id": {
                        "type": "string",
                        "description": "Target section ID from get_toc, search_sections, etc."
                    }
                },
                "required": ["repo", "section_id"]
            }
        ),
        Tool(
            name="get_orphan_sections",
            description="v1.39+ — list sections whose doc_path receives zero inbound references from any other doc. Companion to get_broken_links and get_stale_pages: documentation that exists but nobody links to. Inbound links are counted across indexed docs only, so a link from code or an external site does not rescue a section.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "include_same_doc": {
                        "type": "boolean",
                        "description": "If true, count intra-document anchor links as inbound (e.g. a TOC at the top of a page). Default false — only cross-document references count.",
                        "default": False
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="get_section_summary",
            description="v1.38+ — return full indexed metadata (title, summary, role, tags, metadata, parent_id, children, content_hash, byte_start/end, byte_length) for one section without fetching content. Use to inspect role/tags before deciding whether to read the content via get_section.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "section_id": {
                        "type": "string",
                        "description": "Target section ID from get_toc, search_sections, etc."
                    }
                },
                "required": ["repo", "section_id"]
            }
        ),
        Tool(
            name="get_section_summaries",
            description="v1.48+ — batch version of get_section_summary. Resolve metadata for many ids in one call against a single index load. Per-id errors are reported in-line on the corresponding result entry rather than aborting the batch.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier"
                    },
                    "section_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of section IDs to look up. Order preserved in response; each entry carries `requested_id` for correlation."
                    }
                },
                "required": ["repo", "section_ids"]
            }
        ),
        Tool(
            name="delete_index",
            description="Remove a repo index and its cached raw files. Deletes the index and its cached files, never your source documents. There is no undo; re-index to restore.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="get_broken_links",
            description=(
                "Scan indexed doc files for internal cross-references that no longer resolve. "
                "Checks markdown links, RST :ref:/:doc: directives, and anchor-only links (#heading). "
                "External links (http/https) are skipped. "
                "Output: list of {source_file, source_section, target, reason} where reason is "
                "'file_not_found', 'section_not_found', or 'anchor_not_found'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="get_doc_coverage",
            description=(
                "Check which jcodemunch symbols have matching documentation in this doc index. "
                "Given a list of jcodemunch symbol IDs, reports which symbols are mentioned in "
                "section titles (documented) vs absent (undocumented). "
                "Bridges jcodemunch <-> jdocmunch. symbol_ids capped at 200. "
                "Output: {documented, undocumented, coverage_pct}."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Doc repo identifier (owner/repo or just repo name)"
                    },
                    "symbol_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of jcodemunch symbol IDs to check coverage for"
                    }
                },
                "required": ["repo", "symbol_ids"]
            }
        ),
        Tool(
            name="get_backlinks",
            description=(
                "Find all sections that link TO a given document (inverse reference graph). "
                "Useful for the LLM Wiki pattern: when a source changes, find which wiki pages reference it. "
                "Output: list of {source_file, source_section, source_section_id, link}."
            
                " Counts links found inside indexed docs only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "doc_path": {
                        "type": "string",
                        "description": "Target document path to find backlinks for (e.g., 'raw/article.md' or 'wiki/concepts/auth.md')"
                    }
                },
                "required": ["repo", "doc_path"]
            }
        ),
        Tool(
            name="get_stale_pages",
            description=(
                "Find wiki pages whose declared sources have been modified on disk. "
                "Convention: wiki pages include YAML frontmatter with a 'sources' list of relative paths "
                "to raw source files. This tool checks whether those source files have changed since the "
                "page was last indexed. Output: list of {doc_path, title, stale_sources} where each "
                "stale source has a reason: 'modified', 'missing', or 'untracked'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "sources_dir": {
                        "type": "string",
                        "description": "Base directory for resolving relative source paths. If omitted, uses the index's source_root."
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="get_wiki_stats",
            description=(
                "Wiki health dashboard. Returns: orphan pages (zero inbound internal links), "
                "most-linked pages (top 10), tag distribution, total internal link count, "
                "and sections-per-doc min/max/avg. Use for periodic wiki lint checks."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="analyze_perf",
            description=(
                "Per-tool latency analysis. window='session' reads the in-memory ring "
                "(last 512 calls per tool — always available); window='1h'|'24h'|'7d'|'all' "
                "reads the persistent SQLite sink at ~/.doc-index/telemetry.db (opt-in via "
                "JDOCMUNCH_PERF_TELEMETRY=1). Returns {window, telemetry_enabled, source, "
                "per_tool:{tool:{count,p50_ms,p95_ms,max_ms,errors,error_rate}}}."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window": {
                        "type": "string",
                        "enum": ["session", "1h", "24h", "7d", "all"],
                        "default": "session",
                        "description": "Time window. 'session' uses the in-memory ring; longer windows require JDOCMUNCH_PERF_TELEMETRY=1."
                    }
                }
            }
        ),
        Tool(
            name="get_session_stats",
            description=(
                "Session self-monitor: returns {latency_per_tool, total_tokens_saved}. "
                "Lightweight; reads the in-memory latency ring + persistent savings counter. "
                "For windowed analysis use analyze_perf."
            
                " The latency ring lives in memory, so a server restart clears it."
            ),
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="get_watch_status",
            description=(
                "Doc-watcher coverage + login-service state. Reports whether the "
                "`jdocmunch-watch` background service is active and, per locally-indexed "
                "doc repo, whether its source_root still exists on disk (watchable). "
                "Returns {service, watchable_repo_count, local_repo_count, repos[], hint}. "
                "Run `jdocmunch-mcp watch` (foreground) or `watch-install` (login service) "
                "to keep indexes fresh on any on-disk doc change."
            
                " Covers locally-indexed repos only; a GitHub-indexed repo has no source_root to watch."
            ),
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="finalize_handoff",
            description=(
                "Finalize one canonical Markdown handoff for a completed documentation "
                "audit/analysis (jdocmunch.handoff/v1; suite parity with jCodeMunch). "
                "The server assembles YOUR sections deterministically, validates every "
                "evidence_refs entry against what this session actually retrieved "
                "(section ids or doc paths served by search_sections / search_titles / "
                "get_section / get_sections — unknown refs fail closed), persists the "
                "result session-scoped, and returns a compact receipt {handoff_id, "
                "resource_uri, sha256, length, canonical:true}. Read the immutable body "
                "via the munch://handoff/<id> resource; repeated reads are byte-identical. "
                "Appendices are included exactly once; no character limit; never writes "
                "to the documentation corpus."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Doc repo identifier the handoff is about.",
                    },
                    "task": {
                        "type": "string",
                        "description": "The task/question this handoff answers (becomes the title).",
                    },
                    "sections": {
                        "type": "array",
                        "description": "Ordered report sections, each {heading, content} (markdown). The caller authors these; the server only assembles. Optional per-section claims[] bind evidence to an individual claim instead of one global list (handoff/v2).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string"},
                                "content": {"type": "string"},
                                "claims": {
                                    "type": "array",
                                    "description": "Optional caller-authored claims, each {id, statement, evidence_refs, classification?}. Ids must be unique across the handoff; each claim's refs are attested separately and rendered beside the claim.",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string"},
                                            "statement": {"type": "string"},
                                            "evidence_refs": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                            },
                                            "classification": {"type": "string"},
                                        },
                                        "required": ["id", "statement", "evidence_refs"],
                                    },
                                },
                            },
                            "required": ["heading"],
                        },
                    },
                    "evidence_refs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Section ids or doc paths retrieved this session; validated against the session retrieval record.",
                    },
                    "profile": {
                        "type": "string",
                        "default": "general",
                        "description": "Handoff profile label (e.g. doc_audit).",
                    },
                    "appendices": {
                        "type": "array",
                        "description": "Optional named appendices, each {name, content, content_type?}; names must be unique.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "content": {"type": "string"},
                                "content_type": {"type": "string"},
                            },
                            "required": ["name", "content"],
                        },
                    },
                },
                "required": ["repo", "task", "sections", "evidence_refs"],
            },
        ),
        Tool(
            name="find_code_examples",
            description=(
                "Search fenced code blocks across the indexed docs by BM25 over the block "
                "content. Returns one row per block with {block_id, section_id, doc_path, "
                "title, lang, byte_start, byte_end, snippet, _score}. Optional lang filter "
                "(e.g. 'python', 'bash') and doc_path/path_glob scope filters (applied "
                "before scoring, same contract as search_sections). Use after index_local; "
                "requires INDEX_VERSION>=3."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "jdocmunch repo identifier"},
                    "query": {"type": "string", "description": "Free-form code-content query"},
                    "lang": {"type": "string", "description": "Optional case-insensitive language filter"},
                    "doc_path": {"type": "string", "description": "Optional exact-document scope: only blocks in the section with this doc_path"},
                    "path_glob": {"type": "string", "description": "Optional fnmatch glob (e.g. 'docs/api/**') scoping blocks to matching document paths"},
                    "max_results": {"type": "integer", "default": 10}
                },
                "required": ["repo", "query"]
            }
        ),
        Tool(
            name="link_code_to_symbols",
            description=(
                "Best-effort bridge from doc code blocks to jcodemunch code symbols. "
                "For each block, tokenizes identifiers and looks them up via "
                "jcodemunch's search_symbols. Returns {by_block, by_symbol, _meta} where "
                "_meta.bridge_available reports whether jcodemunch-mcp is importable."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "jdocmunch repo identifier"},
                    "code_repo": {"type": "string", "description": "jcodemunch repo identifier"},
                    "max_examples": {"type": "integer", "default": 200},
                    "max_symbols_per_block": {"type": "integer", "default": 5}
                },
                "required": ["repo", "code_repo"]
            }
        ),
        Tool(
            name="resolve_related_code_repos",
            description=(
                "Map a jdocmunch docs repo to candidate jCodeMunch code repo handles by "
                "source_root. The two suites use independent repo-identity models, so a docs "
                "handle (e.g. 'local/foo-docs') is NOT a valid jCodeMunch code_repo. Given a "
                "docs repo, returns candidates[{repo, confidence, reason, source_root}] "
                "(exact source_root match = high; containment = medium/low), an 'ambiguous' "
                "flag, and _meta.bridge_available. Use it to pick the right code_repo for the "
                "link_code_to_symbols / get_undocumented_symbols bridge tools."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "jdocmunch docs repo identifier"}
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="doc_resolve_repo",
            description=(
                "Resolve a filesystem path (index root, subfolder, or file) to its indexed "
                "documentation repo handle via stored source_root metadata — O(1)-sized "
                "response, use instead of doc_list_repos when the path is known. Exact root "
                "match wins, then the most specific containing root; equally-specific "
                "duplicates return ambiguous:true with a bounded candidates list (max 5) "
                "plus total_matches. GitHub-indexed corpora (no source_root) never match. "
                "Read-only: never creates, refreshes, or deletes an index. Prefer absolute "
                "paths; relative paths resolve against the server CWD (echoed as "
                "_meta.resolved_path)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Filesystem path — index root, subfolder, or file (absolute preferred)"
                    }
                },
                "required": ["path"]
            }
        ),
        Tool(
            name="find_endpoint",
            description=(
                "Find OpenAPI operations by path glob, method, and/or tag. All filters AND'd. "
                "Returns one row per match with {section_id, doc_path, method, path, "
                "operationId, summary, tags, deprecated}. Requires the spec to have been "
                "indexed under v1.18+ so structured metadata is present."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "path": {"type": "string", "description": "fnmatch glob (e.g. '/pets/*'); case-sensitive"},
                    "method": {"type": "string", "description": "HTTP method; case-insensitive"},
                    "tag": {"type": "string", "description": "Exact tag match"}
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="list_endpoints_by_tag",
            description=(
                "Return every operation whose tags list contains the given tag (exact). "
                "Convenience wrapper around find_endpoint with only a tag filter."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "tag": {"type": "string"}
                },
                "required": ["repo", "tag"]
            }
        ),
        Tool(
            name="find_operations_using_schema",
            description=(
                "Return every operation whose request body or any response references the "
                "given schema. Each row gets a referenced_in list of all schema names that "
                "operation pulls in (so you can see the broader dependency cluster)."
            
                " Resolves references inside the indexed OpenAPI document only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "schema_name": {"type": "string"}
                },
                "required": ["repo", "schema_name"]
            }
        ),
        Tool(
            name="get_schema_graph",
            description=(
                "BFS walk of the schema reference graph from a root schema name. Returns "
                "{root, nodes:{name:{type, properties, required, refs}}, edges:[[from, to]], "
                "unresolved}. max_depth bounds the walk (default 5)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "schema_name": {"type": "string"},
                    "max_depth": {"type": "integer", "default": 5}
                },
                "required": ["repo", "schema_name"]
            }
        ),
        Tool(
            name="lookup_term",
            description=(
                "Glossary lookup. Returns every entry whose term equals the query (case-"
                "insensitive, exact). Glossary entries are extracted at index time from "
                "**Term** — definition Markdown patterns and RST .. glossary:: blocks."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "term": {"type": "string"}
                },
                "required": ["repo", "term"]
            }
        ),
        Tool(
            name="list_terms",
            description=(
                "List glossary terms in alphabetical order, optionally filtered by prefix. "
                "Capped at max_results (default 100)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "prefix": {"type": "string"},
                    "max_results": {"type": "integer", "default": 100}
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="get_related_sections",
            description=(
                "v2.0+ related-section graph. Returns structural neighbors (siblings, "
                "children, parent, optional cousins) and semantic neighbors (top-N "
                "cosine over stored embeddings, score >= min_score). mode: structural "
                "| semantic | both."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "section_id": {"type": "string"},
                    "mode": {"type": "string", "enum": ["structural", "semantic", "both"], "default": "both"},
                    "top_n": {"type": "integer", "default": 5},
                    "min_score": {"type": "number", "default": 0.6},
                    "max_per_kind": {"type": "integer", "default": 10}
                },
                "required": ["repo", "section_id"]
            }
        ),
        Tool(
            name="get_section_diff",
            description=(
                "Unified diff between the indexed snapshot and the current on-disk byte "
                "range for a section. Returns hashes + diff text; identical=true when "
                "the section is in sync with disk."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "section_id": {"type": "string"}
                },
                "required": ["repo", "section_id"]
            }
        ),
        Tool(
            name="get_doc_health",
            description=(
                "One-shot index health diagnostics. Returns section_count, doc_count, "
                "role_distribution, freshness counts, broken_link_count, drift status, "
                "BM25 corpus sanity, and embedding coverage."
            
                " Diagnoses the index, not the writing; a healthy report says nothing about whether the docs are correct."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string"}
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="doc_health_radar",
            description=(
                "Six-axis health radar for a doc repo: freshness, link_integrity, "
                "orphan_health, embedding_coverage, role_coverage, drift_health "
                "(omitted when no canary). Each axis is 0-100, plus composite + A-F grade. "
                "Pairs with diff_doc_health_radar for snapshot deltas. Mirrors jcm's "
                "and jData's health-radar shape — third leg of the suite-wide pattern."
            
                " Grades the index, not the prose; none of the six axes read the writing itself."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string"}
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="get_doc_pr_risk_profile",
            description=(
                "Composite doc-PR risk profile. Fuses volume + blast_radius + "
                "backlink_burden + tutorial_disruption + role_weight signals over "
                "a caller-supplied list of changed sections into a 0-1 risk_score "
                "with risk_level (low/medium/high/critical), top-5 blockers, and "
                "a one-line recommended_action. Caller computes the change list "
                "from a git diff or pairs with get_recent_changes. Mirrors jcm's "
                "get_pr_risk_profile."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "changed_sections": {
                        "type": "array",
                        "description": (
                            "List of changed sections. Each entry can be a bare "
                            "section_id (str, kind defaults to 'modified') or "
                            "{section_id, kind} where kind in {added, modified, deleted}."
                        ),
                        "items": {
                            "oneOf": [
                                {"type": "string"},
                                {
                                    "type": "object",
                                    "properties": {
                                        "section_id": {"type": "string"},
                                        "kind": {"type": "string", "enum": ["added", "modified", "deleted"]},
                                    },
                                    "required": ["section_id"],
                                },
                            ]
                        },
                    },
                },
                "required": ["repo", "changed_sections"],
            },
        ),
        Tool(
            name="diff_doc_health_radar",
            description=(
                "Diff two doc_health_radar payloads. Pure function — pass the `radar` "
                "sub-field from two doc_health_radar responses (e.g. yesterday vs today). "
                "Returns per-axis deltas, composite delta, grade change, regression and "
                "improvement lists (threshold: 3 points), one-line verdict."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "baseline": {"type": "object", "description": "Baseline radar payload."},
                    "current": {"type": "object", "description": "Current radar payload."}
                },
                "required": ["baseline", "current"]
            }
        ),
        Tool(
            name="get_tutorial_path",
            description=(
                "Reconstruct an ordered tutorial chain starting from section_id. Detects "
                "frontmatter next:/prev: keys, inline 'Next:' / 'Previous:' markdown links, "
                "or ordered numeric filename prefixes (01-intro.md). Returns chain[] of "
                "{section_id, doc_path, title} plus the strategy used."
            
                " Follows only the three conventions above, so a tutorial wired up any other way returns a short chain."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "section_id": {"type": "string"}
                },
                "required": ["repo", "section_id"]
            }
        ),
        Tool(
            name="get_undocumented_symbols",
            description=(
                "Best-effort inverse coverage: enumerate symbols in the jcodemunch code_repo "
                "and return those whose name (or qualified name) does not appear anywhere in "
                "this doc index. _meta.bridge_available=false when jcodemunch-mcp is not "
                "importable in this environment."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "code_repo": {"type": "string"},
                    "max_symbols": {"type": "integer", "default": 1000}
                },
                "required": ["repo", "code_repo"]
            }
        ),
        Tool(
            name="list_repo_groups",
            description=(
                "List defined repo groups (v1.26+). Each group is a named alias for a "
                "set of indexed repos that search_sections can fan out across via the "
                "repo_group kwarg."
            
                " Lists the group definitions only; it does not check that every member repo is still indexed."
            ),
            inputSchema={"type": "object", "properties": {}}
        ),
        Tool(
            name="define_repo_group",
            description=(
                "Create, replace, or delete a repo group (v1.26+). Empty repos list "
                "deletes the group. Persisted to ~/.doc-index/_groups.jsonc (JSONC — "
                "hand-edits welcome)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "repos": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["name", "repos"]
            }
        ),
        Tool(
            name="tune_weights",
            description=(
                "Online weight tuning. Reads ranking_events from "
                "~/.doc-index/telemetry.db (requires JDOCMUNCH_PERF_TELEMETRY=1) "
                "and proposes a per-repo semantic_weight step. dry_run=true skips "
                "the disk write. min_events gates against early overfitting. "
                "Learns from a recency window of the ledger (default 90 days) so "
                "stale events can't anchor the weights. Learning compares "
                "confidence WITH vs WITHOUT the semantic channel, so a workload "
                "that only ever runs one mode produces no signal at all — use "
                "set_weight there instead of waiting."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Optional — single repo to tune. Omit to scan all repos with events. Required with set_weight."},
                    "min_events": {"type": "integer", "default": 50},
                    "dry_run": {"type": "boolean", "default": False},
                    "max_age_days": {"type": "integer", "default": 90, "description": "Only learn from ledger events newer than this many days. Keeps stale events from anchoring weights to an outdated query distribution. 0 = lifetime ledger."},
                    "set_weight": {"type": "number", "description": "Persist this semantic_weight for repo directly, skipping the ledger. For when you have measured the right value for a corpus rather than waiting for the tuner to walk there. Does not require telemetry. Clamped to the allowed bounds, and the response reports whether clamping occurred."}
                }
            }
        ),
        Tool(
            name="verify_index",
            description=(
                "Byte-offset integrity check. Walks every section, byte-range-reads "
                "the bytes, recomputes SHA-256, and compares to the stored content_hash. "
                "source='cache' (default) checks the INDEX MIRROR: a clean result proves "
                "the index is internally consistent, NOT that the source document is "
                "still current. source='live' checks the real workspace files under the "
                "index source_root, so an edited source drifts and a deleted one goes "
                "missing. _meta.verify_layer always names which one ran. Reports drift / "
                "missing / error counts plus the drifting section ids. Sample N sections "
                "via the sample arg for cheap CI checks."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "sample": {"type": "integer", "description": "Only verify the first N sections."},
                    "source": {
                        "type": "string",
                        "enum": ["cache", "live"],
                        "description": (
                            "Which bytes to verify. 'cache' (default) = the indexed raw "
                            "mirror; clean means the index is self-consistent and says "
                            "nothing about whether the source changed. 'live' = the "
                            "workspace files under source_root."
                        ),
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="check_embedding_drift",
            description=(
                "Embedding-drift canary. Without args, re-embeds the saved CANARY_STRINGS "
                "and reports per-canary cosine drift; alarm fires when max_drift > threshold "
                "(default 0.05 ≈ cosine<0.95). Pass capture=true to seed the snapshot first "
                "(idempotent unless force=true). Catches silent provider model upgrades that "
                "would otherwise corrupt index recall without changing dim."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "capture": {
                        "type": "boolean",
                        "default": False,
                        "description": "Embed CANARY_STRINGS and persist the snapshot."
                    },
                    "force": {
                        "type": "boolean",
                        "default": False,
                        "description": "With capture=true, overwrite an existing snapshot."
                    },
                    "threshold": {
                        "type": "number",
                        "default": 0.05,
                        "description": "Max allowed drift (1 - cosine). Default 0.05."
                    }
                }
            }
        ),
        Tool(
            name="find_similar_sections",
            description=(
                "Multi-signal section dedup detection. Fuses embedding cosine "
                "(when available) with title + body lexical Jaccard, clusters via "
                "union-find, ranks each cluster's canonical by backlink_count + "
                "size. Verdict tiers: near_duplicate, overlapping_topic, "
                "parallel_tutorial. Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "min_score": {
                        "type": "number",
                        "default": 0.7,
                        "description": "Pairwise score floor for clustering. Default 0.7."
                    },
                    "near_duplicate_threshold": {
                        "type": "number",
                        "default": 0.92,
                        "description": "Score at/above which a cluster is flagged near_duplicate."
                    },
                    "max_clusters": {"type": "integer", "default": 50},
                    "exclude_same_doc": {
                        "type": "boolean",
                        "default": False,
                        "description": "Skip pairs in the same doc. Useful for long pages with repeated structure."
                    },
                    "max_sections": {
                        "type": "integer",
                        "default": 1000,
                        "description": "Hard cap on sections examined. Default 1000."
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="get_section_blast_radius",
            description=(
                "Transitive impact of rewriting / restructuring a section. Walks the "
                "inbound reference graph to max_depth (default 3), classifies each "
                "hit as anchor / doc / tutorial, and returns direct_impact, "
                "transitive_impact, a summary, and a normalised blast_score in [0, 1]. "
                "Companion to get_backlinks (which is depth 1 only). Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "section_id": {
                        "type": "string",
                        "description": "Stable section ID, format owner/repo::doc_path::slug#level"
                    },
                    "max_depth": {
                        "type": "integer",
                        "default": 3,
                        "description": "BFS depth over the inbound reference graph. Default 3."
                    }
                },
                "required": ["repo", "section_id"]
            }
        ),
        Tool(
            name="check_section_delete_safe",
            description=(
                "Composite preflight: is this section safe to delete? Fuses tutorial-path "
                "membership, anchor-specific backlinks, transitive doc-level backlinks, and "
                "recent-edit recency into a single verdict (safe_to_delete, "
                "tutorial_path_blocking, anchor_referenced, backlinks_blocking, "
                "recently_edited_blocking) plus up to 5 ranked blockers and a one-line "
                "recommended_action. Read-only — never mutates the index."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "section_id": {
                        "type": "string",
                        "description": "Stable section ID, format owner/repo::doc_path::slug#level"
                    },
                    "transitive_depth": {
                        "type": "integer",
                        "default": 3,
                        "description": "Backlink BFS depth. Default 3."
                    },
                    "recent_edit_days": {
                        "type": "integer",
                        "default": 14,
                        "description": "Days within which a recent edit becomes a soft blocker. Default 14."
                    }
                },
                "required": ["repo", "section_id"]
            }
        ),
        Tool(
            name="jdocmunch_guide",
            description=(
                "Return the version-current CLAUDE.md / AGENT.md policy snippet for "
                "jdocmunch-mcp. Lets an agent keep a one-line CLAUDE.md (e.g. \"Call "
                "jdocmunch_guide and strictly follow its instructions.\") instead of "
                "pasting a static snippet that drifts from the installed version. "
                "Idempotent, no repo context required. Sibling of jcodemunch_guide."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


def _generate_doc_md_snippet() -> str:
    """Return the recommended CLAUDE.md prompt-policy snippet for jdocmunch-mcp.

    Mirrors jcodemunch-mcp's `_generate_claude_md_snippet`. Idempotent: produces
    the same text on every call for a given installed version.
    """
    categories = [
        ("Indexing", ["index_local", "doc_index_repo", "delete_index", "verify_index"]),
        ("Discovery", ["doc_list_repos", "doc_resolve_repo", "list_docs", "list_terms", "get_all_roles", "get_all_tags",
                       "get_index_overview", "list_repo_groups", "define_repo_group"]),
        ("Document navigation", ["get_doc", "get_toc", "get_toc_tree", "get_document_outline",
                                  "get_tutorial_path"]),
        ("Section retrieval", ["search_sections", "search_titles", "count_sections",
                                "get_section", "get_sections", "get_section_excerpt",
                                "get_section_excerpts", "get_section_context",
                                "describe_section", "section_neighbors", "get_section_path",
                                "get_section_descendants", "get_section_summary",
                                "get_section_summaries"]),
        ("Cross-references & graph", ["get_backlinks", "get_related_sections",
                                       "get_broken_links", "get_orphan_sections",
                                       "find_similar_sections", "get_section_diff",
                                       "get_section_blast_radius", "check_section_delete_safe"]),
        ("OpenAPI / schema tools", ["find_endpoint", "list_endpoints_by_tag",
                                     "find_operations_using_schema", "get_schema_graph"]),
        ("Glossary / terms", ["lookup_term"]),
        ("Code linking", ["find_code_examples", "link_code_to_symbols",
                          "get_undocumented_symbols", "resolve_related_code_repos"]),
        ("Health & metrics", ["get_doc_coverage", "get_stale_pages", "get_wiki_stats",
                               "get_recent_changes", "get_doc_health", "doc_health_radar",
                               "diff_doc_health_radar", "get_doc_pr_risk_profile",
                               "get_watch_status"]),
        ("Utilities", ["analyze_perf", "get_session_stats", "tune_weights",
                        "check_embedding_drift"]),
        ("Self-Guide", ["jdocmunch_guide"]),
    ]
    from . import __version__ as _ver
    lines = [
        f"## jdocmunch-mcp (v{_ver})",
        "",
        "Use jdocmunch-mcp tools instead of Read/Grep/Glob for any indexed documentation.",
        "",
        "### Quick start",
        "1. `doc_list_repos` -- check if the docs are indexed.",
        "   If not: `index_local` (local folder) or `doc_index_repo` (GitHub URL).",
        "2. `search_sections` -- BM25 + optional semantic over section headings + bodies.",
        "3. `get_section` / `get_sections` -- pull section body by ID (byte-precise).",
        "4. `get_toc_tree` -- heading hierarchy for orientation.",
        "",
        "### All tools",
    ]
    for cat, tools in categories:
        lines.append(f"**{cat}:** " + ", ".join(f"`{t}`" for t in tools))
    lines.append("")
    lines.append("Never fall back to Read, Grep, or Glob for indexed docs.")
    lines.append("")
    return "\n".join(lines)


@server.list_resources()
async def list_resources() -> list[Resource]:
    """Advertise the runtime identity resource (munch.runtime.identity/v1)
    plus any session-finalized canonical handoffs (jdocmunch.handoff/v1)."""
    resources = [
        Resource(
            uri=runtime_identity.IDENTITY_URI,
            name="runtime-identity",
            description=(
                "Process provenance for this server instance "
                f"({runtime_identity.IDENTITY_SCHEMA}): product, version, "
                "transport, pid, OS-derived process_start, per-process "
                "instance_id, optional launch_id echo. Read-only, no side effects."
            ),
            mimeType="application/json",
        )
    ]
    from . import handoff as _handoff_mod
    for row in _handoff_mod.list_handoff_resources():
        resources.append(
            Resource(
                uri=row["uri"],
                name=row["name"],
                description=row["description"],
                mimeType=_handoff_mod.HANDOFF_CONTENT_TYPE,
            )
        )
    return resources


@server.read_resource()
async def read_resource(uri) -> "list[ReadResourceContents]":
    if str(uri) == runtime_identity.IDENTITY_URI:
        return [
            ReadResourceContents(
                content=runtime_identity.identity_json(),
                mime_type="application/json",
            )
        ]
    from . import handoff as _handoff_mod
    rec = _handoff_mod.handoff_for_uri(str(uri))
    if rec is not None:
        return [
            ReadResourceContents(
                content=rec["body"],
                mime_type=_handoff_mod.HANDOFF_CONTENT_TYPE,
            )
        ]
    raise ValueError(f"Unknown resource: {uri}")


@server.list_prompts()
async def list_prompts() -> list:
    """Return empty prompt list for client compatibility (e.g. Windsurf)."""
    return []



# --- jdoc#104: unknown-argument disclosure -----------------------------------

_SCHEMA_PROPS_CACHE: "dict[str, set[str]] | None" = None

# Arguments consumed by the dispatcher itself rather than declared per tool.
# Empty today; kept so a future cross-cutting knob is exempted deliberately by
# name rather than by widening the check.
_NON_SCHEMA_ARGS: "set[str]" = set()


def _declared_properties(tool_name: str) -> "set[str] | None":
    """Declared inputSchema property names for a tool, or None if unknown.

    Built from `_all_tools()` — the UNFILTERED catalog — on purpose. A tool
    hidden by `JDOCMUNCH_DISABLED_TOOLS` or a tier filter still has a schema,
    and resolving against the visible list would report every argument of a
    hidden tool as unknown.
    """
    global _SCHEMA_PROPS_CACHE
    if _SCHEMA_PROPS_CACHE is None:
        cache: "dict[str, set[str]]" = {}
        try:
            for t in _all_tools():
                props = ((t.inputSchema or {}).get("properties") or {})
                cache[t.name] = set(props.keys())
        except Exception:
            # ⚠ `logging.getLogger(__name__)`, NOT a module-level `logger`:
            # server.py has never defined one, so the original line raised
            # NameError from inside the handler that exists to swallow errors,
            # converting a handled failure into a crash. Shipped in v1.124.0 and
            # invisible until this repo gained a lint job (ruff F821).
            logging.getLogger(__name__).debug(
                "Could not build schema property cache", exc_info=True
            )
            return None
        _SCHEMA_PROPS_CACHE = cache
    canonical = _ALIAS_TO_CANONICAL.get(tool_name, tool_name)
    return _SCHEMA_PROPS_CACHE.get(canonical)


def _unknown_arguments(tool_name: str, arguments: dict) -> "list[str]":
    """Argument names the caller passed that the tool does not declare (jdoc#104).

    ⚠ Reported, never rejected. `additionalProperties: false` would be the
    stricter answer, but making a previously-accepted call raise is forbidden by
    the 1.x compatibility contract, so this is additive disclosure instead.

    The direction of the failure is what makes it worth surfacing at all: an
    agent that means to SCOPE a call and misnames the parameter silently gets a
    much LARGER response than it asked for. `get_toc{doc_path: ...}` returns the
    whole-corpus TOC, because `doc_path` is `get_document_outline`'s parameter
    name and `get_toc` scopes with `path_glob`. Dropping a scope is precisely
    the outcome this server exists to prevent.
    """
    if not isinstance(arguments, dict) or not arguments:
        return []
    declared = _declared_properties(tool_name)
    if declared is None:
        return []  # unknown schema: silence beats a false accusation
    return sorted(k for k in arguments if k not in declared and k not in _NON_SCHEMA_ARGS)


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls.

    v1.14.0: every dispatch is timed and recorded into the per-tool latency
    ring (and persistent SQLite sink when ``JDOCMUNCH_PERF_TELEMETRY=1``).
    """
    import time as _time
    from .storage.token_tracker import record_tool_latency as _record_latency

    storage_path = os.environ.get("DOC_INDEX_PATH")

    _t0 = _time.perf_counter()
    _ok = True
    _repo = arguments.get("repo") if isinstance(arguments, dict) else None
    # jdoc#104: computed BEFORE dispatch, since a tool may mutate `arguments`.
    _ignored_args = _unknown_arguments(name, arguments)

    # Honor JDOCMUNCH_DISABLED_TOOLS at call time. Tools in _UNDISABLEABLE_TOOLS
    # are exempt (currently empty; jdocmunch_guide is intentionally disable-able).
    # #36: resolve a deprecated call-time alias to its canonical name FIRST, so
    # disabling a canonical tool blocks every spelling that dispatches to it
    # (the bare aliases are never advertised, so _filter_tools can't help).
    _canonical_name = _ALIAS_TO_CANONICAL.get(name, name)
    _disabled_at_call = _get_disabled_tools() - _UNDISABLEABLE_TOOLS
    if name in _disabled_at_call or _canonical_name in _disabled_at_call:
        return [TextContent(type="text", text=json.dumps({
            "error": (
                f"Tool '{name}' is disabled via JDOCMUNCH_DISABLED_TOOLS. "
                f"Remove it from the env var to re-enable."
            )
        }, indent=2))]

    try:
        if name == "index_local":
            # jdoc#34: index_local is a long synchronous pipeline (walk +
            # parse + summarize + embed). Called inline it blocks the single
            # asyncio event loop past client tool timeouts, and every
            # subsequent call then times out while the server keeps working.
            # Run it in a worker thread so cheap tools stay responsive; the
            # cross-process index-write lock (v1.69.2) already serializes
            # concurrent same-repo writes.
            result = await asyncio.to_thread(
                index_local,
                path=arguments["path"],
                name=arguments.get("name"),
                use_ai_summaries=arguments.get("use_ai_summaries", True),
                use_embeddings=arguments.get("use_embeddings", "auto"),
                storage_path=storage_path,
                extra_ignore_patterns=arguments.get("extra_ignore_patterns"),
                include_dot_dirs=arguments.get("include_dot_dirs"),
                follow_symlinks=arguments.get("follow_symlinks", False),
                incremental=arguments.get("incremental", True),
                max_files=arguments.get("max_files", 10_000),
                sort_by=arguments.get("sort_by", "newest"),
                autotune=arguments.get("autotune", False),
                paths=arguments.get("paths"),
                worktree_mode=arguments.get("worktree_mode", "reuse_equivalent"),
                legacy_reconcile=arguments.get("legacy_reconcile"),
            )
        elif name in ("doc_index_repo", "index_repo"):  # index_repo kept for backward compat
            result = await index_repo(
                url=arguments["url"],
                use_ai_summaries=arguments.get("use_ai_summaries", True),
                use_embeddings=arguments.get("use_embeddings", "auto"),
                storage_path=storage_path,
                incremental=arguments.get("incremental", True),
                name=arguments.get("name"),
                ref=arguments.get("ref"),
            )
        elif name in ("doc_list_repos", "list_repos"):  # list_repos kept for backward compat
            result = list_repos(storage_path=storage_path)
        elif name == "list_docs":
            result = list_docs(
                repo=arguments["repo"],
                storage_path=storage_path,
            )
        elif name == "get_doc":
            result = get_doc(
                repo=arguments["repo"],
                doc_path=arguments["doc_path"],
                storage_path=storage_path,
            )
        elif name == "get_index_overview":
            result = get_index_overview(
                repo=arguments["repo"],
                top_n=arguments.get("top_n", 5),
                storage_path=storage_path,
            )
        elif name == "get_toc":
            result = get_toc(
                repo=arguments["repo"],
                path_glob=arguments.get("path_glob"),
                storage_path=storage_path,
            )
        elif name == "get_toc_tree":
            result = get_toc_tree(
                repo=arguments["repo"],
                path_glob=arguments.get("path_glob"),
                storage_path=storage_path,
            )
        elif name == "get_document_outline":
            result = get_document_outline(
                repo=arguments["repo"],
                doc_path=arguments["doc_path"],
                storage_path=storage_path,
            )
        elif name == "search_sections":
            result = search_sections(
                repo=arguments.get("repo"),
                query=arguments["query"],
                doc_path=arguments.get("doc_path"),
                path_glob=arguments.get("path_glob"),
                max_results=arguments.get("max_results", 10),
                semantic=arguments.get("semantic"),
                semantic_only=arguments.get("semantic_only", False),
                # jdoc#106: no default here. An omitted argument must reach
                # search_sections as None so the tuner can answer; filling in
                # 0.5 would make every call look explicit.
                semantic_weight=arguments.get("semantic_weight"),
                role=arguments.get("role"),
                profile=arguments.get("profile"),
                dedupe=arguments.get("dedupe", False),
                repo_group=arguments.get("repo_group"),
                min_answerability=arguments.get("min_answerability"),
                min_quotability=arguments.get("min_quotability"),
                min_level=arguments.get("min_level"),
                max_level=arguments.get("max_level"),
                tags=arguments.get("tags"),
                exclude_tags=arguments.get("exclude_tags"),
                roles=arguments.get("roles"),
                exclude_roles=arguments.get("exclude_roles"),
                min_byte_length=arguments.get("min_byte_length"),
                max_byte_length=arguments.get("max_byte_length"),
                compact=arguments.get("compact", False),
                fields=arguments.get("fields"),
                snippet_bytes=arguments.get("snippet_bytes", 0),
                storage_path=storage_path,
            )
        elif name == "search_titles":
            result = search_titles(
                repo=arguments["repo"],
                query=arguments["query"],
                max_results=arguments.get("max_results", 10),
                storage_path=storage_path,
            )
        elif name == "count_sections":
            result = count_sections(
                repo=arguments["repo"],
                doc_path=arguments.get("doc_path"),
                path_glob=arguments.get("path_glob"),
                role=arguments.get("role"),
                roles=arguments.get("roles"),
                exclude_roles=arguments.get("exclude_roles"),
                tags=arguments.get("tags"),
                exclude_tags=arguments.get("exclude_tags"),
                min_level=arguments.get("min_level"),
                max_level=arguments.get("max_level"),
                min_byte_length=arguments.get("min_byte_length"),
                max_byte_length=arguments.get("max_byte_length"),
                storage_path=storage_path,
            )
        elif name == "get_section":
            result = get_section(
                repo=arguments["repo"],
                section_id=arguments["section_id"],
                verify=arguments.get("verify", False),
                strip_boilerplate=arguments.get("strip_boilerplate", False),
                compress_code=arguments.get("compress_code", False),
                storage_path=storage_path,
            )
        elif name == "get_sections":
            result = get_sections(
                repo=arguments["repo"],
                section_ids=arguments["section_ids"],
                verify=arguments.get("verify", False),
                strip_boilerplate=arguments.get("strip_boilerplate", False),
                compress_code=arguments.get("compress_code", False),
                storage_path=storage_path,
            )
        elif name == "get_section_context":
            result = get_section_context(
                repo=arguments["repo"],
                section_id=arguments["section_id"],
                max_tokens=arguments.get("max_tokens", 2000),
                include_children=arguments.get("include_children", True),
                include_related=arguments.get("include_related", False),
                strip_boilerplate=arguments.get("strip_boilerplate", False),
                storage_path=storage_path,
            )
        elif name == "section_neighbors":
            result = section_neighbors(
                repo=arguments["repo"],
                section_id=arguments["section_id"],
                storage_path=storage_path,
            )
        elif name == "describe_section":
            result = describe_section(
                repo=arguments["repo"],
                section_id=arguments["section_id"],
                storage_path=storage_path,
            )
        elif name == "get_section_summary":
            result = get_section_summary(
                repo=arguments["repo"],
                section_id=arguments["section_id"],
                storage_path=storage_path,
            )
        elif name == "get_section_summaries":
            result = get_section_summaries(
                repo=arguments["repo"],
                section_ids=arguments["section_ids"],
                storage_path=storage_path,
            )
        elif name == "get_orphan_sections":
            result = get_orphan_sections(
                repo=arguments["repo"],
                include_same_doc=arguments.get("include_same_doc", False),
                storage_path=storage_path,
            )
        elif name == "get_section_path":
            result = get_section_path(
                repo=arguments["repo"],
                section_id=arguments["section_id"],
                storage_path=storage_path,
            )
        elif name == "get_section_excerpt":
            result = get_section_excerpt(
                repo=arguments["repo"],
                section_id=arguments["section_id"],
                max_bytes=arguments.get("max_bytes", 500),
                storage_path=storage_path,
            )
        elif name == "get_section_excerpts":
            result = get_section_excerpts(
                repo=arguments["repo"],
                section_ids=arguments["section_ids"],
                max_bytes=arguments.get("max_bytes", 500),
                storage_path=storage_path,
            )
        elif name == "get_section_descendants":
            result = get_section_descendants(
                repo=arguments["repo"],
                section_id=arguments["section_id"],
                max_depth=arguments.get("max_depth"),
                storage_path=storage_path,
            )
        elif name == "get_all_tags":
            result = get_all_tags(
                repo=arguments["repo"],
                min_section_count=arguments.get("min_section_count", 1),
                storage_path=storage_path,
            )
        elif name == "get_all_roles":
            result = get_all_roles(
                repo=arguments["repo"],
                sample_size=arguments.get("sample_size", 3),
                storage_path=storage_path,
            )
        elif name == "get_recent_changes":
            result = get_recent_changes(
                repo=arguments["repo"],
                include_stale=arguments.get("include_stale", True),
                include_edited=arguments.get("include_edited", True),
                live_source=arguments.get("live_source", False),
                storage_path=storage_path,
            )
        elif name == "delete_index":
            result = delete_index(
                repo=arguments["repo"],
                storage_path=storage_path,
            )
        elif name == "get_broken_links":
            result = get_broken_links(
                repo=arguments["repo"],
                storage_path=storage_path,
            )
        elif name == "get_doc_coverage":
            result = get_doc_coverage(
                repo=arguments["repo"],
                symbol_ids=arguments["symbol_ids"],
                storage_path=storage_path,
            )
        elif name == "get_backlinks":
            result = get_backlinks(
                repo=arguments["repo"],
                doc_path=arguments["doc_path"],
                storage_path=storage_path,
            )
        elif name == "get_stale_pages":
            result = get_stale_pages(
                repo=arguments["repo"],
                sources_dir=arguments.get("sources_dir"),
                storage_path=storage_path,
            )
        elif name == "get_wiki_stats":
            result = get_wiki_stats(
                repo=arguments["repo"],
                storage_path=storage_path,
            )
        elif name == "get_watch_status":
            result = get_watch_status(storage_path=storage_path)
        elif name == "finalize_handoff":
            from . import handoff as _handoff_mod
            result = _handoff_mod.finalize_handoff(
                repo=arguments["repo"],
                task=arguments["task"],
                sections=arguments["sections"],
                evidence_refs=arguments["evidence_refs"],
                profile=arguments.get("profile", "general"),
                appendices=arguments.get("appendices"),
            )
        elif name == "analyze_perf":
            result = analyze_perf(
                window=arguments.get("window", "session"),
                storage_path=storage_path,
            )
        elif name == "get_session_stats":
            result = get_session_stats(storage_path=storage_path)
            # Tool-surface schema receipt (v1.112.0, jcm v1.108.153 parity).
            # Advisory only — a failure here must never break the stats tool.
            try:
                result["tool_surface"] = _tool_surface_stats()
            except Exception:
                import logging
                logging.getLogger(__name__).debug(
                    "tool_surface stats failed", exc_info=True
                )
        elif name == "check_embedding_drift":
            result = check_embedding_drift(
                capture=arguments.get("capture", False),
                force=arguments.get("force", False),
                threshold=arguments.get("threshold", 0.05),
                storage_path=storage_path,
            )
        elif name == "find_code_examples":
            result = find_code_examples(
                repo=arguments["repo"],
                query=arguments["query"],
                lang=arguments.get("lang"),
                doc_path=arguments.get("doc_path"),
                path_glob=arguments.get("path_glob"),
                max_results=arguments.get("max_results", 10),
                storage_path=storage_path,
            )
        elif name == "link_code_to_symbols":
            result = link_code_to_symbols(
                repo=arguments["repo"],
                code_repo=arguments["code_repo"],
                max_examples=arguments.get("max_examples", 200),
                max_symbols_per_block=arguments.get("max_symbols_per_block", 5),
                storage_path=storage_path,
            )
        elif name == "resolve_related_code_repos":
            result = resolve_related_code_repos(
                repo=arguments["repo"],
                storage_path=storage_path,
            )
        elif name == "doc_resolve_repo":
            result = doc_resolve_repo(
                path=arguments["path"],
                storage_path=storage_path,
            )
        elif name == "find_endpoint":
            result = find_endpoint(
                repo=arguments["repo"],
                path=arguments.get("path"),
                method=arguments.get("method"),
                tag=arguments.get("tag"),
                storage_path=storage_path,
            )
        elif name == "list_endpoints_by_tag":
            result = list_endpoints_by_tag(
                repo=arguments["repo"],
                tag=arguments["tag"],
                storage_path=storage_path,
            )
        elif name == "find_operations_using_schema":
            result = find_operations_using_schema(
                repo=arguments["repo"],
                schema_name=arguments["schema_name"],
                storage_path=storage_path,
            )
        elif name == "get_schema_graph":
            result = get_schema_graph(
                repo=arguments["repo"],
                schema_name=arguments["schema_name"],
                max_depth=arguments.get("max_depth", 5),
                storage_path=storage_path,
            )
        elif name == "lookup_term":
            result = lookup_term(
                repo=arguments["repo"],
                term=arguments["term"],
                storage_path=storage_path,
            )
        elif name == "list_terms":
            result = list_terms(
                repo=arguments["repo"],
                prefix=arguments.get("prefix"),
                max_results=arguments.get("max_results", 100),
                storage_path=storage_path,
            )
        elif name == "get_related_sections":
            result = get_related_sections(
                repo=arguments["repo"],
                section_id=arguments["section_id"],
                mode=arguments.get("mode", "both"),
                top_n=arguments.get("top_n", 5),
                min_score=arguments.get("min_score", 0.6),
                max_per_kind=arguments.get("max_per_kind", 10),
                storage_path=storage_path,
            )
        elif name == "get_section_diff":
            result = get_section_diff(
                repo=arguments["repo"],
                section_id=arguments["section_id"],
                storage_path=storage_path,
            )
        elif name == "get_doc_health":
            result = get_doc_health(
                repo=arguments["repo"],
                storage_path=storage_path,
            )
        elif name == "doc_health_radar":
            result = doc_health_radar(
                repo=arguments["repo"],
                storage_path=storage_path,
            )
        elif name == "diff_doc_health_radar":
            result = diff_doc_health_radar(
                baseline=arguments["baseline"],
                current=arguments["current"],
            )
        elif name == "get_doc_pr_risk_profile":
            result = get_doc_pr_risk_profile(
                repo=arguments["repo"],
                changed_sections=arguments["changed_sections"],
                storage_path=storage_path,
            )
        elif name == "get_tutorial_path":
            result = get_tutorial_path(
                repo=arguments["repo"],
                section_id=arguments["section_id"],
                storage_path=storage_path,
            )
        elif name == "get_undocumented_symbols":
            result = get_undocumented_symbols(
                repo=arguments["repo"],
                code_repo=arguments["code_repo"],
                max_symbols=arguments.get("max_symbols", 1000),
                storage_path=storage_path,
            )
        elif name == "tune_weights":
            result = tune_weights(
                repo=arguments.get("repo"),
                min_events=arguments.get("min_events", 50),
                dry_run=arguments.get("dry_run", False),
                max_age_days=arguments.get("max_age_days", 90),
                set_weight=arguments.get("set_weight"),
                storage_path=storage_path,
            )
        elif name == "list_repo_groups":
            result = list_repo_groups(storage_path=storage_path)
        elif name == "define_repo_group":
            result = define_repo_group(
                name=arguments["name"],
                repos=arguments["repos"],
                storage_path=storage_path,
            )
        elif name == "verify_index":
            result = verify_index(
                repo=arguments["repo"],
                sample=arguments.get("sample"),
                storage_path=storage_path,
                source=arguments.get("source", "cache"),
            )
        elif name == "find_similar_sections":
            result = find_similar_sections(
                repo=arguments["repo"],
                min_score=arguments.get("min_score", 0.7),
                near_duplicate_threshold=arguments.get("near_duplicate_threshold", 0.92),
                max_clusters=arguments.get("max_clusters", 50),
                exclude_same_doc=arguments.get("exclude_same_doc", False),
                max_sections=arguments.get("max_sections", 1000),
                storage_path=storage_path,
            )
        elif name == "get_section_blast_radius":
            result = get_section_blast_radius(
                repo=arguments["repo"],
                section_id=arguments["section_id"],
                max_depth=arguments.get("max_depth", 3),
                storage_path=storage_path,
            )
        elif name == "check_section_delete_safe":
            result = check_section_delete_safe(
                repo=arguments["repo"],
                section_id=arguments["section_id"],
                transitive_depth=arguments.get("transitive_depth", 3),
                recent_edit_days=arguments.get("recent_edit_days", 14),
                storage_path=storage_path,
            )
        elif name == "jdocmunch_guide":
            from . import __version__ as _ver
            result = {
                "version": _ver,
                "content": _generate_doc_md_snippet(),
            }
        else:
            result = {"error": f"Unknown tool: {name}"}

        # jdoc#104 / v1.124.1: an argument we ignored cannot back an absence
        # claim. This MUST run before the absence block below reads the verdict,
        # or a call whose scoping argument was silently dropped can still reach
        # `absent` and mint a citable ref. Suite parity with jcm v1.108.175 and
        # jdata; v1.124.0 disclosed the ignored argument but skipped this half.
        try:
            if _ignored_args:
                from .tools import _arg_contract as _ac
                _ac.degrade_absent_verdict(result, _ignored_args)
        except Exception:
            pass

        # Absence evidence (#377 phase 3): record the verdict BEFORE
        # meta_fields filtering, which by default strips `_meta` entirely — the
        # v1.104.0 budget lesson. The citable ref is re-attached after
        # filtering, below, so the token-efficient default can't delete it.
        _absence_ref = None
        _absence_blocked = None
        try:
            if isinstance(result, dict):
                _v = (result.get("_meta") or {}).get("verdict")
                if isinstance(_v, dict):
                    from . import handoff as _handoff_abs
                    _absence_ref, _absence_blocked = _handoff_abs.note_absence(
                        name,
                        arguments.get("repo"),
                        arguments.get("query"),
                        _v,
                        arguments=arguments,
                    )
                    if _absence_blocked and _v.get("state") != "absent":
                        _absence_blocked = None
        except Exception:
            pass

        if isinstance(result, dict):
            result.setdefault("_meta", {})["powered_by"] = "jdocmunch-mcp by jgravelle · https://github.com/jgravelle/jdocmunch-mcp"

            # meta_fields filtering (matches jcodemunch-mcp behaviour)
            from .config import get_meta_fields
            meta_fields = get_meta_fields()
            if meta_fields == []:
                result.pop("_meta", None)
            elif isinstance(meta_fields, list):
                existing_meta = result.pop("_meta", {})
                _meta: dict[str, Any] = {}
                if "powered_by" in meta_fields:
                    _meta["powered_by"] = existing_meta.get("powered_by", "")
                for field in meta_fields:
                    if field in existing_meta:
                        _meta[field] = existing_meta[field]
                if _meta:
                    result["_meta"] = _meta

        # v1.114.2: session retrieval record for handoff attestation
        # (jdocmunch.handoff/v1 — suite parity with jcm #374). Records the
        # section ids / doc paths this server actually served, so
        # finalize_handoff can attest evidence_refs against real retrieval.
        try:
            from . import handoff as _handoff_record
            if isinstance(result, dict):
                if name in ("search_sections", "search_titles"):
                    _handoff_record.note_served_rows(result.get("results") or [])
                elif name == "get_section" and isinstance(result.get("section"), dict):
                    _row = dict(result["section"])
                    _cit = (result.get("_meta") or {}).get("citation") or {}
                    _row.setdefault("id", _cit.get("section_id"))
                    _row.setdefault("doc_path", _cit.get("doc_path"))
                    _handoff_record.note_served_rows([_row])
                elif name == "get_sections":
                    _handoff_record.note_served_rows(result.get("sections") or [])
        except Exception:
            pass

        # v1.104.0: advisory session budget (suite parity with jcm). Attached
        # AFTER meta_fields filtering so the warning survives the
        # token-efficient default (_meta stripped) — an advisory the default
        # config silently deletes would be no advisory at all. Never blocks.
        try:
            from .storage.token_tracker import budget_status as _budget_status
            _b = _budget_status()
            if _b is not None and _b["state"] in ("approaching", "over") and isinstance(result, dict):
                result.setdefault("_meta", {})["budget"] = _b
        except Exception:
            pass

        # Absence evidence, part two (#377 phase 3). Attached AFTER meta_fields
        # filtering for the same reason the budget block is: a token the
        # default config deletes is a token the agent can never cite.
        try:
            if isinstance(result, dict) and (_absence_ref or _absence_blocked):
                _blk = (
                    {"ref": _absence_ref}
                    if _absence_ref
                    else {"citable": False, "blocked_by": _absence_blocked}
                )
                result.setdefault("_meta", {})["absence_evidence"] = _blk
        except Exception:
            pass

        # jdoc#104: unknown arguments, attached AFTER meta_fields filtering for
        # the same reason the budget and absence blocks are — the default config
        # strips `_meta` entirely, and a warning the default deletes is no
        # warning at all. Omitted when empty, per omit-when-empty.
        #
        # ⚠ TOP-LEVEL is the suite shape for a `_meta`-stripping server (jdata
        # documents the same reasoning). `_meta.ignored_arguments` is ALSO kept
        # because v1.124.0 shipped it and the 1.x contract forbids removing a
        # response key; it is what jcm emits, so a cross-server consumer reading
        # either spelling works.
        try:
            if _ignored_args and isinstance(result, dict):
                from .tools import _arg_contract as _ac2
                _ac2.disclose(result, _ignored_args)
                result.setdefault("_meta", {})["ignored_arguments"] = _ignored_args
        except Exception:
            pass

        _text = json.dumps(result, separators=(',', ':'))
        try:
            from .storage.token_tracker import record_response_text as _rrt
            _rrt(_text)
        except Exception:
            pass
        return [TextContent(type="text", text=_text)]

    except Exception as e:
        _ok = False
        print(traceback.format_exc(), file=sys.stderr)
        return [TextContent(type="text", text=json.dumps({"error": str(e)}, separators=(',', ':')))]
    finally:
        _record_latency(
            tool=name,
            duration_ms=(_time.perf_counter() - _t0) * 1000.0,
            ok=_ok,
            repo=_repo if isinstance(_repo, str) else None,
            base_path=storage_path,
        )


async def run_server():
    """Run the MCP server."""
    import threading

    import anyio

    from jdocmunch_mcp import __version__
    from jdocmunch_mcp.embeddings.provider import warmup as _embedding_warmup
    from jdocmunch_mcp.preload import preload_embedding_stack, preload_native_deps
    from jdocmunch_mcp.stdio_guard import claim_stdout
    from mcp.server.stdio import stdio_server

    # jdoc#110: take the real stdout for JSON-RPC and point fd 1 at stderr
    # BEFORE anything else runs. From here on no library, thread or child
    # process can reach the framed stream, so the warmup no longer has to
    # finish before the transport starts — which is what kept provider init on
    # the startup path and cost ~7.6 s on every launch.
    _private_stdout, _swapped = claim_stdout()

    print(f"jdocmunch-mcp {__version__} by jgravelle · https://github.com/jgravelle/jdocmunch-mcp", file=sys.stderr)
    if not _swapped:
        # ⚠ Worth saying out loud: this is the configuration where a stray
        # library write can still corrupt a response.
        print(
            "jdocmunch-mcp: could not isolate stdout for JSON-RPC; "
            "library output on stdout may corrupt framing",
            file=sys.stderr,
        )

    # jdoc#118: do the native imports HERE, while this thread is still the only
    # one in the process. Their DllMains deadlock against concurrent loader
    # activity, and the warmup thread started below is exactly that. See
    # preload.py — the ordering relative to Thread(...).start() IS the fix, and
    # the embedding stack costs nothing extra because warmup imports it anyway.
    _preloaded = {**preload_native_deps(), **preload_embedding_stack()}
    for _mod, _outcome in _preloaded.items():
        if not _outcome.startswith("absent"):
            print(f"jdocmunch-mcp: preloaded {_mod} ({_outcome})", file=sys.stderr)

    # Warm off the critical path. Startup no longer waits on a model load, and
    # any chatter it produces lands on stderr by construction. `warmup` itself
    # still declines an uncached model — downloading hundreds of megabytes
    # unasked, at every server start, for a feature the session may never use
    # is not something to do in the background either.
    def _warm() -> None:
        try:
            warmed = _embedding_warmup()
        except Exception:
            return
        if warmed:
            print(f"jdocmunch-mcp: warmed embedding provider ({warmed})", file=sys.stderr)

    threading.Thread(target=_warm, name="jdocmunch-embed-warmup", daemon=True).start()

    _stdout_arg = anyio.wrap_file(_private_stdout) if _private_stdout is not None else None
    async with stdio_server(stdout=_stdout_arg) as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            _initialization_options()
        )


def _force_utf8_stdio() -> None:
    """Make CLI output UTF-8 regardless of the platform locale.

    Suite parity with jcodemunch-mcp v1.108.262, where this was a live crash.

    ⚠⚠ On Windows, `sys.stdout` is the **console** stream (already UTF-8) when
    attached to a terminal and the **locale** stream (cp1252) when piped or
    redirected. So output containing any non-ASCII character works interactively
    and goes out as cp1252 bytes the moment anything consumes it -- mojibake for
    a UTF-8 reader, with nothing raised on our side. A character cp1252 cannot
    encode at all crashes the command outright.

    jdoc's `init` prints `—` and `•` today. Both ARE cp1252-encodable, so this
    repo has the silent half and not (yet) the crash; the fix is at the entry
    point precisely so the next character added cannot decide that for us.

    ⚠ The MCP stdio transport is unaffected: it wraps `sys.stdout.buffer` in its
    own TextIOWrapper and never reads the text layer reconfigured here.

    ⚠ `errors="replace"` is deliberate -- filesystem paths can carry surrogates
    from a `surrogateescape` decode, which raise even under UTF-8. Mangling one
    display character beats killing the command.

    ⚠ `PYTHONIOENCODING` is honoured as an explicit opt-out.
    """
    if os.environ.get("PYTHONIOENCODING"):
        return
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # replaced by a test harness or captured buffer
            continue
        current = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if current in ("utf8", "utf8mb4"):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main(argv: Optional[list] = None):
    """Main entry point."""
    _force_utf8_stdio()
    from .security import verify_package_integrity
    verify_package_integrity()

    parser = argparse.ArgumentParser(
        prog="jdocmunch-mcp",
        description="jDocMunch MCP — structured documentation retrieval server.",
    )
    from . import __version__ as _ver
    parser.add_argument(
        "-V", "--version", action="version", version=f"%(prog)s {_ver}",
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- serve (default) ---
    subparsers.add_parser("serve", help="Run the MCP server (default)")

    # --- init ---
    init_parser = subparsers.add_parser(
        "init",
        help="One-command onboarding: detect clients, write config, install policy, hooks, index",
    )
    init_parser.add_argument(
        "--hooks", action="store_true",
        help="Install enforcement hooks into ~/.claude/settings.json",
    )
    init_parser.add_argument(
        "--index", action="store_true",
        help="Index the current working directory",
    )
    init_parser.add_argument(
        "--client", dest="clients", action="append",
        help="MCP client to configure (auto|none|claude-code|claude-desktop|cursor|windsurf|continue)",
    )
    init_parser.add_argument(
        "--claude-md", dest="claude_md", choices=["global", "project"],
        help="Install Doc Exploration Policy into CLAUDE.md",
    )
    init_parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be done without making changes",
    )
    init_parser.add_argument(
        "--demo", action="store_true",
        help="Demo mode: dry-run with benefit summary",
    )
    init_parser.add_argument(
        "-y", "--yes", action="store_true",
        help="Accept all defaults non-interactively",
    )
    init_parser.add_argument(
        "--no-backup", action="store_true",
        help="Skip creating .bak backups before modifying files",
    )

    # --- claude-md ---
    cmd_parser = subparsers.add_parser(
        "claude-md",
        help="Print or install the Doc Exploration Policy for CLAUDE.md",
    )
    cmd_parser.add_argument(
        "--install", choices=["global", "project"],
        help="Append policy to CLAUDE.md (global or project scope)",
    )

    # --- index-file ---
    if_parser = subparsers.add_parser(
        "index-file",
        help="Re-index a single file within an existing index",
    )
    if_parser.add_argument(
        "file", help="Path to the file to re-index",
    )
    if_parser.add_argument(
        "--name",
        help=(
            "Index name (owner/name or bare name) to update directly, skipping "
            "folder-name detection. Required for a custom --name index or a "
            "corpus root whose folder name is not path-safe (e.g. has spaces)."
        ),
    )

    # --- index-local ---
    il_parser = subparsers.add_parser(
        "index-local",
        help="Index a local folder (CLI equivalent of the MCP index_local tool)",
    )
    il_parser.add_argument(
        "--path", required=True,
        help="Path to the folder to index",
    )
    il_parser.add_argument(
        "--name",
        help="Optional repo identifier override",
    )
    il_parser.add_argument(
        "--paths-from",
        metavar="FILE",
        help=(
            "Read explicit paths to index (one per line) from FILE. Use '-' for "
            "stdin. When set, the directory walk is skipped — only the listed "
            "paths are indexed. Entries may be absolute or relative to --path. "
            "Pipe-friendly with find / fd / fzf / rg."
        ),
    )
    il_parser.add_argument(
        "--rebuild",
        action="store_true",
        help=(
            "Rebuild from scratch instead of diffing against the existing index "
            "(equivalent to the MCP tool's incremental=false). Re-parses and "
            "re-embeds every file. Use after changing the embedding model."
        ),
    )
    # jdoc#108: the MCP tool could express both of these per call and the CLI
    # could express neither, so the documented CLI route sent a private corpus
    # to whatever summarizer the environment happened to expose, with nothing
    # in --help saying it would. Same flag spelling as `watch`.
    il_parser.add_argument(
        "--no-ai-summaries", action="store_true",
        help="Skip AI section summaries (no summarizer calls, nothing leaves the machine)",
    )
    # jdoc#116: the third and last member of the #108 set. Worse than the two
    # fixed there, because the CLI did not merely fail to EXPRESS the setting,
    # it DESTROYED one already persisted: passing nothing computed a `full`
    # selection that overwrote the stored `full+shape:<hash>` and re-admitted
    # every excluded file.
    il_parser.add_argument(
        "--extra-ignore-pattern", action="append", default=None,
        metavar="PATTERN", dest="extra_ignore_pattern",
        help=(
            "Gitignore-style pattern to exclude from the corpus; repeatable. "
            "Patterns are stored with the index, so a later refresh that omits "
            "this flag INHERITS them rather than widening the corpus. Pass "
            "--no-extra-ignore-patterns to deliberately clear them."
        ),
    )
    il_parser.add_argument(
        "--no-extra-ignore-patterns", action="store_true",
        help=(
            "Clear any stored exclusion patterns and index the full corpus. "
            "Explicit, because omitting --extra-ignore-pattern now means "
            "'inherit', not 'none'."
        ),
    )
    il_parser.add_argument(
        "--embeddings", choices=("auto", "on", "off"), default="auto",
        help=(
            "Semantic embeddings: 'auto' (default) enables them when a provider "
            "is configured, 'on' requests them even where auto-detect would "
            "decline, 'off' keeps the index lexical-only"
        ),
    )
    il_parser.add_argument(
        "--no-embeddings", action="store_true",
        help="Alias for --embeddings off",
    )

    # --- verify-index (v1.27.0) ---
    vi_parser = subparsers.add_parser(
        "verify-index",
        help="Byte-offset integrity check across an indexed repo",
    )
    vi_parser.add_argument("--repo", required=True, help="jdocmunch repo identifier")
    vi_parser.add_argument("--sample", type=int, default=None,
                           help="Verify only the first N sections (cheap CI mode)")

    # --- delete-index ---
    di_parser = subparsers.add_parser(
        "delete-index",
        help="Delete a repo index and its cached raw files (offline)",
    )
    di_parser.add_argument("--repo", required=True, help="jdocmunch repo identifier")

    # --- hook-pretooluse ---
    subparsers.add_parser(
        "hook-pretooluse",
        help="PreToolUse hook: intercept Read on large doc files (reads stdin)",
    )

    # --- hook-posttooluse ---
    subparsers.add_parser(
        "hook-posttooluse",
        help="PostToolUse hook: auto-reindex doc files after Edit/Write (reads stdin)",
    )

    # --- hook-precompact ---
    subparsers.add_parser(
        "hook-precompact",
        help="PreCompact hook: session snapshot before context compaction (reads stdin)",
    )

    # --- hook-reindex ---
    hook_reindex_parser = subparsers.add_parser(
        "hook-reindex",
        help="Throttled background reindex worker spawned by the PostToolUse hook (jdoc#76)",
    )
    hook_reindex_parser.add_argument("file", help="Path to the edited doc file to reindex")

    # --- watch (jdoc#78) ---
    watch_parser = subparsers.add_parser(
        "watch",
        help="Auto-reindex every locally-indexed doc repo on any on-disk doc change (foreground daemon)",
    )
    watch_parser.add_argument(
        "--no-ai-summaries", action="store_true",
        help="Skip AI section summaries on re-index (faster, no summarizer calls)",
    )
    watch_parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress per-change stderr log lines",
    )

    # jdoc#120: the installed service wraps this same daemon, so it takes the
    # same flags. Same spellings as `watch` — one source of truth for what the
    # watcher does, whether it runs in the foreground or at login.
    watch_install_parser = subparsers.add_parser(
        "watch-install",
        help="Install the doc watcher as a login service (systemd/launchd/Task Scheduler)",
    )
    watch_install_parser.add_argument(
        "--no-ai-summaries", action="store_true",
        help="Install the watcher with AI section summaries off (no summarizer calls)",
    )
    watch_install_parser.add_argument(
        "--quiet", action="store_true",
        help="Install the watcher with per-change log lines suppressed",
    )
    subparsers.add_parser(
        "watch-uninstall",
        help="Remove the installed doc-watcher login service",
    )
    subparsers.add_parser(
        "watch-status",
        help="Print doc-watcher service state + per-repo watch coverage",
    )

    args = parser.parse_args(argv)

    # Default to serve when no subcommand given
    if args.command is None or args.command == "serve":
        asyncio.run(run_server())
        return

    if args.command == "init":
        from .cli.init import run_init
        rc = run_init(
            clients=args.clients,
            claude_md=args.claude_md,
            hooks=args.hooks,
            index=args.index,
            dry_run=args.dry_run,
            demo=args.demo,
            yes=args.yes,
            no_backup=args.no_backup,
        )
        sys.exit(rc)

    if args.command == "claude-md":
        from .cli.init import run_claude_md
        sys.exit(run_claude_md(install=args.install))

    if args.command == "index-file":
        import contextlib
        from .tools.index_file import index_file_cli
        # Reserve stdout for the final JSON body only: embedding-provider
        # initialization (e.g. sentence-transformers lazy model load) can write
        # progress/warning chatter to stdout during computation, which would
        # corrupt the machine-readable result. Redirect it to stderr while we
        # compute, then print JSON after leaving the redirect (jdoc#65, same
        # guard the serve path uses for jdoc#19).
        with contextlib.redirect_stdout(sys.stderr):
            result = index_file_cli(args.file, name=getattr(args, "name", None))
        print(json.dumps(result, indent=2))
        sys.exit(0 if result.get("success") else 1)
        return

    if args.command == "index-local":
        import contextlib
        from .tools.index_local import index_local
        paths_from = getattr(args, "paths_from", None)
        if paths_from:
            paths_arg, err = _load_paths_from_arg(paths_from)
            if err is not None:
                print(json.dumps({"success": False, "error": err}, indent=2))
                sys.exit(1)
        else:
            paths_arg = None
        # Guard stdout against provider/library chatter during computation; the
        # final JSON is the only thing that should reach stdout (jdoc#65).
        with contextlib.redirect_stdout(sys.stderr):
            # jdoc#108: "off" beats "auto" whichever spelling asked for it —
            # a caller that named a value never has it widened by a default.
            _emb = getattr(args, "embeddings", "auto")
            if getattr(args, "no_embeddings", False):
                _emb = "off"
            _use_embeddings = {"auto": "auto", "on": True, "off": False}[_emb]
            result = index_local(
                path=args.path, name=args.name, paths=paths_arg,
                # jdoc#109: without this the CLI had no way to force a full
                # re-embed short of delete-index, which is the workaround the
                # reporter had to use.
                incremental=not getattr(args, "rebuild", False),
                use_ai_summaries=not getattr(args, "no_ai_summaries", False),
                use_embeddings=_use_embeddings,
                # jdoc#116: None and [] mean different things downstream.
                # None = "said nothing" = inherit the stored patterns;
                # [] = "explicitly none" = widen, with the change disclosed.
                extra_ignore_patterns=(
                    [] if getattr(args, "no_extra_ignore_patterns", False)
                    else getattr(args, "extra_ignore_pattern", None)
                ),
            )
        print(json.dumps(result, indent=2))
        return

    if args.command == "verify-index":
        from .tools.verify_index import verify_index as _verify
        result = _verify(repo=args.repo, sample=args.sample)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result.get("drift_count", 0) == 0 else 2)

    if args.command == "delete-index":
        from .tools.delete_index import delete_index as _delete
        result = _delete(repo=args.repo)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result.get("success") else 1)

    if args.command == "hook-pretooluse":
        from .cli.hooks import run_pretooluse
        sys.exit(run_pretooluse())

    if args.command == "hook-posttooluse":
        from .cli.hooks import run_posttooluse
        sys.exit(run_posttooluse())

    if args.command == "hook-precompact":
        from .cli.hooks import run_precompact
        sys.exit(run_precompact())

    if args.command == "hook-reindex":
        from .cli.hooks import run_hook_reindex
        sys.exit(run_hook_reindex(args.file))

    if args.command == "watch":
        from .watch import watch_docs
        try:
            asyncio.run(watch_docs(
                use_ai_summaries=not args.no_ai_summaries,
                quiet=args.quiet,
            ))
        except KeyboardInterrupt:
            pass
        return

    if args.command in ("watch-install", "watch-uninstall", "watch-status"):
        from . import service_installer
        if args.command == "watch-install":
            watch_args = []
            if getattr(args, "no_ai_summaries", False):
                watch_args.append("--no-ai-summaries")
            if getattr(args, "quiet", False):
                watch_args.append("--quiet")
            try:
                result = service_installer.install_service(watch_args)
                print(json.dumps(result, indent=2))
                # jdoc#120: the install always rewrites the service definition.
                # Say so when that discarded a hand-edited one, so the revert is
                # visible at the moment it happens rather than at the next
                # unexplained re-index.
                replaced = result.get("replaced_exec")
                if replaced:
                    print(
                        "\nWARNING: this replaced a customised service definition.\n"
                        f"  was:  {replaced['previous']}\n"
                        f"  now:  {replaced['installed']}\n"
                        "Pass the watcher's flags to `watch-install` itself "
                        "(--no-ai-summaries, --quiet) so the next install keeps them.",
                        file=sys.stderr,
                    )
                print(
                    "\nInstalled. The doc watcher runs in the background and keeps "
                    "every locally-indexed doc repo fresh. Remove it with "
                    "`jdocmunch-mcp watch-uninstall`.",
                    file=sys.stderr,
                )
            except service_installer.InstallerError as exc:
                print(json.dumps({"error": str(exc)}, indent=2))
                sys.exit(1)
        elif args.command == "watch-uninstall":
            result = service_installer.uninstall_service()
            print(json.dumps(result, indent=2))
        else:  # watch-status
            from .tools.get_watch_status import get_watch_status
            print(json.dumps(get_watch_status(
                storage_path=os.environ.get("DOC_INDEX_PATH"),
            ), indent=2))
        return


if __name__ == "__main__":
    main()
