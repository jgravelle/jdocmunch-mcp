<!-- mcp-name: io.github.jgravelle/jdocmunch-mcp -->

## You're Wasting Tokens... That Stops NOW

[![MCP Toplist](https://mcptoplist.com/badge/io.github.jgravelle%2Fjdocmunch-mcp.svg)](https://mcptoplist.com/server/io.github.jgravelle%2Fjdocmunch-mcp)

Most AI agents still explore documentation the expensive way:

open file → skim hundreds of irrelevant paragraphs → open another file → repeat

That burns tokens, floods context windows with noise, and forces models to reason through a lot of text they never needed in the first place.

**jDocMunch-MCP lets AI agents navigate documentation by section instead of reading files by brute force.**  
It indexes a documentation set once, then retrieves exactly the section the agent actually needs, with byte-precise extraction from the original file.

| Task | Traditional approach | With jDocMunch |
| --- | ---: | ---: |
| Find a configuration section | ~12,000 tokens | ~400 tokens |
| Browse documentation structure | ~40,000 tokens | ~800 tokens |
| Explore a full doc set | ~100,000 tokens | ~2,000 tokens |

Index once. Query cheaply forever.  
**Precision context beats brute-force context.**

---

# jDocMunch MCP

### AI-native documentation navigation for serious agents

![License](https://img.shields.io/badge/license-dual--use-blue)
![MCP](https://img.shields.io/badge/MCP-compatible-purple)
![Local-first](https://img.shields.io/badge/local--first-yes-brightgreen)
![jMRI](https://img.shields.io/badge/jMRI-Full-blueviolet)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20102349.svg)](https://doi.org/10.5281/zenodo.20102349)
[![PyPI version](https://img.shields.io/pypi/v/jdocmunch-mcp)](https://pypi.org/project/jdocmunch-mcp/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/jdocmunch-mcp)](https://pypi.org/project/jdocmunch-mcp/)

> ## Commercial licenses
> jDocMunch-MCP is **free for non-commercial use**.
>
> **Commercial use requires a paid license.**
>
> **jDocMunch-only licenses**
> - [Builder — $29](https://j.gravelle.us/jCodeMunch/descriptions.php#builder) — 1 developer
> - [Studio — $99](https://j.gravelle.us/jCodeMunch/descriptions.php#studio) — up to 5 developers
> - [Platform — $499](https://j.gravelle.us/jCodeMunch/descriptions.php#platform) — org-wide internal deployment
>
> **Want the full jMunch suite (code + docs + data)?**
> - [Munch Trio Builder Bundle — $99](https://j.gravelle.us/jCodeMunch/descriptions.php#builder)
> - [Munch Trio Studio Bundle — $449](https://j.gravelle.us/jCodeMunch/descriptions.php#studio)
> - [Munch Trio Platform Bundle — $2,499](https://j.gravelle.us/jCodeMunch/descriptions.php#platform)

> ### 1.x compatibility commitment
> Every 1.x license entitles you to every future 1.x release. We will never ship a 1.x version that:
> - removes or renames an MCP tool (deprecated tool names keep their aliases),
> - drops a `Section` field from the response shape,
> - forces a reindex without auto-migrating your existing index on first load,
> - changes the JSON wire format of any tool response in a way that breaks an existing consumer,
> - or makes a previously-default behavior raise.
>
> Anything that would require breaking these promises is reserved for a future major version (2.x). The full machine-checked contract is enforced via `tests/test_server.py` (tool-name and required-field invariants) and the replay-fixture gate that runs on every release.

**Stop dumping documentation files into context windows. Start navigating docs structurally.**

jDocMunch indexes documentation once by heading hierarchy and section structure, then gives MCP-compatible agents precise access to the explanations they actually need instead of forcing them to brute-read files.

It is built for workflows where token efficiency, context hygiene, and agent reliability matter.

---

## Why this exists

Large context windows do not fix bad retrieval.

Agents waste money and reasoning bandwidth when they:

- open entire documents to find one configuration block
- repeatedly re-read headings, boilerplate, and unrelated sections
- lose important explanations inside oversized context payloads
- consume documentation as flat text instead of structured knowledge

jDocMunch fixes that by changing the unit of access from **file** to **section**.

Instead of handing an agent an entire document, it can retrieve exactly:

- an installation section
- a configuration section
- an API explanation
- a troubleshooting section
- a specific subtree of related headings

That makes documentation exploration cheaper, faster, and more stable.

---

## What makes it different

### Section-first retrieval
Search and retrieve documentation by section, not just file path or keyword match.

### Byte-precise extraction
Full content is pulled on demand from exact byte offsets into the original file.

### Stable section IDs
Sections retain durable identities across re-indexing when path, heading text, and heading level remain unchanged.

### Local-first architecture
Indexes and raw docs are stored locally. No hosted dependency required.

### MCP-native workflow
Works with Claude Desktop, Claude Code, Google Antigravity, and other MCP-compatible clients.

---

## What gets indexed

Every section stores:

- title and heading level
- one-line summary
- extracted tags and references
- SHA-256 content hash for drift detection
- byte offsets into the original file

This allows agents to discover documentation structurally, then request only the specific section they need.

---

## Why agents need this

Traditional doc retrieval methods all break in different ways:

- **File scanning** loads far too much irrelevant text
- **Keyword search** finds terms but often loses context
- **Chunking** breaks authored hierarchy and separates explanations from examples

jDocMunch preserves the structure the human author intended:

- heading hierarchy
- parent/child relationships
- section boundaries
- coherent explanatory units

Agents do not need bigger context windows.  
They need better navigation.

---

## How it works

jDocMunch implements **[jMRI-Full](https://dev.to/jgravelle/your-ai-agent-is-dumpster-diving-through-your-code-326f)** — the open specification for structured retrieval MCP servers. jMRI-Full covers the full stack: discover, search, retrieve, and metadata operations with batch retrieval, hash-based drift detection, byte-offset addressing, and a complete `_meta` envelope on every call.

1. **Discovery**
   GitHub API or local directory walk

2. **Security filtering**
   Traversal protection, secret exclusion, binary detection

3. **Parsing**
   Format-aware section splitting: heading-based (Markdown/MDX/HTML/RST/AsciiDoc), structure-based (OpenAPI tags, JSON keys, XML elements), or cell-based (Jupyter)

4. **Hierarchy wiring**
   Parent/child relationships established

5. **Summarization**
   Heading text → AI batch summaries → title fallback

6. **Storage**
   JSON index + raw files stored locally under `~/.doc-index/`

7. **Retrieval**
   O(1) byte-offset seeking via stable section IDs

---

## Stable section IDs

```text
{repo}::{doc_path}::{ancestor-chain/slug}#{level}
```

The slug is prefixed with the ancestor heading chain, making IDs both readable and stable. A new heading inserted in one branch of a document never renumbers IDs in another branch.

Examples:

* `owner/repo::docs/install.md::installation#1`
* `owner/repo::docs/install.md::installation/prerequisites#3`
* `owner/repo::README.md::usage/configuration/advanced-configuration#4`
* `local/myproject::guide.md::configuration#2`

IDs remain stable across re-indexing when the file path, heading text, heading level, and parent heading chain do not change.

---

## Installation

### Prerequisites

* Python 3.10+
* `pip`

### Install

```bash
pip install jdocmunch-mcp
```

Verify:

```bash
jdocmunch-mcp --help
```

---

## Configure an MCP client

> **PATH note:** MCP clients often run with a restricted environment where `jdocmunch-mcp` may not be found even if it works in your shell. Using [`uvx`](https://github.com/astral-sh/uv) is the recommended approach because it resolves the package on demand without relying on your system PATH. If you prefer `pip install`, use the absolute path to the executable instead.

### Common executable paths

* **Linux:** `/home/<username>/.local/bin/jdocmunch-mcp`
* **macOS:** `/Users/<username>/.local/bin/jdocmunch-mcp`
* **Windows:** `C:\\Users\\<username>\\AppData\\Roaming\\Python\\Python3xx\\Scripts\\jdocmunch-mcp.exe`

---

## Claude Desktop / Claude Code

Config file location:

| OS      | Path                                                              |
| ------- | ----------------------------------------------------------------- |
| macOS   | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Linux   | `~/.config/claude/claude_desktop_config.json`                     |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json`                     |

### Minimal config

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

### With optional AI summaries and GitHub auth

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

For Anthropic or Gemini, the base `uvx jdocmunch-mcp` command is enough once the
corresponding API key is present. For OpenAI-compatible providers such as OpenAI,
MiniMax, or GLM-5, include the optional dependency in the launcher command:

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

After saving the config, **restart Claude Desktop / Claude Code**.

### Claude Code hooks (recommended)

jDocMunch ships enforcement hooks that keep your agent honest:

- **PreToolUse** — warns when Claude tries to `Read` a large doc file, suggesting `search_sections` + `get_section`
- **PostToolUse** — auto-reindexes doc files after `Edit`/`Write` so the index never goes stale
- **PreCompact** — injects a session snapshot before context compaction so doc orientation survives

Install everything in one command:

```bash
jdocmunch-mcp init
```

This detects your MCP clients, patches their config, installs a Doc Exploration Policy into CLAUDE.md, sets up enforcement hooks, and indexes your current directory. Use `--dry-run` to preview, `--demo` for a benefit summary, or `--yes` for non-interactive mode.

For hooks only:

```bash
jdocmunch-mcp init --hooks
```

If you also use [jCodeMunch](https://github.com/jgravelle/jcodemunch-mcp), run both:

```bash
jcodemunch-mcp init
jdocmunch-mcp init
```

### CLI subcommands

| Subcommand | Purpose |
|------------|---------|
| `serve` (default) | Run the MCP server (stdio) |
| `init` | One-command onboarding: detect clients, write config, install policy, hooks, index |
| `claude-md` | Print or install the Doc Exploration Policy (`--install global\|project`) |
| `index-local --path <dir>` | Index a local folder (CLI, no MCP session needed) |
| `index-file <path>` | Re-index a single file within an existing index |
| `hook-pretooluse` | PreToolUse hook handler (reads JSON from stdin) |
| `hook-posttooluse` | PostToolUse hook handler (reads JSON from stdin) |
| `hook-precompact` | PreCompact hook handler (reads JSON from stdin) |

---

## Google Antigravity

1. Open the Agent pane
2. Click the `⋯` menu → **MCP Servers** → **Manage MCP Servers**
3. Click **View raw config** to open `mcp_config.json`
4. Add the entry below, save, then restart the MCP server

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

## OpenClaw

**Option A — CLI (one command):**

```bash
openclaw mcp set jdocmunch '{"command":"uvx","args":["jdocmunch-mcp"]}'
```

**Option B — Edit config directly:**

Add the entry to `~/.openclaw/openclaw.json` under `mcpServers`:

```json
{
  "mcpServers": {
    "jdocmunch": {
      "command": "uvx",
      "args": ["jdocmunch-mcp"],
      "transport": "stdio"
    }
  }
}
```

With optional AI summaries:

```json
{
  "mcpServers": {
    "jdocmunch": {
      "command": "uvx",
      "args": ["jdocmunch-mcp"],
      "transport": "stdio",
      "env": {
        "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}"
      }
    }
  }
}
```

Restart the gateway and verify:

```bash
openclaw gateway restart
openclaw mcp list
```

**Per-agent routing (optional):**

```json
{
  "agents": {
    "researcher": {
      "mcpServers": ["jdocmunch", "brave-search", "fetch"]
    }
  }
}
```

### Tell your OpenClaw agent to use it

Without explicit instructions, your agent will ignore jDocMunch even though it's connected. Create a system prompt file (e.g. `~/.openclaw/agents/researcher.md`) with:

```markdown
## Documentation Policy
Always use jDocMunch-MCP tools for documentation exploration.
- Before reading a doc file: use search_sections or get_toc
- To retrieve specific content: use get_section with the section ID
- To index local docs: use index_local with the docs folder path
- Never open documentation files directly — navigate by section.
```

Point your agent at it in `~/.openclaw/openclaw.json`:

```json
{
  "agents": {
    "named": {
      "researcher": {
        "systemPromptFile": "~/.openclaw/agents/researcher.md"
      }
    }
  }
}
```

---

## Usage examples

```json
index_local:          { "path": "/path/to/docs" }
index_repo:           { "url": "owner/repo" }

get_toc:              { "repo": "owner/repo" }
get_toc_tree:         { "repo": "owner/repo" }
get_document_outline: { "repo": "owner/repo", "doc_path": "docs/config.md" }
search_sections:      { "repo": "owner/repo", "query": "authentication" }
get_section:          { "repo": "owner/repo", "section_id": "owner/repo::docs/config.md::authentication#1" }
```

---

## Tool surface

| Tool                    | Purpose                                               |
| ----------------------- | ----------------------------------------------------- |
| `index_local`           | Index a local documentation folder                    |
| `index_repo`            | Index a GitHub repository’s docs                      |
| `list_repos`            | List indexed documentation sets                       |
| `doc_resolve_repo`      | Resolve a filesystem path to its doc-index handle     |
| `get_toc`               | Flat section list in document order                   |
| `get_toc_tree`          | Nested section tree per document                      |
| `get_document_outline`  | Section hierarchy for one document                    |
| `search_sections`       | Weighted search returning summaries only              |
| `get_section`           | Full content of one section                           |
| `get_sections`          | Batch content retrieval                               |
| `get_section_context`   | Section + ancestor headings + child summaries         |
| `delete_index`          | Remove a doc index                                    |
| `get_broken_links`      | Detect internal links/anchors that no longer resolve  |
| `get_doc_coverage`      | Which jcodemunch symbols have matching doc sections   |

Search and retrieval tools include a `_meta` envelope with timing, token savings, and cost avoided.

Example:

```json
"_meta": {
  "latency_ms": 12,
  "sections_returned": 5,
  "tokens_saved": 1840,
  "total_tokens_saved": 94320,
  "cost_avoided": { "claude_opus": 0.0276, "gpt5_latest": 0.0184 },
  "total_cost_avoided": { "claude_opus": 1.4148, "gpt5_latest": 0.9432 }
}
```

`total_tokens_saved` and `total_cost_avoided` accumulate across tool calls and persist to `~/.doc-index/_savings.json`.

### Check your token savings

Every jDocMunch tool response includes a `_meta` block with `tokens_saved` (this call) and `total_tokens_saved` (lifetime). To check your cumulative savings, ask your agent to call any jDocMunch tool (e.g. `get_toc` or `search_sections`) and look at the `_meta` envelope. Lifetime stats persist in `~/.doc-index/_savings.json` across sessions.

---

## Supported formats

| Format             | Extensions                          | Notes                                                                          |
| ------------------ | ----------------------------------- | ------------------------------------------------------------------------------ |
| Markdown           | `.md`, `.markdown`                  | ATX (`# Heading`) and setext headings                                          |
| MDX                | `.mdx`                              | JSX tags, frontmatter, import/export stripped before parsing                   |
| Plain text         | `.txt`                              | Paragraph-block section splitting                                              |
| reStructuredText   | `.rst`                              | Adornment-based heading detection                                              |
| AsciiDoc           | `.adoc`                             | `=` and `==` heading hierarchy                                                 |
| Jupyter Notebook   | `.ipynb`                            | Markdown cells used as sections; code cells attached as content                |
| HTML               | `.html`                             | `<h1>`–`<h6>` headings; boilerplate stripped                                  |
| OpenAPI / Swagger  | `.yaml`, `.yml`, `.json`, `.jsonc`  | OpenAPI 3.x and Swagger 2.x; operations grouped by tag as sections             |
| JSON / JSONC       | `.json`, `.jsonc`                   | Top-level keys as sections; JSONC comments stripped before parsing             |
| XML / SVG / XHTML  | `.xml`, `.svg`, `.xhtml`            | Element hierarchy used for section structure                                   |
| Office documents   | `.pdf`, `.docx`, `.pptx`, `.epub`   | Optional: `pip install jdocmunch-mcp[office]` — converted to Markdown locally via [markitdown](https://github.com/microsoft/markitdown) at index time |

### Office document support (optional `[office]` extra)

`pip install jdocmunch-mcp[office]` adds PDF, Word, PowerPoint, and EPUB
ingestion for **local indexing** (`index_local` / `index-file` / the `watch`
daemon). Files are converted to Markdown on your machine at index time and then
sectioned, searched, and health-checked like any other doc.

Disclosure, in full:

* **Conversion is 100% local.** markitdown's optional cloud converters (LLM
  image description, Azure Document Intelligence, YouTube transcription) are
  never enabled — no network request originates from office conversion.
* Converted Markdown is cached under the index storage root (`.office_cache/`,
  keyed by file-content hash), so refreshes don't re-convert unchanged files.
* Tabular formats (`.csv`, `.xlsx`) are deliberately **not** wired in — the
  jMunch suite routes tabular data to
  [jdatamunch-mcp](https://github.com/jgravelle/jdatamunch-mcp).
* GitHub remote indexing (`doc_index_repo`) does not fetch office files; the
  feature is local-only.
* Without the extra installed, office files are skipped at discovery and the
  coverage report names the reason (`office_extra_not_installed`).
* PDF text extraction is best-effort: layout-heavy or scanned (image-only)
  PDFs may yield sparse Markdown. Section quality follows heading detection in
  the converted output.

See `ARCHITECTURE.md` for parser details.

---

## Security

Built-in protections include:

* path traversal prevention
* symlink escape protection
* secret file exclusion (`.env`, `*.pem`, and similar)
* binary file detection
* configurable file size limits
* storage path injection prevention via `_safe_content_path()`
* atomic index writes

See `SECURITY.md` for details.

---

## Best use cases

* agent-driven documentation exploration
* finding configuration and API reference sections
* onboarding to unfamiliar frameworks
* token-efficient multi-agent documentation workflows
* large documentation sets with dozens of files

---

## Not intended for

* source code symbol indexing (use [jCodeMunch](https://github.com/jgravelle/jcodemunch-mcp) for that)
* cross-repository global search
* semantic/vector similarity search as a standalone product (hybrid BM25 + semantic fusion is supported when embeddings are enabled — defaults to `"auto"`, on whenever a provider is configured — but the core workflow remains structure-first)

---

## Environment variables

| Variable                          | Purpose                                                           | Required |
| --------------------------------- | ----------------------------------------------------------------- | -------- |
| `GITHUB_TOKEN`                    | GitHub API auth                                                   | No       |
| `ANTHROPIC_API_KEY`               | Section summaries via Claude Haiku                                | No       |
| `GOOGLE_API_KEY`                  | Section summaries via Gemini Flash; also Gemini embeddings        | No       |
| `OPENAI_API_KEY`                  | OpenAI embeddings (text-embedding-3-small)                        | No       |
| `JDOCMUNCH_EMBEDDING_PROVIDER`    | Force provider: `gemini`, `openai`, `openai-compatible`, `sentence-transformers`, `none` | No |
| `JDOCMUNCH_OPENAI_COMPAT_URL`      | Endpoint URL for `openai-compatible` embeddings                  | No       |
| `JDOCMUNCH_OPENAI_COMPAT_MODEL`   | Model for `openai-compatible` embeddings                          | No       |
| `JDOCMUNCH_OPENAI_COMPAT_API_KEY` | Dedicated optional API key for `openai-compatible` embeddings     | No       |
| `JDOCMUNCH_OPENAI_COMPAT_BATCH_SIZE` | Batch size for `openai-compatible` embeddings (default: `32`)   | No       |
| `JDOCMUNCH_ST_MODEL`              | sentence-transformers model (default: `all-MiniLM-L6-v2`)        | No       |
| `DOC_INDEX_PATH`                  | Custom cache path                                                 | No       |
| `JDOCMUNCH_SHARE_SAVINGS`         | Set to `0` to disable anonymous community token savings reporting | No       |
| `JDOCMUNCH_SESSION_TOKEN_BUDGET`  | Advisory session budget over response tokens served. When set, responses carry `_meta.budget` at >=80% (`approaching`) / >=100% (`over`) and `get_session_stats` reports the block. Never blocks or truncates. Unset/`0` = off | No |
| `JDOCMUNCH_WATCH_POLL_DELAY_MS`   | Poll interval (ms) used only when the watcher falls back to polling (e.g. under WSL); default `1000` | No |
| `JDOCMUNCH_LAUNCH_ID`             | Opaque launch token echoed back as `launch_id` in the `munch://runtime/identity` resource (fallback: `MUNCH_LAUNCH_ID`); omitted when unset | No |

`get_session_stats` also reports a `tool_surface` receipt: visible vs catalog tool counts, estimated schema tokens for each (bytes/4 scale), tokens avoided by the active tool profile, and the heaviest tool schemas. Computed inline on the stats call; nothing persisted.

---

## Runtime identity resource

The server exposes one MCP resource, `munch://runtime/identity` — a read-only `munch.runtime.identity/v1` JSON document identifying this exact server process (`product`, `version`, `transport`, `pid`, OS-derived `process_start`, per-process-lifetime `instance_id`, optional `launch_id` echo). Multi-agent harnesses use it to tell command-line-identical servers apart and detect restarts. Computed on demand with no disk reads, writes, or network; when the OS process-start probe is unavailable the timestamp is disclosed as `source: "self_recorded"`, never fabricated. Command lines, env, cwd, hostnames, and corpus paths are deliberately excluded. Same contract in jcodemunch-mcp and jdatamunch-mcp.

---

## Canonical handoff (`finalize_handoff` + `munch://handoff/<id>`)

A multi-step documentation audit can end with one authoritative, server-attested result. The assistant authors the analysis; `finalize_handoff` takes those sections plus `evidence_refs`, validates every reference against what this session **actually retrieved** (section ids or doc paths served by `search_sections` / `search_titles` / `get_section` / `get_sections` — unknown refs fail closed), deterministically assembles one canonical Markdown handoff (`jdocmunch.handoff/v1`), and returns a compact receipt: `{handoff_id, resource_uri, sha256, length, canonical: true}`. The immutable body is served by the `munch://handoff/<id>` resource — repeated reads are byte-identical. Session-scoped, in-memory, never writes to your documentation corpus; appendices appear exactly once; no character limit. Suite parity with jcodemunch-mcp and jdatamunch-mcp.

---

## Keeping indexes fresh (the `watch` daemon)

By default jDocMunch's index freshness rides the PostToolUse hook, which only
fires when the agent itself edits a doc file. Docs changed outside the agent (a
git pull, an editor, a build step, a teammate) go stale until the agent happens
to touch that file again.

The `watch` daemon closes that gap. It auto-discovers every locally-indexed doc
repo and re-indexes the owning index incrementally whenever a documentation file
(`.md`, `.rst`, `.txt`, `.adoc`, `.ipynb`, `.html`, and the other supported
formats) changes on disk.

```bash
# Foreground — stays running, Ctrl+C to stop:
jdocmunch-mcp watch

# Or install it as a background login service (systemd / launchd / Task Scheduler):
jdocmunch-mcp watch-install
jdocmunch-mcp watch-status      # is it active? which repos are covered?
jdocmunch-mcp watch-uninstall   # remove it
```

Repos indexed while the watcher runs are picked up on the next discovery pass.
GitHub-sourced indexes (no local source_root) are skipped — there's nothing
on-disk to watch. Coverage is also queryable from an agent via the
`get_watch_status` tool.

---

## Background behavior, fully disclosed

jDocMunch does nothing over the network or in the background that isn't listed
here. Keep this section current whenever any new background, persistent, or
network behavior ships.

* **File watching (opt-in):** `jdocmunch-mcp watch` runs a foreground daemon
  that watches your locally-indexed doc folders and re-indexes changed files.
  It runs only while you run it; nothing is watched unless you start it.
* **Watch login service (explicit opt-in):** `jdocmunch-mcp watch-install`
  registers the `watch` daemon as a per-user login service (systemd user unit /
  launchd LaunchAgent / Task Scheduler task) so it starts at login.
  `jdocmunch-mcp watch-uninstall` removes it. Never installed unless you run
  `watch-install`.
* **PostToolUse / PreToolUse / PreCompact hooks (opt-in):** installed only if
  you run `jdocmunch-mcp init --hooks`. The PostToolUse hook spawns a throttled
  single-file re-index after an Edit/Write to a doc file.
* **Anonymous savings telemetry (opt-out):** a tokens-saved delta + a random
  anonymous install ID are POSTed to a live counter (see "Community savings
  meter" below). Disable with `JDOCMUNCH_SHARE_SAVINGS=0`.
* **Local index store:** indexes live under `~/.doc-index/` (override with
  `DOC_INDEX_PATH`). No index content leaves your machine.

---

## Community savings meter

Each tool call can contribute an anonymous delta to a live global counter at [j.gravelle.us](https://j.gravelle.us). Only two values are sent:

* tokens saved
* a random anonymous install ID

No content, file paths, repo names, or identifying material are sent.

The anonymous install ID is generated once and stored in `~/.doc-index/_savings.json`.

To disable reporting, set:

```bash
JDOCMUNCH_SHARE_SAVINGS=0
```

---

## Contributing

PRs welcome! All contributors must sign the [Contributor License Agreement](https://cla-assistant.io/jgravelle/jdocmunch-mcp) before their PR can be merged — CLA Assistant will prompt you automatically. See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

---

## Documentation

* [USER_GUIDE.md](USER_GUIDE.md)
* [ARCHITECTURE.md](ARCHITECTURE.md)
* [SPEC.md](SPEC.md)
* [SECURITY.md](SECURITY.md)
* [TOKEN_SAVINGS.md](TOKEN_SAVINGS.md)

---

## License (dual use)

This repository is **free for non-commercial use** under the terms below.
**Commercial use requires a paid commercial license.**

---

## Works with

jDocMunch plugs into any MCP-compatible agent or IDE. Tested configurations:

| Platform | Config |
|----------|--------|
| **Claude Code / Claude Desktop** | `jdocmunch-mcp init` (auto-detects and patches config) |
| **Cursor / Windsurf** | `jdocmunch-mcp init` or manual `mcp.json` |
| **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** | Add to `~/.hermes/config.yaml` — see [skill](https://github.com/NousResearch/hermes-agent/pull/10413) |
| **Any MCP client** | stdio: `jdocmunch-mcp` |

<details>
<summary>Hermes Agent config</summary>

```yaml
# ~/.hermes/config.yaml
mcp_servers:
  jdocmunch:
    command: "uvx"
    args: ["jdocmunch-mcp"]
```
</details>

## Star History

<a href="https://www.star-history.com/?repos=jgravelle%2Fjdocmunch-mcp&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/image?repos=jgravelle/jdocmunch-mcp&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/image?repos=jgravelle/jdocmunch-mcp&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/image?repos=jgravelle/jdocmunch-mcp&type=date&legend=top-left" />
 </picture>
</a>

---

## Copyright and license text

Copyright (c) 2026 J. Gravelle

### 1. Non-commercial license grant (free)

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the “Software”), to use, copy, modify, merge, publish, and distribute the Software for **personal, educational, research, hobby, or other non-commercial purposes**, subject to the following conditions:

1. The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.
2. Any modifications made to the Software must clearly indicate that they are derived from the original work, and the name of the original author (J. Gravelle) must remain intact. He's kinda full of himself.
3. Redistributions of the Software in source code form must include a prominent notice describing any modifications from the original version.

### 2. Commercial use

Commercial use of the Software requires a separate paid commercial license from the author.

“Commercial use” includes, but is not limited to:

* use of the Software in a business environment
* internal use within a for-profit organization
* incorporation into a product or service offered for sale
* use in connection with revenue generation, consulting, SaaS, hosting, or fee-based services

For commercial licensing inquiries:
**[j@gravelle.us](mailto:j@gravelle.us)**
**[https://j.gravelle.us](https://j.gravelle.us)**

Until a commercial license is obtained, commercial use is not permitted.

### 3. Disclaimer of warranty

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NONINFRINGEMENT.

IN NO EVENT SHALL THE AUTHOR OR COPYRIGHT HOLDER BE LIABLE FOR ANY CLAIM, DAMAGES, OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT, OR OTHERWISE, ARISING FROM, OUT OF, OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
