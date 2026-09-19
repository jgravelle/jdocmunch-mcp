# jDocMunch Roadmap

Accepted design work that is **sequenced but not started**.

## Why this file exists

An issue is a problem to fix or a feature to build. Something we have agreed to
build *eventually*, with no start date and an unmet dependency, is neither. It is
a plan. Leaving plans open as issues makes the tracker a to-do list, and a
tracker that mixes "someone is blocked on this" with "we like this idea" tells
you nothing at a glance about either.

So: **an issue opens when work starts or when a user is blocked. Accepted but
unscheduled design lives here.**

Nothing on this page is rejected. Everything here has been reviewed, agreed to,
and given a close condition. When work begins, the entry gets an issue and this
page links to it.

---

## Bounded wait on the internal record lock

**Design and analysis by [@rknighton](https://github.com/rknighton)**, on
[#95](https://github.com/jgravelle/jdocmunch-mcp/issues/95) and
[#97](https://github.com/jgravelle/jdocmunch-mcp/pull/97).

`begin_retirement` acquires `hold_record_lock(blocking=True)`, which it did not
do before the QA-19 work. Both platforms block indefinitely: Windows through the
`LK_LOCK` retry loop in `_acquire_fd`, and POSIX through
`flock(fd, LOCK_EX)` called without `LOCK_NB`.

⚠ **This is NOT Windows-only.** The maintainer's review framed it that way and
was corrected. Any bound has to cover both platforms.

**Why it is not urgent.** The exposure is availability, not authority: a blocked
caller cannot authorize a deletion while it waits. A holder that exits releases
the lock, because both primitives are OS-managed and drop on fd close or process
death, which `test_spawn_interrupt_after_primary_is_not_falsely_pending` already
exercises. The QA-14 two-lock cycle is not reintroduced, since the record lock is
taken first and the retained gate is nonblocking. Legitimate holds are short:
3.02 ms measured at 2 MB, extrapolating to roughly 150 ms at 100 MB. The real
case is a holder that stays alive but hangs, such as an unresponsive mount or a
suspended process, which is rare and needs a second stuck process to matter.

**Close condition.** A deadline with nonblocking retries on both platforms,
returning `None` on timeout through the existing fail-closed publication path.

**Sequencing.** Separate hardening. Explicitly NOT a correction required for #95.

---

## Quarantine or repair for unreadable retirement records

**Design and analysis by [@rknighton](https://github.com/rknighton)**, same
threads.

`void_retirements_referencing` no longer unlinks unconditionally. When
`_read_record` returns `None` it no-ops, so a corrupt record naming a handle as
its retained peer is inert but persists.

**This is deliberate and stays that way.** If the record's identity cannot be
established there is no verified publication to act on, and unlinking anyway
would mutate retirement state without proving it is the intended publication,
which is the class of authority problem QA-19 exists to prevent. The record is
disclosed rather than hidden: `pending_retirement` returns
`{"record_state": "unreadable"}`. It is also not necessarily permanent, since a
later valid `begin_retirement` for the same retiring slot replaces the path under
the record lock.

**Close condition.** An explicit quarantine or repair path, if one is wanted. The
refusal to delete unverifiable state is not up for revision.

**Sequencing.** Separate hardening, like the entry above.

---

## A rerank stage, and Jev as a provider for it

**Studied 2026-09-19. Not shipping. Gated on a published bar, not on a date.**
Harness, criteria and both decision memos:
[jdoc-rerank-bench](https://github.com/jgravelle/jdoc-rerank-bench).

The idea: take the top 15 to 20 rows `search_sections` already returns and
re-order them with a model that reads the query and each passage together. Two
candidates were planned. Arm C is a local ONNX int8 cross-encoder. Arm B is
**Jev**, a third-party hosted service, called through the same provider
interface.

**What was measured.** Arm C only, on two frozen test splits with the pass marks
committed before any score. The gain was real, +0.097 and +0.058 nDCG@5, and
latency passed at 195 / 288 ms p50 / p95 on CPU. It also made 14.9% and 13.7% of
queries worse against a limit of 12%. That limit failed twice and was not
overridden, so there is no `[rerank]` extra and no `rerank` parameter.

**Jev was never run, and nothing here is a verdict on it.** The criteria judge
arm B against a local reranker that has already passed, and none has. There is
no Jev code in this package, no Jev setting, and no network call to it.

**Close condition**, condensed from `DECISION_CRITERIA.md` in that repo, which
is the authority where the two differ:

- Section 6, local reranker, all of: pooled nDCG@5 gain with its lower bound
  above zero and a point estimate of at least +0.04; every corpus above zero and
  none with an interval entirely below zero; at most 12% of queries made worse;
  no query class with an interval entirely below zero; p50 at most 300 ms and
  p95 at most 500 ms.
- Section 7, Jev, only after section 6 passes: B minus C with its lower bound
  above zero and a point estimate of at least +0.04, **or** B within ±0.02 of C
  with live p95 at or below C's. In both cases live p95 at most 1,000 ms, and
  the vendor's terms permit publishing the comparison. If neither holds, Jev is
  not integrated and that result is published.

**Constraints on any future build.**

- The parameter is named `rerank`. `semantic` already means embedding fusion in
  `search_sections` and keeps that meaning on 1.x.
- Confidence, the verdict and the ranking ledger keep reading the RETRIEVAL
  order, not the re-ordered one.
- int8 cross-encoder scores depend on what else is in the batch. One passage per
  inference call.
- A hosted provider sends the query and candidate passage text off the machine.
  It would be opt-in, off by default, and disclosed in the README under
  "Background behavior, fully disclosed" before it ships.

**Sequencing.** No third test split with the studied approach. A new local
reranker needs a new pre-registered split. What shipped instead is the larger
effect the same study measured: `init` now offers offline embeddings (1.143.0).

---

## Reserved for 2.x (license-blocked)

Each item here would unavoidably break a 1.x licensee, so none of it ships until
a major-version license revision is planned. Deferred indefinitely; re-evaluate
only when sales explicitly approves a 2.x cut.

| Item | Why it is 2.x-only |
|---|---|
| Drop the bytes/4 token estimate (require tiktoken) | Removes a fallback an existing user might rely on |
| Rename the `list_repos` MCP name (drop the `index_repo` / `list_repos` aliases) | Tool removal breaks agents pinned to the name |
| Forced reindex on a schema bump | We auto-migrate on 1.x; "force" would break offline upgrades |
| MCP wire-format change (e.g. envelope rename) | Breaks every existing consumer at once |

This is the canonical list referenced by the 1.x compatibility contract in
`CLAUDE.md`. It moved here from `todo.md` on 2026-08-03 when that file was
retired; the table is unchanged.

**Close condition.** None. These stay parked until a 2.x is approved.

---

## Conventions

- Entries here are **accepted**, not speculative. A rejected proposal gets a
  closed issue with reasoning, not a roadmap line.
- Each entry keeps its **close condition** verbatim from the design that was
  accepted, so scope cannot drift quietly between filing and building.
- When an entry starts, it gets an issue, and its line here gains the link.
- Credit stays attached to the entry. Sequencing is not authorship.
