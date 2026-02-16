## Read documentation using **1–3% of the usual tokens**

Documentation queries normally require loading entire README and docs folders into context, wasting tens of thousands of tokens per question.

**jDocMunch converts documentation into indexed, section-level retrieval** so agents load only the relevant paragraphs instead of entire files.

| Task                         | Traditional approach | With jDocMunch          |
| ---------------------------- | -------------------- | ----------------------- |
| Answer doc question          | ~50,000 tokens       | ~1–2k tokens            |
| Multi-query research session | Linear token growth  | Mostly cached retrieval |
| Large repo docs              | Slow + expensive     | Instant + predictable   |

Index once, navigate intelligently, retrieve only what matters.

# jDocMunch MCP

## Precision Documentation Intelligence for AI Agents

![License](https://img.shields.io/badge/license-MIT-blue)
![MCP](https://img.shields.io/badge/MCP-compatible-purple)
![Local-first](https://img.shields.io/badge/local--first-yes-brightgreen)

**Stop loading entire documentation sets. Retrieve only what matters.**

jDocMunch MCP converts large documentation repositories into a structured, queryable intelligence layer for AI agents. Instead of repeatedly loading hundreds of files, agents retrieve only the relevant documentation sections, dramatically reducing token cost, latency, and API usage.

> **Part of the Munch Trio** — see [The Munch Trio](#the-munch-trio) for the complete ecosystem.

---

## Architecture

```
MCP Client (Claude Desktop, OpenClaw, etc.)
    |
    v
+-----------------------------------+
|  jDocMunch MCP Server              |
|                                    |
|  Tools:                            |
|    index_repo / index_local        |
|    get_toc / get_toc_tree          |
|    get_document_outline            |
|    search_sections                 |
|    get_section / get_sections      |
|    list_repos / delete_index       |
|                                    |
|  +----------+  +---------------+   |
|  | Parser   |  | Summarizer    |   |
|  | md/mdx   |  | Anthropic API |   |
|  | rst      |  | Ollama (local)|   |
|  +----------+  +---------------+   |
|        |                           |
|  +----------+                      |
|  | Storage  | (~/.doc-index/)      |
|  | JSON idx |                      |
|  | raw files|                      |
|  +----------+                      |
+-----------------------------------+
    |                |
    v                v
GitHub API      Local Filesystem
```

---

## Why jDocMunch Exists

Large documentation repositories often contain hundreds or thousands of files. Traditional AI workflows repeatedly load entire documentation sets for each query, resulting in:

* Excessive token consumption
* Slower response times
* Rate-limit pressure
* Higher operational cost

jDocMunch indexes documentation once and enables precise retrieval for every subsequent query.

---

## Proven Real-World Benchmark

**Repository:** `openclaw/openclaw`
**Documentation size:** 583 files (~812K tokens)

### Cost Breakdown

| Phase                   | Tokens | Frequency              |
| ----------------------- | ------ | ---------------------- |
| **Indexing (one-time)** | ~708K  | Once per repo          |
| **Per-query (cached)**  | ~500   | Every subsequent query |
| **Incremental reindex** | Varies | Only changed files     |

### Session Results

| Query     | Without MCP    | With MCP       | Savings |
| --------- | -------------- | -------------- | ------- |
| 1st query | 811,756 tokens | 708,794 tokens | 12.7%   |
| 2nd query | 811,756 tokens | 534 tokens     | 99.9%   |
| 3rd query | 811,756 tokens | 542 tokens     | 99.9%   |

**Session Total**

* Without MCP: 2,435,268 tokens + 1,752 API calls
* With MCP: 709,870 tokens + 0 API calls
* **Savings:** 70.9%

---

## Scale Economics

| Monthly Queries | Without MCP | With MCP    | Savings |
| --------------- | ----------- | ----------- | ------- |
| 20 queries      | 16M tokens  | 716K tokens | 95.6%   |
| 100 queries     | 81M tokens  | 741K tokens | 99.1%   |
| 1,000 queries   | 812M tokens | 1.2M tokens | 99.8%   |

As query volume grows, savings approach **two orders of magnitude**.

---

## Installation

### Prerequisites

* **Python 3.10+**
* **pip** (or any Python package manager)

### Install

```bash
pip install git+https://github.com/jgravelle/jdocmunch-mcp.git
```

Verify:

```bash
jdocmunch-mcp --help 2>&1 | head -1
```

---

## Configure MCP Client

### Claude Desktop / Claude Code

**macOS/Linux:** `~/.config/claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "jdocmunch": {
      "command": "jdocmunch-mcp",
      "env": {
        "GITHUB_TOKEN": "ghp_...",
        "ANTHROPIC_API_KEY": "sk-ant-..."
      }
    }
  }
}
```

Environment variables are optional:

* **GITHUB_TOKEN** — higher GitHub rate limits and private repo access
* **ANTHROPIC_API_KEY** — enables AI-generated summaries (otherwise keyword extraction is used)

Any MCP client supporting stdio transport can run `jdocmunch-mcp`.

---

## Quickstart

```
1. index_local(path="/path/to/project")
2. get_toc(repo="my-project")
3. search_sections(repo="my-project", query="authentication")
4. get_section(repo="my-project", section_id="readme-md--installation")
```

Typical queries return **hundreds of tokens instead of hundreds of thousands**.

---

## Tools

| Tool              | Purpose                                   |
| ----------------- | ----------------------------------------- |
| `index_repo`      | Index a GitHub repository’s documentation |
| `index_local`     | Index a local documentation directory     |
| `list_repos`      | List indexed repositories                 |
| `get_toc`         | Table of contents with summaries          |
| `get_toc_tree`    | Nested TOC structure                      |
| `get_section`     | Retrieve a single section                 |
| `get_sections`    | Batch retrieve sections                   |
| `search_sections` | Search by title, keywords, and summaries  |

---

## Features

* Markdown, MDX, and reStructuredText support
* Incremental reindexing (changed files only)
* Symlink protection, secret detection, `.gitignore` awareness
* Local-only mode (`JDOCMUNCH_LOCAL_ONLY=true`)
* Optional AI summaries via Anthropic or local Ollama
* Automatic cache versioning and invalidation

---

## How It Works

1. Documentation indexed once (GitHub or local directory)
2. Structured section index and summaries generated
3. Cached locally (~100KB–1MB per repo)
4. Precision MCP queries served instantly
5. Only relevant fragments retrieved

---

## Environment Variables

| Variable               | Purpose                   | Required |
| ---------------------- | ------------------------- | -------- |
| `GITHUB_TOKEN`         | GitHub API authentication | No       |
| `ANTHROPIC_API_KEY`    | AI-generated summaries    | No       |
| `JDOCMUNCH_LOCAL_ONLY` | Disable GitHub fetching   | No       |

---

## Troubleshooting

| Problem            | Cause               | Fix                       |
| ------------------ | ------------------- | ------------------------- |
| GitHub rate limits | No token configured | Set `GITHUB_TOKEN`        |
| Generic summaries  | Missing API key     | Set `ANTHROPIC_API_KEY`   |
| Command not found  | Package not on PATH | Reinstall and verify PATH |
| Index outdated     | Docs changed        | Re-run indexing           |

---

## The Munch Trio

| Package                                                             | Purpose                                           | Repo     |
| ------------------------------------------------------------------- | ------------------------------------------------- | -------- |
| [jcodemunch-mcp](https://github.com/jgravelle/jcodemunch-mcp)       | Code symbol indexing via AST parsing              | 11 tools |
| **jdocmunch-mcp**                                                   | Documentation section indexing                    | 8 tools  |
| [jcontextmunch-mcp](https://github.com/jgravelle/jcontextmunch-mcp) | Unified orchestration and hybrid context assembly | 9 tools  |

Using the full stack? Configure **jcontextmunch-mcp** only — it launches the other two automatically.

---

## Documentation

* USER_GUIDE.md — Usage workflows and examples
* SECURITY.md — Threat model and controls
* CACHE_SPEC.md — Cache format and versioning
* TOKEN_COMPARISON.md — Token usage analysis
* TOKEN_SAVINGS_COMPARISON.md — Cost impact analysis

---

## Vision

jDocMunch provides the **documentation intelligence layer** for the agent era — enabling autonomous systems to reason over large knowledge bases efficiently, predictably, and at scale.

---

## License

MIT
