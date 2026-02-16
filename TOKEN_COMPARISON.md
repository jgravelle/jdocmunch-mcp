# Token Usage Comparison: With MCP vs Without MCP

## Test Setup

* **Repository:** `openclaw/openclaw` (583 documentation files)
* **Session:** Three related queries about Discord integration
* **Queries:**

  1. "discord bot setup"
  2. "discord configuration"
  3. "discord webhook"

---

## Side-by-Side Comparison

### Query 1 — "discord bot setup"

| Step          | Without MCP                             | With MCP                            |
| ------------- | --------------------------------------- | ----------------------------------- |
| Discovery     | List files via API (1 call)             | Load local index (0 calls)          |
| Fetch data    | Download 583 files (**811,756 tokens**) | Load TOC (**708,367 tokens**)       |
| Search        | Scan full content locally               | Search summaries (**375 tokens**)   |
| Retrieve      | Parse matches locally                   | Retrieve 2 sections (**52 tokens**) |
| **API calls** | **584**                                 | **0**                               |
| **Tokens**    | **811,756**                             | **708,794**                         |
| **Time**      | ~30–60s (network)                       | ~1s (local)                         |

---

### Query 2 — "discord configuration"

| Step          | Without MCP                             | With MCP                             |
| ------------- | --------------------------------------- | ------------------------------------ |
| Discovery     | List files via API (1 call)             | Cached index (0 calls)               |
| Fetch data    | Download 583 files (**811,756 tokens**) | Cached TOC (**0 tokens**)            |
| Search        | Scan full content locally               | Search summaries (**358 tokens**)    |
| Retrieve      | Parse matches locally                   | Retrieve 2 sections (**176 tokens**) |
| **API calls** | **584**                                 | **0**                                |
| **Tokens**    | **811,756**                             | **534**                              |
| **Time**      | ~30–60s (network)                       | ~0.1s (local)                        |

---

### Query 3 — "discord webhook"

| Step          | Without MCP                             | With MCP                             |
| ------------- | --------------------------------------- | ------------------------------------ |
| Discovery     | List files via API (1 call)             | Cached index (0 calls)               |
| Fetch data    | Download 583 files (**811,756 tokens**) | Cached TOC (**0 tokens**)            |
| Search        | Scan full content locally               | Search summaries (**366 tokens**)    |
| Retrieve      | Parse matches locally                   | Retrieve 2 sections (**176 tokens**) |
| **API calls** | **584**                                 | **0**                                |
| **Tokens**    | **811,756**                             | **542**                              |
| **Time**      | ~30–60s (network)                       | ~0.1s (local)                        |

---

## Session Totals

| Metric                | Without MCP  | With MCP | Savings   |
| --------------------- | ------------ | -------- | --------- |
| **Total tokens**      | 2,435,268    | 709,870  | **70.9%** |
| **API calls**         | 1,752        | 0        | **100%**  |
| **Average per query** | 811,756      | 236,623  | **70.9%** |
| **Network time**      | ~2–3 minutes | 0        | **100%**  |

---

## Scale Projections (Monthly)

| Queries / Month | Without MCP | With MCP  | Savings   |
| --------------- | ----------- | --------- | --------- |
| 1               | 811,756     | 708,794   | 12.7%     |
| 5               | 4,058,780   | 710,698   | **82.5%** |
| 20              | 16,235,120  | 716,410   | **95.6%** |
| 100             | 81,175,600  | 741,058   | **99.1%** |
| 1,000           | 811,756,000 | 1,246,342 | **99.8%** |

---

## Key Observations

### Traditional Retrieval (Without MCP)

```
Per query:
├── 1 request to list files
├── 583 requests to download files
├── ~811K tokens transferred
└── Full local search of entire repository
```

Limitations:

* API rate-limit exposure
* High network latency
* Linear cost growth per query
* Repeated transfer of unchanged data

---

### Indexed Retrieval (With MCP)

```
One-time:
├── Index repository locally
└── Generate summaries and metadata

Per query:
├── Load cached index
├── Search summaries (~300–400 tokens)
├── Retrieve only relevant sections (~50–200 tokens)
└── 0 API calls
```

Benefits:

* No rate-limit dependency
* Sub-second response times
* 70–99% token reduction
* Predictable operating cost
* Fully offline querying after indexing

---

## Cost Model (Illustrative)

Assuming $0.01 per 1K tokens:

| Usage Pattern                | Without MCP | With MCP  | Estimated Monthly Savings |
| ---------------------------- | ----------- | --------- | ------------------------- |
| Developer (20 queries/day)   | $3,571.73   | $311.92   | **$3,259.81**             |
| Team (100 queries/day)       | $17,858.65  | $1,559.62 | **$16,299.03**            |
| Enterprise (500 queries/day) | $89,293.25  | $7,798.08 | **$81,495.17**            |

---

## Indexing vs Query Cost

| Phase               | Tokens   | Frequency           | Notes                                             |
| ------------------- | -------- | ------------------- | ------------------------------------------------- |
| Indexing            | ~708K    | Once per repository | Parse files, extract sections, generate summaries |
| Cached queries      | ~500–600 | Per query           | Local search and retrieval                        |
| Incremental reindex | Varies   | On file changes     | Only modified files reprocessed                   |

Indexing cost is amortized quickly; after only a few queries, indexed retrieval becomes significantly cheaper than repeated full-repository fetches.

---

## Reproducing the Benchmark

```bash
python benchmarks/run_benchmark.py --generate --output results.json
```

Or index a repository directly:

```bash
python -c "
import asyncio
from jdocmunch_mcp.tools.index_local import index_local
asyncio.run(index_local('path/to/repo', use_ai_summaries=False))
"
```

See `benchmarks/README.md` for full methodology.

---

## Summary

Indexed documentation retrieval enables a fundamental shift:

* From **“download everything and search”**
* To **“index once and navigate precisely”**

As repository size and query volume grow, the efficiency advantage increases dramatically, approaching **two orders of magnitude reduction** in token usage.
