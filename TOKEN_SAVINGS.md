# Token Savings: jDocMunch MCP

## Why this exists

An agent that must read whole documentation files to find one section pays for
every paragraph it did not need. jDocMunch indexes documentation once by heading
hierarchy and retrieves **exact sections on demand**, so the containing file
never enters the context window.

This document records what that is worth, using measured results only.

> **Every figure below comes from a benchmark run recorded in
> [`benchmarks/`](benchmarks/), against a named public corpus on a stated date.**
> Earlier revisions of this file carried an "Example Scenario" and a
> "Typical Savings by Task" table built from round numbers that were never
> measured, plus a "Scaling Impact" table that multiplied one of those invented
> figures out to 12 million tokens. Those are removed. If a number is not
> traceable to a benchmark file, it does not belong here.

---

## Measured: Kubernetes documentation

**Corpus:** `docs/` from `kubernetes/website`, complete English documentation
· **Date:** 2026-03-04
· [Full run](benchmarks/jDocMunch_Benchmark_Kubernetes.md)

| Property | Value |
|---|---|
| Files indexed | 1,569 `.md` |
| Sections extracted | 4,355 |
| Corpus size | 16 MB |
| Index time | 3,352 ms (once) |

Per-query results from that run:

| Query | Latency | Tokens saved |
|---|---:|---:|
| Node affinity scheduling | 100 ms | 27,285 |
| (second query) | 83 ms | 5,987 |
| (third query) | 85 ms | 31,757 |
| (fourth query) | 83 ms | 7,346 |
| (fifth query) | 86 ms | 17,561 |
| Batch call | 754 ms | 34,222 |

⚠ **Note the spread: 5,987 to 31,757 tokens on the same corpus.** Savings depend
on how large the containing file is relative to the section you needed. No single
number describes every query, and any figure quoted from this page should carry
the range.

---

## Measured: a small wiki corpus

**Corpus:** 8 `.md` files, 68 sections, 29,280 bytes
· [Full run](benchmarks/jDocMunch_Benchmark_Wiki.md)

| Approach | Tokens |
|---|---:|
| Read the corpus to answer one question | 7,449 |
| `search_sections(max_results=3)` | ~190 + the one section retrieved |

This corpus is included deliberately because it is **small**. A 7,449-token
corpus is one that an agent could simply read, and the absolute saving is
correspondingly modest. The mechanism scales with corpus size; the Kubernetes
run is where it pays.

---

## Measured: SciPy and LangChain

| Corpus | Sections | Index time | Result |
|---|---:|---:|---|
| [SciPy](benchmarks/jDocMunch_Benchmark_SciPy.md) | 10,402 | 2,247 ms | 135–152 ms per query across sparse-solver, FFT, and optimization lookups; ~855,000 corpus tokens |
| [LangChain MDX](benchmarks/jDocMunch_Benchmark_LangChain_MDX.md) | 5,973 | 5,204 ms | MDX-aware sectioning found 754% more sections than the naive pass |

---

## Measured: response projection (v1.121.0)

On this repository's own documentation at `max_results=10`:

| Mode | Chars per row | Change |
|---|---:|---|
| Default | 1,989 | — |
| `compact=true` | 319 | **−84%** |
| `compact=true, snippet_bytes=200` | 431 | −78%, and the follow-up `get_section` call is no longer needed |

---

## What the numbers do not say

- **They are not a single headline multiple.** The per-query range on one corpus
  spans more than 5x.
- **They do not describe small documents.** A 40-line file with one heading costs
  about the same either way.
- **They measure retrieval volume, not answer quality.** Retrieval quality is
  gated separately: every release runs a replay fixture over a frozen golden set
  and fails below **nDCG 0.95**. That gate has blocked releases.
- **Index time is paid once**, and is listed above so it can be weighed against
  the per-query saving rather than omitted.

---

## Live savings counter

Every retrieval and search response carries real-time accounting in `_meta`:

```json
"_meta": {
  "tokens_saved": 1840,
  "total_tokens_saved": 94320,
  "cost_avoided": {
    "claude_opus": 0.0092,
    "claude_sonnet": 0.0037,
    "claude_haiku": 0.0018,
    "gpt5_latest": 0.0184
  },
  "total_cost_avoided": {
    "claude_opus": 0.4716,
    "claude_sonnet": 0.1886,
    "claude_haiku": 0.0943,
    "gpt5_latest": 0.9432
  }
}
```

- **`tokens_saved`** — this call: raw doc bytes of matched documents versus the
  served response bytes, ÷ 4.
- **`total_tokens_saved`** — cumulative, persisted to `~/.doc-index/_savings.json`.
- **`cost_avoided` / `total_cost_avoided`** — the same token count valued at four
  input rates, per million input tokens:

  | Key | Model | Rate |
  |---|---|---:|
  | `claude_opus` | Claude Opus 5 | $5.00 |
  | `claude_sonnet` | Claude Sonnet 5 | $2.00 |
  | `claude_haiku` | Claude Haiku 4.5 | $1.00 |
  | `gpt5_latest` | GPT-5.2 | $10.00 |

⚠ **`claude_sonnet` read $3.00 until 2026-09-01.** That was Sonnet 5's
scheduled-but-cancelled increase, not a price it ever carried; $3.00 is the
superseded Sonnet 4.6's rate. A key naming a family inherits whichever member's
price someone last looked at, so each row above now names one model.

⚠ **A previous revision of this file documented `claude_opus` at $15/1M.** That
was the retired Opus 4.0/4.1 rate; the shipped code has used $5.00/1M since those
models were superseded, so the published figure overstated avoided cost threefold.
It also listed only two of the four rates the server actually reports. Rates are
input-token rates and are a valuation input, not a claim about your bill.

Telemetry network failures are silent and never affect tool performance. The
anonymous community counter is opt-out via `JDOCMUNCH_SHARE_SAVINGS=0`; see
[SECURITY.md](SECURITY.md).
