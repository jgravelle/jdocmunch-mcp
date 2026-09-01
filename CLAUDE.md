# jdocmunch-mcp

**Version:** 1.139.1 |
**Tests:** `PYTHONPATH=src python -m pytest tests/ -q`

⚠ **`python -m pytest`, not bare `pytest`**, matching the suite rule in
`C:\MCPs\CLAUDE.md`. The `-m` form guarantees the interpreter that receives
`PYTHONPATH=src` is the one running the tests; a bare `pytest` shim can resolve
into a different environment, and without `PYTHONPATH` the INSTALLED package
shadows `src/`. ⚠⚠ **Neither form reproduces CI** — see "reproduce CI" under
Standing operational notes; this one is the edit loop, not the gate.

## v1.139.1 — a rate written for a date that never arrived

**`token_tracker.PRICING["claude_sonnet"]` was $3.00/1M input tokens.** Claude
Sonnet 5 is **$2.00/1M** and always has been: it launched at $2.00 with a rise
to $3.00 scheduled for **2026-09-01**, and Anthropic cancelled that increase the
day before it would have applied. $3.00 is the superseded Sonnet 4.6's rate —
exactly what the line's comment ("Claude Sonnet 5 / 4.6") conflated.

⚠⚠ **A constant written for a FUTURE date is wrong for the whole interval
before it, and reads identically to a stale one.** The header said "As of
2026-06-24", which made the value look *checked*. It was wrong on that date too.
**A date on a table is evidence of when someone looked, never of what they saw.**

⚠⚠ **A key that names a FAMILY inherits whichever member's price someone last
looked at.** Three of the four keys are family names; each comment now names the
ONE model its rate belongs to. ⚠ **The KEYS are unchanged** — `claude_sonnet` is
emitted verbatim in the `cost_avoided` block of every retrieval response, so a
rename is a wire change on 1.x. The model identity goes in the comment.

⚠⚠ **Four copies of this rate exist across the suite and they AGREED WITH EACH
OTHER while being wrong together**, which is why nothing caught it. Verified
against the source page's *Base Input Tokens* column, not another copy of the
table. ⚠ `TOKEN_SAVINGS.md` was the fourth copy here and carried two figures
DERIVED from the rate (`0.0055` / `0.2830` in the worked `_meta` example) —
derived literals move when a rate moves and are invisible to a search for the
rate's name.

⚠ **`gpt5_latest` is UNTOUCHED and the CHANGELOG says so.** Not an Anthropic
model, no source consulted; pinned at the value it shipped with so a drift is
visible, not because $10.00 was verified. **Pinning a number is not the same as
vouching for it, and the pin must say which it is.**

`tests/test_pricing_rates.py` (4). The only prior reference to `PRICING` was a
key-PRESENCE check (`tests/test_storage.py:259`), so **no test pinned any value**
and a wrong rate could sit here indefinitely. ⚠ The prices are **restated** from
the source page, not imported from the module — a pin that reads the value it
checks asserts nothing. Proven non-vacuous: with $3.00 put back, 3 of 4 fail.

Suite **2722 / 11** under the CI-equivalent sync; `ruff check src/` clean. No
tool, schema or INDEX_VERSION change; `cost_avoided` VALUES change, keys do not.

## v1.139.0 — a token count with no time basis, and a tier that is not a lever

**`schema_tokens_avoided` was published bare.** `get_session_stats` →
`tool_surface` reported it beside `schema_tokens_visible` /
`schema_tokens_catalog` with no interval attached, and a reader supplies the
missing one: **per request.** ⚠⚠ **The schema block is STABLE**, so it is paid
at full rate roughly once per cache lifetime and at cache-read rates (~0.1x)
after — jcm measured **86% of baseline input cached**
(`benchmarks/codex_surface/`) and says in its own words that "N tokens in every
request" is wrong *and that the repo said exactly that before measuring*. The
field overstated the cost impact by about an order of magnitude, **in the
direction that flatters us.** New `schema_tokens_basis` +
`schema_tokens_basis_note`, from `src/jdocmunch_mcp/schema_basis.py`.

⚠ **The count is NOT discounted.** It answers a real question — payload size —
and a silently scaled one answers neither that nor the cost question. The fix
for an unstated basis is a LABEL. (`analyze_perf`'s raw `hit_rate` beside
`hit_rate_basis` is the same rule.)

⚠⚠ **jcm shipped TWO releases that day and only one was ours.** 1.108.312 is
this. **1.108.311 — refusing a mid-session tier switch that cannot repay the
cache it invalidates — CANNOT occur here**: `JDOCMUNCH_TOOL_PROFILE` is read at
STARTUP, there is no runtime switch, so there is no invalidation to price.
Porting the gate would be machinery for a mechanism we do not have. A ratchet
in `tests/test_schema_tokens_basis.py` fails the day
`notifications/tools/list_changed` appears in `src/` with no pricing helper, and
names the module to port from. ⚠ It is the ONE new test that passes against the
unfixed tree, so it is the one that needed proving non-vacuous — proven by
adding the forbidden call and watching it fire.

⚠ **jdatamunch CAUGHT UP 2026-08-31, both halves** — v1.31.13 stamps the basis
(`schema_token_basis.py`, singular `token`, not this repo's `schema_basis.py`)
and measures its tiers in `benchmarks/tier_surface.json`: `core` 65.8% avoided,
`standard` **5.6%** over three tools. Same verdict as here, a scope bundle
rather than a token lever. Re-read that repo before quoting this line.

**The tiers are MEASURED for the first time** — `benchmarks/tool_surface/`, a
regenerable harness plus JSON artifact. At 64 tools / 13,252 schema tokens:
`core` **−62.08%** (49 tools dropped), `standard` **−9.39%** (8 tools).
⚠⚠ **`standard` is a SCOPE choice, not a token lever**, and the config surface
implied otherwise. It stays — deleting a shipped profile breaks a 1.x config —
and the config comment now says what it does. **A setting that implies a saving
it does not deliver is the same defect class as an unstated basis.** jcm's
`standard` measured 9 of 91 tools and 6.7%; same shape in both servers.

⚠⚠ **Weigh what the client RECEIVES, never the catalog filtered by the tier
bundle.** jcm's first attempt did the latter and was wrong by three tools in
every tier — it kept a hidden set and dropped force-included ones, pricing a
surface no client is sent. Here `_ALWAYS_PRESENT_TOOLS` and
`JDOCMUNCH_DISABLED_TOOLS` both change the answer. New `_build_tools_list()` is
the ONE producer of the published surface (`list_tools`, the meter and the
benchmark all route through it) and `_schema_weight` the ONE estimator; the
closure inside `_tool_surface_stats` is gone. ⚠ `_filter_tools` takes
`profile_override` so a tier is priced **without switching to it** — answering a
question about a surface must not mutate the session's.

`tests/test_schema_tokens_basis.py` (9; **8 seen failing against the unfixed
tree**). No tool, schema or INDEX_VERSION change; additive response keys only.

## v1.138.0 — #129 + #130: a fusion over one channel counted twice, and a 1.25 MB document on the floor

Four findings reported from OUTSIDE this repo, while doc-indexing
`jcodemunch-mcp` from a jcodemunch session. ⚠ Tracker was clean at the start
(0 issues, 0 PRs) — re-verified, never transcribed.

**#129 — `find_similar_sections` scored summaries and called them bodies.**
The description advertised "title + body lexical Jaccard" and there was **no
body channel**: `body_text = sec.get("summary")` was unconditional, and under
`use_ai_summaries=False` a summary IS the heading text. So
`body_tokens == title_tokens` and `0.70 * body + 0.30 * title` weighted one
input against itself.

⚠⚠ **The finding is not a wrong score, it is a TWO-CHANNEL VERDICT REPORTED
OVER ONE CHANNEL** — complete with a `dominant_signal` naming which of the two
won. 8 of 8 clusters on a 955-section corpus were the artifact; the "identical"
pair was 1,105 bytes of ASCII diagram against a `> **Version note:**` paragraph.

⚠ **The tool's own diff output was the tell it could not read.** Every variant
returned `body_unique_a: []` AND `body_unique_b: []`. Two sections with
different byte ranges cannot both be that, so "no unique content on either
side" and "I did not read either side" were indistinguishable. `differs_by`
now carries `body_signal`.

⚠ **The cost was MEASURED, because the comment being deleted named a real
tradeoff.** Reading every examined body: **0.24 s** at the 1000-section cap,
2.5 s for all 9,507. The obvious optimisation — read only pairs surviving the
title pre-filter — saves **~6%**, because **899 of 955 sections survive into
some pair** on a doc set with repetitive headings. Rejected on the measurement,
not on taste.

⚠⚠ **AN "ALL PAIRS WERE title_only" CAP DOES NOT CLOSE THIS, AND MY FIRST ONE
WAS THAT CAP.** Union-find merges transitively: two empty `## Architecture`
stubs (title_only, 1.0) join a cluster holding two real unrelated Architecture
sections, so the cluster contains a body pair, passes an all-pairs test, and
takes its 1.0 from the pair that read nothing. The verdict now rests on
**`evidence_max_score`** — the best pair that actually compared bodies —
while reported `max_score` stays the true max, so the two numbers together
show what happened (`max_score: 1.0`, `evidence_max_score: 0.3406`,
`overlapping_topic`). **Found by re-running the fix on the real corpus, not by
the tests I had just written.**

⚠ The `title_only` refusal is scoped to the LEXICAL-ONLY path on purpose:
cosine is a channel that did not come from the title, so refusing there would
suppress genuine duplicates the embedding channel found. Pinned by a test.

⚠ **Sibling CHECKED and CLEAN**: `search_sections(dedupe=true)` reads
`retrieval/dedup.py`'s cluster sidecar, which uses real `content`. The defect
did not reach ranked search.

⚠⚠ **The fixtures were reading site-packages, and so were the PRE-EXISTING
ones.** `test_find_similar_sections.py` left `use_embeddings` at `"auto"`,
which enables embeddings whenever an offline provider happens to be installed —
so this box ran a different program from CI, which installs neither. Measured:
under `"auto"` the two empty stubs came back at **cosine 1.0** and the guard
under test never fired. Both files now PIN `use_embeddings=False`.
**[[feedback_an_assumption_about_the_machine_is_not_a_fixture]], one release
after v1.137.1 made the same finding about provider probes.**

**#130 — the best retrieval target in the corpus was silently dropped.**
jcm's `CHANGELOG.md` is 1,252,519 bytes and was **not in the index at all**;
`index_local` returned `file_count: 124`, `success: true`, `truncated: false`.
The skip WAS counted and `coverage.skip_counts` WAS persisted — the response
carried none of it.

⚠⚠ **A count computed and withheld at the one moment the caller could act on
it is the same defect as not computing it.** `truncated` refers only to the
`max_files` cap, so it answered a different question truthfully while the
caller read it as "did I get everything".

⚠ **`truncated` KEEPS its meaning** — changing what a shipped key means is
forbidden on 1.x. New `coverage_complete` is the field it was being misread as,
plus `skip_counts` / `skipped_paths` / `skipped_paths_truncated`.

⚠ **`coverage_complete` is keyed on ACTIONABLE skips only.** `gitignored` and
`unsupported_extension` fire on every real repo (16 and 900 here); keying on
all of them makes it `false` always, and a signal that always fires hides the
case it exists for.

⚠⚠ **The disclosure is on ALL FOUR response paths, and the fourth is
`"No documentation files found"`.** A corpus whose every candidate was dropped
for size returned that error verbatim — reads as "there is nothing here" when
the truth is "there is something here and I refused it", and it is the one
payload with no `file_count` to be suspicious of. Found because a test I wrote
for something else hit it. A test asserts the count of attach sites.

**`DEFAULT_MAX_FILE_SIZE` 500 KB → 5 MB, overridable via
`JDOCMUNCH_MAX_FILE_SIZE`.** ⚠ Measured, not guessed: the real 1.25 MB file
parses in **1.03 s**, **8.3 MB** peak, 1,515 sections at a 565-byte median, so
the parser was never the constraint. ⚠⚠ **The ASYMMETRY is what made 500 KB
indefensible, not the absolute value** — the same walk already granted
`OFFICE_MAX_FILE_SIZE = 25 MB` to `.pdf`/`.docx`, so it accepted a 25 MB
PowerPoint and refused a 600 KB Markdown file. ⚠ The resolver fails OPEN on
garbage/`0`/negatives (a typo must not shrink a corpus) and resolves at CALL
time — a default argument binds the constant at import, so an env var set
afterwards is read and ignored.

Measured end to end: **9,624 → 11,138 sections**, 4.7 s. ⚠ The disclosure
surfaced a SECOND unfiled skip on its first run — `office_extra_not_installed:
1`, `jcodemunch_whitepaper.pdf` — persisted and unreported the whole time.

**sdist allowlist guard PORTED** (`tests/test_sdist_exclusions.py`, 10). This
repo had NEITHER the canaries nor the allowlist; `pyproject.toml` excluded only
`.claude/`. ⚠ The canary half proves NAMED bad paths are absent and a scratch
file has no name to plant a canary under — jcm 1.108.305 shipped `relnotes.md`
that way. ⚠ The reverse assertion (the allowlist names nothing that stopped
shipping) is what catches a wholesale copy of jcm's list, which carries
`uv.lock`/`Dockerfile` and two dozen root docs this repo does not have. ⚠ Also
a per-member size budget — `tests/infographic.png` was 87% of this sdist until
1.123.2 and no guard could see it. ⚠⚠ **jdatamunch was CHECKED and is missing
the same guard**; ported there separately.

⚠⚠ **JSON indexing INVESTIGATED and DELIBERATELY NOT CHANGED — the obvious
remedy is wrong and the measurement says so.** The corpus was 88.2% `.json`
sections, 80.8% from `benchmarks/`. General JSON indexing IS intended
(`parser/json_parser.py` exists for it; OpenAPI is a separate sniffed path).
**A `benchmarks/` skip drops 20 GENUINE documentation files** —
`METHODOLOGY.md`, `REPRODUCING.md`, `whitepaper.md`, four `README.md`s — to
remove 37 data files: **the directory is the wrong axis, the split is by file
KIND.** ⚠ `SKIP_PATTERNS` is matched as a path SUBSTRING, so the entry would
also take `docs/benchmarks/`. ⚠ Skip-name authority checked for the
fourth-undeclared-copy problem and is CLEAN: `tools/_constants.py`, imported by
`index_local` and `index_repo`, defined nowhere else. `extra_ignore_patterns`
stays the mechanism.

Tests `tests/test_jdoc_129_body_channel.py` (20; **11 fail / 9 pass** against
the full pre-fix behaviour) and `tests/test_jdoc_130_oversize_disclosure.py`
(21). Suite **2700 / 6**; `ruff check src/` clean. No tool, schema or
INDEX_VERSION change.

## Lessons from rotated entries (v1.116.0–v1.137.0, lifted 2026-08-29 / 2026-08-30)

⚠⚠ **These outlived the releases that produced them.** Each line names the
version whose full narrative now lives in `docs/CLAUDE-history.md`. **Read the
line here; go to the archive only when you need the evidence behind it.** An
entry that earned no reusable rule got no line.

**Releasing**

- ⚠⚠ **The build reads the WORKING TREE, not HEAD.** v1.134.0 was built in the
  main checkout while a concurrent session held uncommitted edits there, and
  they went into the published wheel — caught only by a post-publish credential
  sweep. **Build from a dedicated `git worktree`.** Committing by name protects
  HISTORY, not the ARTIFACT. ⚠ `git worktree add` needs `-b` here: `master` is
  already checked out in the main tree. (v1.134.1)
- **PyPI cannot be re-uploaded, so the remedy for a bad artifact is FORWARD.**
  Rebuilding an already-published version from its tag would revoke whatever
  users already received. (v1.134.1)
- ⚠ **A green suite is not a green build.** CI lints; the local pytest run does
  not. Four consecutive jcm releases shipped on a red lint nobody read.
  (v1.124.3)
- ⚠⚠ **ruff's `select` is EXPLICIT (`E4,E7,E9,F`) on purpose.** Ruff's DEFAULT
  rule set is not stable across versions and this repo has no lock pinning ruff
  — measured, an `ignore`-only config gave **446 findings** where a handful were
  intended. Widening the set is a DECISION, not a ruff upgrade. (v1.124.3)

**Writing a fix**

- ⚠⚠ **An equivalence you ASSERT to ship an allow-list is owed a MEASUREMENT,
  and the measurement is a SEPARATE release.** 1.137.0 shipped the allow-list on
  an asserted equivalence; 1.137.1 measured it (max drift 6.0e-13 over 16 canary
  strings). Editing 1.137.0's entry instead would have rewritten what is already
  on PyPI — that artifact's CHANGELOG is what it shipped with. (v1.137.1)
- ⚠⚠ **A cross-implementation measurement needs a CONTROL saying which
  implementation ran.** After the pass, `sys.modules` held no torch /
  sentence_transformers / transformers and did hold onnxruntime. Without that
  check a silent fallback to the other provider scores perfectly and means
  nothing. ⚠ One box, one version pair — which is exactly why the allow-list is
  a LIST and not a rule. (v1.137.1)
- ⚠⚠ **A rename that makes two derivations COMPARE EQUAL is worse than the
  re-embed it avoids.** `cache.load` matches `(provider, model, dim)` by exact
  equality and both offline providers report `dim=None`, which the cache reads as
  a WILDCARD — so there is no dim backstop under a normalized provider name, and
  a blanket rename merges vectors from two models into one sidecar that search
  ranks across. A full re-embed is expensive and OBSERVABLE; this is cheap and
  invisible. Allow-list per MODEL, fail closed, and **a model earns its place by
  being MEASURED.** (v1.137.0)
- ⚠ **A closed if-chain and a factory map are two lists that must agree, and
  nothing asserts it.** Assert the resolved name EQUALS the requested one — the
  weaker `name in FACTORIES` form passes pre-fix, because an unrecognised
  spelling resolves to something else. (v1.137.0)
- ⚠⚠ **A cache probe is PROVIDER-AWARE, and a populated cache for a different
  runtime is not evidence.** Sharing one probe reports "cached" for a machine
  whose HF cache holds the model for torch while the ONNX provider still fetches
  it; warmup then blocks on a download that reaches the user as "connection timed
  out". **Guessing "cached" is the harmful guess; "not cached" costs a deferred
  load.** (v1.137.0)
- ⚠⚠ **Check the SIBLINGS before implementing a suite-relevant fix.** jdoc was
  the one server of three missing an argument contract, which is exactly why the
  defect was reportable here and nowhere else. A defect reportable in only one of
  three repos is a parity gap until proven otherwise. (v1.124.1)
- ⚠⚠ **Verify which BRANCH a repro takes before fixing the line a reporter
  cites.** An unusually good report put the bug one branch too low; fixing only
  the cited line would have shipped with the reporter's own repro still broken.
  (v1.127.0)
- **`None` and `[]` are different, and the distinction is often the fix.** None
  = "said nothing" and INHERITS; `[]` = "explicitly none" and widens WITH
  disclosure. Resolve it before discovery, not after. (v1.130.0)
- ⚠ **Disclosure is not a safeguard when the entry point cannot avoid triggering
  it.** A silent re-entry point that widens coverage and discloses is still a
  defect; inheritance is the fix. (v1.130.0)
- ⚠⚠ **A "purge stale state" branch must distinguish "nothing to write" from
  "the write FAILED."** Purging on a failed pass empties the store, stamps a
  fresh header on top, and the next run sees a matching identity and never
  rebuilds — permanent and silent. (v1.127.0, v1.125.0)
- ⚠⚠ **A cache-key format change is a MIGRATION, not a refactor.** Check what is
  already on disk. Absence of a field means the LEGACY DEFAULT, not "unknown" —
  reading it as a mismatch bills every existing user for a rebuild they did not
  ask for. (v1.127.0)
- ⚠ **A bare `except Exception: pass` makes a genuine failure indistinguishable
  from a clean result.** Three sidecars kept that silence for four months after
  the argument for removing it had already been made for the fourth. (v1.131.0)
- ⚠ **Skip by RULE, not by a denylist of names.** A denylist skips exactly the
  cases somebody thought of and walks into every other one. (v1.126.1)
- ⚠ **A score's units must match the curve that reads them.** A strength term
  hardcoded to the BM25 scale penalised cosine and RRF by 7x on identical
  separation, and two consumers silently inherited it. (v1.126.0)
- ⚠ **Projection runs LAST**, after every filter and after the consumers that
  read the fields it drops. (v1.121.0)

**Testing and measurement**

- ⚠⚠ **Adding a second member to an auto-detect chain invalidates every test
  that pinned the first one.** Installing fastembed turned FIVE existing tests
  red; they stubbed one provider probe and read the developer's site-packages
  for the other. CI saw none of it — CI installs neither provider. ⚠ The AST
  ratchet is SCOPED to functions that actually reach the fallback: unscoped it
  flagged every import-probe test, and **a guard with false positives is one
  nobody believes.** (v1.137.1)
- ⚠⚠ **Never restate a timing budget as a literal in a test.** A test asserted
  that a whole call beat the budget of one step inside it. Import the constant.
  (v1.128.0)
- ⚠⚠ **A toy fixture hides an incremental-path defect.** Under ~5 documents the
  incremental path re-materializes everything, so the bug looks absent. Size
  fixtures so the pass genuinely skips. (v1.125.0, v1.131.0)
- ⚠ **A subprocess probe cannot detect a deadlock.** The probe asks "would this
  import RAISE?"; a wedge is not a raise, and the probe's own subprocess is
  single-threaded, so it answers True in exactly the condition that hangs.
  (v1.132.0)
- ⚠ **`text=True` without `encoding=` decodes as cp1252 on Windows.** Only five
  bytes are undefined there, so most non-ASCII produces silent mojibake rather
  than a crash — never read "it did not raise" as evidence the decode was right.
  ⚠ `UnicodeDecodeError` is raised in subprocess's READER THREAD, so no
  try/except around the call catches it. (v1.121.1)
- ⚠ **Force UTF-8 stdio in CLI entry points.** On Windows `sys.stdout` is the
  console stream on a terminal and the LOCALE stream when PIPED, which is why it
  works by hand and fails for a script. (v1.124.2)
- ⚠ **Suite parity is for BEHAVIOUR CONTRACTS, not for whatever the other repo
  happens to have in `tests/`.** A ported lockfile test would have failed on a
  fresh clone here, where `uv.lock` is gitignored. (v1.124.2)

**Claims and evidence**

- ⚠⚠ **A rebuild underneath a scan cannot prove absence.** Staleness that means
  "the SOURCE moved" is blind to an index being rewritten under an unchanged
  tree. (v1.119.0)
- ⚠ **Never quote a fallback disclosure sentence as if it shipped.** When a
  weaker fallback was written and then not needed, the record must make the
  STRONGER claim — keeping that distinction honest in the direction that favours
  the other party is the point of having written it down. (v1.120.0)
- ⚠ **An id is a citation anchor**, so it must be unique across the whole
  document, not per section. (v1.116.0)

## Standing operational notes (jdoc-specific)

⚠⚠ **These were orphaned under a DATED `## v1.137.0` heading until 2026-08-29
and would have rotated into the archive with it.** They are standing rules, not
release narrative. **A standing rule filed under a dated heading has an expiry
date nobody chose** — when a note outlives the release that produced it, move it
here.

⚠ **`tests/` is shipped inside the sdist, so anything dropped there is
distributed.** `tests/infographic.png` — a 5.9 MB promotional image, referenced
by nothing — sat there from the initial commit and was **87% of the whole
source distribution** until 1.123.2 removed it. ⚠⚠ **No guard would have caught
it**: it is a tracked file, so exclusion rules and untracked-file scans are both
blind to it, and nothing asserts a size budget. **Inspect the artifact's LARGEST
entries, not just its file list** — a clean-looking 607-entry tarball was almost
entirely one image. ⚠ `uv.lock` is **gitignored here** (unlike jcm), so it is
never distributed and never validated; do not reason about it as a pinned input.

⚠ **`numpy` is in the dev group as of 2026-07-31 — test-only, and it must stay
that way.** The runtime import in `storage/doc_store.py` (vectorized semantic
search, pure-Python reference as fallback) stays OPTIONAL and LAZY; numpy is a
dev dep purely so the six tests asserting **fast path == reference** actually
run. They had skipped on EVERY CI run — a divergence between the two paths would
have shipped unnoticed. CI-equivalent env went 1982 passed/14 skipped → 1988
passed/8 skipped. ⚠ **`PYTHONPATH=src pytest` on a dev box is NOT the same run as
CI** — this box has numpy from other packages, so the gap was invisible locally;
reproduce CI by SYNCING FIRST, with the flags the workflow itself uses:
`uv sync --group dev --python 3.13` then `uv run --python 3.13 pytest tests/ -q`
([[feedback_an_assumption_about_the_machine_is_not_a_fixture]]).
⚠⚠ **A `uv run` with no `uv sync` in front of it inherits a `.venv` it did not
create**, so it silently tests whatever happens to be installed. jcm shipped
that exact command and measured the cost: the run came back **exit 0 with the
totals reconciling exactly** while `passed` fell 8,721 to 8,634 and `skipped`
rose 19 to 124 — **105 tests did not execute.** Exit code and total were both
"green". **Read the SKIP count**; jdoc's baseline on this box is 6 POSIX-only
skips, and CI's is 11 (it installs neither offline embedding provider nor
`openai`). ⚠ **Do NOT copy jcm's `uv sync --locked --group dev --extra watch`** —
this repo's `uv.lock` is gitignored so `--locked` fails, and there is no `watch`
extra. `tests/test_brief_bindings.py` binds this command to
`.github/workflows/test.yml`, so the workflow changing turns CI red instead of
this paragraph going quietly stale. Remaining 6
skips are legitimate: all POSIX-only, and they DO run on Linux CI. ⚠ **The two
`_PAIR_LOCK_API` tests were DELETED 2026-07-31** (`a4cef61`) — they called
`DocStore.hold_index_locks`, which does not exist, so a `hasattr` guard skipped
them on every platform on every run since the day they were written: tests for a
canonical-order pair-lock design that was considered and not taken. **The passed
count did not move (1988 → 1988), which is the check that matters** — nothing
that ever executed was removed. If that design is ever revived, write the tests
against the API that exists.

⚠ **2026-08-07: #102/#103/#104/#105 all CLOSED in 1.124.0.** ⚠ Re-run
`gh issue list --state open` before quoting any tracker state; never transcribe
a count into this file. **The `coordinated-retirement` hold is OVER** — #92
merged as `3037428`, branch deleted from the workflow. Nothing is held; ship
from `master`.

## Standing lessons (suite-wide)

Drawn from jcodemunch-mcp 1.108.291 (2026-08-22) and recorded here because each
one is about how we WORK, not about that repo's code. ⚠ **The byte-mass defect itself was CHECKED HERE and is ABSENT** (2026-08-22),
so do not re-run the audit: `get_document_outline` / `get_toc` / `get_toc_tree`
sum `content` over `index.sections`, which LOOKS like the jcm defect but is not.
`markdown_parser._finalize_section` slices each section from its own heading to
the NEXT heading of any level, so sections PARTITION the document instead of
nesting. Measured on this repo's own README: file 15,967 bytes, sum of 19
sections 15,967 — ratio exactly **1.0000**. ⚠⚠ **It is clean for a REASON, not
by luck, and the reason is load-bearing**: if section bodies ever become
descendant-inclusive, every one of those sums silently starts double-counting.

- **A competitor's fix list is a free defect probe.** A rival shipped
  `fix(gini): measure a file's lines as its own span, not the sum of every node`
  and named the defect precisely enough to check ours in one query — jcm's
  byte-concentration metric summed nested symbol spans and read 2.85x the real
  size of the files it described. Read competitors' `fix(...)` TITLES against
  whatever we built the same way; it is minutes, and it finds what our own tests
  were written not to see.
- **A ratchet can pass against the defect it names.** The guard written for that
  fix used a depth-limited regex and walked straight past
  `sum(int(s.get("byte_length", 0) or 0) for ...)`, two parens deep. **A green
  ratchet and an absent ratchet look identical**, because the tree is clean when
  you write it. Run every text-scanning guard against the defect PUT BACK, and
  add a positive test pinning a correct shape it must not flag — otherwise the
  ratchet becomes standing pressure to "fix" working code.
- **A defect is not evidence against the number it did not produce.** The same
  release nearly published a claim that the correction put a basis change behind
  our public savings figure. It did not: the number quoted was one seat's local
  meter, the site publishes a separate opt-in aggregate, and the tools feeding it
  were already correct. **Trace the path from a defect to the SPECIFIC figure
  before implicating it**, and never net coverage-conservatism (who reports)
  against a per-call arithmetic error (one call's maths) — different axes,
  opposite directions. If the apportionment cannot be computed, the honest output
  is "not computable" plus the bound, never a restated number. **Not restating is
  the conservative action, not the convenient one.**
- **A metric that credits us is the one direction a defect must never sit.** When
  the same shape turned up in jcm's token-savings baseline, the correction
  LOWERED our own reported numbers and shipped in the same release rather than
  as follow-up work. Where history could not be recomputed it was disclosed
  (`lifetime_unattributed`, a basis generation) rather than quietly carried or
  quietly rewritten — **a recomputed history is a guess wearing a measurement's
  clothes.**


## Release: the two steps that are only written down here

⚠⚠ **The full checklist lives in the `release` skill, which is GITIGNORED and
therefore MACHINE-LOCAL.** A checkout on another box, a fresh clone, or a
session without that skill loaded has none of it. These two items were lost that
way and are restated here because each one has already cost a real incident.

### 1. READ CI FOR THE PUSHED SHA BEFORE ANY IRREVERSIBLE STEP

Build, PyPI upload, tag, GitHub release, registry publish — none of them can be
taken back. **Push, then read the run, then continue.**

```bash
GITHUB_TOKEN="" gh run list --repo jgravelle/jdocmunch-mcp --limit 3 \
  --json headSha,conclusion,status,name
```

⚠⚠ **This matters more in jdoc than anywhere else in the suite, and the reason
is structural.** **jdoc has no release workflow at all** —
`.github/workflows/` holds `test.yml` and `replay.yml` and nothing else — so
every irreversible step here is taken by a human, and a human can take it
against a red build. `tests/test_brief_bindings.py` fails if that workflow set
ever changes, so THAT half cannot go stale quietly.

⚠⚠ **The contrast is with jdatamunch, and this file CANNOT bind that half.**
jdata's `release.yml` runs `on: workflow_run: workflows: ["Tests"]` and gates on
`conclusion == 'success'`, so a red build there does not release — verified
2026-08-30 by reading the file, not inferred from the release skill's prose.
**A claim about another repo's workflow is exactly the shape this section was
written to distrust**, and no test here can check it. Re-read it rather than
quoting this line:

```bash
GITHUB_TOKEN="" gh api repos/jgravelle/jdatamunch-mcp/contents/.github/workflows/release.yml \
  --jq '.content' | base64 -d | head -30
```

⚠ Same file also no-ops when `pyproject.toml`'s version already has a release
(`gh release view "$TAG" ... && exit 0`), so a docs-only push to jdata master is
safe and does NOT need a version bump to avoid a failed tag. ⚠⚠ **I advised the
opposite on 2026-08-29 from the release skill's one-line summary without opening
the workflow** — the summary says jdata "AUTO-RELEASES on push to master", which
is true and incomplete, and the missing half is the guard.

⚠ **Pushing is not checking, and a green local suite is not a green build.**
Four consecutive jcm releases (.259–.262) were published, tagged and
PyPI-uploaded on a RED build: the 8-job test matrix passed the whole time and
the failure was a lint job nobody read. jdoc 1.127.0 did all the irreversible
steps and THEN read CI, which came back red — it was a flake, which is luck, not
process. 1.128.0 read CI first and caught a real failure on all 8 jobs with
nothing shipped.

⚠ Read the JOBS, not just the run conclusion — a matrix still in progress
reports neither success nor failure at the run level.

### 2. THE REGISTRY'S ROWS ARE NESTED, AND A FLAT READ LIES

Verify a registry publish against the live API; the CLI checkmark is not proof.

⚠⚠ **Each row is `{server: {...}, _meta: {...}}` (schema 2025-12-11).** `name`,
`version` and `packages[]` live under `server`; `isLatest` and `publishedAt`
live under `_meta["io.modelcontextprotocol.registry/official"]`. **A flat
`row["name"]` read returns ZERO rows on a publish that completely succeeded.**

⚠⚠ **That is a SECOND false negative on top of the known paging trap, and
unlike that one it SURVIVES `&limit=100`** — so the documented remedy does not
help and the symptom is indistinguishable from a failed publish. **A zero-row
read is NEVER grounds to re-publish. Fix the parse first.**

⚠ Confirm `server.packages[].version` advanced too, not only `server.version`;
an entry can move one and not the other.

⚠ **Read-after-write lag is real** — a good publish has read back as absent ~90
seconds later, then appeared and held. **Absence is not evidence of failure.**

```bash
curl -s 'https://registry.modelcontextprotocol.io/v0/servers?search=jdocmunch-mcp&limit=100' -o reg.json
python - reg.json <<'PY'
import json, sys
rows = json.load(open(sys.argv[1], encoding="utf-8"))["servers"]
ours = [r for r in rows if r["server"]["name"].endswith("/jdocmunch-mcp")]
print(f"{len(ours)} rows")
for r in ours:
    off = r["_meta"]["io.modelcontextprotocol.registry/official"]
    if off.get("isLatest"):
        print("isLatest:", r["server"]["version"],
              "| packages:", [p.get("version") for p in r["server"].get("packages", [])])
PY
```

⚠ Write `reg.json` and any release notes **OUTSIDE the repository**. A stray
`.md` or `.json` in the repo root is swept up by `git add -A` and ships inside
the sdist — `relnotes.md` and `suite.log` both did in jcm.
`tests/test_sdist_exclusions.py` is the allowlist that catches it here.

## Issue + release policy (suite-wide, 2026-07-28)

**1. One issue, one verdict.** A multi-finding report gets SPLIT at triage into
one issue per finding, cross-linked, credit on each. Detail is not discouraged;
the reason is closure mechanics. A 4-finding issue closes only when the last one
settles, so three finished fixes sit behind one unfinished conversation.

⚠⚠ **THIS REPO IS WHERE THE LESSON CAME FROM.** On 2026-07-27 five issues
(#80/#89/#90/#93) were CONSOLIDATED into one gate, #95. That cut the open count
from 5 to 1 and manufactured a single artifact with the power to block a
release, which is exactly what it then did. **Tracker-tidiness and granularity
pull in opposite directions; do not optimize the count.**

**2. A release is NEVER blocked on an open issue**, including a verification we
asked for. Done + tested + green ships on schedule, carrying a plain
verification-status line. The #95 sentence is the canonical template and is
deliberately WEAKER than a sign-off; never blur the two in a changelog. Late
re-verification counts IN FULL and is announced retroactively. Nothing expires.
**Every timebox names its default action** ("verification by X, or Y ships with
disclosure Z").

⚠ **A reviewer's thoroughness must never become a veto.** If being careful can
stall a release, careful review becomes expensive to accept, which is backwards.

**3. A contributor's PR is never the only path.** Timebox and keep our own path
warm.

**3a. NO TIMEBOX WE OFFER RUNS LONGER THAN 24 HOURS — absolute, no exceptions**
(jjg, 2026-08-14; closed to exceptions 2026-08-20). It covers every shape:
signing the CLA, opening a PR already written, and taking an issue to implement.

⚠⚠ **The window is only fair BECAUSE the default action preserves credit.** At
expiry we implement the fix ourselves and credit them in the CHANGELOG, the
release notes and the close comment. So the 24 hours decide whose COMMIT it is,
never whether they are credited and never whether the fix ships. **Quote the
default in the same comment as the deadline** — a clock with an unstated
consequence reads as a threat, and it is not one.

⚠⚠ **The failure mode has a name: a CLA hostage negotiation.** jcodemunch-mcp
#443 ran EIGHT DAYS on a 2026-08-26 window — a real security fix, reviewed and
green, held behind a 30-second form, while SEVEN of our own merges conflicted its
branch. **Not one of those days bought anything.** A window over 24 hours
purchases exactly one thing, the chance the contributor's commit is theirs, and
pays for it in users' exposure to an unfixed defect.

⚠ **An extension the contributor ASKS FOR is not the same as a default we hand
out**, and CONTRIBUTING.md invites the ask by name. Hold it when they ask; the
clock exists to stop work going quiet, not to catch anyone out.

⚠ **Do not shorten a timebox already posted.** A public promise outlives the
policy that produced it. State the new window on new PRs.

**3b. A MERGEABLE contributor PR merges BEFORE any changelog-touching work of
our own.** Every entry we add occupies the same `[Unreleased]` block a
contributor's entry occupies, so each of our merges conflicts their branch — and
**a CONFLICTING fork PR has no `refs/pull/N/merge` and therefore gets NO CI AT
ALL.** Their branch goes dark for a reason unrelated to their change.

```bash
GITHUB_TOKEN="" gh pr list --state open --json number,author,mergeable,mergeStateStatus   --jq '.[] | select(.author.login != "jgravelle") | "#\(.number) \(.author.login) \(.mergeable) \(.mergeStateStatus)"'
```

⚠ **The boundary:** a BLOCKED PR cannot go first. Then we ship anyway (policy 2)
and **we own the resolution** — push the merge to their branch and say on the
thread that the conflict was ours.

⚠⚠ **`license/cla` IS A REQUIRED STATUS CHECK ON `master` (2026-08-21).** Until
that date this repo was PROTECTED BUT REQUIRED NOTHING, so the CLA was
read and never enforced — an open PR read `MERGEABLE/UNSTABLE` and one distracted
click could have merged unsigned code. jcm has had this since 2026-08-17 (policy
3d); **a setting fixed in one repo of a suite is fixed in one repo.** All three
now read `contexts ["license/cla"]`, `strict false`, `enforce_admins false`,
force-push and deletion off.
⚠ `enforce_admins: false` is deliberate — it is what lets a merge be pushed to a
contributor's fork. `strict: false` avoids forcing a rebase after every release.
⚠⚠ **This composes with the status-erasure hazard below and now FAILS CLOSED.**
Our push to a fork wipes `license/cla` from the new head, and with the check
required that reads as `BLOCKED` until the bot re-posts. Correct, and it will
look like a new problem the first time.

⚠⚠ **A FORK PR SHOWING ONLY `license/cla` HAS NOT BEEN TESTED — IT HAS BEEN
SILENTLY HELD**, and `gh pr checks` lists only checks that RAN, so the hold is
invisible from the place you would look. Measured on jdoc #122 and jdata #4 on
2026-08-20: four and two held runs respectively.

```bash
GITHUB_TOKEN="" gh api "repos/jgravelle/<repo>/actions/runs?status=action_required&per_page=30"   --jq '.workflow_runs[] | "\(.id)|\(.name)|\(.head_branch)"'
GITHUB_TOKEN="" gh api --method POST "repos/jgravelle/<repo>/actions/runs/<id>/approve"
```

⚠ **Both repos' `fork-pr-contributor-approval` was relaxed to
`first_time_contributors_new_to_github` on 2026-08-20**, matching jcm since
2026-08-13. Until then every first-time fork contributor's runs were created
`action_required` and never executed.

⚠⚠ **Do NOT answer "an issue is stuck" with aggregate stats.** jdoc's median
time-to-close is 1 day (60 issues, 45 within a day, 1 ever past a week). True,
and NOT a response: the cost of a blocked issue is CONCENTRATED, not
distributed. Design the fix at the OUTLIER. See
[[feedback_dont_answer_pain_with_aggregates]].

Surfaces: `CONTRIBUTING.md` + `.github/ISSUE_TEMPLATE/`.

## #95 SPLIT 2026-07-28: 15 of 19 criteria satisfied, 3 split out and fixed

Applied the one-issue-one-verdict rule to our own gate. All 19 acceptance
criteria were checked **against the branch**, not against a summary of it:
**15 satisfied**, evidenced by 58 tests across six `test_issue95_*.py` files.
⚠ **PR #97 was FAR larger than the four items recorded above** — it includes
seven real-subprocess `test_spawn_*` cases, which is the "real-process
interruption, not mocked exceptions" criterion nobody had ticked.

Three were genuinely open, split into their own issues, and all three are now
fixed and closed:

- **#98** QA-25 exhaustiveness (`b476e09`). The old guard was a PRESENCE check by
  design; it could not fail when a NEW caller arrived with no policy. Now every
  production `delete_index` call must pass `lock_wait` or be named in
  `UNCONTENDED_EXEMPT` with a reason. ⚠ **`UNCONTENDED_EXEMPT` is empty ON
  PURPOSE** — add a site with its reason, never loosen the rule.
- **#99** installed-wheel smoke (`a84c757`). New `package-smoke` CI job on ubuntu
  + windows builds the wheel, installs into a clean venv, runs
  `scripts/smoke_installed.py` from a dir with no `src/` reachable. ⚠ **The
  script REFUSES to run if it imported from a source tree** — without that it
  passes by testing `src/` again and the job is decorative.
- **#100** machine-generated evidence (`a84c757` + `79c6542` + `132c8e1`).
  `scripts/evidence_receipt.py` emits a receipt per matrix job from
  `pytest --junitxml` (built in, no new dep) and rolls them into a summary.

⚠⚠ **The receipt tool took THREE defects, all found by reading a REAL CI receipt
rather than the local one.** (1) `tree_clean` false on a pristine checkout,
because the run writes junit.xml/coverage/receipts before emitting — **a signal
that always fires hides the case it exists for**. (2) It recorded the synthetic
PR-merge SHA, which is `fatal: bad object` in branch history. (3) The fix for (2)
then CLAIMED `GITHUB_SHA` was the branch head — **on a `pull_request` event
`GITHUB_SHA` IS the merge commit**; the head is reachable ONLY via
`github.event.pull_request.head.sha` passed from the workflow as `PR_HEAD_SHA`.
**A false provenance line inside the provenance artifact.**

Two honesty guards, both proven to fire: a dirty tree is recorded and surfaced,
and a summary spanning >1 SHA prints `MIXED SHAs. This is not evidence for a
single candidate.` rather than averaging runs into a figure describing nothing.

⚠ Receipt counts split **1972 Linux / 1967 Windows on identical 1981 totals**
(9 vs 14 skips) — that is the five POSIX-only tests, i.e. the QA-24 mechanism
showing itself. **Never read a Windows pass as verifying a locking contract.**

⚠ **The QA harness is an ISSUE ATTACHMENT, not a repo file** — `find` in the
tree returns nothing. Pull it from the #95 body links, copy into `tests/` to
run, then DELETE it.

⚠ **State that lives only on a PR is state the gate does not carry.** On
2026-07-28 every substantive point was answered on #97 and NOT on #95, leaving
the gate of record showing "Ready for your review" as its last word. Mirror PR
outcomes back to #95.

## #93/#95 contribution path DECIDED 2026-07-26: rknighton implements, via PR

Answered the contribution-path question he raised on #93 and escalated on #95 as
formally unanswered: **option 3, a PR against `coordinated-retirement`.**
⚠ **The deciding factor is the CLA, not review convenience.** `CONTRIBUTING.md:7`
makes a signed CLA a hard merge gate (jdoc is dual-licensed, paid commercial
tier), and he has **16 issues / ZERO PRs** here, so nothing is on file.
⚠ **A patch pasted into an issue is the WORST of the three options** — real code
with no signing record at all — which inverts the intuition that a patch is the
lighter-weight ask. A PR makes cla-assistant prompt automatically. Same
reasoning that closed jcm#380.

⚠ **Independence: the ORACLE survives his authorship, his JUDGMENT does not.**
jjg committed on #90 that v1.115 is held for independent re-verification and
QA-17 will not be self-certified. `qa_lifecycle_contract.py` is already LOCKED
with published pre-fix receipts (3 passed / 4 failed at `99a31c1`, identical
across 5 runs) and #95's acceptance criteria predate any implementation — that
is pre-registration, so the gate cannot be reshaped to fit the fix. What is lost
is his adversarial pass on the new code; **that role moves to US, and the release
notes must SAY so** rather than let the record imply author-verification.

PR scope requested: QA-19 + QA-23 + QA-21 + the `reason_code` vocabulary w/
SPEC.md drift guard, Path A. Follow-ons: process-interruption durability,
installed-wheel matrix, frozen-SHA run. **QA-25 was in that scope and we then
took it — see below. Disclosed on #95 rather than left for him to find, with an
offer to revert if his local version differs, since he owns the contract.**

## ⚠ "v1.115.0" IS A LABEL, NOT THE SHIPPED VERSION (clarified 2026-07-27)

⚠ **The retirement release ships as 1.120.0 or later. It can never be 1.115.0,
and the reservation plan never actually said it would be.** Read this before
writing "v1.115.0" in another release note or issue comment.

What is TRUE: master deliberately skipped 1.115.0 (1.114.2 -> 1.116.0) to reserve
the number for the held branch, and `CHANGELOG.md` on `coordinated-retirement`
carries a `[1.115.0]` entry that master's does not. **That part worked as
designed and the entry STAYS** as the historical record of this branch's work.

What is ALSO true and was being missed: **the branch's `pyproject.toml` has NEVER
said 1.115.0 — zero occurrences across its entire history** (`git log -p
coordinated-retirement -- pyproject.toml`). Master merges carried it forward and
it currently reads 1.119.0. The plan always said "resolve version conflicts to
the HIGHER number," so the shipped artifact was ALWAYS going to be >= 1.119.0.

⚠ **Publishing 1.115.0 after 1.119.0 would ALSO be self-defeating even if we
tried: `pip install jdocmunch-mcp` resolves to the HIGHEST version, so the
retirement work would ship into a version nobody receives by default.**

**How to say it:** "tracked as the 1.115.0 CHANGELOG entry, shipping as 1.120.0."
Disclosed to rknighton on
[#95](https://github.com/jgravelle/jdocmunch-mcp/issues/95) 2026-07-27 rather
than left for him to notice, since he has been verifying something under a name
that will never appear in `pip show`. ⚠ **Nothing about the harness, the frozen
oracle, the acceptance criteria or any receipt depends on the number.**

## ⏰ Retirement release TIME-BOXED through 2026-08-02 (set 2026-07-26) — RESOLVED

✅ **RESOLVED 2026-07-29: he completed it AHEAD of the box, so the fallback
never fired.** Kept below for the reasoning, which stands: the time-box existed
to stop OUR latency becoming HIS obligation, and it is now the suite-wide policy
in `CONTRIBUTING.md`. ⚠ **Do NOT quote the fallback sentence as if it shipped.**

⚠ **Original text follows (historical):** ACTION DUE 2026-08-02. The release was gated on an unpaid volunteer's
re-verification with no deadline, holding #80/#89/#90 open indefinitely. That is
our design error, not his. Posted on
[#95](https://github.com/jgravelle/jdocmunch-mcp/issues/95#issuecomment-5083861358)
+ [#90](https://github.com/jgravelle/jdocmunch-mcp/issues/90#issuecomment-5083862220).

**If his re-verification/PR lands by 2026-08-02** it is the gate, as agreed.
**If it does not**, release on his pre-registered harness green at a frozen SHA,
with the release notes carrying VERBATIM: *"Verified against the reviewer's
pre-registered lifecycle harness at a frozen SHA. Not independently re-verified
by its author."* ⚠ **That exact wording is the point** — jjg promised on #90 that
QA-17 would not be self-certified, and a harness pass is a WEAKER claim than his
sign-off. Label it as weaker; never let the changelog blur the two.

⚠ **Nothing expires.** Findings stay credited by ID, issues stay open, and a
re-verification arriving AFTER the box still counts in full — correct anything it
contradicts, in a follow-up release if needed. He was also told explicitly he may
hand back QA-19/QA-23/QA-21 at no cost, because a clear no beats an open-ended
maybe.

**Engagement data behind the decision (2026-07-26):** he is NOT disengaged — his
median turnaround is **3.1h vs our 6.7h**, his longest self-gap in the arc is
50.3h and he broke it unprompted, and he filed #95 with 5 attachments 14.4h
before this was written. ⚠ **His activity clusters at UTC 00-04 and 17-23, so
posts landing 13:00-14:00 UTC sit in his off-hours** — silence there is his
normal pattern, not a warning sign. **We have been the slower party**; he had to
re-raise the contribution-path question in #95 before we answered ~20h later.
The time-box exists to stop OUR latency becoming HIS obligation.

## QA-25 SHIPPED by us 2026-07-26 (`8d15897`): intent is stated, never inferred

Closes the branch's single known red test, Linux-only
`test_v1_115_0_lifecycle_v2.py::test_three_processes_keep_one_lock_inode`
("DID NOT RAISE Empty"). ⚠ **Root cause is NOT the default's value — it is that
two tests asked the default to arbitrate a question it cannot answer.** Both
production callers were ALREADY explicit (`tools/delete_index.py:36` `False`,
`tools/index_local.py:231` `True`), so **those two tests were the only implicit
callers in the entire repo**, requiring OPPOSITE behavior on the SAME lock. No
default could satisfy both. At `False` the QA-15 deleter returned instead of
blocking, so nothing reached the queue and `pytest.raises(queue.Empty)` got a
value.

Fix is the reviewer's rule, verbatim: every contention-sensitive caller states
whether it waits or refuses; the lock never infers intent from surrounding
state. QA-15 deleter → `lock_wait=True`; QA-17 gate contender →
`lock_wait=False`; `⚠ UNRESOLVED` docstring block replaced with the resolution.
⚠ **This SUPERSEDES our proposed retirement-record inference — do not resurrect
it.** It also dissolved a constraint that was OURS, not his: we were trying to
satisfy both tests WITHOUT editing either, and their author told us to edit them.

⚠ **The default STAYS `False`, and that is a data-loss argument, not a
preference:** a caller that forgets to say gets the REFUSING behavior, which
preserves the QA-17 guarantee that both participating indexes are never
simultaneously absent. Defaulting to blocking would make forgetting cost an
index. New `tests/test_v1_115_0_qa25.py` pins it by signature inspection.

⚠ **The second guard is a PRESENCE check, deliberately, and its docstring says
so.** It asserts each contention-sensitive function contains a
`delete_index(..., lock_wait=<expected>)` call and says nothing about its other
calls. **Our first version demanded EVERY call be explicit and produced 8
findings that were all noise** — two of those functions also delete uncontended,
as first acquirers where the flag cannot change the outcome. It is a
signature-level assertion on purpose: it runs on BOTH platforms, whereas the
behavioral test that would catch the loss SKIPS on Windows, which is exactly how
this regressed unnoticed. Both guards proven non-vacuous (remove the argument →
guard fails naming function+line; flip the default → drift assertion fails).

Receipts, all 8 jobs green at `8d158975ad2b515289c8ad524f3e2b971d397dbe`
([run 30204690565](https://github.com/jgravelle/jdocmunch-mcp/actions/runs/30204690565)):
Linux 1875 passed / 9 skipped ×4, Windows 1870 passed / 14 skipped ×4. Against
`69c91c4`, Linux went 1 failed / 1872 passed → 0 failed / 1875 passed.
⚠ **The 1875/1870 split is 5 POSIX-only tests (9 vs 14 skips, identical 1884
totals) and QA-15 is one of them — that number IS the QA-24 mechanism**, so
never read a Windows pass as verifying a locking contract.

**CI: `fail-fast: false` added to the Tests matrix (`69c91c4`).** One ubuntu-3.10
failure was cancelling all four Windows jobs, so the frozen review SHA carried
NO Windows result while the panel showed 8 failures where there was 1. Code
identical to the old pin (`git diff --stat 99a31c1 69c91c4 -- src/ tests/
pyproject.toml` is empty), announced on-issue rather than pushed quietly.
⚠ **RETRACTED on the record: our claim that the draft PR's `synchronize` event
"has not been firing" is WRONG** — `gh run list` shows `pull_request`-event runs
at BOTH `99a31c1` and `69c91c4`. Branch CI has been firing on push all along;
cancellations made those runs unreadable in the panel. `workflow_dispatch` is
still worth keeping (re-run any ref without pushing), but the diagnosis attached
to it was false. Head is HELD from here while he works.

## CHANGELOG maintenance warning (2026-07-18 incident)
CHANGELOG.md's established format is `## [X.Y.Z] - date - title` with curated
prose. Do NOT run `scripts/generate_changelog.py` against it: the script emits
a different heading format and rewrites all historical entries, which changes
every CHANGELOG section id — and the replay self-fixture's goldens for
'hybrid search' / 'broken links' / 'openai compatible embeddings' point at
CHANGELOG section ids, so regeneration turned Tests+Replay red on an otherwise
docs-only commit (recall 1.0 -> 0.7, exactly the 3 CHANGELOG goldens).
Maintain CHANGELOG by hand-appending entries in the established format, and
keep new entry wording clear of fixture query phrases
([[feedback_fixture_query_corpus_pollution]] class).


## Replay-corpus warning: trimming CLAUDE.md can break the replay gate

Same class as the CHANGELOG incident above, hit 2026-07-26 while tracking
`docs/CLAUDE-history.md`. ⚠ **The replay self-fixture indexes `repo_path: "."` —
the WHOLE REPO — so any large markdown file added to the tree joins the retrieval
corpus and competes with the goldens.** Tracking 115 KB of trimmed release-brief
prose dropped nDCG to **0.906 against a 0.95 gate with recall still 1.0** (every
golden found, just outranked), failing
`test_replay_metrics.py::TestGate::test_pass_when_within_gate` and
`TestBaselineLock::test_self_fixture_meets_lock`.

⚠ **It was INVISIBLE to CI because the file was UNTRACKED — a fresh clone did not
have it.** Local runs were red while all 8 CI jobs at `69c91c4` were green. Any
"CI is green at the frozen SHA" claim is blind to untracked working-tree files.

Fix was not a new judgment: **`CLAUDE.md` is ALREADY in the fixture's
`extra_ignore_patterns`** for precisely this reason (recorded there: its
tool-keyword-dense entries shadow stable CHANGELOG goldens, e.g. 'broken links'
demoted by the #47-50 release notes at v1.77.0). `docs/CLAUDE-history.md` **IS
that content**, so trimming into it moved the shadowing prose out from under its
own exclusion; the archive inherits the pattern. Goldens and the 0.95 gate
UNTOUCHED — ⚠ **never fix this class by moving a golden or lowering the gate; the
signal is correct, the corpus scope was wrong.**

Every future batch trimmed into `docs/CLAUDE-history.md` stays excluded, so this
will not recur for that file. It WILL recur for any new large doc added at a new
path ([[feedback_fixture_query_corpus_pollution]]).

## Release history

⚠ **This file keeps the THREE newest dated `## vX.Y.Z` sections. Everything
older is in `docs/CLAUDE-history.md`** — v1.137.1 rotated there 2026-09-01, v1.137.0 on 2026-08-30,
v1.116.0 through v1.135.0 on 2026-08-29, v1.115.0 and earlier on 2026-07-25. `CHANGELOG.md` covers most of
them, but 1.67.0-1.92.0 and 1.96.0 exist ONLY in the history file.

⚠⚠ **The archive is NOT loaded into a session.** What each rotated entry EARNED
was lifted into "Lessons from rotated entries" above before it moved, so the
rule is here and only the evidence is there. **A split with no pointer is a
deletion** — that is what this paragraph is for. `tests/test_brief_bindings.py`
asserts the pointer exists, that at most three dated sections remain, and that
every heading present at the previous commit now lives in exactly one of the two
files.

⚠⚠ **Never quote an open-issue count, an open-PR count or a timebox date from
the archive.** Those are the only facts in it with a guaranteed expiry date. Run
the query.

⚠⚠ **The rotation now has a GATE: `tests/test_claude_md_size.py`.** This file is
budgeted at 130,000 chars against the 150,000 the harness will load, and today it
is ~101,600 with 27 embedded release sections making up 96% of it. When the gate
fires, move the OLDEST `## vX.Y.Z` sections into `docs/CLAUDE-history.md`.
**Rotate, never delete** — every version here also exists in `CHANGELOG.md`, but
four sections carry analysis the CHANGELOG does not.

⚠⚠ **Rotate into THAT path, not a new one.** The replay self-fixture indexes
`repo_path: "."`, so a large markdown file at a new path joins the retrieval
corpus and outranks the goldens — measured once at nDCG 0.906 against a 0.95 gate
with recall still 1.0. `CLAUDE.md` and `docs/CLAUDE-history.md` are both in the
fixture's `extra_ignore_patterns`, and the gate asserts it for every whole-repo
fixture. ⚠ jcodemunch-mcp rotates into a ROOT-LEVEL `ISSUE-HISTORY.md`; copying
that choice here is the specific mistake the gate exists to stop.

⚠ **The sibling repo is why this exists.** jcm's `CLAUDE.md` hit 200,543 chars
and stopped loading on 2026-08-21 while its size practice was being followed —
the practice named one section and the growth was everywhere else. **A rule that
names one section licenses every other section to grow**, and a budget stated
only in prose is not a budget.

## Purpose
Documentation section indexing for the jMunch suite. Companion to jcodemunch-mcp (which owns code symbols). Do NOT add code/docstring parsing here.

## Supported Formats
`.md/.mdx`, `.rst`, `.adoc`, `.ipynb`, `.html`, `.txt`, `.yaml/.yml` (OpenAPI only), `.json/.jsonc`, `.xml/.svg/.xhtml`, `.tscn/.tres` (Godot scenes/resources), `.pdf/.docx/.pptx/.epub` (optional `[office]` extra, local indexing only, markitdown conversion)

## Key Modules
- `storage/doc_store.py` — DocIndex, DocStore, detect_changes, incremental_save
- `parser/` — one file per format (markdown, rst, asciidoc, notebook, html, text, openapi, json, xml)
- `tools/` — index_local, index_repo, index_file, get_toc, get_toc_tree, search_sections, get_section, get_sections, list_repos, delete_index, get_broken_links, get_doc_coverage, get_backlinks, get_stale_pages, get_wiki_stats, check_section_delete_safe, get_section_blast_radius, find_similar_sections
- `cli/hooks.py` — PreToolUse (Read interceptor) + PostToolUse (auto-reindex) + PreCompact (session snapshot) hook handlers for Claude Code; owns `_DOC_EXTENSIONS`
- `watch.py` — (#78) `watch` daemon: `discover_local_doc_repos` + `watch_docs` (watchfiles-based, incremental `index_local` refresh, rediscover loop)
- `service_installer.py` — (#78) cross-platform login-service installer for `watch` (`jdocmunch-watch`; systemd/launchd/Task Scheduler)
- `cli/init.py` — `jdocmunch-mcp init` full onboarding: client detection, config patching, CLAUDE.md policy, Cursor/Windsurf rules, hooks, index; `claude-md` subcommand
- `embeddings/` — provider.py (Gemini + OpenAI), cosine_similarity, embed_sections, embed_query

## CLI Subcommands
| Subcommand | Purpose |
|------------|---------|
| `serve` (default) | Run the MCP server (stdio) |
| `init` | One-command onboarding: detect clients, write config, install policy, hooks, index |
| `claude-md` | Print or install the Doc Exploration Policy (`--install global\|project`) |
| `index-local --path <dir>` | Index a local folder (CLI, no MCP session needed) |
| `index-file <path>` | Re-index a single file within an existing index |
| `hook-pretooluse` | PreToolUse hook: intercept Read on large doc files (reads stdin) |
| `hook-posttooluse` | PostToolUse hook: auto-reindex doc files after Edit/Write (reads stdin) |
| `hook-precompact` | PreCompact hook: session snapshot before context compaction (reads stdin) |
| `watch` | (#78) Foreground daemon: auto-reindex every locally-indexed doc repo on any on-disk doc change. `--no-ai-summaries`, `--quiet` |
| `watch-install` / `watch-uninstall` | (#78) Install/remove the doc watcher as a login service (systemd/launchd/Task Scheduler; `jdocmunch-watch`). `watch-install` takes `watch`'s flags: `--no-ai-summaries`, `--quiet` (#120) |
| `watch-status` | (#78) Print doc-watcher service state + per-repo watch coverage (also the `get_watch_status` MCP tool) |

## 1.x compatibility contract (license-binding)

Existing 1.x licensees must be able to upgrade between any two 1.x versions
with zero surprise. This is a hard constraint, not a guideline.

**Never on 1.x:**
- Remove or rename an MCP tool. Aliases for any rename must stay in place forever.
- Remove a `Section` field from `to_dict` output (additive only; new fields use the "omit when empty" convention).
- Drop a runtime dependency that an existing user might rely on (e.g. tiktoken stays optional; bytes/4 fallback stays).
- Force a reindex without auto-migrating on load. `INDEX_VERSION` bumps are allowed when the loader silently migrates v(N-1) → v(N) on first read.
- Change the JSON wire format of any tool response in a way that breaks an existing consumer. New keys are fine; renames + removals are not.
- Make a previously-default behavior raise. If we deprecate a flag value, keep it accepted (with a deprecation note in `_meta`) until a 2.x is approved.

**Acceptable on 1.x:**
- Add new tools, fields, response keys, env vars, kwargs (all defaulted to backwards-compat values).
- Tighten internal behavior (faster algorithms, better defaults) when no public output changes.
- Add new error returns for inputs that previously errored differently.
- Add new opt-in code paths gated by env var or kwarg.

**Reserved for 2.x (won't ship until a major-version license revision is planned):**
- See `ROADMAP.md` § "Reserved for 2.x" for the canonical list.

## Architecture
- INDEX_VERSION=3; version mismatch triggers auto-migration on first load (NEVER a forced reindex on 1.x)
- O(1) section lookup via `DocIndex.__post_init__` id dict
- `pyyaml>=6.0` required (hard dep)
- Hybrid search (v1.9.0): `search_sections` fuses BM25 + semantic cosine when embeddings exist. `use_embeddings` defaults to `"auto"` (embed when provider configured). `search_sections` params: `semantic` (None/auto, True, False), `semantic_only`, `semantic_weight` (0.0–1.0, default 0.5). `_meta.search_mode` reports `hybrid`/`semantic_only`/`lexical`.
- Embedding providers: GOOGLE_API_KEY (Gemini, text-embedding-004), OPENAI_API_KEY (text-embedding-3-small), openai-compatible + JDOCMUNCH_OPENAI_COMPAT_URL + JDOCMUNCH_OPENAI_COMPAT_MODEL, or sentence-transformers; override with JDOCMUNCH_EMBEDDING_PROVIDER env var
- Summarizer providers: ANTHROPIC_API_KEY, GOOGLE_API_KEY, OPENAI_API_KEY, MINIMAX_API_KEY, ZHIPUAI_API_KEY; override with JDOCMUNCH_SUMMARIZER_PROVIDER env var (values: anthropic, gemini, openai, minimax, glm, none)
