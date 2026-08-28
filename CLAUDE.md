# jdocmunch-mcp

**Version:** 1.137.1 |
**Tests:** `PYTHONPATH=src pytest tests/ -q`

## v1.137.1 — the equivalence is MEASURED, and five tests were reading site-packages

⚠⚠ **v1.137.0 shipped the allow-list with the equivalence ASSERTED, not
measured — this closes that.** `capture_canary` under sentence-transformers
2.5.0 / torch 2.5.1, then `check_drift` under fastembed 0.8.0 / onnxruntime
1.23.2 against that snapshot: over 16 canary strings **max drift 6.0e-13** (min
cosine 0.9999999999993988), **max per-component delta 1.9e-07** on 384 dims.
Float32 rounding, not a model difference. ⚠ **The control is what makes it a
measurement of ONNX rather than of torch**: after the pass, `sys.modules` held
none of torch / sentence_transformers / transformers, and onnxruntime was
loaded. Without that check a silent fallback to the ST provider would produce
a perfect score and mean nothing.

End to end on a 32-section corpus indexed under ST, re-indexed
`incremental=False` under FastEmbed: **0 embedder calls, 0 texts**, no
`embedding_rotation`, header unchanged, coverage 1.0. Fail-closed half at the
same entry point: `JDOCMUNCH_FASTEMBED_MODEL=BAAI/bge-small-en-v1.5` discloses
`full_re_embed` and re-embeds all 32. ⚠ One box, one version pair — **the
allow-list is a LIST rather than a rule for exactly this reason.**

⚠⚠ **Installing fastembed on this box turned FIVE existing tests red, and CI
could not see it because CI installs neither offline provider.** They stub
`_sentence_transformers_available` and not `_fastembed_available`, so they
pinned half the fallback and read the developer's site-packages for the other
half ([[feedback_an_assumption_about_the_machine_is_not_a_fixture]]).
**Adding a second member to an auto-detect chain invalidates every test that
pinned the first one.** New AST ratchet in
`tests/test_jdoc_126_fastembed_provider.py` fails when a test pins one probe
and not the other. ⚠ Scoped to functions that actually call
`get_provider_name`/`should_embed` — without that it flags every jdoc#118
import-probe test, which never reaches the fallback, and **a guard with false
positives is one nobody believes**. Exemptions carry reasons; proven against
the defect put back AND against three shapes it must not flag.

⚠ The measurement is a SEPARATE release, not an edit to 1.137.0's entry —
that artifact is on PyPI and its CHANGELOG is what it shipped with
(the v1.134.1 provenance lesson).

## v1.137.0 — #126: the alias keys on the MODEL, because the sidecar has no dim backstop

FastEmbed as an offline provider (`[fastembed]` extra, onnxruntime not torch),
reported by @LuigiNicaPRO. The reported defect is narrow — `get_provider_name()`
is a closed if-chain, so `fastembed` fell through it to auto-detect and
`_PROVIDER_FACTORIES` was unreachable for the name. ⚠ **A closed chain and a
factory map are two lists that must agree and nothing asserted it**; a test now
does, and it asserts the resolved name EQUALS the requested one — the weaker
`name in _PROVIDER_FACTORIES` form passes pre-fix, because an unrecognised
spelling resolves to something else.

⚠⚠ **The reported remedy — normalize the provider name so the header keeps
saying `sentence-transformers` — is UNCONDITIONAL, and that is the whole
finding.** `cache.load` matches `(provider, model, dim)` by exact equality and
`_provider_identity` returns `dim=None` for BOTH offline providers, which the
cache reads as a **wildcard**. So there is no dim backstop underneath a
normalized provider name. `JDOCMUNCH_ST_MODEL` is user-settable, so a blanket
rename writes `sentence-transformers` over vectors another model produced,
`cache.load` then MATCHES, and the two derivations merge into one sidecar that
search ranks across. **jdoc#111's shape, and worse than the re-embed it avoids:
a full re-embed is expensive and observable, this is cheap and invisible.**

`_FASTEMBED_ST_EQUIVALENT_MODELS` is an explicit per-model allow-list, failing
closed on an unlisted model, a divergent `JDOCMUNCH_ST_MODEL`, and an empty
model. ⚠ **A model earns a place there by being MEASURED**, and
`check_embedding_drift` is the measurement — a canary captured under one runtime
and re-run under the other reports exactly the equivalence being claimed.

⚠ Two details that read as oversights and are not. The alias writes the
sentence-transformers **SPELLING** (`all-MiniLM-L6-v2`), not the canonical hub
id, because the header is an exact string match against a file that already
exists. And **dim stays `None`** — every ST sidecar ever written stores `None`,
so an active 384 compares unequal and purges the file the alias exists to reuse
(the same trap `_sentence_transformers_factory` documents for the worker).

⚠ New `sidecar_identity()` is the ONE place resolving the header triple;
`index_local`'s rotation detector reads it too, or it reports a rotation on every
index for a corpus that never moved (jdoc#109).

⚠⚠ **The cache probe is provider-aware, and this is a jdoc#110 regression
waiting to happen.** `_st_model_is_cached` probes the HF hub layout; FastEmbed
downloads to `FASTEMBED_CACHE_PATH` / `<tempdir>/fastembed_cache`. Sharing the
probe reports "cached" for a machine whose HF cache holds the model for TORCH
while FastEmbed still fetches it — warmup then blocks the handshake on a
download, which reaches the user as nothing but "connection timed out". **A
populated HF cache is deliberately NOT evidence**: guessing "cached" is the
harmful guess, "not cached" costs a deferred load.

⚠ **The ST import probe and the embed worker do NOT fire for FastEmbed.**
Neither is about embeddings; both are about torch. onnxruntime loads a different
DLL set, so jdoc#118 is an ARGUMENT here and not evidence — and probing a package
this provider never imports would suppress a working provider on a machine where
sentence-transformers is broken. If FastEmbed wedges the same way on Windows the
worker is the fix, and the measurement comes first.

⚠ Auto-detect prefers FastEmbed over sentence-transformers when both are
installed; explicit `JDOCMUNCH_EMBEDDING_PROVIDER=sentence-transformers` is the
way back. Extra is OPTIONAL, never a runtime dep. First-use model download is
README-disclosed before release (PyPI-quarantine rule).

Tests `tests/test_jdoc_126_fastembed_provider.py` (42; **41 fail pre-fix**, the
one both-sides pass being the control that demonstrates the unconditional alias
matching a sidecar it should not). Suite **2646 / 6**; `ruff check src/` clean.
No tool, schema or INDEX_VERSION change.

- **v1.136.0 - the one string that survives tool deferral.** The MCP `initialize` response now carries an `instructions` string; it did not before, because the transport called `create_initialization_options()` bare and the field went out empty. ⚠⚠ **Invisible in a normal session, CONCENTRATED in a deferred one**: a host over its schema budget ships tool NAMES and withholds the JSONSchemas, so an agent sees 64 bare strings and none of the descriptions. The spec delivers `instructions` on a SEPARATE TRACK from the tool list, so it arrives whole - in a plain MCP client it is the entire steering budget this server gets. 909 chars against a 1,000 cap. ⚠ Also sets `Server(..., version=__version__)`: without it the SDK reports ITS OWN version in `serverInfo`. ⚠ `__version__` is `"unknown"` under `PYTHONPATH=src`, so a green test does NOT prove the wire carries a real number. ⚠⚠ **Ported from jcodemunch-mcp v1.108.292 - both defects were present here unchanged, and neither had a symptom anyone could report.** ⚠⚠ **The port also reproduced a NameError in BOTH repos** (`logger` through a module-level name neither server.py defines) and **only jdoc caught it** - jdata's suite was GREEN with the identical bug, because it had no F821 gate. jdoc's `test_lint_gate_regressions.py` did its job. **A setting fixed in one repo of a suite is fixed in one repo, and that applies to the GATES as much as the code.** `tests/test_mcp_instructions.py` binds the prose to the catalog; all three guards were verified by reintroducing the defect each names.

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
reproduce CI with `uv run --python 3.13 python -m pytest tests/ -q`
([[feedback_an_assumption_about_the_machine_is_not_a_fixture]]). Remaining 6
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


## v1.135.0 — #121: the filter keys on the SUFFIX, because the orphan case is the worse one

`list_repos` globbed `*/*.json`, excluded only `_` and `.summary.json`, and so
opened and json-parsed every `.terms`/`.related`/`.boilerplate`/`.duplicates`
sidecar in the store before discarding it for lacking primary-index fields.
@rknighton measured it on a controlled 75-index store: **2,044.2 ms → 3,459.5 ms
median, non-overlapping ranges, 300 extra parses.** Documented first-call hot
path, also hit by the PreCompact hook.

⚠⚠ **Keying on "does the primary exist" would have fixed the LIVE case and left
the worse one untouched.** A store that lost an index to a pre-1.108.0
`delete_index` still carries all four sidecars; theirs held **1,093 such files,
2.0 GB, opened on every call to return nothing** — `.related.json` was 98.3% of
those bytes and ONE file was 1.24 GB. Their repro ships both arrangements
precisely so the fix cannot be shaped to the easy half.

⚠ **Row counts cannot test this.** The primary-absent case returns zero repos
pre- and post-fix. The tests patch `json.load` and assert on what was OPENED.

⚠⚠ **The tuple was hand-copied to three places and the copy that mattered was
NEVER WRITTEN — that absence IS #121.** `delete_index` had one,
`_leftover_artifacts` had another, `list_repos` had none. Now one
`INDEX_OWNED_SIDECAR_SUFFIXES` in `storage/doc_store.py`, read by all three,
with a test that derives each suffix from the module that WRITES it (each
`_path`/`_terms_path` called with a sentinel name) and fails if one is missing.
**Same shape as jdoc#116's `_index_to_dict` allow-list**: a convention held in
three copies is a convention with a hole in it.

⚠ **Repo names may contain dots** — `is_safe_path_component` allows
`[A-Za-z0-9._-]` — so a repo named `api.related` writes its PRIMARY monolith to
`api.related.json`, and a bare suffix test would have silently unlisted it. A
sidecar-looking candidate is readmitted when it has its own `.summary.json`
(every index saved since jdoc#77 writes one; nothing writes a summary beside a
real sidecar). One `stat`, zero parses. ⚠ A **pre-jdoc#77** index whose name
ends in a sidecar suffix has no summary to vouch for it and is not listed —
recorded, not solved: distinguishing it from an orphan requires opening the
file, which is the cost being removed.

⚠ One test asserts each suffix **ALONE**, no siblings. A filter reasoning from
the sidecar SET (`.related` is a sidecar because `.terms` sits beside it) passes
every other test here and then parses the 1.24 GB file once its peers are
cleaned up by hand.

Local A/B on a **synthetic** 40-index store (23.1 MB, 22.8 MB of it
`.related.json`) — the reporter's fixture is private and was NOT re-run:
json loads **200 → 40**, median **188.9 ms → 8.0 ms** over 5 runs. ⚠ The ratio
is a property of that store's sidecar bytes; the LOAD COUNTS are the durable
claim, the milliseconds are an illustration.

Tests `tests/test_jdoc_121_list_repos_sidecars.py` (13). ⚠ The file cannot
IMPORT pre-fix, so non-vacuity used a behaviour-only subset: **9 fail / 1 pass**
(the `_`-prefix control). Suite **2582 / 6**; `ruff check src/` clean. No tool,
schema or INDEX_VERSION change.

## v1.134.1 — ⚠⚠ the build reads the WORKING TREE, and a shared checkout is not release-safe

Provenance repair, no behavior change. 1.134.0 was built in the main checkout
while a **concurrent session** held uncommitted `description=` edits there, so
`python -m build` put them in the wheel and the published package did not match
its own tag. Caught AFTER the upload, by the post-publish credential sweep
noticing an unexpected `M src/jdocmunch_mcp/server.py`.

⚠ **Severity was measured, not assumed**: every shipped module was diffed against
`v1.134.0` — only `server.py` differed, 12 `description=` strings, zero
non-description added lines, no schema and no code. So the artifact was
functionally the tag.

⚠⚠ **The remedy is FORWARD, and it carries the descriptions rather than
reverting them.** PyPI cannot be re-uploaded. Building 1.134.1 from the tag would
have REVOKED descriptions users installing 1.134.0 already received — trading a
provenance defect for a silent downgrade. So the edits were committed with
credit and 1.134.1 is **byte-equal to the shipped 1.134.0** plus the bump. jjg's
call, and the right one.

⚠⚠ **The standing remedy already existed and I did not apply it**: build from a
dedicated `git worktree`. It is in the release skill, it is
[[feedback_build_reads_the_working_tree_not_head]], and jcm hit the neighbouring
form of it twice (.278 nearly published .277 because `server.json` was stale in
the publish directory). **Committing by name protects HISTORY, not the ARTIFACT.**
⚠ `git worktree add` needs `-b` here — `master` is already checked out in the
main tree, so a bare `worktree add <path> master` fails.

## v1.134.0 — #120: the installed watcher runs the flags you chose

`watch --no-ai-summaries` worked; `watch-install` wrapped the same daemon and
could not pass it through (`subparsers.add_parser("watch-install")` took no
arguments, `_exec_cmd()` was a constant). A corpus indexed without summaries
regained them on the watcher's first refresh, silently. `watch-install` now
takes `watch`'s flags under the same spellings and threads them through
`install_service(watch_args)` into all three platform installers.

⚠⚠ **The asymmetry is the reason this bit, and it is worth carrying forward.**
#116 made `index-local`'s exclusions DURABLE (`corpus_shape_patterns`, inherited
by a silent re-entry point); `use_ai_summaries` is a per-call argument with no
manifest field. **A setting that persists and a setting that does not will
diverge at the first background caller** — and the watcher is a background
caller by construction. Check both halves when adding a per-call knob.

⚠ **The hand-edit is STILL reverted, on purpose.** Every installer rewrites its
whole definition; merging a user's argv with a generated one makes the installed
command unpredictable. What changed is that the rewrite REPORTS what it replaced
(`replaced_exec` + a stderr warning). The reporter filed the revert and the
silence as one issue; only the silence was fixable without making the argv
unknowable.

⚠⚠ **The first cut of the comparison was WRONG and a control caught it.**
Reading `ExecStart` back with `shlex.split` (POSIX mode) eats backslashes, so an
interpreter path containing one never round-tripped and EVERY re-install reported
a customisation nobody had made. The comparison target is a string this module
generated — so it is compared as a string, no quoting convention in the middle.
Same reasoning already applied to the `schtasks` reading. ⚠ Every reader fails
QUIET (localised `schtasks` labels, unreadable plist, absent unit → None): a
false "we overwrote your customisation" is worse than no warning.

⚠ README's "Background behavior, fully disclosed" gained the LOGIN SERVICE
itself — it previously documented only the embedding child process. Per the
PyPI-quarantine standing rule, that section is the compliance surface and the
login service is exactly the behavior the quarantine was about.

Tests `tests/test_jdoc_120_watch_install_flags.py` (30; **22 fail pre-fix**).
Suite **2569 / 6**; `ruff check src/` clean.

## v1.133.0 — #118 CLOSED: the import leaves the server, and the choice goes with it

⚠⚠ **This is the exit #118 named for itself, and it is ON BY DEFAULT.**
sentence-transformers runs in a child process, so the import is concurrent with
the live server AND alone in its own loader. **Backgrounding becomes safe again
because the concurrency leaves the loader that deadlocks** — the only way out of
v1.132.0's structural collision (main thread ⇒ slow handshake, background thread
⇒ possible indefinite hang).

⚠⚠ **THE DEFAULT IS THE FIX, and shipping it opt-in was a mistake caught in
review.** The first cut of this was `JDOCMUNCH_EMBED_WORKER=1`, off by default —
i.e. **a third switch on a pile of two, leaving the user picking between
outages, which is exactly why #118 stayed open after v1.132.0.** A fix the
reporter has to opt into has not resolved their issue; it has documented it.
`JDOCMUNCH_EMBED_WORKER=0` opts out and `JDOCMUNCH_PRELOAD_EMBEDDINGS=1` still
selects v1.132.0's main-thread import, so nobody loses a choice they made — but
nobody has to make one.

⚠⚠ **Defaulting it required the spawn-failure fallback FIRST.** A child that
cannot be spawned (odd `sys.executable`, frozen bundle, locked-down sandbox)
would otherwise silently remove semantic search from machines where it works
today — **a NEW defect traded for #118's**, which is not a trade to make on a
user's behalf. ⚠ A child that dies LATER does not fall back: by then the machine
has proven it can spawn, and importing the stack into a running multi-threaded
server would trade a degraded feature for the deadlock.

⚠ **One method crosses the boundary**: `embed_texts`. Provider detection, the
HF-cache probe, cache keys, the sidecar, identity headers and rotation detection
all need no import and stay put. ⚠⚠ **numpy STAYS in the server** — `doc_store`
and `related_persist` use it to SCORE, and a section matrix down a pipe per query
is absurd; `preload_native_deps()` still covers them and is unchanged. End state:
the server loads numpy and nothing else native.

⚠⚠ **The identity header is deliberately NOT given the dim the child reports.**
`_provider_identity` returns `None` for sentence-transformers and the cache reads
that as a wildcard; filling it in would fail `identity_matches` on every sidecar
written before this change — **jdoc#109's corpus-wide re-embed, triggered by a
refactor instead of a rotation.** Pinned by a test.

⚠ **A timeout is what makes this a fix and not a relocation.** A thread wedged in
`LdrLoadDll` is a kernel-mode wait that no timeout, thread-kill or try/except can
touch; a process is killable. Timeouts RAISE rather than returning empty vectors —
`embed_sections` reads an exception as `embed_failed` and preserves the sidecar,
while empty vectors read as "this corpus has none" (jdoc#107/#109's shape).
Requests are chunked at 256 because `embed_sections` hands over every cache miss
in ONE call, so an unchunked request has no timeout that is both survivable and
meaningful.

⚠ **The guard that can close #118 without reproducing the race:** after a real
embed, the parent's `sys.modules` holds none of sentence_transformers / transformers
/ torch / scipy / sklearn. That proves the deadlock **unreachable** rather than
observing it did not fire this time. Detector proven non-vacuous against a probe
that imports sklearn.

⚠ `test_subprocess_stdin_guard` FAILED on `stdin=PIPE` and its own docstring named
the remedy — an explicit `_INTENTIONAL_STDIN_PIPE` entry with a reason, never a
loosened rule, plus a test that the exempted module really does pass PIPE.

Measured here, healthy install, delta not absolute (jdoc#114): `initialize` with
the worker is **inside run-to-run noise of the in-process default** (+0.04 s over
3 runs) vs +**5.6 s** for 1.132.0's `JDOCMUNCH_PRELOAD_EMBEDDINGS`. ⚠ The cost is
a SPAWN, not an import, so it does not grow on a healthy install.
1000 sections **1.72 s** through the pipe vs **1.64 s** in-process (base64 float32,
~5%). Real 384-dim vector end to end in 8.62 s cold. ⚠ **The wedge itself is still
not reproducible on this box**, which is exactly why the `sys.modules` guard exists.
⚠ This adds a child process, so it is README-disclosed under the PyPI-quarantine
rule — new "Background behavior, fully disclosed" section, written BEFORE release.
⚠ **Four version pin sites, and the fourth is `.claude-plugin/plugin.json`** — the
suite caught it, which is the only reason it is not still at 1.132.0.
Suite **2539 / 6**.

## v1.132.0 — #118: the loader deadlock has a stack, and the fix has a price tag

**A subprocess probe cannot fix a deadlock.** `py-spy dump --native` on a wedged
server, twice 25 s apart, identical: `ZwWaitForAlertByThreadId` /
`RtlEnterCriticalSection` / `<libopenblas64_ DllMain>` / `LdrLoadDll` /
`LoadLibraryExW`. The Windows **loader lock**, not the GIL and not CPython's
import lock — both candidates in the report are ruled out, and "both threads
idle" is explained (a thread parked in `ZwWaitForAlertByThreadId` samples idle).
Second blocked thread, Python-visible: `threading.Thread.start()` from
`subprocess.communicate` in `local_git_head`, because a new thread needs the
same lock for `DLL_THREAD_ATTACH`. Reproduced **7 runs in 8**; an **idle server
never wedges** — no second party, no deadlock.

⚠ **NOT the OpenBLAS thread pool**, the first explanation offered.
`OPENBLAS_NUM_THREADS=1` measured against that reproduction and it **still
wedged**; at one thread the pool is never spawned. ⚠⚠ **The probe answers
"would this import RAISE?", and the deadlock is not a raise** — a probe
subprocess is single-threaded, so on a healthy install it returns True and
`warmup` then imports sentence-transformers **on the warmup thread** beside the
live server, which IS the wedge condition. It rescued this machine only because
sentence-transformers genuinely raises here (`HybridCache`, transformers 5.12 /
ST 5.5.1). Kept on its own merits. ⚠ **Preloading numpy alone is also
insufficient** — the chain loads scipy/sklearn/torch and the issue's FIRST dump
is wedged in `scipy/sparse/linalg/_svdp.py`, a different DLL.

⚠⚠ **The real remedy is OFF by default because it collides with #110 and the
collision is STRUCTURAL**: the import is either on the main thread (slow
handshake) or on a background thread (possible indefinite hang). Windows nearly
took the cost by default on two readings, 6.5 s and 11.4 s vs a ~1.0 s baseline
— then a **cold** run of the same import took **73.77 s** vs 5.71/5.51/5.53 s
warm. torch is GBs of DLLs; the slow end of a 13x spread lands on the first
start after a boot, past a 30 s connect timeout, i.e. **#110's outage verbatim**.
That swaps a probabilistic hang for a fairly reliable cold-start failure, so the
switch goes to whoever knows which they have. Wedged users set
`JDOCMUNCH_PRELOAD_EMBEDDINGS=1`. Default handshake **1.13 s**, opt-in **6.91 s**.
⚠ torch/scipy/sklearn are **not new cost** — declared ST deps `warmup` always
loaded (all five reach `sys.modules` even on the failing import); the work only
MOVED.

⚠⚠ **VERIFICATION: mechanism measured, remedy NOT demonstrated. There is no
A/B.** The wedge stopped reproducing after ~30 server starts in **both** arms —
the same cold/warm effect the 73.77 s reading later made concrete. An earlier
attempt was invalidated outright when a **concurrent editing session** changed
`provider.py` mid-experiment, so every later trial in both arms already carried
the other fix ([[ab-test-invalidated-by-concurrent-session]]). **Do not read a
green run as proof.** ⚠ Four warmup tests fixed: they shelled out to the real
sentence-transformers and so asserted a property of the developer's
site-packages. `test_startup_warmup_gate.py` got an **autouse** stub, not three
targeted ones — with the probe answering False, `warmup()` returns before the
cache gate, so neighbours still PASSED having exercised nothing, and the vacuity
was the worse half. #110's guard is unchanged by default, with a note on why
#118 must not quietly relax it, plus a banner-asserting opt-in test (a ceiling
in seconds is the runner-speed assertion #114 warned about).
`tests/test_preload.py` (32) + `tests/test_jdoc_118_import_probe.py`. Suite
**2495 passed / 6 skipped**; `ruff check src/` clean.

## v1.131.1 — #119: a lexical corpus stops paying for numpy

⚠⚠ **Found by RUNNING 1.131.0, not by reviewing it.** `_semantic_edges_matrix`
imported numpy as its FIRST statement, then returned `{}` a few lines later
whenever no section carried an embedding. Pure cost inside a function guaranteed
to produce an empty map — and **1.131.0 made it a PER-REFRESH cost**, because
putting the sidecar rebuild on the incremental path (#117) also put this import
on the path a watch/refresh loop takes every time. Previously only a full
re-index paid it. **Fixing #117 without this trades unbounded staleness for a
recurring import.**

Early-out now runs first. ⚠ The reorder swaps which sentinel a numpy-less
lexical corpus gets (`None` → `{}`) and that is asserted, not argued: `build`
maps an absent id to `[]` for BOTH, so output is identical. Both paths pinned.

⚠⚠ **On the machine where it was found this is a WORKAROUND, not the fix.**
There `import numpy` inside the running server does not run slowly, it **WEDGES**
— same C-extension frame (`numpy/core/overrides.py:8`) across dumps 30 and 50
min apart, while the identical import is **0.10 s** standalone, 0.10 s on a
worker thread, and 0.10 s with the #110 fd swap replayed. That is
[#118](https://github.com/jgravelle/jdocmunch-mcp/issues/118), UNEXPLAINED. This
release only stops the lexical path from REACHING the import.

Tests `tests/test_jdoc_119_no_numpy_when_lexical.py` (6; **4 fail pre-fix**, 2
controls both sides). Suite **2439 / 6**; CI-equiv **2436 / 9**; 11/11 CI green
at `0aaec69`. PyPI + tag + release + registry (1.131.1, `isLatest: true`).

## v1.131.0 — #117: the sidecars refresh on every path, and say when they don't

All four derived sidecars (glossary / related-graph / boilerplate / dedup) were
written on the **full-index path ONLY**. The incremental path returns before
reaching them, so an index kept alive by incremental refreshes served sidecars
from whenever the last FULL index ran — `get_related_sections` answering off an
arbitrarily old corpus, unbounded, and silent because the write was never
ATTEMPTED. Found in-house: this repo's own memory-store index had four sidecars
three days older than the `.json` beside them.

⚠⚠ **The naive fix is a SILENT WIPE and is strictly worse than the staleness.**
Persisted section dicts carry NO body text (`Section.to_dict` drops `content`;
search re-reads it by byte range at query time). Rebuilding from them hands all
four builders empty strings — glossary empties, boilerplate and dedup find
nothing — **and every one of them reports success.** `_sidecar_view(...,
content_for=...)` hydrates first: the store's byte-range loader for untouched
docs, shadowed by THIS run's in-memory text for the files it just changed
(their new bytes are not on disk under the old offsets yet).

⚠ **The mtime check is NOT the regression test** — an empty rebuild passes it.
The test that pins this asserts a term from an **UNTOUCHED** document survives
an incremental refresh, which is only possible if the body was re-read.

⚠ Three of the four sat behind a bare `except Exception: pass`, so a genuine
failure was indistinguishable from a clean result. **#103 already made exactly
this argument for the dedup sidecar and it was never generalised** — the other
three kept the silence for four more months. New `sidecars_skipped` block on
both paths. `dedup_skipped` is RETAINED beside it: 1.x forbids removing a
shipping response key.

⚠ Fixtures are 12 documents ON PURPOSE. Under ~5 the incremental path
re-materializes everything and the defect hides — the jdoc#107 lesson.

Tests `tests/test_jdoc_117_sidecar_refresh.py` (10). ⚠ The file cannot IMPORT
pre-fix, so non-vacuity used a behaviour-only subset: **2 fail / 2 pass**.
Suite **2433 / 6**; CI-equivalent **2430 / 9**; 11/11 CI jobs green at `3935c35`.

⚠⚠ **The issue title was MINE and it was WRONG.** #117 was filed claiming
`index_local` takes 40-70 min on a 253-file corpus. Measured in-process on the
same corpus, arguments and index: **3 seconds** (6.4 s cold, 0.7 s warm). The
stall is real but lives in the **MCP transport path**, not in this tool.
**A 40-minute wall-clock through MCP is not evidence about the tool body** —
measure the function directly before attributing the cost to it. The O(N^2)
suspicion from #14/#62 was also dead: both fixes are present and working.
#117 stays OPEN for the transport half, which is not this repo's bug.

## v1.130.0 — #116 + #115: a corpus exclusion survives every re-entry point



**#116** (@pnm-jgb): the `index-local` CLI could not express
`extra_ignore_patterns`, so its call computed a `full` selection that
**OVERWROTE** the stored `full+shape:<hash>` and re-admitted every excluded
file. Third and last member of the #108 set, and worse than the two fixed there:
the CLI did not merely fail to EXPRESS the setting, it DESTROYED one already
persisted.

⚠⚠ **The reported remedy would have been WORSE on its own, and this is the part
to remember.** "Preserve the stored selection" was the right target, but only
the DIGEST was persisted, never the patterns: `corpus_selection` records THAT a
corpus was shaped and never HOW. An inherited descriptor would therefore assert
an exclusion the walk could not reapply — an index claiming `full+shape:...`
while containing the excluded file. The pre-fix behaviour at least DISCLOSED the
widening. **Persisting the patterns is what makes inheritance honest**, so it is
part 1: `corpus_shape_patterns` through all five persistence paths.

⚠⚠ **`None` and `[]` are DIFFERENT and that distinction IS the fix.** None
("said nothing" — the CLI, a watch refresh, every silent re-entry point)
INHERITS. `[]` ("explicitly none") widens WITH disclosure. Resolved BEFORE
discovery, because inheritance must change which files the walk visits, not
merely which descriptor is stored.

⚠⚠ **THIS REVERSED A DELIBERATE PRIOR DECISION.** jdoc#82's
`test_changed_ignore_selection_reconciles_and_discloses` asserted that a silent
refresh widens AND discloses — the exact behaviour #116 reports as the bug.
jdoc#82's stated rule is "stored coverage never shifts under an unchanged
identity", and inheritance satisfies it MORE strongly: neither side moves, so
there is nothing to disclose. **The old test pinned one INSTANCE of the rule,
not the rule.** Rewritten to assert the invariant, with a comment block saying
why, so nobody "restores" it without reading the argument.
**Disclosure is not a safeguard when the entry point cannot avoid triggering it.**

⚠ **`_index_to_dict` is an explicit ALLOW-LIST, not `asdict()`.** The new field
round-tripped as EMPTY through the dataclass, `save_index`, `update_index` and
`load` until it was named THERE. That cost a debugging cycle; a test now pins
the serializer specifically. **Any future field-adder hits this.**

⚠ Legacy indexes carry `full+shape:<hash>` with nothing to reapply, so they
still widen — but now WARN that the shape is unrecoverable and name the remedy.

**#115** (@MotoMato85): after full discovery excluded a file via the source
root's `.gitignore`, editing it made `watch` add it.

⚠⚠ **The fix is in `watch.py` and NOT in `index_local`'s `paths=` branch — the
reporter said so before we did, and they were right.** A caller naming a file
explicitly and bypassing `.gitignore` is INTENTIONAL and documented (SPEC.md,
the 1.61.0 changelog): a human asking for a specific generated file should get
it. The watcher is not that caller; it manufactures the path list from
filesystem events, so the bypass fires for files nobody asked for. **jcodemunch
splits the same way for `CACHEDIR.TAG`**: explicit paths opt past the rules, the
watcher fast path applies them. `test_caller_supplied_path_still_indexes_an_ignored_file`
guards that contract — if it ever fails, the filter leaked out of `watch.py`.

⚠ **The two fixes INTERLOCK and neither issue could see it.** Once #116 made
patterns durable, a watcher ignoring them would reinstate pattern-excluded files
— #115's defect in a different costume. The watcher applies BOTH the source
root's `.gitignore` and the stored `corpus_shape_patterns`.

⚠ A batch of only-ignored edits is dropped BEFORE `index_local`, so the log
cannot report "re-indexed 1 file(s)" for work that did not happen.

Tests: `test_jdoc_116_corpus_shape_inheritance.py` (10; **7 fail pre-fix**) and
`test_jdoc_115_watch_respects_gitignore.py` (6; **4 fail pre-fix**). ⚠ Of #116's
3 both-side passes, `test_clearing_is_durable` passes pre-fix for the WRONG
reason (everything widened back then) — a regression guard, not evidence.

## v1.129.0 — #110 CLOSED: JSON-RPC owns a PRIVATE stdout (fd swap)

⚠⚠ **`redirect_stdout` was never enough and this is why.** It rebinds
`sys.stdout` ONLY — it cannot catch a C extension calling `write(1, ...)`
(tqdm/tokenizers/torch), a subprocess that inherited fd 1, or another thread.
Those are exactly what a model download emits, which is why warmup HAD to
finish before the transport existed, which is what pinned provider init to the
startup path at ~7.6s.

`stdio_guard.claim_stdout()`: `os.dup(1)` → give the duplicate to
`stdio_server(stdout=...)` (the transport already accepts it) →
`os.dup2(stderr_fd, 1)`. After that fd 1 **IS** stderr process-wide. Warmup then
runs in a **daemon thread**. Measured provider cost: **+7047ms → -94ms**.

⚠ `_get_provider` is now LOCK-GUARDED — the warmup thread and an early tool
call would otherwise both construct, i.e. two simultaneous model loads.
⚠ **Do NOT delete the jdoc#19 / jdoc#65 guards.** They are belt-and-braces now;
removing them in the same pass turns a safety win into an incident.
⚠ Fails OPEN (pythonw / replaced stderr) and says so on stderr.
⚠ Warmup still declines an UNCACHED model — backgrounding is not a licence to
download hundreds of MB unasked at every start.
⚠ Tests go through REAL SUBPROCESSES; an in-process test of an fd swap tests
the mock. Handshake test asserts the DELTA, not an absolute time ([[jdoc#114]]).

## v1.128.0 — tracker to ZERO: #108, #110, #112, #114

**#112** `openai-compatible` summarizer (`JDOCMUNCH_SUMMARIZER_URL`+`_MODEL`).
⚠ A configured local target **outranks every cloud key** in auto-detect, and is
deliberately NOT in `_PAID_CLOUD_PROVIDERS` — an explicit URL+model cannot be
reached by a stray ambient key, so configuring it IS the opt-in (embedding-side
precedent). Explicit `none` and explicitly-named cloud still win.

**#108** `index-local --no-ai-summaries` / `--embeddings auto|on|off`.

**#110** ⚠⚠ **The report asked for background/lazy init and that is NOT what
shipped.** Warmup exists so the model load finishes BEFORE `stdio_server` owns
stdout; `redirect_stdout` is **process-global**, so a load racing JSON-RPC
cannot be redirected and its chatter corrupts framing for EVERY request.
**Skipping is safe, backgrounding is not.** So: skip warmup when the model is
not in the HF cache (kills the 30s-connect-timeout outage), `JDOCMUNCH_EMBED_WARMUP=0`
to opt out of the rest. The cached ~7.6s remains ON PURPOSE.
⚠ Two probe bugs caught pre-release: a **bare name is not the cache key**
(`all-MiniLM-L6-v2` → `models--sentence-transformers--all-MiniLM-L6-v2`, so the
DEFAULT model read as uncached everywhere), and **`os.altsep` is `/` on Windows**
so every org-qualified hub id was probed as a filesystem path — broken on
Windows and nowhere else. Probe fails OPEN.

**#114** `RECORD_LOCK_WAIT_SECONDS` named in `doc_store.py` and imported by the
test. ⚠ A test asserted the whole call beat the budget of one step inside it.
**Never restate a timing budget as a literal in a test.**

## v1.127.0 — #109 + #111: a model rotation left the index UNQUERYABLE, reporting success

**#111 rode along** because it is the same hazard one layer down: a change to
the embed-text derivation that the sidecar header cannot see. `JDOCMUNCH_EMBED_CHARS`
(default **1000**, unchanged on purpose) now salts the cache key AND sits in the
header identity, so a cap change escalates and discloses like a model rotation.
⚠ Salting the key ALONE is not enough and the report's "minimum" framing
under-states it: the header still matches, so old entries load and merge and the
sidecar accumulates BOTH derivations — and on an unchanged corpus nothing
reaches the embedder at all.

⚠⚠ **Absence of `embed_chars` means 1000, NOT unknown — in the header AND in
the key.** Every pre-1.127.0 sidecar lacks the field and was built at 1000.
Reading absence as a mismatch would escalate EVERY existing index to a full
re-embed on its next run — a corpus-wide bill for users who changed nothing.
`_LEGACY_EMBED_CHARS` in `embeddings/cache.py` is the header half. The key half
is `_embed_cache_key` returning the UNSALTED `h#pv1` at the default: salting
unconditionally (as the report's sketch does) makes `h#pv1-1000` miss `h#pv1`
and re-embeds the world for byte-identical vectors. **A cache-key format change
is a migration, not a refactor** — check what is already on disk before
changing one.

The 41.2% figure is @pnm-jgb's measurement over 1,992 sections, not ours.


⚠⚠ **The reported line was not the one that fired.** @pnm-jgb's analysis put
the bug at the incremental path's `embed_sections` call (`entries` ends up
empty, the `if entries:` guard skips the write). Real mechanism, wrong branch:
with **zero changed files** `index_local` returns from **"No changes detected"**
further up and never calls `embed_sections` at all. Fixing only the guard would
have shipped with the reporter's own repro still broken. **Verify which branch
a repro takes before fixing the line a reporter cites** — this report was
unusually good and still pointed one branch too low.

Detection therefore sits **before** the incremental branch, keyed on
`cache.identity()`. ⚠ `load()` returned `{}` for both "no sidecar" and
"different model", which is exactly why nothing could act on a rotation; the
None-vs-dict split is the whole point and must not be "simplified" back.

⚠⚠ **The numpy-free path was the worse bug and nobody had filed it.**
`cosine_similarity` zips the two vectors, so a 768-dim query against a 384-dim
vector truncates to the shorter and returns **0.707** — an ordinary-looking
similarity. The numpy path raised `matmul: ... size 768 is different from 384`
and was therefore visible; this one returned confident garbage silently. Note
**numpy is dev-only here** (see the header), so the silent path is what a
plain `pip install jdocmunch-mcp` user actually runs.

Third site, also unfiled: a sidecar can hold **two widths at once** after a
rotation that touched some files, and `np.asarray` on ragged rows raised before
any query was scored. Matrices are now bucketed by width; a query scores only
against the bucket it fits.

⚠ **Degrading quietly would have been the same defect wearing a hat.** A width
mismatch yields lexical results **plus** `_meta.embedding_stale` naming both
dims and the fix. Same reasoning as #113's `skip_counts`: the silence is the
reportable half. `semantic_search` is now on the no-change payload too — it was
on the full-rebuild path only, so absence read as "fine" rather than "never
looked at".

Escalation to a full re-embed is **disclosed** as `embedding_rotation`.

⚠⚠ **PAID providers (`openai`/`gemini`) are NEVER auto-escalated** — they get
`action: "rebuild_required"`, keep their old vectors, and let search degrade +
disclose. `watch.py` calls `index_local` from a **background daemon** on every
file-change batch and prints only "re-indexed N file(s)", so an unattended
service would re-send the whole corpus to a billed third party with the
disclosure reaching nobody. ⚠ Identity can also flip with **no user action**:
an `openai-compatible` endpoint restarted on another model reprobes a new dim.
Gate reuses the EXISTING `JDOCMUNCH_ALLOW_PAID_EMBEDDINGS` — do not mint a
second consent knob. ⚠ The disclosure is attached to **all three** payloads
(nochange / incremental / full); the gated path returns from the NOCHANGE
branch, which is the one that most needs it.

⚠⚠ **`embed_failed` gates the purge.** Purging on an empty pass is right when
the corpus produced no vectors and is DATA LOSS when `embed_texts` threw: the
sidecar empties, the NEW header lands on top, and the next run sees a matching
identity and never re-embeds — permanent, silent, jdoc#107's exact shape. My
own #109 purge introduced it; found while reviewing the paid-provider question,
not by a test. **A "purge stale state" branch needs to know the difference
between "nothing to write" and "the write failed."**

⚠ The dollar cost is NOT the argument and I overstated it twice before
checking: ~457k tokens ≈ **one cent** on text-embedding-3-small. The argument
is unattended spend and undisclosed egress.

⚠ The detector reads the provider name from `embeddings.provider`, **not** the
name bound into `tools/index_local` at import. `embed_sections` writes the
header from the provider module's view; reading it anywhere else let the
detector disagree with the writer and report rotations that never happened —
caught by the #107 suite, which went red on a false escalation.

`tests/test_embedding_rotation.py` (24). Non-vacuity proven against the
v1.126.1 tree in a throwaway worktree: 19 fail, and the three end-to-end
indexing tests fail there **on behavior**, not on a missing helper.

## v1.126.1 — #113: dotted dirs skipped by RULE, not by a list of twelve

⚠⚠ **`SKIP_PATTERNS` was a DENYLIST**, so the walk skipped the dotted dirs
someone thought of and descended into every other one. **Not #102** — that was
`lstrip("./")` on a *gitignored* path; here nothing is gitignored, so the dirs
were never pruning candidates. Found in-house: a sibling tool's projection in
`.jmemorymunch/` made a **243-note corpus index as 486 docs**, the second copy a
**lossy condensation ~1/5 the size and frozen mid-day** — so `search_sections`
could answer from a summary with nothing marking it as one. `.claude/` is the
same hazard in a code repo (agent instructions returned as project docs).

`is_skipped_dot_dir()` in `tools/_constants.py`, called by BOTH walkers.
`.github` ALLOWLISTED (skipping it trades one silent omission for another);
`include_dot_dirs` opts back in (names, not paths); pruned dirs counted as
`dot_directory` in `skip_counts` — **the silence was the reportable half**.
⚠ `index_repo` has no `os.walk` to prune, so it checks every leading component.
⚠⚠ **`SKIP_PATTERNS` KEEPS its dotted members on purpose** — removing `.venv/`
/`.git/` looks redundant and regresses callers that match it as a substring.

⚠ Three cases pinned BY TEST because reasoning gets them wrong: **a corpus whose
root is itself dotted** (`~/.claude/projects/<slug>/memory` — matching the
absolute path EMPTIES it and reports success); a dotted dir named in `paths` is
still indexed (a request, not a cache); `.gitignore` is kept (a dotfile FILE is
not a directory). `tests/test_dot_directory_pruning.py` (30). ⚠ The file cannot
IMPORT pre-fix, so non-vacuity was proven with a behaviour-only subset:
**6 fail / 2 pass**, both passes being controls. Suite **2295 / 6 skipped**.

## v1.126.0 — the tuner wasn't slow, it was walking the wrong way

⚠⚠ **Opened by checking a claim WE made to a reporter.** On #106 we said the
tuner "exists precisely to move" the weight, and disclosed only that it was
slow (7 rounds × 50 events). Verifying that produced a worse finding.
**Re-examine the workaround you recommended; it is the claim least likely to
have been tested.**

**`confidence`'s `strength` term reads a RAW top-1 score through a curve
hardcoded to the BM25 scale**, and every mode went through it. BM25 tops out
in the tens; RRF fused tops out at `1/(k+1)` ≈ 0.0164; cosine at 1.0. Measured
on IDENTICAL relative separation: **lexical 0.6205 vs hybrid 0.0872, a 7×
penalty from units alone.**

⚠⚠ **Two consumers, and the tuner is the less important one.**
`build_verdict`'s `low_confidence` REFUSES to mint a citable absence claim — so
hybrid searches were disqualified from evidence they had earned. And
`tune_one_repo` subtracts the two means: delta **−0.53** ⇒ `semantic_hurts` ⇒
step DOWN, **every round, to the 0.10 floor, on data where the semantic channel
was the one answering.** Measured over 7 consecutive rounds pre-fix; 1 round
post-fix (`no_significant_signal`, delta −0.0139).

Fix: each scorer declares its ceiling; `_strength(t, ceiling) = 1-exp(-3t/c)`.
⚠ **The BM25 path is byte-identical** — `1-exp(-3t/12)` IS `1-exp(-t/4)`, so
only the modes that were wrong move, and there is a test on that algebra.
⚠ An unknown mode falls back to the BM25 ceiling, so an un-updated caller is
unchanged rather than newly wrong.

⚠ **`_meta.confidence` moves UP for semantic modes, and some `low_confidence`
verdicts with it.** Wire-visible; disclosed in the release notes. No existing
test pinned a hybrid confidence value, which is itself the gap that let this
live.

**Reachability proper**, all three blockers:
- **Proportional step**, 0.05 at the threshold ramping to a bounded `MAX_STEP`
  0.20. ⚠ Still bounded on purpose — the ledger gives us the SIGN; the
  magnitude of a confidence delta is not a calibrated distance to the optimum.
- **`no_signal_split` names the remedy.** It is the COMMON outcome, not an edge
  case: the tuner compares modes, so a single-mode workload never produces a
  signal however long it runs.
- **New `tune_weights(set_weight=...)`** persists a measured value. ⚠ It
  deliberately does NOT require telemetry — that gate exists because there is
  nothing to LEARN from without a ledger, and writing down a measured number
  needs no ledger. Clamped, and says when it clamped.

Tests `tests/test_tuner_reachability.py` (20; **18 fail pre-fix**). Suite
**2265 passed / 6 skipped**. `ruff check src/` clean. Replay unchanged at
0.9631 (⚠ the `stale pages` 0.631 is PRE-EXISTING — re-run the fixture on
stashed HEAD before blaming your own prose).

## v1.125.0 — #107: a partial embed pass was destroying the vector store

⚠⚠ **Not a cache. Since jdoc#75 the sidecar IS the vector store** —
`_index_to_dict` strips `embedding` from the monolith and
`_rehydrate_embeddings` reads it back from `<name>.embeddings.jsonl`. @faxik
filed this as a cache bug, following **our own naming**, and it is data loss.
**Their severity read was conservative for the fourth time; verify UPWARD.**

`embed_sections` ended with `cache.write`, documented as an atomic REWRITE of
the whole sidecar. True on a full index, where `sections` is the corpus. On an
incremental refresh `sections` is only the changed documents, so **every vector
belonging to an untouched document was dropped**. Reported three times on one
5,300-section corpus: **5,316 vectors → 21**, then 224, then 48. Exit 0, no
warning, `search_sections` still answering — with a semantic channel ranking
almost nothing. With a 30-minute reindex cron that lands within half an hour of
any edit.

⚠ **It does not reproduce on a toy corpus.** Under ~5 documents the incremental
path re-materializes everything anyway, so the rewrite happens to contain the
corpus and looks correct. The reporter says they failed to reproduce it at that
scale first; **so would we have.** Fixtures are sized so the incremental pass
genuinely skips.

**Two more sites in the same family, neither filed, both found by asking who
else writes this sidecar:**

- ⚠⚠ **`index_file` passed NO owner/name**, so the cache was disabled and its
  vectors reached disk only through the save-time safety net — which returned
  early whenever a sidecar already existed, i.e. always. **Every vector that
  path ever produced was discarded.** That is the PostToolUse auto-reindex
  hook, so it ran on every doc edit.
- **`index_repo` passed no owner/name either**, on both paths: no cache hits
  ever, full re-embed every refresh.

Fixes: `embed_sections(..., prune=False)` merges the identity-matched `cached`
forward; **`prune=True` is passed ONLY from the two full-corpus call sites**,
where pruning stale vectors is correct. ⚠ Merge is the DEFAULT so an
unconverted caller cannot lose data — there is a test on the default itself.
⚠ Provider rotation still purges, because `cache.load` already returns `{}` on
an identity mismatch and the merge then has nothing to carry.

New `cache.append_entries` for the safety net: appends missing keys and
**never touches an existing header**. ⚠ Stamping the `__inline__` placeholder
over a real provider identity would make the next embed pass see a mismatch and
purge the entire file — the fix's own failure mode.

⚠⚠ **The silence was the other half.** New `tools/_embedding_coverage.py` puts
`embedded_sections` / `embedding_coverage` on every index response and warns
below 50%. **The reporter had already built exactly this externally**, because
we emitted nothing. Counted off sidecar KEYS, never vectors (26k sections cost
no memory). ⚠ Omitted entirely when no sidecar exists — a lexical index must
not grow a `0.0` that reads as a regression.

⚠ Coverage is NOT `section_count`: sections are keyed by CONTENT hash, and
empty `# Title` parents across many files share one. A test asserts the
coverage ratio, not the row count, and says why.

Tests `tests/test_embedding_sidecar_preservation.py` (18; **14 fail pre-fix**),
including the end-to-end `index_local` refresh and a check at the ENTRY POINT
that every section can still rank after it
([[feedback_verify_at_the_users_entry_point]]).

## v1.125.0 — #106: the weight declares where it came from

@faxik measured a 5,315-section Markdown corpus: stock `semantic_weight=0.5`
answered **0/15 paraphrased queries** in the top 5 — **worse than turning
semantic off** (1/15) — while pure cosine over the *same stored vectors* got
5/15. ⚠⚠ **The vectors were never the problem and neither, really, was 0.5.**
The defect is that a first-time embeddings user watches retrieval get WORSE with
nothing in the response indicating a weight is in play, so it reads as broken
embeddings. They filed it explicitly as *not* a defect; it is three.

⚠⚠ **RRF with `k=60` structurally penalises a single-channel win, and our
`reciprocal_rank_fusion` makes it worse than the reporter modelled.** An absent
item contributes **0**, not `1/(60+rank)` — they assumed a deep lexical rank:

```
excellent-in-one:  0.5/(60+1) + 0            = 0.00820   (they wrote 0.00836)
mediocre-in-both:  0.5/(60+50) + 0.5/(60+50) = 0.00909   <- still wins
```

Compounded here because the lexical ranking only holds sections scoring `> 0`
(`doc_store.py`) while `_semantic_scored` returns EVERY embedded section — so on
a paraphrase the right answer is *guaranteed* single-channel. Their keyword
recall was **flat at 93.3% from 0.0 through 0.95**, so the low default buys no
keyword safety on that corpus; it only costs paraphrase recall.

Three fixes, each with a pre-fix-failing test
(`tests/test_semantic_weight_provenance.py`, 22; **17 fail pre-fix, 5 controls
pass both sides**):

- **Provenance.** `_meta.semantic_weight` already shipped — only the VALUE.
  ⚠ Do not tell a reporter their suggestion is unimplemented when half of it is;
  the missing half was `semantic_weight_source`
  (`caller` / `tuning.jsonc` / `default`), plus `semantic_weight_clamped_to`
  when a hand-edit is out of bounds. New `tuning.resolve_semantic_weight`
  returns `{weight, source, clamped}`; `get_semantic_weight` stays as a legacy
  scalar wrapper with its old quirk intact.
- **Ceiling 0.85 → 0.95.** ⚠⚠ **The bound clamped INCONSISTENTLY**: a
  `tuning.jsonc` value was clamped, but an explicit call argument returned
  unclamped — so 0.95 was reachable per-call and impossible to persist, and the
  reporter's 0.95/1.0 rows are valid measurements. 1.0 is the value worth
  excluding (their keyword recall 93.3% → 86.7% only at 1.0).
- **`None` is the sentinel for unset.** It used to be the literal `0.5`, so a
  caller deliberately pinning the documented default was indistinguishable from
  one who omitted the argument and silently lost to the tuner. ⚠ The schema's
  `"default": 0.5` is REMOVED for the same reason — a client that materialises
  schema defaults would make every call look explicit and permanently mute the
  tuner. A test asserts both the schema and the dispatcher line.
  ⚠ `0.0` is falsy but is a real caller value; there is a test.

⚠ **`repo_group` fan-out reports weights PER MEMBER** in `per_repo`, not once —
the tuner is per-repo, so a group has no single number and a fused ranking would
otherwise be unexplainable. Same class as
[[feedback_a_flag_that_fits_one_caller_breaks_on_the_second]].

**Deliberately NOT changed, recorded so nobody "fixes" it by accident:**

- ⚠⚠ **The tuner is the documented escape hatch and is close to unreachable.**
  `tune_one_repo` steps ±0.05, needs `MIN_EVENTS=50` per round, and returns
  `no_signal_split` unless the ledger holds BOTH semantic-used and
  semantic-unused events. 0.5 → 0.85 is **seven successful rounds ≥ 350
  qualifying events with a mode split** — a single-mode workload never gets
  there. Raising the ceiling does not touch this. **Open, undecided.**
- **The default 0.5 and `k=60`.** One corpus. The reporter said so themselves and
  declined to argue for changing the default on their own evidence; matching that
  restraint is correct. Score-based fusion / a smaller semantic `k` is the real
  answer and is not in this change.

⚠ Release suite (both issues) **2245 passed / 6 skipped**, was 2205/6.
`uv run ruff check src/` clean. No INDEX_VERSION or tool-count change.
⚠ `ruff check tests/` is 112 findings and always has been — **CI lints `src/`
only**; do not read that as a regression.

## v1.124.3 — a lint gate, and the NameError it found

CI had no lint job. Adding one immediately surfaced a defect **we shipped in
v1.124.0**: `server.py:2003` logged through a module-level `logger` **this file
has never defined**, inside the `except Exception:` block that exists to swallow
errors — so a real failure of `_all_tools()` raised `NameError` OUT of the
handler. ⚠ `logging` was not imported at module scope either. Four regression
tests force the error path.

⚠ The other two F821s were `OrderedDict` in **string annotations** (never
evaluated at runtime, real local import) — not bugs, but unresolvable for
`get_type_hints`; now under `TYPE_CHECKING`.

⚠⚠ **`select` is EXPLICIT** (`["E4","E7","E9","F"]`). Ruff's DEFAULT set is **not
stable across versions** and this repo has no lock to pin ruff: measured, an
`ignore`-only config under ruff 0.16.2 gave **446 findings** vs a handful
intended. **Widening the set is now a DECISION, not a ruff upgrade.** ⚠ `ruff` is
in the dev group — `uv run ruff` fetching on demand locally is exactly what hides
its absence from CI (jdata shipped a lint job that could not spawn).

⚠ Fixed 14 unused imports, **each verified to have no importers elsewhere**
(an unused import can still be a re-export). Grandfathered WITH counts: `E402`
(65), `F841` (2). ⚠ **Not copied from jcm**: its job runs `uv sync --locked`,
which would FAIL here (lock gitignored); this uses plain `uv sync --group dev`.

⚠⚠ **The lesson is NOT "add a linter".** jcm HAD the check; it failed on FOUR
consecutive releases and nobody read it. [[feedback_a_green_suite_is_not_a_green_build]]

## v1.124.2 — text-mode IO and CLI output declare their encoding

Suite parity with jcm, which swept three directions of the cp1252 hazard.
⚠ **This repo was scanned SEPARATELY** — a defect in one server implies nothing
either way about its siblings. Found here: subprocess input **0** (already closed
by v1.121.1 below), our own output **7 lines**, file IO **4 sites**.

⚠⚠ **We had the QUIET half of jcm's crash.** `cli/init.py` prints `—` and `•`.
Both **are** cp1252-encodable, so nothing ever raised — piped output simply went
out as cp1252 bytes and a UTF-8 consumer got mojibake, silently. jcm's `receipt
--explain` carried U+2212, which cp1252 cannot encode at all, and died outright.
**Same defect, different symptom, and the quiet one is harder to notice.**
`_force_utf8_stdio()` now runs at the top of `main()` so the next character added
does not decide which symptom we get. On Windows `sys.stdout` is the CONSOLE
stream (UTF-8) on a terminal and the LOCALE stream (cp1252) when PIPED, which is
why it works by hand and fails for a script.

⚠ The 4 file-IO sites (savings tracker ×3, `/proc/version` WSL probe) all hold
ASCII-only content today, so **nothing was corrupt** — unlike jcm's
`tuning.jsonc`, which genuinely carried an em-dash. Preventive, and said plainly.

⚠⚠ **The ported scanner needed THREE iterations.** Mode at `args[1]`
false-positived `path.open("rb")`; branching on Name-vs-Attribute then
false-positived `wave.open(f, "rb")` (a module call is attribute-shaped but
builtin-signatured); matching the mode **BY VALUE** made position stop mattering.
Tested in BOTH directions — **a guard with false positives is one nobody
believes, and a ratchet nobody believes collects exemptions.** Non-vacuity floor
sized to THIS tree (100+ of 123 files), not copied from jcm.

⚠⚠ **NEAR-MISS: a `test_lockfile_version_sync.py` port was nearly included.**
`uv.lock` is **gitignored here** (the warning at the top of this file says so),
so the "stale lock" that prompted it is a LOCAL ARTIFACT that cannot drift across
releases, and the ported test would have **FAILED ON A FRESH CLONE** where no
lock exists. **Suite parity is for BEHAVIOUR CONTRACTS, not for whatever the
other repo happens to have in `tests/`.**

Tests: `test_file_io_encoding_guard.py` (39) + `test_cli_output_encoding.py` (10).

## v1.124.1 — an ignored argument must not be able to back an absence claim

Completes #104. ⚠⚠ **v1.124.0 shipped the SMALLER half.** It disclosed ignored
arguments and did not degrade the absence verdict — and jdoc mints citable
`absent:<sha>` refs (v1.117.0), so a call whose scoping argument was silently
dropped could still reach `absent` and be cited as proof the target is not
there. Per #104's own logic that call returns a **wider** result than requested,
which makes "not found" the claim it is least entitled to make.

⚠⚠ **This contract already existed in the other two servers and I did not look.**
jcm shipped it in v1.108.175 (found live: `search_text` passed `regex=true` when
the parameter is `is_regex`); jdata carries the port. **jdoc was the one server
without it, which is exactly why #104 was reportable here and not there.** The
v1.124.0 fix was written from the REPORT instead of from the sibling
implementations, so it reproduced the disclosure and missed the refusal.
**Check the siblings before implementing a suite-relevant fix**; a defect
reportable in only one of three is a parity gap until proven otherwise.

`tools/_arg_contract.py` now exists in all three with the shared note text.
`degrade_absent_verdict` runs BEFORE the absence block reads the verdict, so
`note_absence` refuses to mint rather than minting and retracting — **ordering
is the fix**, and a test asserts the call order in the dispatcher source. Only
the absence CLAIM is refused; `ok`/`degraded`/`low_confidence` are untouched.
Disclosure is now ALSO top-level (`ignored_arguments` + `ignored_arguments_note`,
jdata's shape, chosen for the same reason: this server strips `_meta` by
default); `_meta.ignored_arguments` is RETAINED because v1.124.0 shipped it and
1.x forbids removing a response key. Tests: `test_unknown_arguments.py` 15 -> 23.
Suite **2152 passed / 6 skipped / 0 failed**.

## v1.124.0 — four reported defects (#102/#103/#104/#105), all from fresh reporters

⚠⚠ **#102 `lstrip("./")` takes a character SET, not a prefix**, so
`"./.worktrees/"` became `"worktrees/"` and **every gitignored DOT-directory was
walked and indexed** (`.venv/`, `.tox/`, `.next/`, `.cache/`, `.worktrees/`).
Undotted dirs pruned correctly, which is exactly why it read as working.
Reported: **9,813 docs indexed where ~3,100 exist**, ~48 copies of one corpus;
duplicates also DEGRADE retrieval (stale-branch sections compete with the live
one). Fixed with a shared `_walk_rel`; the per-file fallback was corrupted the
same way. ⚠ **Swept the suite first: 14 occurrences in jcm, 5 here — but jcm's
indexer does NOT share it** (it prunes on the bare dir name). Verified, not
assumed; jcm's are path normalizers, a separate lower-severity class.

⚠⚠ **#103 the dedup sidecar was unbounded AND its skips were silent.**
All-pairs Jaccard, ~O(n^2.3) measured (5,931→7.4s, 12,493→42.2s, 25,329→206.7s);
the length pre-filter is a CONSTANT, not a change of asymptote. The code comment
already said "fine up to a few thousand sections" and was right — **nothing
ENFORCED or SURFACED that ceiling**, and the caller's bare `except: pass` meant
a skip would be silent too. **The silence was the defect; the runtime was its
symptom** — a skipped sidecar was indistinguishable from one that found no
duplicates. Now: ceiling (20k default, `JDOCMUNCH_DEDUP_MAX_SECTIONS`, `0`
disables, garbage→default so a typo cannot uncap it), `enabled` opt-out, and a
`dedup_skipped` block naming count/ceiling/knob. ⚠ MinHash+LSH is the REAL fix
and is deliberately NOT in this release.

⚠ **#104 unknown arguments were silently dropped.** `get_toc{doc_path:...}`
returned the WHOLE-CORPUS TOC (`doc_path` is `get_document_outline`'s param).
⚠⚠ **The direction is the harm**: an agent that means to SCOPE and misnames the
param silently gets a LARGER response. `additionalProperties:false` is forbidden
by the 1.x contract (a previously-accepted call must not start raising), so it
is additive `_meta.ignored_arguments`. ⚠ Built from the **UNFILTERED** catalog (a
tier-hidden tool still has a schema) and attached **AFTER meta_fields
filtering** — the default strips `_meta`, and a warning the default deletes is
no warning at all.

⚠⚠ **#105 `verify_index` verified the CACHED MIRROR while its docstring promised
"its current on-disk content".** Both sides came from the index, so an edited /
truncated / DELETED source still verified CLEAN (reporter proved it with a
SAME-LENGTH modification, ruling out a size check). **The description was wrong,
not the behaviour** — cache verification is a real check (B1/B2 of the v1.10
audit) and flipping the default would silently change what existing CI gates on.
Default kept and now honest; new `source="live"` checks the workspace under
`source_root`; `_meta.verify_layer` names which ran on EVERY call and
`_meta.verifies` says in words that clean is NOT proof the source is current.
⚠ **Live with no `source_root` REFUSES (`no_source_root`) rather than falling
back** — a fallback would answer the cache question under the live label, i.e.
the exact confusion reported. Same discipline as v1.122.0's content tools.
⚠ **OPEN for jjg, recorded not decided: should `live` be the DEFAULT?** v1.122.0
flipped content tools to live-when-available (argues yes); it changes counts for
anyone gating CI on `drift_count == 0` (argues do it deliberately).

Tests: `test_gitignore_dot_directories.py` (20; **10 fail pre-fix**, 10 controls
pass BOTH sides), `test_dedup_ceiling.py` (17), `test_unknown_arguments.py` (15,
incl. a whole-catalog round-trip proving no tool flags its OWN declared args),
`test_verify_index_source_layer.py` (19, the reporter's 4-file fixture).

⚠⚠ **A SECOND blindness in the same tool, found 2026-08-25 by sweeping for jcm
v1.108.298's defect class, SHIPPED IN v1.136.1 (#125)**: the comparison was `if expected_hash and actual !=
expected`, so a section with **no stored hash** fell to the `else` and was
counted **CLEAN**. Unverifiable is not verified, and a caller gating on
`drift_count == 0` reads the two identically. ⚠⚠ **The accounting invariant
`clean+drift+missing+error+skipped == section_count` still held** -- the row was
counted, just misfiled -- so a totals check cannot see this class at all. Now
`skipped` with reason `no_stored_hash`, beside `empty_byte_range`. ⚠ **LATENT,
not live**: every producer goes through `compute_content_hash()` (sha256 of ""
for an empty body, never `""`), but `Section.content_hash` DEFAULTS to `""` and
the text parsers assign it at the END of a loop, so one early return
reintroduces it silently. `TestTheProducerIsCurrentlyClean` pins that premise
and fails if it ever goes live. ⚠ The non-vacuity test EXECUTES the pre-fix
module source; simulating it by patching `hashlib.sha256` broke every section
and passed for the wrong reason.
Suite **2144 passed / 6 skipped / 0 failed**. No INDEX_VERSION change.

## v1.123.0 — offloadable-work annotation, OFF BY DEFAULT

`JMUNCH_OFFLOADABLE=1` (suite) or `JDOCMUNCH_OFFLOADABLE=1` (this server;
narrower scope WINS) makes `get_section`/`get_sections` carry an advisory
`_meta.offloadable` block. ⚠⚠ **We LABEL. We never route, execute, or hold
model credentials** — no process, no network, no new tool, no model of ours
runs. Routers classify the PROMPT because that is all they can see; this sits
downstream of retrieval and classifies THE EVIDENCE JUST ASSEMBLED. Tri-state +
reason-coded, fails closed; `verify_with` names the call that ADJUDICATES a
cheap model's answer.

⚠⚠ **This is WHY v1.122.0 shipped first.** A section whose source cannot be
checked, or that comes back stale, is REFUSED rather than labelled — before the
identity tools disclosed freshness there was nothing to refuse on and every
payload would have gated on `TRI_STATE_UNKNOWN`.

⚠ **Suite contract**: identical in jcm (symbols/files) and jdata
(columns/datasets). `EvidenceShape` speaks *units*/*containers*; a pinned
`CONTRACT_DIGEST` + generated contract test fails the build in any of the three
that drifts. ⚠ **This copy is GENERATED from jcodemunch's module** — never
hand-edit it; edit jcm and re-run the maintainer sync. Additive `_meta` key,
emitted only when gated on; no tool/schema/INDEX_VERSION change. Tests
`tests/test_offload_contract.py` (23).

## v1.122.0 — a content read discloses its freshness, and `fresh` means proven

`get_section`/`get_sections` handed back bytes with NO freshness and NO verdict
— `search_sections` has carried per-section freshness since v1.16.0, the tools
that serve actual content carried none. Now emit `_meta.freshness`,
`_meta.verdict`, `_meta.drift_layer`.

⚠⚠ **Two over-claims INSIDE the probe were fixed first; without them the new
disclosure would have been worse than none.** `_classify` answered `fresh`
having compared NOTHING for (a) a section with no `doc_path` and (b) a file that
exists but is unreadable (`_file_hash` → `(None, True)` on `OSError`) — both
fell through to a closing `return "fresh"`. Both now `unknown`. `summary()`
tallied three buckets and SILENTLY DROPPED anything else, so such a section
vanished and the counts could sum to fewer than the sections described; it now
counts `unknown`, and an absent/unrecognised bucket counts as `unknown`.

⚠⚠ **The DEFAULT probe reads jdoc's CACHED MIRROR, not the workspace.** Wired
naively the new reading said `fresh` for a file that had been EDITED and for one
that had been DELETED — verified at the entry point, which is the only reason it
was caught. Content tools now use the jdoc#71 live-source layer when the index
records a usable `source_root`, and DISCLOSE which layer answered.

⚠ `build_verdict` had the same two-state `"stale" if index_stale else "fresh"`
as jcm's v1.108.240 defect; extracted as `index_channel` with an optional
richer reading (Boolean-only callers unchanged). ⚠ **`stale_index` was missing
from its accepted set on the first pass, so a DELETED source fell through to
`fresh`** — the exact failure the function exists to prevent, reintroduced by
its own membership test.

New `section_verdict_for_index`. ⚠ A batch verdict takes the **WORST** section
reading, never first-or-average: otherwise one stale section rides out under an
`ok` covering the others. Additive `_meta` keys only; no tool/schema/
INDEX_VERSION change. Tests `tests/test_identity_freshness.py` (20). Suite 2063
passed / 6 skipped.

## v1.121.1 — git output is decoded as UTF-8, not as cp1252

In-house, found by an AST sweep across the suite after the same defect was
reproduced in jcm. **9 call sites** carried `text=`/`universal_newlines=` with no
`encoding=` — `tools/_git.py` x2, `service_installer.py` x5, `cli/init.py`,
`scripts/evidence_receipt.py` — all now pass `encoding="utf-8", errors="replace"`.

⚠⚠ **`index-local` FAILED OUTRIGHT for any corpus checked out under a non-ASCII
path** — `{"success": false, "error": "Indexing failed: 'NoneType' object has no
attribute 'strip'"}`. Not a degraded index, no index at all. jcm's version of the
same bug merely degraded silently, so **do not reason about jdoc's blast radius
from jcm's**.

⚠ **The trigger is `git rev-parse --show-toplevel`, which prints the repo path
RAW AND UNQUOTED** — unlike `status`/`ls-files`, whose paths go through
`core.quotepath` and come back ASCII. `_git_root` is the gateway every git-aware
path here goes through. On Windows the usual way to have a non-ASCII character in
your checkout path is your own user name.

⚠ **All three of `_git`'s carefully-separated except clauses were bypassed.**
`UnicodeDecodeError` is raised inside `subprocess`'s **reader thread**, so no
`try/except` around the call catches it; `proc.stdout` returns `None` and the
caller dies later on `.strip()`, naming neither git nor encoding.

⚠⚠ **`local_git_paths_tracked` was ALREADY correct and is untouched** — it uses
`_git_bytes` + an explicit `.decode("utf-8", errors="surrogateescape")`. Someone
recognised this hazard for `ls-files` and did not generalise it. **That is the
exact shape of gap a convention-without-a-test leaves**, and why the fix ships
with `tests/test_subprocess_encoding_guard.py` (12) rather than a habit.

⚠ Only `81 8d 8f 90 9d` are undefined in cp1252 — `0x9f` IS defined (`Ÿ`), so
many non-ASCII bytes produce **silent mojibake** instead of a crash. Never read
"it did not raise" as evidence the decode was right; check the `repr`.

Guard proven non-vacuous BOTH ways: stashing `src/`+`scripts/` fails it naming
all 9 sites, and the detector is parametrized over known-good/known-bad through
the SAME function the repo-wide check uses. `tests/` and `unused/` are **EXEMPT
BY NAME with reasons recorded**, not skipped; `KNOWN_UNENCODED` is an empty
ratchet with its own anti-rot test.

⚠ **Invisible to CI and to any UTF-8 dev box** — CI is Linux. Verified at
`index-local`, not at the function edited. Suite 2020 passed / 6 skipped (was
2008/6; +12 is the guard). No INDEX_VERSION, tool-count or wire-format change.

## v1.121.0 — search_sections projection + snippets (#101, @vondecron)

Three opt-in knobs, default response byte-identical (pinned by a test):
`compact=true`, `fields=[...]` (whitelist, wins over compact, `id` always
survives), `snippet_bytes=N`. **1,989 chars/row → 319 compact (-84%) → 431 with
`snippet_bytes=200` (-78% AND the `get_section` hop is gone)**, measured on this
repo's own docs at `max_results=10`.

⚠ **Projection runs LAST — after every filter, after `attach_scores`, after the
ranking/replay logs and the verdict.** Those consumers read fields compact drops
(`min_byte_length` reads `byte_start`/`byte_end`), so projecting earlier would
silently starve them. There is a test asserting `min_byte_length` still filters
under `compact=True` ([[feedback_strip_a_field_after_its_consumer_reads_it]]).

⚠ **In a `repo_group` fan-out compact KEEPS `repo`** — dead weight on a
single-repo row, and the ONLY thing telling two members' rows apart in a fused
one. That is what `project(..., extra_keep=...)` exists for; the same flag
means different things on the two code paths
([[feedback_a_flag_that_fits_one_caller_breaks_on_the_second]]). Snippets are
produced member-side (they need the member's index to read content); projection
is applied once to the fused list.

⚠ Per-row `_freshness` is dropped **only when it is `fresh`**. An all-fresh set
is what `_meta.freshness` already reports; a single stale row is a signal the
caller needs, so noise-dropping must not become signal-dropping.

`_meta.tokens_saved` now measures the **served** (post-projection) payload —
it previously measured rows that had not yet had `_answerability`/`_quotability`
attached either, so the figure never described what crossed the wire.

**Not adopted:** jcm's interned `#MUNCH/1` wire format, which the reporter
raised as prior art. Changing a tool response's JSON shape is forbidden by the
1.x contract; `compact`/`fields` reaches the same saving additively.

New `retrieval/projection.py`. Tests `tests/test_v1_121_0.py` (20). Suite 2008
passed / 6 skipped. No INDEX_VERSION or tool-count change.

## v1.120.0 SHIPPED: the retirement arc closed, independently verified

@rknighton re-verified QA-15 + QA-17 together at **exactly `132c8e1`** on Linux,
in a clean detached checkout inside an isolated container: **10 passed, 2
skipped, 0 failed**, plus his frozen harness **7/7** at sha256 `88381e18…`,
byte-identical to ours. ⚠ **`test_three_processes_keep_one_lock_inode` EXECUTED
rather than skipping** — the reason Linux was the platform that mattered. His
acceptance criteria PREDATED any implementation, so the gate could not be
reshaped to fit the fix.

⚠⚠ **The fallback disclosure sentence was NOT used and must never be quoted as
if it were.** It would have been false: QA-17 was independently re-verified, so
the notes make the STRONGER claim. **Keeping that distinction honest in the
direction that favored HIM is the whole point of having written it down.**

⚠ **Shipped as 1.120.0, NOT 1.115.0.** That heading stays in CHANGELOG as the
branch's historical record; `pip` resolves to the highest version, so 1.115.0
after 1.119.0 would ship into a version nobody receives. See the label section
below.

⚠ The reconcile that moved the head past his verified SHA was **docs-only**:
`git diff 132c8e1 4122a56 -- src/ tests/` EMPTY, and that empty diff is PUBLISHED
in the release notes so a reader can check it rather than trust us. Same argument
used to accept his pre-rebase evidence on #97.

Suite 1973 passed / 8 skipped local; CI 10/10 + Replay at the merge commit.

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

## v1.119.0 — 5th absence refusal rule: a rebuild underneath a scan cannot prove absence

Suite parity with jcm v1.108.168. v1.117.0's four rules (only `absent`;
not `low_confidence`/`degraded`; not stale; not truncated) had **no rule for an
index being REWRITTEN while the scan reads it**. Index staleness here is
`source_dirty` = the SOURCE moved; it is **blind to a reindex that rewrites
sections under an unchanged tree**, so such a scan reported `index:"fresh"`,
reached `absent`, and minted a citable `absent:<sha>` ref over a half-written
index. **Worse here than in the siblings**: sections score through a lazy
`_content_loader` that reads body text from disk at scan time, so a rebuild
mid-scan can move the very bytes being ranked.

Fix: zero results + detected rewrite ⇒ `degraded`, so the 5th rule **falls out
of the existing "only `absent` proves absence" check** — nothing new to keep in
sync. `absence_refusal` gains a branch BEFORE the generic state check so the
reason names the rebuild. `channels.index` gains **`"rebuilding"`, disclosed on
EVERY state** (an `ok` caller deserves to know the index moved under it); only
the absence CLAIM is refused.

⚠ Detection is a **FILESYSTEM** signal — `DocStore._stamp_load_provenance`
stamps `_index_path` + `_loaded_mtime_ns` at BOTH load return points (cache hit
and cold), `retrieval.verdict.index_changed_since_load` re-stats. **NOT
in-process reindex state**: a separate watcher process drives most rebuilds and
in-process state cannot see it. **Unknown ≠ changed** (unstamped index → False).
⚠ `doc_store.py` has NO module `logger` — the helper builds one locally in its
except (the jcm v1.108.100 NameError-in-except trap).

Files: `storage/doc_store.py`, `retrieval/verdict.py`, `handoff.py`,
`tools/search_sections.py`. Tests `tests/test_v1_119_0.py` (13). NO
tool/schema/INDEX_VERSION change. jdoc publishes no JSON Schema, so unlike jcm
there was no enum to update.

## v1.118.0 - lexical query no longer lowercased before tokenizing (#91 follow-up)
Reported by @tetiz123 while validating the v1.114.1 CJK tokenizer on a real
111-doc / 2,053-section Korean corpus (fix confirmed: no reindex, lexical went
from nothing to their best ranker). Second, unrelated defect found while
measuring. `DocIndex._lexical_search` passed `query.lower()` to the scorer, but
`bm25.tokenize` inserts CamelCase boundaries BEFORE lowercasing, so the query
side and document side disagreed for case-bearing identifiers:
`tokenize("OvertimeService")` -> `['overtime','service']` (doc) vs
`tokenize("overtimeservice")` -> `['overtimeservice']` (query). Every
code-identifier query scored 0.0 and returned a SILENT empty list - silent
because the Stage-A posting prune tokenizes the ORIGINAL query, so candidates
survive the prune then each scores 0 in Stage B. CamelCase + acronym-suffix
(`HCA060T`) hit; underscore names (`SPM_NOTIFICATION`) unaffected (delimiter is
case-independent). **Fix:** pass the raw query to `_score_section` ->
`bm25.score_section`; `tokenize` lowercases internally after de-camel, so it is
correct and free. `_score_section`'s first param renamed `query_lower` ->
`query` (both call sites in `_lexical_search` + the hybrid lexical leg updated);
`query_words` (tag kicker) stays the lowercased set (tags matched case-folded).
Consumer-layer, NO reindex (`tokenize` runs on stored content at scoring time).
Tests `tests/test_v1_118_0.py` (7: root-cause asymmetry + e2e CamelCase/acronym/
repository identifiers + underscore control + lowercase-prose control); suite
1831. Additive/1.x, no INDEX_VERSION or tool-count change. **Shipped from MASTER
as a patch while `coordinated-retirement` (1.115.0) stays HELD; on merge resolve
versions up and keep all CHANGELOG entries.**

## v1.117.0 - absence evidence (handoff/v2 phase 3, suite parity)
Suite parity with jcm v1.108.166 (jcodemunch-mcp#377 phase 3, design by
@mightydanp). A ZERO-RESULT section search is now citable proof. v1/v2 could
not cite it (nothing served, no id), yet "searched the complete/fresh/
non-truncated index and it is NOT there" is the claim audits most need.
`build_verdict` already emits state/scanned/channels/coverage/scorer;
`handoff.note_absence` records those under a deterministic ref. An `absent`
verdict surfaces a citable ref. **jdoc-specific carrier:** its default
`meta_fields` STRIPS `_meta`, so the ref rides in `_meta.absence_evidence`,
re-attached AFTER filtering (the v1.104.0 budget lesson) - a token the default
config deletes is one the agent can never cite. **Refusal rules (his, verbatim):**
only `absent` proves absence; `low_confidence`/`degraded` do NOT; stale index
does NOT; truncated index does NOT. Refused scans STILL recorded so citing
returns the REASON (`refused_absence` / `refused_absence_claims`), not a bare
unknown-ref; absent-but-not-citable -> `_meta.absence_evidence.citable:false` +
`blocked_by`. Rendered proof carries tool+query, SCOPE, sections/documents
scanned, channels, coverage w/ exclusion counts, scorer; unknown coverage
disclosed as unknown NEVER as complete; detail renders ONCE. Ref = sha256[:12]
over `(tool, repo, query, scope)`; jdoc `_SCOPE_ARGS` = doc_path/path_glob/role/
tag/repo_group/lang. Session-scoped, in-memory, capped. Receipt gains
`absence_attested`. Additive/1.x, NO INDEX_VERSION/tool-count change. Tests
`tests/test_v1_117_0.py` (23, one per refusal rule); suite 1824.
**Shipped from MASTER while `coordinated-retirement` (1.115.0) stays HELD;
1.115.0 SKIPPED so the held branch keeps it; on merge resolve versions up + keep
all CHANGELOG entries.**

## v1.116.0 - claim-scoped evidence (handoff/v2 phase 1, suite parity)
Suite parity with jcm v1.108.165 / jdata v1.25.0 (jcodemunch-mcp#377 phase 1,
design by @mightydanp). A handoff section may now carry caller-authored
`claims`, each with its OWN `evidence_refs`. v1 proved a ref was retrieved
this session but never bound it to a sentence - refs landed in ONE global
block at the end of the body. New `_validate_claims` takes
`{id, statement, evidence_refs, classification?}`; **ids unique across the
WHOLE handoff, not per section** (the id is the citation anchor - two sections
owning one id makes a citation ambiguous); statements/classifications
preserved VERBATIM (server never authors); each claim's refs attested
SEPARATELY through the unchanged `_validate_evidence`, so an unknown ref
returns `invalid_claims: [{claim_id, unknown_refs}]` naming the claim instead
of one global failure list. `render_handoff` prints `### <statement>` +
`- Claim id:` + indented evidence, and takes the schema string as a param.
**Three calls carried from jcm:** (1) the INPUT picks the contract - no claims
anywhere means the schema stays `jdocmunch.handoff/v1`, body BYTE-IDENTICAL to
v1, `claims_attested` omitted (not `0`); any claim promotes to `.../v2`.
(2) claims can satisfy `evidence_refs` (top-level may be empty when claims
carry refs - strictly more permissive, no existing call changes). (3) claim
refs join the canonical index, caller order first, so a v1 consumer reading a
v2 handoff sees every ref where it expects. Section `content` optional ONLY
when claims present. Additive/1.x, no INDEX_VERSION or tool-count change.
Tests `tests/test_v1_116_0.py` (18, incl. the byte-identical-v1 guard); suite 1801.
WARNING **Known limit, disclosed on #377 first:** phase 1 does NOT narrow what
counts as a match - the doc-path broadening in `_validate_evidence` means
citing a whole document still attests when one unrelated section from it was
served. That is phase 2 (evidence receipts), DEFERRED.
**Shipped from MASTER as a patch (like 1.114.1 / 1.114.2) while
`coordinated-retirement` (CHANGELOG entry `[1.115.0]`) stays HELD for
rknighton's re-verification. 1.115.0 deliberately SKIPPED on master so the held
branch keeps that CHANGELOG heading; on merge, resolve version conflicts to the
higher number and keep all CHANGELOG entries.** ⚠ **The shipped version is
1.120.0+, NOT 1.115.0 — see the label section below.**

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
Versions 1.115.0 and earlier: see `docs/CLAUDE-history.md` (moved out of this file
2026-07-25). `CHANGELOG.md` covers most of them, but 1.67.0-1.92.0 and 1.96.0 exist
ONLY in the history file.

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
