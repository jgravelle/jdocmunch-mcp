# User Guide

## Installation

```bash
pip install jdocmunch-mcp
```

Or with `uvx` (no install required):

```bash
uvx jdocmunch-mcp --help
```

Or from source:

```bash
git clone https://github.com/jgravelle/jdocmunch-mcp.git
cd jdocmunch-mcp
pip install -e .
```

---

## Configuration

> **PATH note:** MCP clients often run with a limited environment where `jdocmunch-mcp` may not be found even if it works in your terminal. Using [`uvx`](https://github.com/astral-sh/uv) is the recommended approach — it resolves the package on demand without requiring anything to be on your system PATH.

### Claude Desktop / Claude Code

Config file location:

| OS      | Path |
| ------- | ---- |
| macOS   | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Linux   | `~/.config/claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

**Minimal config (no API keys needed):**

```json
{
  "mcpServers": {
    "jdocmunch": {
      "command": "uvx",
      "args": ["jdocmunch-mcp"]
    }
  }
}
```

**With optional AI summaries and GitHub auth:**

```json
{
  "mcpServers": {
    "jdocmunch": {
      "command": "uvx",
      "args": ["jdocmunch-mcp"],
      "env": {
        "GITHUB_TOKEN": "ghp_...",
        "ANTHROPIC_API_KEY": "sk-ant-..."
      }
    }
  }
}
```

For Anthropic or Gemini, `uvx jdocmunch-mcp` is sufficient once the matching API
key is set. For OpenAI-compatible providers such as OpenAI, MiniMax, or GLM-5,
start the server with the optional `openai` dependency:

```json
{
  "mcpServers": {
    "jdocmunch": {
      "command": "uvx",
      "args": ["--with", "openai", "jdocmunch-mcp"],
      "env": {
        "MINIMAX_API_KEY": "mx-...",
        "JDOCMUNCH_SUMMARIZER_PROVIDER": "minimax"
      }
    }
  }
}
```

After saving the config, **restart Claude Desktop / Claude Code** for the server to appear.

### VS Code

Add to `.vscode/settings.json`:

```json
{
  "mcp.servers": {
    "jdocmunch": {
      "command": "uvx",
      "args": ["jdocmunch-mcp"],
      "env": {
        "GITHUB_TOKEN": "ghp_..."
      }
    }
  }
}
```

### Google Antigravity

1. Open the Agent pane → click the `⋯` menu → **MCP Servers** → **Manage MCP Servers**
2. Click **View raw config** to open `mcp_config.json`
3. Add the entry below, save, then restart the MCP server from the Manage MCPs pane

```json
{
  "mcpServers": {
    "jdocmunch": {
      "command": "uvx",
      "args": ["jdocmunch-mcp"]
    }
  }
}
```

---

## Workflows

### Index and Browse a Documentation Folder

```
index_local:         { "path": "/path/to/docs" }
get_toc:             { "repo": "docs" }
get_document_outline: { "repo": "docs", "doc_path": "README.md" }
```

### Index a GitHub Repository

```
index_repo:   { "url": "owner/repo" }
get_toc_tree: { "repo": "owner/repo" }
```

### Find and Read a Section

```
search_sections: { "repo": "owner/repo", "query": "authentication" }
get_section:     { "repo": "owner/repo", "section_id": "owner/repo::docs/auth.md::authentication#1" }
```

### Narrow Search to a Specific Document

```
search_sections: {
  "repo": "owner/repo",
  "query": "timeout",
  "doc_path": "docs/configuration.md"
}
```

### Search Without the Follow-up Read (v1.121+)

`compact` strips the per-row fields an agent can't act on (`content_hash`,
`parent_id`, byte offsets, empty collections, a `summary` identical to the
`title`). `snippet_bytes` inlines the head of each section body, so a confident
top hit needs no `get_section` call. Together they cut a 10-result response by
roughly 78% on our own docs.

```
search_sections: {
  "repo": "owner/repo",
  "query": "authentication",
  "compact": true,
  "snippet_bytes": 200
}
```

For total control, `fields` is an explicit whitelist and wins over `compact`
(`id` is always returned):

```
search_sections: {
  "repo": "owner/repo",
  "query": "authentication",
  "fields": ["title", "doc_path", "_score"]
}
```

### Read a Section with Full Hierarchy Context

```
get_section_context: {
  "repo": "owner/repo",
  "section_id": "owner/repo::docs/api.md::authentication/oauth/token-refresh#4",
  "max_tokens": 2000,
  "include_children": true
}
```

Returns the ancestor heading chain (for orientation), the section's full content, and summaries of immediate child sections — all in one call. Useful when a section alone is too thin to answer a question but a full file read is wasteful.

### Batch Retrieve Related Sections

```
get_sections: {
  "repo": "owner/repo",
  "section_ids": [
    "owner/repo::docs/config.md::database-settings#2",
    "owner/repo::docs/config.md::connection-pool#2"
  ]
}
```

### Verify Content Hasn't Changed

```
get_section: {
  "repo": "owner/repo",
  "section_id": "owner/repo::README.md::installation#1",
  "verify": true
}
```

`section.hash_verified` will be `true` if the cached file content matches the stored hash, `false` if the cache has been modified. This is **cache integrity verification** — it checks that the locally cached copy is intact, not that the upstream source is unchanged.

### Force Re-index

```
delete_index: { "repo": "owner/repo" }
index_local:  { "path": "/path/to/docs" }
```

Or use the `incremental: false` flag to force a full re-index without deleting:

```
index_repo: { "url": "owner/repo", "incremental": false }
```

---

## Tool Reference

| Tool                    | Purpose                                          | Key Parameters                                                   |
| ----------------------- | ------------------------------------------------ | ---------------------------------------------------------------- |
| `index_local`           | Index local documentation folder. An already-indexed source reuses its established handle; an explicit conflicting `name` returns a conflict instead of creating a duplicate index; several equivalent legacy indexes return bounded ambiguity. A proven-fresh equivalent corpus in a linked Git worktree is reused rather than duplicated (`worktree_mode="branch_local"` opts out) | `path`, `name`, `use_ai_summaries`, `extra_ignore_patterns`, `follow_symlinks`, `incremental`, `paths`, `worktree_mode`, `legacy_reconcile` |
| `index_repo`            | Index GitHub repository docs                     | `url`, `use_ai_summaries`, `incremental`                         |
| `list_repos`            | List all indexed documentation sets              | —                                                                |
| `doc_resolve_repo`      | Resolve a path to its doc-index handle (O(1)-sized). In a linked Git worktree of an indexed corpus, the not-found response additively lists the established handle in `canonical_candidates` + `worktree_resolution` (read-only) | `path`                                                        |
| `get_toc`               | Flat section list in document order              | `repo`, `path_glob`, `limit`, `offset`                           |
| `get_toc_tree`          | Nested section tree per document                 | `repo`, `path_glob`, `limit`, `offset`                           |
| `list_docs`             | One row per document: sections, format, size     | `repo`, `path_glob`, `limit`, `offset`                           |
| `get_document_outline`  | Section hierarchy for one document               | `repo`, `doc_path`                                               |
| `search_sections`       | Weighted search across sections                  | `repo`, `query`, `doc_path`, `max_results`, `compact`, `fields`, `snippet_bytes` |
| `get_section`           | Full content of one section                      | `repo`, `section_id`, `verify`                                   |
| `get_sections`          | Batch content retrieval                          | `repo`, `section_ids`, `verify`                                  |
| `get_section_context`   | Section + ancestor headings + child summaries    | `repo`, `section_id`, `max_tokens`, `include_children`           |
| `delete_index`          | Delete index and cache                           | `repo`                                                           |

---

## Large Indexes: Paging and the Response Ceiling

`get_toc`, `get_toc_tree` and `list_docs` describe a whole index, so on a
large one the answer can be megabytes. Since v1.147.0 two things bound it.

**Paging.** Pass `limit` and `offset`. `get_toc` pages by section;
`get_toc_tree` and `list_docs` page by document, and a document's tree is
never split across pages. Every response carries the same three keys, paged
or not:

| Key | Meaning |
| --- | --- |
| `sections_total` / `documents_total` / `docs_total` | How many exist after `path_glob`. This is the authority, not the length of the list. |
| `sections_truncated` / `documents_truncated` / `docs_truncated` | `true` when more follow this page. |
| `next_offset` | Pass it as `offset` for the next page; `null` on the last one. |

`list_docs` also takes `path_glob`. Its `total_section_count` and
`total_byte_size` still describe the whole index; `_meta.docs_with_mtime`
counts the documents on the page, so it compares with `doc_count`.

**The ceiling.** A response over `JDOCMUNCH_RESPONSE_MAX_BYTES` (default
1 MiB) is refused, never truncated. The refusal names the parameters that
narrow the call (`narrow_with`), the size it would have been
(`response_bytes`), the item total, and a `suggested_limit` that fits. Set
the variable to `0` to turn the ceiling off and get the pre-1.147 behaviour.
An unparseable or negative value keeps the default.

```json
{"error": "get_toc response would be 3,580,802 bytes, over the 1,048,576-byte ceiling ...",
 "response_bytes": 3580802, "response_max_bytes": 1048576,
 "sections_total": 12000, "narrow_with": ["path_glob", "limit"], "suggested_limit": 2811}
```

---

## Legacy Index Reconciliation (`legacy_reconcile`)

Indexes created before v1.102.0 predate corpus identity and can coexist with a
modern index of the same source. `index_local` accepts an opt-in
`legacy_reconcile` parameter to resolve one explicitly:

- `legacy_reconcile="report"` — prove (or fail to prove) the explicitly named
  legacy handle is an exact duplicate of a single modern peer: same verified
  corpus identity, same clean certified commit, and full path-and-hash
  coverage. Report performs no writes on any outcome — it proves from stored
  snapshots plus live Git evidence, so it requires a clean checkout at the
  certified commit. Reports `legacy_reconcile_ready` when the proof passes.
- `legacy_reconcile="apply"` — repeat the proof immediately before retirement,
  then retire the selected legacy handle and return the modern peer's handle.

Both modes require an explicit `name=` selecting the legacy handle (the
selected handle is the only possible loser — it is never derived or guessed), a
full refresh (no `paths` subset), and confirmed Git lineage. Any precondition
failure returns the top-level error `legacy_reconcile_not_applicable` with
nothing written. Every outcome's `legacy_reconciliation.reason_code` is listed
in SPEC.md's vocabulary table.

---

## Section IDs

Section IDs follow the format:

```
{repo}::{doc_path}::{ancestor-chain/slug}#{level}
```

The slug is prefixed with the ancestor heading chain, making IDs hierarchical and stable:

```
owner/repo::README.md::installation#1
owner/repo::docs/config.md::installation/prerequisites#3
owner/repo::docs/config.md::usage/configuration/advanced-configuration#4
local/myproject::guide.md::quick-start#1
```

IDs are returned by `get_toc`, `get_toc_tree`, `get_document_outline`, and `search_sections`. Pass them to `get_section`, `get_sections`, or `get_section_context` to retrieve content.

For local folders, `repo` defaults to `local/{folder-name}` — use the bare folder name when calling retrieval tools:

```
index_local: { "path": "/home/user/docs" }
get_toc:     { "repo": "docs" }
```

---

## Community Savings Meter

jDocMunch contributes an anonymous token savings delta to a live global counter with each tool call, POSTed to `https://j.gravelle.us/APIs/savings/post.php` and displayed at [jcodemunch.com](https://jcodemunch.com). Only two values are ever sent: the tokens saved (a number) and a random anonymous install ID. No content, paths, repo names, or anything identifying is transmitted. Network failures are silent and never affect tool performance.

The anonymous install ID is generated once and stored locally in `~/.doc-index/_savings.json`.

To disable, set `JDOCMUNCH_SHARE_SAVINGS=0` in your MCP server env:

```json
{
  "mcpServers": {
    "jdocmunch": {
      "command": "uvx",
      "args": ["jdocmunch-mcp"],
      "env": {
        "JDOCMUNCH_SHARE_SAVINGS": "0"
      }
    }
  }
}
```

---

## Runtime Identity Resource

The server publishes one MCP **resource** (not a tool): `munch://runtime/identity`, a read-only `munch.runtime.identity/v1` JSON document identifying this exact server process. Multi-agent harnesses use it to tell command-line-identical servers apart, detect restarts, and refuse cleanup when identity doesn't match.

Fields: `schema`, `product`, `version`, `transport`, `pid`, `process_start {value, source}`, `instance_id` (uuid4 minted once per process lifetime — a restart always changes it, even if the PID is reused), and optional `launch_id`.

`process_start.source` is `"os"` when the timestamp comes from the operating system (Windows `GetProcessTimes`, Linux `/proc` starttime); where that's unobtainable it falls back to the server's own first-read clock and says so with `"self_recorded"` — the value is never fabricated as OS evidence.

Set `JDOCMUNCH_LAUNCH_ID` (or the suite-generic `MUNCH_LAUNCH_ID`) in the server's environment to have an opaque launch token echoed back as `launch_id`; unset means the field is omitted. The resource is computed on demand, reads nothing from disk, and writes nothing. Command lines, env, cwd, hostnames, and corpus paths are deliberately excluded. The same contract ships in jcodemunch-mcp and jdatamunch-mcp.

---

## Troubleshooting

**"Repo not found"**
Check the repo identifier format. For local folders indexed as `local/myproject`, use `"repo": "myproject"` (bare name) or `"repo": "local/myproject"` (full form).

**"No documentation files found"**
The folder may not contain supported doc formats (`.md`, `.mdx`, `.txt`, `.rst`), or all files are excluded by skip patterns or `.gitignore`.

**"No sections extracted from files"**
Files may not contain headings. Plain-text files without recognized heading patterns produce a single root section.

**Rate limiting on GitHub**
Set `GITHUB_TOKEN` to increase GitHub API limits (5,000 requests/hour vs 60 unauthenticated).

**AI summaries not working**
Set `ANTHROPIC_API_KEY` (Claude Haiku) or `GOOGLE_API_KEY` (Gemini Flash). Anthropic takes priority if both are set. Without either key, summaries fall back to heading text or the title fallback.

**Stale index**
Use `delete_index` followed by `index_local` or `index_repo` to force a clean re-index.

**Encoding issues**
Files with invalid UTF-8 are handled safely using replacement characters.

---

## Storage

Indexes are stored at `~/.doc-index/` (override with the `DOC_INDEX_PATH` environment variable):

```
~/.doc-index/
├── {owner}/
│   ├── {name}.json       # Index metadata + section metadata (no content)
│   ├── {name}.postings.jsonl  # Per-section term rows for the first search (v1.146+)
│   └── {name}/           # Raw doc files for byte-range content reads
│       ├── README.md
│       └── docs/
│           └── guide.md
└── _savings.json         # Cumulative token savings counter
```

`{name}.postings.jsonl` stores each section's terms, so the first query in a
new server process does not re-read every document, and an incremental save
re-tokenizes only the sections that changed. The BM25 statistics are
recomputed exactly on every save either way, and the index file records the
tokenizer that counted them in `bm25_stats.stats_tokenizer`. The file is a
cache: a row is used only while it matches its section, and a missing or
damaged file means a slower first query, never a wrong one. Set
`JDOCMUNCH_POSTING_CACHE=0` to neither read nor write it.

---

## Tips

1. Start with `get_toc` or `get_toc_tree` to understand the structure of an indexed doc set.
2. Use `get_document_outline` when you already know which document is relevant — lighter than a full TOC.
3. Narrow `search_sections` with `doc_path` to avoid cross-document noise when searching within a known file.
4. Batch-retrieve related sections with `get_sections` instead of repeated `get_section` calls.
5. Use `verify: true` on `get_section` to detect whether the doc source has changed since indexing.
6. For docs without AI summaries, `search_sections` still works well — it scores on heading text and content words.
