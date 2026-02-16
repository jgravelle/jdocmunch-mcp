# Token Savings: jDocMunch MCP

## Why This Exists

Documentation repositories often contain hundreds of Markdown files.
Traditional agent workflows repeatedly download entire documentation sets, consuming tokens every query.

jDocMunch indexes documentation into **section-level objects**, allowing retrieval of only the relevant sections.

---

## Example Scenario

**Documentation set:** 500+ Markdown files
**Task:** Find webhook configuration instructions

| Approach             | Tokens Consumed | Process                             |
| -------------------- | --------------- | ----------------------------------- |
| Raw document loading | ~800,000 tokens | Download and search all files       |
| jDocMunch MCP        | ~534 tokens     | Search summaries → retrieve section |

**Savings:** ~99.9%

---

## Typical Savings by Task

| Task                    | Raw Approach     | With jDocMunch | Savings |
| ----------------------- | ---------------- | -------------- | ------- |
| Search documentation    | ~800,000 tokens  | ~500 tokens    | ~99.9%  |
| Read installation guide | ~50,000 tokens   | ~1k tokens     | ~98%    |
| Repeated queries        | Linear growth    | Near-constant  | ~99%    |

---

## Scaling Impact

| Queries | Raw Tokens        | Indexed Tokens | Savings |
| ------- | ----------------- | -------------- | ------- |
| 10      | 8,000,000         | ~5k            | 99.9%   |
| 100     | 80,000,000        | ~50k           | 99.9%   |
| 1,000   | 800,000,000       | ~500k          | 99.9%   |

---

## Key Insight

jDocMunch converts documentation from **files** into **navigable knowledge sections**, dramatically reducing context usage.
