<!-- mcp-name: io.github.jgravelle/jdocmunch-mcp -->

# jDocMunch MCP

**jDocMunch is an MCP server for coding agents that retrieves the exact documentation section a task needs, without loading whole files into the context window.**

Index a documentation set once by heading hierarchy, then fetch a single section, a heading subtree, or a ranked search result — extracted byte-precisely from the original file.

[**Install**](#install) · [**Quickstart**](#quickstart) · [**Benchmarks**](benchmarks/) · [**Commercial licensing**](#licensing-and-commercial-use)

[![PyPI version](https://img.shields.io/pypi/v/jdocmunch-mcp)](https://pypi.org/project/jdocmunch-mcp/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/jdocmunch-mcp)](https://pypi.org/project/jdocmunch-mcp/)
![License](https://img.shields.io/badge/license-dual--use-blue)
![MCP](https://img.shields.io/badge/MCP-compatible-purple)
![Local-first](https://img.shields.io/badge/local--first-yes-brightgreen)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20102349.svg)](https://doi.org/10.5281/zenodo.20102349)

**Free for personal use.** Commercial use requires a paid license — [terms below](#licensing-and-commercial-use).

---

## Why jDocMunch?

**The problem.** An agent asked "how do I configure authentication?" opens a documentation file, skims hundreds of paragraphs it does not need, opens another, and repeats. Large context windows do not fix this. They just make the waste affordable enough to ignore until the bill arrives, and they crowd out the context the model actually needed.

**The mechanism.** jDocMunch parses a documentation set into a section tree keyed by heading hierarchy, stores each section's byte offsets into the original file, and exposes retrieval over MCP. Sections keep durable identities across re-indexing as long as path, heading text, and heading level are unchanged.

**The outcome.** The unit of access changes from *file* to *section*. An agent retrieves the installation section, one configuration block, or a specific heading subtree — and nothing else.

---

## What makes it different

### Section-first retrieval
Search and retrieve documentation by section, not just file path or keyword match.

### Byte-precise extraction
Full content is pulled on demand from exact byte offsets into the original file.

### Stable section IDs
Sections retain durable identities across re-indexing when path, heading text, and heading level remain unchanged.

---

## Evidence

Four benchmarks against public documentation corpora, each with the corpus, date, and per-query results recorded in [`benchmarks/`](benchmarks/).

| Corpus | Scale | Indexed in | Result |
|---|---|---|---|
| [Kubernetes](benchmarks/jDocMunch_Benchmark_Kubernetes.md) (`kubernetes/website`, 2026-03-04) | 1,569 `.md` files, 4,355 sections, 16 MB | 3,352 ms | 27,285 tokens saved on a single node-affinity query; 100 ms latency |
| [SciPy](benchmarks/jDocMunch_Benchmark_SciPy.md) | 10,402 sections, ~855,000 corpus tokens | 2,247 ms | 135–152 ms per query across sparse-solver, FFT, and optimization lookups |
| [LangChain](benchmarks/jDocMunch_Benchmark_LangChain_MDX.md) (MDX) | 5,973 sections | 5,204 ms | MDX-aware sectioning found 754% more sections than the naive pass |
| [Wiki](benchmarks/jDocMunch_Benchmark_Wiki.md) | 7,449-token corpus | — | Search returns ranked metadata in ~190 tokens against a 7,449-token whole-corpus read |

**Read these as per-corpus results, not as a single headline multiple.** Savings depend on how large the containing file is relative to the section you needed: a small file with one heading saves almost nothing, and the Kubernetes corpus saves a great deal. The benchmark files record the queries that did poorly alongside the ones that did well.

A separate, measured result from the [v1.121.0](CHANGELOG.md) projection work, on this repository's own docs at `max_results=10`: a search row went **1,989 chars → 319 with `compact=true` (−84%)**, or 431 with `snippet_bytes=200` (−78%) while removing the follow-up `get_section` call entirely.

**Retrieval quality is gated, not assumed.** Every release runs a replay fixture over a frozen golden set and fails below **nDCG 0.95**. That gate has failed builds and blocked releases; it is not decorative.

---

## Install

**Requirements:** Python 3.10+, any MCP-compatible client.

```bash
uv tool install jdocmunch-mcp
jdocmunch-mcp init
```

No virtualenv to manage, nothing written into system Python, and it works as-is on PEP 668 distros (Ubuntu 24.04+, Debian 12+) where bare `pip install` is refused. [Don't have `uv` yet?](https://docs.astral.sh/uv/getting-started/installation/)

`init` detects your MCP clients, writes their config entries, installs the doc-exploration prompt policy so your agent actually reaches for the tools, and optionally installs hooks and indexes your docs.

<details>
<summary><b>Other install paths</b></summary>

| Command | Use it when |
|---|---|
| `uvx jdocmunch-mcp` | **Zero install.** Runs from an ephemeral environment — nothing lands on disk permanently. The client entries `init` writes already invoke the server this way, so for most setups this is all that ever runs. ⚠ Hooks are the exception: they're spawned by a minimal-PATH subshell and resolve the executable by name, so they need `uv tool install` (or `pipx`/`pip`) to work. |
| `pipx install jdocmunch-mcp` | You already standardise on pipx |
| `pip install jdocmunch-mcp` | Inside a virtualenv you manage yourself |

</details>

Verify:

```bash
jdocmunch-mcp --version
```

**Manual Claude Code setup:**

```bash
claude mcp add -s user jdocmunch -- uvx jdocmunch-mcp
```

No install step — `uvx` fetches and runs the server on demand. Prefer it on your PATH (and required for hooks)? `uv tool install jdocmunch-mcp`, then `claude mcp add -s user jdocmunch jdocmunch-mcp`.

Installing the server makes the tools available; it does not break an agent's habit of brute-reading files. One line in your `CLAUDE.md` does that:

```markdown
Call the jdocmunch_guide tool and strictly follow its instructions.
```

---

## Quickstart

**Assumes:** jDocMunch installed and registered with your client, and a folder of documentation.

Index a local documentation folder:

```bash
jdocmunch-mcp index-local --path ./docs
```

It prints JSON naming the corpus and what it found:

```json
{
  "success": true,
  "repo": "local/docs",
  "file_count": 1,
  "section_count": 4,
  "doc_types": { ".md": 1 },
  "semantic_search": false
}
```

`section_count` greater than `file_count` is the whole point: the index addresses headings, not files.

Then, inside your agent:

> Using jdocmunch, search the docs for "authentication configuration" and show me that section.

The agent should call `search_sections`, then `get_section` on the top hit — returning one section rather than a file. `_meta.tokens_saved` on the response reports what that cost versus reading the containing document.

**Next step:** `get_toc_tree` for a structural view of the whole corpus, or `index_repo` to index documentation straight from a GitHub repository.

---

## What you can do

- **Retrieve one section instead of a document.** `get_section` and `get_sections` pull byte-precise content from the original file; `get_section_excerpt` narrows further.
- **Search by meaning, not just keywords.** `search_sections` fuses BM25 with semantic cosine when an embedding provider is configured. `compact=true`, `fields=[...]`, and `snippet_bytes=N` cut the response further.
- **Navigate structure.** `get_toc`, `get_toc_tree`, `get_section_path`, `get_section_descendants`, and `section_neighbors` traverse the heading tree without reading content.
- **Find what documentation is missing or rotting.** `get_doc_coverage`, `get_undocumented_symbols`, `get_stale_pages`, `get_orphan_sections`, `get_broken_links`, and `doc_health_radar`.
- **Work across API specs.** `find_endpoint`, `list_endpoints_by_tag`, `find_operations_using_schema`, and `get_schema_graph` treat OpenAPI documents as first-class.
- **Preflight documentation changes.** `check_section_delete_safe` and `get_section_blast_radius` before you remove or restructure.
- **Know when an answer is stale.** Content reads disclose `_meta.freshness`, `_meta.verdict`, and which source layer answered.

64 tools in total. The full reference is in [USER_GUIDE.md](USER_GUIDE.md).

---

## How it works

Everything runs locally. Indexes live under your home directory; no hosted service is required for indexing or retrieval.

```text
docs/ ──► parser (per format) ──► section tree ──► local index
                                                      │
                          MCP client ◄── retrieval ◄──┘
```

- **Parsing** is per format, one module each: Markdown/MDX, reStructuredText, AsciiDoc, Jupyter notebooks, HTML, plain text, OpenAPI (YAML), JSON/JSONC, XML/SVG/XHTML, Godot scenes, and — via the optional `[office]` extra — PDF, DOCX, PPTX, and EPUB.
- **Storage** is a versioned local index (`INDEX_VERSION = 3`) that auto-migrates on first load. A 1.x release never forces a reindex.
- **Retrieval** is lexical BM25 by default, hybrid when embeddings are available.
- **Embeddings are optional and provider-agnostic** — Gemini, OpenAI, an OpenAI-compatible endpoint, or a local offline model through either FastEmbed (ONNX) or sentence-transformers (torch). Without one, search stays lexical and entirely offline.

Deeper detail: [ARCHITECTURE.md](ARCHITECTURE.md) and [SPEC.md](SPEC.md).

---

## Security and privacy

Local-first by design. Your documentation is parsed and stored on your machine, and the base package's only default network behavior is an anonymous savings counter — a random ID plus aggregate token counts, no content, no paths, no PII.

Opt out completely:

```bash
JDOCMUNCH_SHARE_SAVINGS=0
```

Embedding and summarizer providers call their configured API **only when you enable them**, and never by default. `watch-install` registers a login service **only** when you run it yourself.

### Background behavior, fully disclosed

**A model download, the first time a local embedding provider runs.** Both
offline providers fetch their model from HuggingFace on first use and cache it
on disk. Nothing downloads until you enable embeddings, and a lexical-only
install never contacts the hub. Startup warmup is **skipped** when the model is
not already cached, so a first run defers the download to your first search
rather than stalling the MCP handshake behind it
([#110](https://github.com/jgravelle/jdocmunch-mcp/issues/110)).

**FastEmbed as the offline provider.** `pip install jdocmunch-mcp[fastembed]`
runs the same `all-MiniLM-L6-v2` model through onnxruntime instead of torch,
which is a much smaller install. When both offline providers are present
FastEmbed is preferred; `JDOCMUNCH_EMBEDDING_PROVIDER=sentence-transformers`
selects the other one. On the shared model the two write the **same** vector
store, so switching runtimes does not re-embed your corpus. Point FastEmbed at
a different model with `JDOCMUNCH_FASTEMBED_MODEL` and it keeps its own vectors
instead, because vectors from two models are not interchangeable
([#126](https://github.com/jgravelle/jdocmunch-mcp/issues/126)).

**A child process, when local embeddings are in use.** When the
`sentence-transformers` provider is active, jDocMunch runs the embedding model
in a **child process** (`python -m jdocmunch_mcp.embeddings.worker`) instead of
inside the server. It:

- starts when something first needs an embedding — at startup if the model is
  already in your local HuggingFace cache, otherwise on the first search or
  index that uses it. A lexical-only install never spawns it;
- opens **no network connection** and speaks only to its parent, over a private pipe;
- exits when the server exits, and is killed if it stops responding;
- is **not** a login service, is not registered anywhere, and survives nothing.

This exists because importing the embedding stack inside the server process can
deadlock in the Windows loader
([#118](https://github.com/jgravelle/jdocmunch-mcp/issues/118)), hanging every
tool call for as long as the server runs. Disable it with
`JDOCMUNCH_EMBED_WORKER=0`, which restores the previous in-process import.

**A login service, only if you install one.** `jdocmunch-mcp watch-install`
registers the doc watcher to start at login (systemd user unit, launchd agent,
or a Task Scheduler task named `jdocmunch-watch`). Nothing installs it for you.
Once installed it:

- re-indexes every locally-indexed doc repo when a doc file on disk changes;
- runs **exactly** `jdocmunch-mcp watch` with the flags you passed to
  `watch-install` — `--no-ai-summaries` to keep the summarizer out of it,
  `--quiet` to suppress its per-change log lines;
- writes to `watch.log` and `watch.err` under your doc-index directory;
- is removed by `jdocmunch-mcp watch-uninstall`.

⚠ Re-running `watch-install` **rewrites** the service definition, so a
hand-edited one is replaced. It now prints what it replaced; pass the flags to
`watch-install` itself so an upgrade keeps them
([#120](https://github.com/jgravelle/jdocmunch-mcp/issues/120)).

Path traversal prevention, symlink escape protection, secret exclusion, file-size limits, binary detection, and encoding safety are documented in [SECURITY.md](SECURITY.md), along with how to report a vulnerability.

---

## Limitations

- **Section retrieval helps least on small files.** If a document has one heading and 40 lines, retrieving the section and reading the file cost about the same.
- **Semantic search requires an embedding provider.** Without one, search is lexical only — good for identifiers and exact phrasing, weaker for paraphrased questions.
- **Office formats need the optional `[office]` extra** and are supported for local indexing only.
- **Freshness is disclosed, not guaranteed.** A section whose source cannot be checked is reported as `unknown` rather than assumed current.
- **jDocMunch does not parse code.** Symbols, signatures, and call graphs belong to [jcodemunch-mcp](https://github.com/jgravelle/jcodemunch-mcp); tabular data belongs to [jdatamunch-mcp](https://github.com/jgravelle/jdatamunch-mcp).

---

## Documentation

| Doc | What it covers |
|-----|----------------|
| [USER_GUIDE.md](USER_GUIDE.md) | Full tool reference, workflows, and best practices |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Storage model, parsing pipeline, extension points |
| [SPEC.md](SPEC.md) | Response contracts and reason-code vocabulary |
| [SECURITY.md](SECURITY.md) | Security controls and vulnerability reporting |
| [TOKEN_SAVINGS.md](TOKEN_SAVINGS.md) | How savings are counted and reported |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development setup and the CLA requirement |
| [CHANGELOG.md](CHANGELOG.md) · [ROADMAP.md](ROADMAP.md) | Release history and what's next |

---

## Licensing and commercial use

Released under the **jDocMunch-MCP Dual-Use License** ([full terms](LICENSE)). **Free for non-commercial use. Commercial use requires a paid license**, one-time, sold by jMunch LLC.

**jDocMunch only:** [Builder, $29](https://jcodemunch.com/descriptions.php#builder) (1 developer) · [Studio, $99](https://jcodemunch.com/descriptions.php#studio) (up to 5) · [Platform, $499](https://jcodemunch.com/descriptions.php#platform) (org-wide internal deployment)

**Full jMunch suite (code + docs + data):** [Trio Builder, $99](https://jcodemunch.com/descriptions.php#builder) · [Trio Studio, $449](https://jcodemunch.com/descriptions.php#studio) · [Trio Platform, $2,499](https://jcodemunch.com/descriptions.php#platform)

Individual developers and non-commercial projects need no license. Organizations deploying jDocMunch across internal teams do.

### 1.x compatibility commitment

Every 1.x license entitles you to every future 1.x release. We will never ship a 1.x version that:

- removes or renames an MCP tool (deprecated tool names keep their aliases),
- drops a `Section` field from the response shape,
- forces a reindex without auto-migrating your existing index on first load,
- changes the JSON wire format of any tool response in a way that breaks an existing consumer,
- or makes a previously-default behavior raise.

Anything that would require breaking these promises is reserved for a future major version (2.x). The full machine-checked contract is enforced via `tests/test_server.py` (tool-name and required-field invariants) and the replay-fixture gate that runs on every release.

---

## Support and project status

Actively maintained. Issues and bug reports: [GitHub Issues](https://github.com/jgravelle/jdocmunch-mcp/issues). Security reports: see [SECURITY.md](SECURITY.md). Commercial licensing questions go through [jcodemunch.com](https://jcodemunch.com/).

Part of the jMunch suite alongside [jcodemunch-mcp](https://github.com/jgravelle/jcodemunch-mcp) (code symbols) and [jdatamunch-mcp](https://github.com/jgravelle/jdatamunch-mcp) (tabular data). All three implement [jMRI](https://github.com/jgravelle/mcp-retrieval-spec), the open retrieval interface spec.
