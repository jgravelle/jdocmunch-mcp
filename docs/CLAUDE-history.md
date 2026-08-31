# jdocmunch-mcp — release history (moved out of CLAUDE.md)

Older per-version notes, moved here 2026-07-25 so they no longer load into every
session under this directory. Versions 1.67.0-1.92.0 and 1.96.0 appear ONLY here,
not in CHANGELOG.md.

## Rotated 2026-08-29 — v1.116.0 through v1.135.0

Moved out of `CLAUDE.md` on 2026-08-29 because 31 dated sections had grown to
**74.7% of the brief** (91,699 of 122,832 chars) against a 130,000 budget. The
three newest sections stay in `CLAUDE.md`; everything older lives here,
VERBATIM, newest-first.

⚠⚠ **What each entry EARNED is not here — it was lifted into `CLAUDE.md`'s
"Standing lessons" and "Standing operational notes" sections before the entry
moved.** The prose is what stops a defect recurring; the dated narrative around
it is what rotates. If you are reading an entry here to find a rule, the rule
should already be in the brief; if it is not, that is the bug.

⚠⚠ **Never quote an open-issue count, an open-PR count or a timebox date from
this file.** Those are the only facts in it with a guaranteed expiry date, and
several entries below carry them. Run the query.

## Rotated 2026-08-30 — v1.137.0

Moved out of `CLAUDE.md` when v1.139.0 made a fourth dated section. What it
EARNED — the wildcard-`dim` sidecar merge, the closed-chain/factory-map
disagreement, and the provider-aware cache probe — was lifted into `CLAUDE.md`'s
"Lessons from rotated entries" before it moved. Only the evidence is here.

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

## v1.115.0 addendum — #93 QA-19/20/21 + QA-23 (branch `coordinated-retirement`, NOT released)
rknighton's third QA round against PR #92 head `e1ca39e`. **All three findings
verified against the source before touching anything — 3/3 real.** Root cause he
named and I confirmed: the QA-17 fix coordinates at **operation ENTRY, not at
COMMIT POINTS**, so work already in flight on the retained handle never enrolls.
`delete_index` runs its void scan ONCE at entry (and the comment there claims it
"guarantees" the invariant — it doesn't); saves void records only AFTER the
atomic replace; the gate verifies the retained handle by FINGERPRINT ONLY, which
proves the file hasn't changed YET and says nothing about a writer holding its
lock one instruction from landing.

**QA-19 (High) = Path A, chosen over Path B.** The final gate now additionally
acquires the RETAINED handle's write lock **NON-BLOCKING** and holds it through
the unlink AND the record removal (`_try_index_write_lock` + `_gate_retained_handle`
in doc_store.py). Failure to acquire ⇒ `RetirementConflict`, both indexes stay
loadable. **Non-blocking is load-bearing:** QA-14's rule is that no caller ever
blocks on two locks, and an attempt that never waits cannot join a cycle. Path B
(canonically ordered pair locks) reopens that surface and hands a 4th architecture
to an audit already 3 rounds deep. ⚠ **Self-conflict audit (do NOT skip if you
touch this):** `_execute_retirement` reaches the retained handle only via
`index_fingerprint` + `load_index`, both READS — it never holds that lock, so the
gate has nothing to conflict with on its own thread. The retained-is-retiring case
is guarded explicitly (the lock is per-fd and NON-REENTRANT — re-acquiring
deadlocks by construction).

**QA-23 = the public delete path is ZERO-WAIT.** ⚠ **The first Path A attempt
BROKE his QA-17 test**: `delete_index` is `@_with_index_lock`-decorated, so the
competing delete blocked AT THE DECORATOR before ever reaching the void scan that
used to refuse it — turning a fast `False` refusal into a wait, then both indexes
absent sequentially. **Fixing that by editing his test would have been rewriting a
reviewer's oracle to match my code.** Instead the public path became zero-wait
(his QA-23 recommendation), and **his QA-17 test then passed UNTOUCHED** — the
strongest signal the design is right. Mechanism: `delete_index` gains
`lock_wait: bool = False`; `_with_index_lock` resolves the default from the
WRAPPED METHOD'S SIGNATURE at decoration time (⚠ reading `kwargs.get("lock_wait",
True)` alone silently ignores the signature default — that was a real bug in the
first cut), so methods without the param (`save_index`, `incremental_save`) CANNOT
become non-blocking. `_execute_retirement` passes `lock_wait=True` explicitly:
it is mid-protocol with a record already on disk, where a bounded wait is correct.

⚠ **QA-23 IS NOT ACTUALLY SETTLED — the `lock_wait` default is CONTESTED BY TWO OF
HIS OWN TESTS, and no default satisfies both.** Discovered 2026-07-25 the first time
CI was ever able to run on this branch. Both call `delete_index(owner, name)` with
NO `lock_wait`, and both contend on the SAME lock (the target handle's write lock
taken by `_with_index_lock`):
* `test_v1_115_0_qa90.py::test_qa17_retained_delete_refused_inside_final_gate`
  needs an IMMEDIATE `False` — a retirement is paused mid-unlink holding this
  handle via `_gate_retained_handle` (`doc_store.py:1014`), so blocking waits on
  work that cannot finish. Passes with default `False`.
* `test_v1_115_0_lifecycle_v2.py::test_three_processes_keep_one_lock_inode`
  (**Linux-only, SKIPS ON WINDOWS**) needs a BLOCK — contention there is an
  ordinary cross-process writer that will release. Passes with default `True`.

**Flipping the default fixes one and breaks the other. Verified both directions on
real Linux; do not attempt a third flip.** ⚠ **The claim above that "his QA-17 test
passed UNTOUCHED = the strongest signal the design is right" WAS OVERREAD** — QA-17
passing was purchased with the default that breaks QA-15, and QA-15 never ran
locally because **every QA-23 verification was done on Windows, where it skips. A
Windows pass was treated as verification of a POSIX locking contract.** That is
precisely his QA-24 objection, now with a concrete example.

**PROPOSED fix (posted on #93, NOT implemented, awaiting his confirmation):** the
lock does not record WHY it is held, which is the real gap. The distinguishing fact
lives outside it — in the QA-17 case a retirement record NAMES this handle as the
RETAINED peer; in the QA-15 case no record exists. So: **refuse immediately when a
pending retirement record names this handle as retained, otherwise honor
`lock_wait` (default blocking)**. Satisfies both with neither test edited. It is a
semantic change to HIS contract and has already been guessed at twice, so it waits
on him.

**Branch is at `f6281ba`, default reverted to `False`, ONE known failure (the
Linux-only QA-15 test).** `False` chosen because it preserves the QA-17 guarantee
that both indexes are never simultaneously absent — a DATA-LOSS property — whereas
the QA-15 cost is coordination semantics: worse to get wrong, not destructive.

⚠ **CI on this branch: `push`/`pull_request` are gated to `master`, so a branch push
fires NOTHING, and the draft PR's `synchronize` event has not been firing either
(cause unestablished; no `types`/`paths` filter and `opened` DID fire, so drafts are
not excluded as a class). `workflow_dispatch` was added to both workflows on BOTH
master and the branch** (it must exist on the DEFAULT branch to be exposed, and a
dispatch runs the workflow file AS IT EXISTS AT THAT REF — hence both):
`gh workflow run test.yml --ref coordinated-retirement`. **Never again read
"no runs" on a branch as a CI outage; on this repo it is the designed behavior.**

⚠ **The branch had drifted 5 releases behind master and that was NOT cosmetic:**
the repo_group fan-out here calls `build_verdict(..., index_changed=...)`, a
parameter that only landed on master in v1.119.0, so `search_sections` with a repo
group raised `TypeError` on the branch. Merged (source clean, only version/CHANGELOG
bookkeeping conflicted). **Rebase/merge a long-lived branch BEFORE trusting any
local green.**

**QA-20 (Medium):** every `False` rendered as *Index not found.*, so lifecycle
contention was indistinguishable from a missing index and an agent would RE-INDEX
— the duplicate creation this whole arc exists to prevent. Added `reason_code` +
`retryable` (`index_deleted` / `index_not_found` / `index_lifecycle_busy`) via an
`outcome` OUT-PARAM so the bool return and every existing caller are untouched.

**QA-21 (Low, POSIX-only):** `delete_index` no longer unlinks the lockfile it
holds. On POSIX the unlink succeeded mid-critical-section and a newcomer created a
fresh inode and ran concurrently; the QA-15 recheck CANNOT catch it (nothing about
the new inode is stale). ⚠ **`_index_write_lock`'s own docstring cites that unlink
as the reason QA-15's retry exists — this fix demotes that retry from load-bearing
to defensive.**

**Also (suite parity with jcm 1.108.169/.170):** the `repo_group` fan-out built its
verdict with NO `index_changed`, so a zero-result GROUP search could mint a citable
absence ref while ANY member index was rebuilding. No single index to re-stat, so
the group now INHERITS its members' detection (their sub-verdicts were already
correctly wired and were simply being discarded).

**Validation.** His design-neutral harness `test_v1_115_0_lifecycle_v2.py` (committed
verbatim) went **4 failed/1 passed → 5 passed/3 skipped**; Windows suite **1813
passed, 3 skipped** (all 3 are the harness's own conditional skips: 2 Path-B-only,
1 Linux-only). New **`tests/test_posix_lock_semantics.py`** covers what Windows
STRUCTURALLY CANNOT: the `fcntl` `LOCK_EX|LOCK_NB` branch (never executed anywhere
before), cross-PROCESS refusal, and inode stability. **It SKIPS on win32 rather than
passing vacuously — a vacuous pass is exactly QA-24's objection.** Verified on
Ubuntu/WSL, py3.12.3, `/tmp` on **ext4** (NOT the DrvFs mount, where inode semantics
don't apply); **non-vacuous — reintroducing the unlink flips QA-21 to FAIL.** Runs
standalone too (`python3 tests/...`), since a POSIX box often has no pytest — its
`import pytest` is deliberately optional.

⚠ **STILL OPEN, not mine to close:** QA-22 applies only to Path B (not built).
**QA-24 is a DOCUMENTATION requirement — SPEC/CHANGELOG claims must match what each
platform actually proves.** rknighton re-verifies against a pinned SHA, as every
round of this arc has closed. Branch pushed for that purpose; PR #92 stays DRAFT,
master untouched, nothing released.

⚠ **CI note (corrected):** jdoc DOES get `ubuntu-latest` × py3.10-3.13 on
`pull_request` — verified by a successful run on `e1ca39e`, PR #92's previous head.
An earlier claim in-session that "CI won't run on this PR" was WRONG, inferred from
two new SHAs showing no runs. There is no `workflow_dispatch`, so a stuck head is
nudged with an empty commit rather than dispatched.

## v1.115.0 addendum — #89 pre-production corrections (QA-06..QA-11, QA-15)
rknighton's branch QA (#89) found the QA-01 coordination still had a
proof-to-capture gap + unverified recovery records. Fixes, all on the same
branch pre-release: **QA-06 (High):** new `_execute_retirement` helper
(index_local.py) is THE destructive step for all 3 retirement paths —
ordering is the contract: (1) capture fingerprints for both handles (None
fails closed, never authorizes), (2) RELOAD both indexes + re-run the
decisive proof predicates (per-path `_reverify` closures: legacy = cert+hash
coverage; supersession = both certified clean at the exact ancestry SHAs;
dedup = subset+hash equality) — token captured BEFORE reload means proved
state and accepted token can't diverge, (3) require `begin_retirement`
receipt, (4) guarded delete. `delete_index` now: `@_with_index_lock` (joins
the save-path lifecycle coordinator; only the TARGET handle is locked, so no
cross-handle lock ordering/deadlock surface — QA-14 moot), rejects expected
`None` fingerprints, and re-verifies the full fingerprint map a SECOND time
immediately before the primary `<name>.json` unlink (catches retained-peer
delete/save mid-cleanup; aborts with handle loadable, aux artifacts may be
gone → refresh rebuilds). **QA-07:** `begin_retirement` → bool receipt
(fsync'd file + per-publication-unique temp name: pid+tid+counter);
publication failure → family cleanup-incomplete, NOTHING removed, no pending
claim; `pending_retirement: true` only when record exists; save/incremental
cancel the record AFTER `_atomic_replace` lands (failed save preserves it).
**QA-08:** `pending_retirement` self-heals a record whose retiring index is
gone (completed retirement, never pending). **QA-09/QA-10 policy (jjg-style
fail-visible):** rewrite OR direct delete of the RETAINED handle voids any
record naming it as retained (`void_retirements_referencing`, called from
`_cancel_pending_retirement` + `delete_index`). **QA-11:** record publication
fsyncs (+ dir fsync on POSIX). **QA-15:** `_index_write_lock` POSIX path
re-verifies st_ino/st_dev after flock (lockfile unlink can't split
coordination; Windows can't unlink open files, exempt). QA-12/13 (perf) +
QA-16 (crash matrix) deferred post-merge per reviewer's own timing.
**QA-15 CONFIRMED on real Linux by rknighton (2026-07-23, #89): three-process
inode-split rig vs exact commit 0d22087 on Ubuntu 24.04/ext4 — waiter
detected stale inode + retried onto current inode as designed; full suite on
Linux 1771 passed / 7 skipped / 0 failed (first complete Linux validation of
the branch).**

**#90 QA-17 (High, FIXED same day): pair coordination via the RECORD LOCK.**
His qa_atomic_gap.py paused the guarded delete AT the primary unlink (after
the final fp check) and deleted the retained peer through the normal path →
both indexes absent. Root insight: single-handle locking can't couple two
files' existence; grabbing both handle locks reopens QA-14. Fix = the
retirement RECORD is the pair coordination point (retirements.py:
`hold_record_lock` blocking ctx mgr + `_acquire_fd` try/blocking w/ QA-15
ino-recheck; `try_void_retirements_referencing(timeout=1s)` bounded;
`void_retirements_referencing` now trylock-skip for save paths). delete_index:
(1) BEFORE any destructive step, try-void records naming target as retained —
busy lock ⇒ RETURN FALSE (refusal; retry succeeds ms later); (2) final gate
under `hold_record_lock(own record)`: fp re-verify + entry-record-still-exists
check (voided ⇒ RetirementConflict([retained])) + unlink + finish_retirement
all inside the lock. Lock order fixed handle→record, record locks
non-blocking on the delete side ⇒ no two-lock blocking anywhere, QA-14 stays
closed. Leftover `.retlock` files in .retirements are permanent by design
(never unlink a lock file you coordinate on — QA-15 lesson). QA-18 = CHANGELOG
now states exact harness results (qa_process 5/6 w/ labeled observation flip)
instead of "pass in full". Tests `tests/test_v1_115_0_qa90.py` (4: refusal
inside gate + voided-record conflict + before-gate void wins + no pending
record post-gate); his qa_atomic_gap both-absent state now unreachable
(harness stops at its delete-returns-True assert — disclose, don't surprise).
Suite 1782. **HOLD branch for his re-review of #90.** GOTCHA:
rknighton's qa_followups qa09/qa10/qa11-pending asserts + qa_process
observation test assert PRE-fix behavior and now flip by design. Tests
`tests/test_v1_115_0_qa89.py` (10); suite 1778. SPEC.md coordination
section rewritten. **NEXT: rknighton re-review on #89 → merge → publish.**

## v1.115.0 - QA-01/QA-03: coordinated & recoverable retirement (#88) — BRANCH, awaiting rknighton QA
PRD `C:\MCPs\business\jdoc-coordinated-retirement\PRD.md`. **QA-01 core =
guarded delete:** `DocStore.delete_index(owner, name, expected_fingerprints=None)`
re-verifies proof-time sha256-of-monolith fingerprints (BOTH retiring +
retained handles, via new `index_fingerprint`) INSIDE the deletion boundary,
before any removal; mismatch raises new `RetirementConflict(changed)` with
nothing touched. KEY INSIGHT (why inside delete_index): rknighton's harness
mutates an index WHEN delete_index is called (wrapping the real method), so
any check-then-delete in the caller loses the race — the precondition must
execute via the real delete under the wrapper. All 3 retirement sites pass
the guard and map RetirementConflict → `legacy_reconcile_conflict` /
`supersession_conflict` / NEW `graduation_conflict` (exact-dedup had no
conflict vocabulary; added to B4 guard + SPEC), each w/ `changed_handles`.
**Recoverability:** new `storage/retirements.py` durable record
(`<owner>/.retirements/<name>.json`: retiring/retained/fingerprints/family/
started_at) written before the destructive step; removed on success + on
conflict (voided); KEPT on cleanup failure (`pending_retirement: true` in
cleanup-incomplete blocks); `save_index`/`incremental_save` CANCEL a pending
record for the handle they rewrite (fail-visible, never silently reroute);
`delete_index` clears the record after primary-record removal (ordering
matters: after, so partial failure keeps it). **QA-03 = read-only report via
certification transitivity:** `legacy_reconcile="report"` diverts BEFORE the
refresh to new `_report_legacy_reconcile` — proof from STORED snapshots +
live Git evidence: stored loser + peer both certified clean at one SHA AND
live checkout clean at that same SHA (three clean legs at one commit ⇒ stored
snapshots describe the live tree; the refresh was only ever needed to
certify). Uncertified legacy → honest `legacy_reconcile_uncertified` +
`checkout_sha` (apply remains the certify-and-retire path). Responses carry
`_meta.read_only: true`. NOTE: planted fieldless indexes ARE certified —
certification comes from `local_git_state` (real git), not the
identity-evidence `collect_git_evidence` that tests monkeypatch. rknighton's
`qa_adversarial_test.py` (from #88) passes 8/8 verbatim; suite 1768; tests
`tests/test_v1_115_0.py` (10). Additive/1.x, no INDEX_VERSION bump. **NEXT:
rknighton branch QA from 2026-07-23 evening → then merge to master, publish
PyPI + tag + GH release, close #88, then close #80 w/ arc summary.**
## v1.114.2 - canonical handoff contract (suite parity, jcm #374)
`finalize_handoff` tool + `munch://handoff/<id>` resource
(`jdocmunch.handoff/v1`; parity with jcm v1.108.162 / jdata v1.24.0). New
`handoff.py`: assistant authors, server assembles/attests/persists/serves.
Attestation substrate = session retrieval record OWNED BY handoff.py (jdoc
has no jcm-style yield tracker): server chokepoint records search_sections/
search_titles `results` rows + get_section (`section` + `_meta.citation`
fallback for id/doc_path) + get_sections `sections` → `note_served_rows`;
ref = served section id OR served doc path OR doc-path component of a
served id; unknown → in-band error + `unknown_refs` (jdoc convention, no
isError). Deterministic body, id = sha256[:16], byte-identical resource
reads, canonical:true ADVISORY. `_TOOL_TIER_STANDARD` + `_NON_READONLY_TOOLS`;
tool count 63→64 (test_server bumped). Tests `tests/test_v1_114_2.py` (15,
incl. real-index chokepoint end-to-end via DOC_INDEX_PATH env); suite 1783.
**Shipped from MASTER as a patch (like 1.114.1) while `coordinated-retirement`
(1.115.0) stays HELD — on merge, resolve version conflicts to 1.115.0, keep
all CHANGELOG entries.**

## v1.114.1 - BM25 tokenizer: Unicode word splitting + CJK bigrams (#91)
First report from @tetiz123 (offered a real Korean corpus, ~114 docs/2,098
sections, for validation — invited in the close comment; watch for their
reply on #91). `_SPLIT_RE` was `[^a-z0-9]+`, so every non-ASCII char was a
separator: CJK content → zero tokens (lexical channel dead; hybrid silently
semantic-only; embedding-less installs had NO working search), accented
Latin mangled (`café`→`caf`), and the docstring falsely claimed Unicode
word boundaries. Fix in `retrieval/tokenize.py`: `_SPLIT_RE=[\W_]+`; new
`_CJK_RE` (Hangul Jamo/compat/syllables, Hiragana/Katakana+ext, Han
unified+ExtA+compat) pads runs with spaces pre-split (mixed-script tokens
like `초과근무OvertimeService` split cleanly), then runs expand to
overlapping character bigrams via `_cjk_bigrams` (lone char passes through;
the <2 length filter now applies to non-CJK only). Same expansion at index
+ query time ⇒ bigram overlap is the match signal. New public
`word_tokens()` (unicode findall + bigrams, no stopwords/minlen) shared
with `search_titles.py`, whose private `[a-z0-9]+` `_TOKEN_RE` had the same
bug. ASCII tokenization byte-identical. NO reindex: BM25/prune/dedup all
tokenize stored content at load/scoring time; `.terms.json` is glossary
(untouched). Tests `tests/test_v1_114_1.py` (11); suite 1768. **Shipped
from MASTER as a patch while `coordinated-retirement` (1.115.0) stays HELD
for rknighton re-review — when that branch merges, resolve the pyproject/
CLAUDE.md version conflicts to 1.115.0 and keep both CHANGELOG entries.**

## v1.114.0 - QA-04/QA-05: Git-verification disclosure + complete result-code contract (#88)
rknighton's follow-up findings on #88, both shipped same-day. **QA-04:**
`doc_resolve_repo` hid a failed Git verification (GIT_UNAVAILABLE) as an
ordinary not-found — `collect_git_evidence` recorded `verification_failed=True`
but the resolver only reported when `in_git=True`. New `elif
evidence.verification_failed:` branch in `resolve_repo.py` attaches a
structured `git_verification` block ({verified: false, reason_code:
`git_verification_unavailable`, detail}) + a hint that worktree discovery was
skipped and index_local still creates a provisional index (#84 behavior
unchanged). **QA-05:** the 12 resolver reason codes in
`resolve_worktree_corpus` were inline literals invisible to the STATUS_*/
REASON_* drift guard — all promoted to REASON_* constants (`_worktree_corpus.py`),
documented in a new SPEC.md `worktree_resolution.reason_code` table, and a new
AST guard (`test_v1_114_0.py::test_no_inline_reason_code_literals`) rejects any
future inline `reason_code` string literal anywhere in src (Call keywords +
dict literals). `provisional_cap_exceeded` + `legacy_reconcile_not_applicable`
moved to a SPEC "Top-level `error` codes" table (they return as top-level
`error`, not reason_code — the guard test asserts they no longer appear as
reason-code rows). USER_GUIDE gains a `legacy_reconcile` section + param row;
CHANGELOG v1.108.0 date fixed 2026-07-28 → 2026-07-20 (real tag date). B4
drift-guard set in test_v1_106_0.py extended with the 13 new constants.
rknighton's `test_remaining_qa_findings.py` passes 3/3 verbatim. Additive/1.x,
no INDEX_VERSION bump. Tests `tests/test_v1_114_0.py` (7); suite 1757.
**#88 QA-01 + QA-03 remain OPEN (dedicated coordinated-retirement release);
rknighton available for branch QA tomorrow evening.**

## v1.113.0 - QA-02 contained fixes: retirement delete result authoritative (#88)
@rknighton's adversarial QA (#88) found 3 reproducible reconciliation-lifecycle
gaps; jjg's call was STAGE — ship the 2 contained QA-02 fixes now, build QA-01
(refresh/retirement coordination) + QA-03 (read-only report) as a dedicated
coordinated/recoverable-retirement release with a PRD (both entangled with the
refresh/certification path: QA-03's honest fix needs a certify-without-persist
step because a genuine fieldless legacy index is only certified BY the report
refresh — the same surface QA-01 reworks). **QA-02.1:** `_resolve_graduation`
exact-dedup path (`index_local.py:~646`) ignored `delete_index`'s return →
reported `reconciled`+`removed_handle` even on `False`; now checks it, reports
new `graduation_cleanup_incomplete`, keeps both indexes, no `removed_handle`.
**QA-02.2:** `DocStore.delete_index` unlinked the primary `<name>.json` FIRST
then rmtree'd content → a mid-cleanup failure left an un-loadable, un-retryable
half-deleted index; primary record now removed LAST (content/summary/sidecars/
claims first), so a partial failure stays discoverable and the caller's retry
(legacy `apply` already returns `legacy_reconcile_cleanup_incomplete`) finds
the handle. New `REASON_GRADUATION_CLEANUP_INCOMPLETE` in `_worktree_corpus.py`
+ B4 drift guard (`test_v1_106_0.py`) + SPEC.md vocabulary table. Reproduced
against rknighton's attached harness (7 fail/1 control on v1.112.0; the 2 QA-02
cases now green; 4 QA-01 cases deferred). Additive/1.x, no INDEX_VERSION bump.
Tests `tests/test_v1_113_0.py` (4). **#88 QA-01 + QA-03 still OPEN.**

## v1.112.0 - tool-surface schema receipt (suite parity, jcm v1.108.153)
`get_session_stats` gains an advisory `tool_surface` block: visible vs catalog
tool counts (after JDOCMUNCH_TOOL_PROFILE + JDOCMUNCH_DISABLED_TOOLS
filtering), schema tokens for each at bytes/4 over the {name, description,
inputSchema} serialization, `schema_tokens_avoided`, `heaviest_tools` top-15,
`estimator:"bytes/4"`. New `server.py::_tool_surface_stats()` after
`_filter_tools`; wired in the get_session_stats dispatch branch under
try/except (failure → block omitted, never a failed call). No `surface` key
(the Counter is jcm-only); `profile` carried. Additive/1.x, no INDEX_VERSION
bump. Tests `tests/test_v1_112_0.py` (6); full suite 1746. Siblings same day:
jcm v1.108.153 + jdata v1.23.0.

## v1.111.0 - runtime identity resource (suite parity, jcodemunch-mcp#371)
New `runtime_identity.py` module + `munch://runtime/identity` MCP RESOURCE
(`munch.runtime.identity/v1`, @rknighton's spec): schema/product/version/
transport (always "stdio" here)/pid/`process_start {value, source}`/
`instance_id` (uuid4 once per process lifetime)/optional `launch_id` echo of
`JDOCMUNCH_LAUNCH_ID` (fallback `MUNCH_LAUNCH_ID`, omitted when unset).
`process_start`: Windows GetProcessTimes via ctypes (argtypes/restype set —
the pseudo-handle truncates on 64-bit otherwise and the call silently fails),
Linux /proc/self/stat starttime + /proc/stat btime, else first-read clock
DISCLOSED as `source:"self_recorded"` — never fabricated as OS evidence.
Wired at the previously-empty `list_resources` + new `read_resource` handler
(`ReadResourceContents` from `mcp.server.lowlevel.helper_types`). Resource not
tool — no tool-count/schema change; on-demand only, no bg/network (README
disclosure unaffected). Deliberately excluded: command lines/env/cwd/
hostnames/corpus paths. Tests `tests/test_v1_111_0.py` (11). README env row +
"Runtime identity resource" section; USER_GUIDE section. Siblings same day:
jcm v1.108.152 + jdata v1.22.0. CHANGELOG hand-appended (wording kept clear of
replay fixture query phrases).

## v1.110.0 - Part C.2: explicit-intent legacy reconciliation (#87)
rknighton's final spec of the #80 arc, implemented under jjg's five decisions
(consent = report/apply param on index_local; selected-handle-only loser;
exactly-one-peer with zero=backfill / several=ambiguity; proof = same clean
certified SHA + full path-and-hash coverage; reuse the #86 retirement
primitive). New `legacy_reconcile` param: precheck (in index_local, after
git-evidence collection) fail-closes with `legacy_reconcile_not_applicable`
unless explicit name + existing handle FIELDLESS at call start (identity
version 0, no lineage key, not provisional) + full refresh + default
worktree_mode + confirmed lineage. Post-refresh `_resolve_legacy_reconcile`
(module-level, index_local.py) wired via `_finish_legacy_reconcile` at all 3
success return sites (no-change / incremental / full): peers via
filter_lineage_candidates + classify_graduation (provisionals never vouch,
basename disclosure never in the proof set), certification gate (both
sha_certified + clean + same head_sha), hash gate (every sel path in peer
with equal stored hash; missing=unproven). report → `legacy_reconcile_ready`
block, no mutation; apply → #86-style final recheck
(`legacy_reconcile_conflict` on drift) → delete_index (loser only; fail →
`legacy_reconcile_cleanup_incomplete`, idempotent) → peer handle returned +
removed_handle/removed_file_count + leftover sidecar disclosure. **KEY
DESIGN (found by the retry test): under C.2 intent the selected handle stays
FIELDLESS — `lineage_kwargs` suppressed — else the first attempt's backfill
makes any retry after cleanup-fail/conflict flunk the fieldless gate.
Backfill stays the ordinary refresh's job (LC2-01).** 9 new REASON_LEGACY_*
codes in _worktree_corpus.py + B4 drift guard (test_v1_106_0) + SPEC.md
table (new `legacy_reconciliation.reason_code` section — the
published-vocabulary guard in test_v1_109_0 checks SPEC contains every
constant). Tests `tests/test_v1_110_0.py` (12: fieldless planting via
monkeypatched `collect_git_evidence` returning default GitEvidence; twin =
`git worktree add --detach` at the same commit for same-SHA certified
pairs; content-differs via monolith JSON hash tamper; multiple-peers via
`worktree_mode="branch_local"` second modern index — the worktree gate
otherwise refuses the duplicate; flaky-filter conflict + delete_index
monkeypatch cleanup-fail rows). Additive/1.x, NO INDEX_VERSION bump.
**#80 arc COMPLETE (A, B, C.1, supersession, C.2).**

## v1.109.0 - modern verified-snapshot supersession (#86)
rknighton's dedicated follow-up spec, implemented in full. New `commit_ancestry`
(`tools/_git.py`): fail-closed tri-plus-one classification (ancestor/descendant/
unrelated/unproven) via bounded `_git_probe` calls (`cat-file -e` existence +
two-direction `merge-base --is-ancestor`; exit-1 = determination, UNAVAILABLE =
unproven). `_resolve_graduation` (index_local.py): inside the `differing` branch,
supersession prereqs = both snapshots `sha_certified` + not `source_dirty` +
valid distinct SHAs + **currency guard** (`wt_evidence.head_sha == stored
provisional head_sha` and checkout not dirty — retiring a stale snapshot would
discard newer content intent; my addition on top of his 5 conditions). Outcomes:
ANCESTOR → MS-02 retire (final recheck re-runs filter/classify + reloads target
+ compares head_sha immediately pre-delete; drift → `supersession_conflict`;
delete fail → `supersession_cleanup_incomplete` retry-idempotent; sidecar
leftovers → `cleanup_incomplete`+`leftover_files` via `_leftover_artifacts`);
DESCENDANT → MS-01 keep-both `provisional_newer_than_established` + next_action
naming the MS-03 completion (refresh established from this checkout, re-run);
UNRELATED/UNPROVEN → content-differs + `ancestry` key. 4 new reason codes in B4
drift guard. **SPEC.md now publishes the complete status/reason_code table**
(#84 item 4 closed) + `test_published_vocabulary_table_is_complete` fails on any
undocumented runtime value; also fixed SPEC's stale "$15/1M" cost_avoided line →
points at token_tracker.PRICING. MS-01 decision (maintainer): keep-both +
explicit refresh path, never auto-replace the established index (rknighton's
recommendation; preserves I5). Tests `tests/test_v1_109_0.py` (11: real git
repos + linked worktrees, byte-for-byte target verify, conflict via flaky
filter monkeypatch counting calls, cleanup-fail retry). GOTCHA: his attached
"current-behavior" harness intentionally asserts v1.108.0 behavior — MS-01/02/03
now flip by design, don't run it as a gate. Suite 1716. **#87 (Part C.2 legacy
fieldless reconcile) still OPEN — the riskiest op, not started.**

## v1.108.0 - C.1 hardening: hash-proven duplicates, complete cleanup, honest listings (#85)
rknighton's QA on the 1.107.0 graduation shipped 4 confirmed fixes + 2 decided
policies. **C1-01/02:** `_resolve_graduation` (index_local.py) adds a second
destructive gate AFTER path coverage — every provisional file must match the
established index's stored hash (`file_hashes`); mismatch/unprovable → new
reason code `graduation_content_differs` (constant
`REASON_GRADUATION_CONTENT_DIFFERS` in `_worktree_corpus.py`, in the B4 drift
guard) + `differing_files` (cap 20) + established_handle; both indexes kept.
Successful reconcile response gains `removed_handle`/`removed_file_count`.
**C1-05 decided:** dirty state never blocks a hash-proven exact duplicate and
never permits a differing one. **C1-06:** hash equality without confirmed
lineage never reconciles (negative test). **C1-03 decided+deferred:**
controlled supersession (certified, strictly ancestry-ordered modern
snapshots) accepted in principle, NOT shipped; scheduled as its own focused
build separate from legacy C.2 — until then different-content pairs stay
separate and visible. **C1-07/08:** `delete_index` removes the 5 index-owned
sidecars (`.embeddings.jsonl`/`.terms.json`/`.related.json`/
`.boilerplate.json`/`.duplicates.json`) — reconcile auto-cleanup inherits.
**C1-09:** `corpus_identity_version` written to the summary sidecar (always,
even 0 — key-presence distinguishes pre-fix summaries) + projected into
`_summary_row`; a summary LACKING the key falls back to the monolith and
self-heals on next save. GOTCHA burned: `Path.write_text` translates `\n` →
CRLF on Windows, so a test comparing a mirror `read_text` (universal
newlines) against disk-byte `file_hashes` (#52 domain) fails ONLY on Windows —
read mirrors with `open(..., newline="")` (`_exact_text` helper in
test_v1_108_0.py); rknighton's verbatim harness is 4/4 under LF sources.
`test_v1_107_0.py::test_reconcile_auto_cleanup_to_established` now plants the
established peer with the provisional's hashes (save_index `file_hashes=`
override). Additive/1.x, no INDEX_VERSION bump. Tests `test_v1_108_0.py` (7);
suite 1705.

## v1.107.0 - provisional-index graduation (#80 Part C)
Provisional indexes (Part B) can now GRADUATE behind the six security
invariants (PRD `C:\MCPs\business\jdoc-worktree-reconcile\PRD.md` §4.1 I1-I6).
Trigger: a provisional index FULLY refreshed (`index_local`, NOT `paths`
subset, not branch_local) while `wt_evidence.lineage_state=="confirmed"`.
`_resolve_graduation()` (module-level in index_local.py) decides via pure
`classify_graduation(est_candidates, selection)`:
- **graduate in place** (no established peer): refresh writes lineage_kwargs +
  `selection_kwargs["reconciliation_state"]=""` clears the flag. Outcome
  `graduated_verified`.
- **reconcile/auto-cleanup** (exactly one established peer, jjg §7 decision):
  load target, verify P.doc_paths ⊆ target.doc_paths (NO document loss), then
  `store.delete_index` the provisional (loser ONLY) + return target handle.
  Outcome `reconciled_to_established`.
- **diverged** (P has docs target lacks): FAIL CLOSED, never delete, stay
  provisional. Outcome `graduation_content_diverged`.
- **ambiguous** (>1 established peer): FAIL CLOSED, stay provisional. Outcome
  `graduation_ambiguous`.
Invariant mapping: I1 confirmed-lineage-only (never weaker/accretion) + full
refresh only; I2 `filter_lineage_candidates` excludes provisional (they never
vouch); I3 event-driven (verifying refresh), never time; I4 authority-free
until graduated; I5 conflict deletes only P, established untouched; I6 >1 match
→ ambiguous, never a tiebreak. Outcome attached via
`_attach_reconciliation_outcome` at all 3 refresh return sites (no-change /
incremental / create-replace). Tests `test_v1_107_0.py` (13 incl. adversarial
§6.6: still-unverifiable/subset/provisionals-never-vouch/authority-free).
Updated B4 drift-guard set in test_v1_106_0.py with the 4 new reason codes.
Additive/1.x, NO INDEX_VERSION bump. **DEFERRED to Part C.2 follow-on: pre-1.102
legacy physical-index MERGE (§4.3) — the riskiest op; Part B `legacy_index_present`
already de-silences it. NOT built.**

## v1.106.0 - reconciliation quarantine, QUARANTINE-ONLY (#80 Part B)
Part B of the #80 reconciliation arc; the safe foundation for Part C.
**NO graduation path ships here** — a provisional index stays provisional
until the Part C reconciler (PRD `C:\MCPs\business\jdoc-worktree-reconcile\PRD.md`,
invariants I1-I6). Pieces:
- **B1 provisional stamp:** new `_git_probe` in `_git.py` classifies a failed
  git call as `GIT_NOT_A_REPO` (git ran, made a determination) vs
  `GIT_UNAVAILABLE` (timeout/missing/OS error). `collect_git_evidence` sets
  `GitEvidence.verification_failed=True` only on UNAVAILABLE. `index_local`
  then creates the index but stamps `reconciliation_state="provisional"` +
  distinct reason_code `provisional_verification_unavailable` + a structured
  `reconciliation` response block. Confirmed-non-Git (NOT_A_REPO) stays normal.
- **DocIndex.reconciliation_state** additive field, omit-when-empty, threaded
  save_index / from_dict / _index_to_dict / incremental_save (_UNSET
  carry-forward — refresh NEVER graduates) / summary sidecar / list_repos row.
  NO INDEX_VERSION bump.
- **I4 authority-free:** `filter_lineage_candidates` skips
  `reconciliation_state=="provisional"` rows (also structurally excluded — a
  provisional index carries no lineage_key). Never an established_handle/reuse.
- **B3 per-root cap:** `count_provisional_for_root` + `PROVISIONAL_PER_ROOT_CAP=3`;
  create beyond cap → fail-closed `provisional_cap_exceeded` (no write). NOTE:
  Item A already dedups same-root+same-selection, so the multi-provisional
  vector is distinct SELECTIONS per root; the cap bounds that.
- **B2 `legacy_index_present`:** `legacy_sibling_handles` flags pre-1.102
  (corpus_identity_version==0) local indexes with matching source_root basename
  on a fresh create; non-blocking disclosure.
- **B4 vocabulary drift-guard:** test asserts every runtime STATUS_*/REASON_*
  is documented (teeth for #84 item 4 until Part C publishes the full table).
Tests `tests/test_v1_106_0.py` (10). GOTCHA burned: cap test can't create N
provisionals via different NAMES on one root+selection — Item A claim dedups
them to `corpus_already_indexed`; plant via save_index or use distinct
selections. Additive/1.x. **Part C (graduation/reconcile) = NOT built; needs
jjg §7 answers: cap N, mismatch cleanup-vs-review, provisional-report surface.**

## v1.105.1 - consistent candidate-list bound on doc_resolve_repo (#84)
rknighton's QA follow-up on the 1.102.0 (#83) worktree work. On the
`doc_resolve_repo` not-found worktree branch, `worktree_resolution.candidates`
(via `ResolutionDecision.to_public`) capped at `MAX_CANDIDATES`=5 with
`total_candidates` reporting the true count, but the sibling top-level
`not_found["canonical_candidates"]` (`resolve_repo.py`) assigned the raw
`decision.candidates` unbounded — so an 8-match case leaked all 8 in one list
and 5 in the other. Fix: cap `canonical_candidates` at `MAX_CANDIDATES` too
(imported the shared constant from `_worktree_corpus`); resolver + all other
fields untouched, full count still visible via
`worktree_resolution.total_candidates`. Boundary regressions
`tests/test_v1_105_1.py` (5: exactly-5 full, 6 capped w/ total=6, 8-match
repro, 0 well-formed/omitted, 1 unchanged; drive doc_resolve_repo with a
monkeypatched resolver decision since the bug is response-assembly not
resolution). Additive/1.x, NO INDEX_VERSION bump. **Report items 2-4 are
policy decisions for Part C (fail-closed-vs-provisional on failed git
verification; pre-1.102 legacy compat/reconciliation; complete public
status/reason_code vocabulary) — jjg-gated, NOT shipped here.** ruff: changed
files clean (81 pre-existing errors elsewhere, unchanged by this).

## v1.105.0 - office document ingestion via optional [office] extra
`pip install jdocmunch-mcp[office]` (= `markitdown[pdf,docx,pptx]>=0.1.6`,
MIT/Microsoft) adds `.pdf/.docx/.pptx/.epub` to LOCAL indexing only. New
`parser/office.py`: `OFFICE_EXTENSIONS`, `office_available()` (find_spec
guard), `convert_office(path, cache_dir)` (MarkItDown(enable_plugins=False)
— local converters ONLY, no llm_client/docintel/network), sha256(bytes +
markitdown version)-keyed cache under `<storage>/.office_cache/` (per-PID
tmp + os.replace), `OFFICE_MAX_FILE_SIZE` 25MB discovery cap (text cap
stays 500KB). KEY DESIGN: office exts are NOT in `ALL_EXTENSIONS` — the
remote GitHub leg, get_broken_links, and hooks mirror all assume text;
each local leg gates explicitly instead: `discover_doc_files` +
`_resolve_explicit_paths` (skip reason `office_extra_not_installed` /
`[office]` warning when unavailable), index_local + index_file read legs
branch to `convert_office` before `preprocess_content`, `parse_file`
routes OFFICE_EXTENSIONS to parse_markdown (content arrives already
converted), `watch._doc_extensions()` unions OFFICE_EXTENSIONS only when
available. FreshnessProbe live-source mode (`_preprocessed_bytes`)
reproduces the conversion leg (cache hit = cheap) and falls back to the
stored mirror for office files when conversion is unavailable/fails —
text-file failure semantics unchanged (OSError → missing). Boundaries:
csv/xlsx stay jdata's lane; remote doc_index_repo does NOT fetch office
files. Additive/1.x, NO INDEX_VERSION bump (new files enter on next
refresh — no schema change, so no observatory cache-key bump either).
README: formats row + full-disclosure subsection. Tests:
`tests/test_v1_105_0.py` (10, stub converter — CI needs no markitdown).

## v1.104.0 - advisory session token budget (suite parity, jcm v1.108.146)
`JDOCMUNCH_SESSION_TOKEN_BUDGET` (env; unset/0/garbage = off) sets an advisory
ceiling over response tokens served, counted at the single JSON return
chokepoint in `server.py` `call_tool` (`record_response_text`, bytes/4). New
`storage/token_tracker.py` section: `record_response_text` /
`get_session_response_tokens` / `budget_status` (ok / approaching >=80% /
over >=100%) / `reset_session_response_tokens` test hook. `_meta.budget
{limit, spent, state}` attaches at approaching/over — deliberately AFTER
meta_fields filtering, because jdoc's DEFAULT strips `_meta` entirely
(`get_meta_fields()` -> []) and an advisory the default config silently
deletes is no advisory at all. `get_session_stats` gains
`session_response_tokens` + the `budget` block (all 3 states) when
configured. Never blocks/truncates; yield block stays jcm-only. Additive/1.x,
no INDEX_VERSION bump, inline compute (no bg-disclosure needed). Tests:
`tests/test_v1_104_0.py` (9). README env-var table row added.

## v1.103.0 - coverage contract on absence claims (suite parity, jcm v1.108.145)
Community feedback on the retrieval-verdict article: an `absent` verdict
backed only by scan counts lies by omission when files were excluded at
index time. INDEX-TIME: `index_local`'s full discovery walk (no `paths`)
now persists `DocIndex.coverage` = {walk:"full", files_indexed,
skip_counts{reason:count}, no_sections_count, recorded_at} — skip reasons
tallied at the EXISTING `discover_doc_files` skip sites via an optional
`skip_counts` dict param (`_count_skip`; omitted = byte-identical), plus
read_error in the read loop and zero-section/parse-failure files counted
on the full-parse path. Persistence follows the corpus_selection pattern:
omit-when-empty in `_index_to_dict`, `.get` on load, `_UNSET` carry-forward
in `incremental_save`, `coverage=` kwarg on `save_index` — NO INDEX_VERSION
bump. Subset/incremental saves preserve; a full re-walk overwrites
(self-heals); a non-incremental `paths` replace clears it (the stored index
no longer reflects a full walk). QUERY-TIME: new
`retrieval/verdict.index_coverage_meta(index)` + `_attach_coverage`;
`build_verdict`/`filter_verdict` gain a `coverage=` kwarg, attached ONLY on
absent/degraded (`ok`/`low_confidence` stay lean); wired in
`search_sections` (single-repo path; repo_group fusion has no single index,
no block) + `find_endpoint`. Block = {generation{indexed_at, index_version,
git_head[:12] if head_sha}, files_indexed, excluded{reason:count},
no_sections_files}; omitted entirely for pre-contract indexes (empty =
unknown, never fabricated). `build_verdict` also emits `scorer:
SCORER_VERSION` (=1) since it reports confidence against the 0.4 floor;
`filter_verdict` deliberately has no pin (no scores). jDoc-shape deviation
from jcm: jcm records coverage in its sqlite meta k-v table and its
incremental path re-records; jDoc's incremental refresh re-walks discovery
but does NOT re-parse unchanged files, so no_sections_count is only
knowable on a full parse — coverage records on the full-index path only
and carries forward otherwise. GOTCHA for tests: a zero-section trip-wire
file = plain non-OpenAPI `.yaml` (routes through the openapi branch,
yields []); an empty `.md` may still mint a root section. Tests:
`tests/test_v1_103_0.py` (9). Additive/1.x. NOT committed/published yet.

## v1.102.0 - Item B: worktree-aware corpus reuse (#83)
rknighton's PRD implemented (all 5 phases, one release). New
`tools/_worktree_corpus.py`: GitEvidence (4 bounded git subprocesses per
REQUEST — common-dir/toplevel/HEAD/status-porcelain-uall-cwd-relative;
NEVER per stored index, I7), lineage states confirmed/unknown/conflicting,
pure `resolve_worktree_corpus(request, candidates)` decision table
(statuses exact/created/reusable/reference_only/ambiguous/related/unknown/
no_match + stable reason codes), `worktree_claim_key` (lineage+rel-root+
selection; both worktrees contend on ONE claim). Persisted (additive,
INDEX_VERSION 3): DocIndex.worktree_lineage_key (sha1[:16] of normcase
common dir), repo_relative_root, corpus_identity_version=1; in summary
sidecar + list_repos rows; backfilled on explicit full-corpus refresh only.
doc_resolve_repo: worktree discovery on the not-found path (additive
canonical_candidates + worktree_resolution; containment allowed for
read-only resolution ONLY — index_local matches exact location, PRD 4.2).
index_local: gate before creation; reusable → returns established handle
NO refresh (PRD 9.3); reference_only/related/ambiguous/unknown → no-write
error = reason_code; created → claim (worktree key when lineage confirmed)
+ R11 under-claim re-resolve; `worktree_mode` param (reuse_equivalent
default / branch_local escape). GOTCHAS: git status pathspec must be "."
(cwd=corpus root), NOT the toplevel-relative root (silently clean
otherwise); test worktrees created via `git worktree add` sit at the
commit at add-time (checkout the new HEAD after later commits); the
persistent creation claim survives metadata stripping (simulating pre-#83
stores requires clearing .corpus_claims too). Legacy without lineage
fields: invisible to translation, never inferred (I6) — a worktree call
then creates its own index. Tests: `tests/test_v1_102_0.py` (27: decision
table + real `git worktree` fixtures); #82 harness stays 4/4; suite 1641
green. Docs: SPEC discovery section + USER_GUIDE rows. Additive/1.x.

## v1.101.0 - Item A hardening: four adversarial-QA gaps closed (#82)
@rknighton's harness reproduced four failures in v1.100.0's identity
guarantees; all fixed, his harness passes 4/4 run verbatim (PYTHONPATH=src +
metadata shim only). (1) Single winner: claims publish payload ATOMICALLY
(temp file + os.link; O_EXCL fallback keeps reader-retry), and
claim-present-but-unreadable now returns `corpus_creation_in_progress`
no-write instead of falling through to create (the v1.100.0 race: loser saw
empty claim -> created anyway). (2) True ambiguity: explicit new name over
MULTIPLE identity matches -> `ambiguous_corpus_identity` with NO
established_handle (was: conflict promoting equivalents[0] by registry
order). (3) Symmetric identity: split `selection_identical` (conflict/
ambiguity basis; reflexive/symmetric/order-independent) from
`selection_covers` (directional refresh routing for omitted-name calls
ONLY) — full-then-named-subset and subset-then-full now both yield 2
indexes; full-covers-subset remains a refresh rule, never identity. (4) No
silent retargeting: `extra_ignore_patterns` + `follow_symlinks` fold into
the descriptor as `+shape:<sha12>`; a refresh that changes coverage updates
corpus_selection AND discloses `corpus_selection_changed: {from,to}` on all
refresh paths (incremental, no-change, full-replace). Tests:
`tests/test_v1_101_0.py` (8 regressions mirroring the harness) +
`test_v1_100_0.py` legacy-conflict test updated to expect ambiguity. Suite
1614 green. Additive/1.x: new error code + response key only.

## v1.100.0 - corpus identity: index_local won't duplicate an equivalent source (#81)
Reported by @rknighton (Item A of the #80 identity meta-issue; complements
#79's read-time detection with index-time prevention). `index_local` used to
let the requested/derived NAME become physical identity, so one local corpus
could be indexed repeatedly under different names. Now a structured corpus
identity is resolved BEFORE storage is chosen: normalized root (same
resolve+normcase comparison as `doc_resolve_repo`) + durable selection
(`"full"` or `subset:<sha>:<count>`; `paths=["."]` = full). Behavior:
established handle reused on omitted-name calls (response carries
`reused_established_handle`/`requested_handle`/`established_handle`); explicit
conflicting name → `corpus_already_indexed` error (established handle + safe
next action, NO write); multiple equivalent legacy indexes + no selection →
`ambiguous_corpus_identity` (bounded candidates ≤5, NO write); explicitly
selected existing handle stays refreshable; a subset `paths` refresh NEVER
redefines durable selection (a `full` index covers any subset refresh from its
root; intentionally different durable subsets are never merged); parent vs
nested roots stay distinct (containment ≠ identity). Concurrent-create race
closed by atomic O_CREAT|O_EXCL claims (`storage/corpus_claims.py`,
`local/.corpus_claims/`; loser routes to winner's handle; 24h abandoned-claim
steal; released on failed create; `delete_index` cleans matching claims).
Persistence: new `DocIndex.corpus_selection` (additive omit-when-empty,
INDEX_VERSION stays 3; legacy ""=presumed full, participates in reuse when
root is unambiguous), carried through save_index/incremental_save (_UNSET
carry-forward), summary sidecar + list_repos rows. Repository lineage /
repo-relative location deliberately NOT in the equivalence check — reserved
as separate concepts for Item B per #80. Tests: `tests/test_v1_100_0.py` (23);
`test_v1_99_0.py` ambiguity setups now plant legacy dupes via direct
save_index (the guard correctly blocks the old index_local route).
1.x-additive: no tool/schema change; new error returns fire only for inputs
that previously created silent duplicates.

## v1.99.0 - `doc_resolve_repo`: path → doc-index handle lookup (#79)
Reported by @rknighton. The only general path→index lookup was `doc_list_repos`,
whose response grows with every indexed corpus on the machine — an agent that
knows the project folder had to pull the full listing to find one handle. New
read-only `doc_resolve_repo(path)` (`tools/resolve_repo.py`) answers via stored
`source_root` metadata in an O(1)-sized response: exact root match wins, then
most-specific containing root (file or nested subfolder resolves to its owning
index); equally-specific duplicates return `ambiguous: true` + bounded
`candidates` (max 5) + `total_matches` instead of guessing; outside-every-index
returns a compact not-found; GitHub corpora (no `source_root`) never match.
Comparison is `os.path.normcase` over `Path.resolve()` so Windows casing /
separator variants and symlinked aliases resolve; relative paths resolve against
server CWD, echoed as `_meta.resolved_path`. Suite parity with jcm's
`resolve_repo` (jcm#296 contract-parity principle); `doc_` prefix keeps the two
servers collision-free, matching `doc_list_repos`/`doc_index_repo`. Core tier;
readOnlyHint true. Tool count 62→63. Docs: SPEC Discovery Tools, README +
USER_GUIDE tables, live guide category. Additive/1.x-compatible; no
INDEX_VERSION bump. Tests: `tests/test_v1_99_0.py` (15) + `test_server.py`
count/name/no-repo updates.

## v1.98.0 - `watch` daemon: keep doc indexes fresh on any on-disk change (#78)
Reported by @oderwat. Freshness previously rode only the PostToolUse hook (fires
only when the agent edits a doc). Docs changed outside the agent (git pull,
editor, build, teammate) went stale until re-touched. jCodeMunch has `watch-all`;
jDocMunch now has the doc-scoped equivalent. New foreground daemon `watch`
(`watch.py`): `discover_local_doc_repos` reads `list_repos` (registry-driven),
watches each `source_root` via `watchfiles`, filters to `_DOC_EXTENSIONS` (reused
from `cli/hooks.py`), and re-indexes the owning index **incrementally** through
`index_local(name=<repo>, paths=[changed])` — jdoc#31 subset semantics
(add/update/delete-on-missing, never prune unlisted). Rediscovers on an interval
(restarts `awatch` when the root set changes); GitHub indexes (no source_root)
skipped; clean SIGINT/SIGTERM; WSL polling awareness
(`JDOCMUNCH_WATCH_POLL_DELAY_MS`). Login service (ported `service_installer.py`,
`jdocmunch-watch`): `watch-install`/`watch-uninstall`/`watch-status` for
systemd/launchd/Task Scheduler; launches via `sys.executable -m jdocmunch_mcp
watch` so a new `__main__.py` was added. New read-only MCP tool `get_watch_status`
(standard tier; service state + per-repo watchable coverage) → tool count 61→62.
New dep `watchfiles>=0.21.0`. README: added "Background behavior, fully disclosed"
section (compliance surface); dropped "real-time file watching" from "Not intended
for." Additive/1.x-compatible; no INDEX_VERSION bump. Tests:
`tests/test_v1_98_0.py` (15) + `test_server.py` count/name/no-repo-required
updates. Smoke-verified end-to-end (edit → incremental reindex, section_count 2→3).

## v1.97.1 - docs only
Documentation wording only; no code, wire, or behavior change from 1.97.0.

## v1.97.0 - update model price constants to current Anthropic pricing
Anthropic has reduced input pricing across the Opus line since these models
launched. `storage/token_tracker.PRICING` (which feeds `cost_avoided()`, emitted
in `_meta` on nearly every tool response) now tracks the current published rates:
Opus $5/MTok, Sonnet $3/MTok, Haiku $1/MTok. Set `claude_opus` to the current $5;
additively added `claude_sonnet` ($3) + `claude_haiku` ($1) so
`cost_avoided`/`total_cost_avoided` show the full current model set (parity with
jcm's price table). The `claude_opus` + `gpt5_latest` keys are unchanged in name,
so the wire stays 1.x-compatible. Token savings are measured in tokens and valued
at the applicable model rate — underlying savings unchanged, only the constants
track current pricing. Does not touch the public token counter (tokens stored,
valued at display time). No INDEX_VERSION bump, no tool add/rename. Tests:
`tests/test_storage.py` green (30). Suite parity: jcm v1.108.130 (receipt table)
+ jdata v1.19.0 (same constants). **Framing note: this tracks a vendor price
REDUCTION, NOT a correction of an inflated figure — never describe it as
"overstating."**

## v1.96.0 - never auto-bill a paid cloud provider from a bare env key (money-safety; suite parity with jcm v1.108.128)
A bare cloud API key in the environment (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
etc.) silently enabled AI summarization — `get_provider_name` auto-selected the
first provider whose key was present, billing per doc section on every index.
Every provider jDoc auto-detects is remote-cloud (its openai target is
`api.openai.com`), so auto-detect now suppresses ALL of them unless the user
explicitly opts in: name the provider (`JDOCMUNCH_SUMMARIZER_PROVIDER=<name>`,
unchanged) or set the new `JDOCMUNCH_ALLOW_PAID_SUMMARIES=1`. A one-time warning
names the exact setting; indexing continues with signature/heuristic summaries.
New `_PAID_CLOUD_PROVIDERS` + `_paid_summaries_allowed()`; guarded auto-detect
loop in `summarizer/batch_summarize.py`. Additive, 1.x-compatible (a previously
auto-billing bare key now needs opt-in — the safe direction; explicit-provider
and no-key paths unchanged). Tests: `tests/test_v1_96_0.py` (12) + 5 existing
auto-detect tests set the opt-in flag. Sibling note: jData's summarizer is
heuristic-only (no LLM client) and its embeddings are gated on an explicit
`*_EMBED_MODEL` — no bare-key billing path, no change needed.

## v1.95.0 - suite-parity retrieval verdict (`_meta.verdict` on search_sections + find_endpoint)
Phase 3 of the suite-wide retrieval-verdict work (jcm v1.108.116/.117 shipped it
for code). `search_sections` and `find_endpoint` now carry `_meta.verdict` — the
same agent-facing honesty contract: an empty/weak section search is positive,
token-saving evidence. States: `ok` / `low_confidence` / `absent` / `degraded`.
`degraded` = semantic requested on an index with no embeddings (lexical-only, so
absence isn't proven; precedence over `absent`); `low_confidence` keys off the
existing confidence score's documented < 0.4 ambiguity floor; `absent` carries a
`did_you_mean` list (documents whose path/title contains a query term, or, for
`find_endpoint`, existing endpoint paths sharing a segment with a missed glob).
Clean-room jDoc implementation in new `retrieval/verdict.py` (`build_verdict` /
`filter_verdict` / `suggest_docs` / `suggest_endpoints`); only the wire shape is
shared with the siblings — no cross-suite import. Additive, 1.x-compatible, no
`INDEX_VERSION` bump, inline compute. Tests: `tests/test_v1_95_0.py` (13).

## v1.94.0 - large-corpus stability: vectors out of the monolith, throttled reindex hook, cheap list_repos (#75, #76, #77)
Three linked reports from @floke75, confirmed on two machines (a 16 GB box hit
cascading jetsam kills + swap storm + WindowServer watchdog restarts from all
three interacting). Additive, 1.x-compatible: `INDEX_VERSION` stays 3, no forced
reindex — existing indexes keep working and shed their inline vectors on the next
save. **#75 (the amplifier):** embedding vectors were persisted inline in the
`<name>.json` monolith, pretty-printed at `indent=2` (~26 KB/section), so a
broadly-indexed corpus made the monolith multi-GB and every `load_index` cost
~8 GB RSS / ~60 s (vectors were already duplicated in `.embeddings.jsonl`).
`_index_to_dict` now strips `embedding` non-mutatingly (in-memory sections keep
vectors for the post-save related/boilerplate/dedup sidecars), the monolith uses
compact `separators=(",", ":")`, and vectors rehydrate lazily from the sidecar as
`array('f')` on first semantic use (`DocIndex._rehydrate_embeddings` — called from
`_ensure_semantic_matrix`, `find_similar_sections`, `get_related_sections`,
`get_doc_health` count). `_has_embeddings` counts a present sidecar; a save-time
safety net writes the sidecar first if sections carry vectors but none exists, so
the strip is always lossless. Result: sub-second / <0.5 GB `load_index`, ranking
unchanged (float32 shifts cosine ~1e-7). **#77:** `list_repos` (documented first
call, also on the PreCompact hook path) parsed every monolith for two `len()`s;
each save now writes a tiny `<name>.summary.json` (atomic, same write lock) and
`list_repos` reads it, falling back to full parse for legacy indexes and surviving
a single corrupt monolith. `delete_index` removes it. **#76:** the PostToolUse
reindex spawned an unthrottled `index-file` per edit; now a per-file leading-edge
debounce (`JDOCMUNCH_HOOK_DEBOUNCE_SECONDS`, default 3 s) coalesces bursts, a new
`hook-reindex` worker acquires one of N cross-process slot locks
(`JDOCMUNCH_HOOK_MAX_REINDEX`, default 2) before loading the index and exits if
over cap, and an opt-in breadcrumb (`JDOCMUNCH_HOOK_LOG=1` → `_hooks/reindex.log`)
makes pile-ups observable. Tests: `tests/test_v1_94_0.py` (21) + updated
`test_hooks.py`.

## v1.93.0 - MCP readOnlyHint annotations (suite parity with jcodemunch PR #361)
Every tool now advertises `ToolAnnotations(readOnlyHint=...)` at the `list_tools`
chokepoint (`_apply_readonly_annotations`, non-mutating `model_copy`), so MCP
clients that gate execution (Claude Code plan mode) run jDoc's query tools
silently while still prompting on the write-set. `_NON_READONLY_TOOLS` =
index_local, doc_index_repo, delete_index, define_repo_group, tune_weights,
check_embedding_drift — any tool that can mutate persistent state (a doc index,
repo group, tuning file, or drift canary) under ANY argument; biased conservative
since mislabeling a writer read-only is the harmful direction. `link_code_to_symbols`
/ `verify_index` / `resolve_related_code_repos` are read (they load + return, never
persist). Suite parity with jcodemunch-mcp (PR #361) and jdatamunch-mcp. Additive,
1.x-compatible (new `tools/list` field only; no tool add/rename/removal). Tests:
`tests/test_v1_93_0.py` (4). (Note: `check_embedding_drift` marked False here since
force=true re-pins the canary; jcm marks it True — jcm is the mild outlier.)

## v1.92.0 - live-source freshness compares in the indexed (preprocessed) domain (#74)
Report from @mmashwani — a regression in v1.91.0's #71 live-source mode. The
index stores `file_hashes`, section `content_hash`, and byte offsets over
*preprocessed* content (transformed formats — `.json`/`.jsonc`/`.svg`/`.xml`/
`.html`/`.mdx`/`.ipynb`/`.tscn`/`.tres` — are converted by `preprocess_content`
before storage; the cached "raw files" mirror is really the preprocessed
representation). But `FreshnessProbe`'s live path read the RAW workspace bytes
and hashed/sliced those with the preprocessed-domain offsets, so a clean index of
any transformed format false-flagged every section as `stale_index` under
`live_source=True` (mmashwani saw 489 false positives on a `.json`/`.jsonc`/`.svg`
corpus while cached-mirror freshness and `verify_index` were clean), and a
reindex never cleared it. Fix: live mode now reproduces `index_local`'s pipeline
exactly — read with `encoding="utf-8", errors="replace", newline=""` then
`preprocess_content(raw_text, doc_path)`, and hash/slice `result.encode("utf-8")`
(new `FreshnessProbe._preprocessed_bytes`, cached per file so every section in a
file reuses one read + one convert). Cached-mirror mode (the default every other
consumer uses) is byte-identical — unchanged seek/read path. Result: unchanged
transformed files are `fresh`; a structural edit that changes the preprocessed
output still drifts; a value/comment-only edit that preprocessing normalizes away
correctly does not (the indexed representation is genuinely unchanged). Also
corrected the misleading doc_store "raw files" mirror comment. Additive,
1.x-compatible (corrects a false-positive only on the v1.91.0 opt-in path;
default behavior, buckets, and wire shape unchanged). Tests:
`tests/test_v1_92_0.py` (5).

## v1.91.0 - get_recent_changes: cached-mirror vs live-source clarity + opt-in live mode (#71)
Report from @mmashwani. `get_recent_changes` is documented as returning sections
whose "source" drifted, but `FreshnessProbe` reads the cached raw-content mirror
under the doc index, not the live workspace files. An empty result therefore
proves "stored mirror and index agree", NOT "unrefreshed workspace files match
the index" - an agent could read an empty result as proof live docs are current.
Two parts, both additive. (1) **Layer disclosure:** the tool description now
states it is a cached-mirror/index check by default, and the response carries
`_meta.drift_layer` (`"cached_mirror"` | `"live_source"`), `live_source_requested`,
`live_source_available`, and `source_root`, plus a docstring note on the separate
Git-head certification boundary (`head_sha`/`source_dirty`/`sha_certified` can lag
content freshness; the refresh-then-commit no-op-refresh workflow). (2) **Opt-in
live-source mode:** new `live_source=True` reads the LIVE files under the index's
`source_root` instead of the mirror, falling back to the mirror (with
`live_source_available=false`) when no usable root is recorded. Implemented by an
optional `source_root` arg on `FreshnessProbe` that swaps the file resolver
(`_resolve_path`, with path-traversal containment); the default `None` keeps the
cached-mirror behavior every other consumer (`get_doc_health`, search freshness)
relies on byte-identical. Server schema + dispatcher updated. Additive,
1.x-compatible (new optional kwarg + new `_meta` keys; default behavior and
`by_bucket` shape unchanged). Tests: `tests/test_v1_91_0.py` (5).

## v1.90.0 - get_section(verify=true): source-integrity hash, not transformed-response hash (#70)
Report from @mmashwani. `get_section` / `get_sections` applied the response-only
transforms `compress_code` / `strip_boilerplate` to `content` BEFORE computing
`hash_verified`, so a transformed read could report `hash_verified: false` even
though the indexed raw section still matched the stored `content_hash`. That
overloaded one flag to mean both "the cached/indexed section is stale" and "the
response was transformed" - a verification-contract ambiguity (sibling of the
#35/#55 byte-range invariant work and the #52/#46 certify-the-right-bytes
lessons). Fix: capture the raw indexed bytes before any transform and verify
against THOSE. `hash_verified` (plus the explicit alias `source_hash_verified`)
is now a source-integrity check and is never flipped false by a response
transform. When a transform actually changed the returned bytes, the section
carries `response_transformed: true` + `transformations: [...]` (disclosure,
present even without `verify`), and `verify=true` additionally reports
`response_hash_matches_content_hash` so returned-byte identity is a separate,
explicitly-named signal rather than an overload of `hash_verified`. Both tools
fixed identically. Additive, 1.x-compatible (new response keys; `hash_verified`
on an untransformed read is unchanged, and a transformed read that previously
reported a confusing `false` now reports the true source verdict - a correctness
fix). Tests: `tests/test_v1_90_0.py` (5).

## v1.89.0 - index_local: zero-config safe default name from spaced folders (#72)
Report from @mmashwani. `index_local(path=...)` with `name` omitted returned the
raw folder basename as the storage name, so a folder whose basename contained a
character invalid for jDocMunch storage (`[A-Za-z0-9._-]` only) failed downstream
with a generic `Invalid name: '<folder label>'`. The tool description says `name`
is optional and defaults to the folder name, so agents hit this on ordinary
local refreshes of, e.g., a folder with a space in its name. The omitted-name
path now derives a deterministic, storage-safe handle: a valid basename is
preserved exactly (backward compatible), otherwise it is slugified to the allowed
charset and suffixed with a short SHA-1 of the folder's ABSOLUTE path, so
`"My Docs"` becomes `"my-docs-<hash>"` and two same-named folders in different
locations don't silently collide. New `_default_local_name(folder_name,
folder_path)`; `normalize_local_index_name` gains an optional `folder_path` arg
(explicit-name + `local/` round-trip behavior unchanged; #67 preserved). When a
name is derived, the response carries `original_folder_label` + `derived_local_name`
and a warning that an explicit `name=` overrides it. Additive, 1.x-compatible
(new optional kwarg + new response keys; a valid-basename or explicit-name call
behaves exactly as before; an omitted-name call that previously *failed* now
succeeds). Tests: `tests/test_v1_89_0.py` (13).

## v1.88.0 - find_code_examples: doc_path / path_glob scope filters (#73)
Report from @mmashwani. `find_code_examples` searched fenced code blocks across
the WHOLE indexed corpus with only `repo`/`query`/`lang`/`max_results`, while
sibling docs tools (`search_sections`, `count_sections`, `get_toc`,
`get_toc_tree`) all accept `doc_path` / `path_glob`. During a scoped audit an
agent could believe it searched examples inside one document or folder while the
returned evidence came from elsewhere - unscoped code-example search producing
false evidence. Added optional `doc_path` (exact-document) and `path_glob`
(fnmatch, e.g. `docs/api/**`) parameters. Both apply BEFORE scoring by reusing
`DocIndex._path_excluded` - the same shared candidate pre-filter
`search_sections` uses since the #32 path-glob fix - so a single-document scope
can't be starved by a corpus-wide top-k cut (filter-before-score, not
post-filter-the-top-k). `_meta` now echoes `doc_path` and `path_glob` on every
return path (results, empty-query, no-blocks) alongside the existing
`lang_filter` and `blocks_scanned` (which already reflects the scoped block set).
Server schema + dispatcher updated. Additive, 1.x-compatible (new optional
kwargs + new response keys; an omitted-scope call behaves exactly as before).
Tests: `tests/test_v1_88_0.py` (7).

## v1.87.0 - get_doc_pr_risk_profile: backlink + tutorial signals no longer silently zero (#69)
Report from @mmashwani. The composite doc-PR risk tool fused five signals, two
of which were dead on every call and swallowed by broad `except Exception`
paths, so the aggregate *understated* risk - a high-severity false-assurance
class (same shape as jcm#338). **Bug 1 (backlink_burden):** Signal 3 called
`get_backlinks(repo=..., section_id=sid)`, but `get_backlinks`'s signature is
`(repo, doc_path, storage_path)` - the unexpected-kwarg TypeError was caught and
the section skipped, so `backlink_burden` scored 0 even with real inbound links.
`get_backlinks` is document-level, so the fix resolves each changed section to
its `doc_path` via the already-built `section_lookup`, queries once per unique
document (cached in `backlink_doc_cache`), and counts each unique document once
toward the aggregate so several changed sections in one doc don't inflate the
burden (per-section counts are still kept for blocker surfacing). **Bug 2
(tutorial_disruption):** Signal 4 guarded on `tp["result"]["chain"]`, but
`get_tutorial_path` returns `chain` at the TOP level (no `result` wrapper) - the
guard never matched, so the signal scored 0 even on a real ordered/Next-Prev
chain. Now reads `tp.get("chain")` and treats an `error` envelope as a recorded
failure. **Diagnostics:** all three delegating signals (blast_radius too) now
record unresolvable sections / raised delegates / unexpected shapes in
`result.diagnostics.signal_failures` (+ `_meta.signal_failure_count`) instead of
being indistinguishable from a true zero-risk verdict. Additive, 1.x-compatible
(new response keys only; a previously-always-zero signal now reflecting real
inbound/tutorial structure is a correctness fix, not a wire change). Tests:
`tests/test_v1_87_0.py` (6).

## v1.86.0 - cross-suite repo identity: local/ name round-trip + bridge handle clarity (#67, #68)
Two reports from @mmashwani, the only two friction points he had left in heavy
dual-suite (jCodeMunch + jDocMunch) autonomous use. **#67 (local/ refresh round
trip):** `doc_list_repos` returns local handles as `local/<name>`, but
`index_local(name=...)` validated `name` as a single storage component, so
reusing a discovered handle as the refresh name raised
`Invalid name: 'local/example-docs'` even though the target index exists and the
intent is unambiguous. New `normalize_local_index_name(name, folder_name)` in
`tools/index_local.py` strips a `local/` prefix back to the bare storage name
and is called before the broad indexing try (so an invalid name returns a clean
error, not the `Indexing failed:` wrapper); other owner prefixes, empty local
names, and nested slashes are still rejected. `doc_list_repos` rows also gain
typed identity fields - `repo_kind` (`"doc_index"`), `owner`, and the bare
`name` - so a consumer can tell the durable lookup handle (`repo`) from the
refresh `name` without parsing, and a doc handle from a code handle.
**#68 (cross-suite handle mismatch):** the two suites keep independent
repo-identity models on purpose, so a jDocMunch docs handle is not a valid
jCodeMunch `code_repo`. Reusing one previously produced *empty* bridge results
with `bridge_available: true` (indistinguishable from "no matches"). Two parts.
(1) `link_code_to_symbols` and `get_undocumented_symbols` now validate
`code_repo` once up front (shared `tools/_bridge.py::probe_code_repo`, which
reads jCodeMunch's own `search_symbols` error envelope) and return an explicit
`{"error": "code_repo_not_found", _meta:{code_repo_resolved: false, hint}}`
diagnostic instead of silent emptiness - fires ONLY on an unresolvable handle, so
a resolved-but-no-links repo keeps its exact prior shape. (2) New
`resolve_related_code_repos(repo)` tool maps a docs repo to candidate jCodeMunch
code handles by `source_root` (exact match = high; `source_root_contains_docs_root`
= medium; `docs_root_contains_source_root` = low), with an `ambiguous` flag and
honest `bridge_available: false` when jCodeMunch isn't importable. Read-only,
best-effort; the suites' identity models stay independent. Tool count 60 -> 61.
Tests: `tests/test_v1_86_0.py` (27); updated `test_v1_17_0`/`test_v1_22_0` for the
shared `_bridge` import and the tool-count/name assertions. Additive,
1.x-compatible (the new error return replaces a previously-empty result only for
the wrong-handle input the reporter flagged).

## v1.85.0 - CLI stdout guard + focused, path-safe PreCompact snapshot (#65, #66)
Two reports from @mmashwani. **#65 (CLI stdout contract):** the JSON-producing
CLI commands `index-file` and `index-local` computed their result — including
embedding-provider initialization — and only printed JSON afterward, with no
guard. The `serve` path already warms providers under
`contextlib.redirect_stdout(sys.stderr)` (jdoc#19) so sentence-transformers
download/progress chatter can't corrupt JSON-RPC framing; the CLI JSON paths had
no equivalent, so if a provider (or a future dependency) wrote to stdout during
computation the result would no longer parse. Fix: both CLI branches in
`server.py` now run the result-producing call inside
`contextlib.redirect_stdout(sys.stderr)`, then print the JSON after leaving the
redirect — stdout is reserved for the final JSON body only, legitimate stderr is
untouched. **#66 (focused, path-safe PreCompact snapshot):** `hook-precompact`
listed *every* indexed doc repo and printed each repo's absolute `source_root`,
and ignored the `cwd` hook field — so a compaction at a high-pressure moment
could inject unrelated corpora and leak local machine paths into agent context.
`run_precompact` now reads `cwd` from the hook JSON and threads it to
`_build_snapshot(cwd=)`, which: surfaces repos on the same path branch as `cwd`
first (`_repo_matches_cwd`), caps the listing to the top 3 and summarizes the
rest as "N omitted. Use `doc_list_repos` if needed", and **hides absolute source
roots by default** (opt back in with `JDOCMUNCH_HOOK_INCLUDE_SOURCE_ROOTS=1`).
Header switches to "Current workspace doc indexes:" when a cwd match exists, else
the compact inventory "Indexed doc repos:". The hook-output systemMessage is
advisory context-injection text, not a tool JSON response, and the change is what
the reporter requested (path-safety), so it is 1.x-compatible; the escape hatch
preserves the old source-root behavior for local-only workflows. Deferred (his
longer-term point 6): a docs session journal so the hook can include recently
searched/read sections rather than repo inventory — that is the larger
jcm-#334-style build, out of scope here. Tests: `tests/test_v1_85_0.py` (9).

## v1.84.0 - get_broken_links: rendered-anchor namespace, no private slugs (#64)
New report from @mmashwani. `get_broken_links` validated `#anchor` links by
accepting jdocmunch's PRIVATE section slug (underscore flattening, hyphen-run
collapse, hierarchical leaf, parse-time `slugify`) alongside the GitHub-rendered
namespace added in v1.77.0 (#50). That private namespace is an internal index
artifact no Markdown renderer ever emits, so a link dead on the rendered page
passed validation whenever it matched the private slug (`#my-function-reference`
for a `## my_function reference` heading GitHub renders as
`my_function-reference`) — a false negative in a link checker. v1.77.0 kept the
private forms deliberately, to avoid false positives from an incomplete rendered
model; the real fix is to COMPLETE the rendered model, then drop the private
crutch. **Two parts.** (1) `_build_rendered_anchors` (was `_build_github_anchors`)
now derives the full namespace a renderer emits: generated github-slugger
heading anchors with the explicit-id marker stripped (so `## H {#id}` yields `id`
and the text slug, never the polluted `h-id`), explicit `{#custom-id}` heading
ids (Kramdown / Python-Markdown / SSG; unsafe ids like `{#1-invalid}` fall back
to the text slug), raw HTML `<a id>` / `<a name>` / `<h* id>` anchors read from
the cached source (section bodies aren't persisted, only byte ranges — the
content cache is the source, populated for local + GitHub indexes), and GitHub
`user-content-` aliases. Fenced/inline code + HTML comments are scrubbed so
example anchors don't count. (2) `_anchor_matches_section` now consults ONLY that
set — the private slug is never trusted. Clean-room (not the reporter's
12-renderer-profile prototype); non-GitHub slug dialects (GitLab hyphen-collapse,
Bitbucket `markdown-header-` prefix, Obsidian wikilinks) are out of scope by
design — modeling them only widens acceptance and re-hides broken links.
Consumer-layer, **no reindex** (titles + cached source already carry the inputs).
Tests: `tests/test_v1_84_0.py` (12). Additive, 1.x-compatible (a link checker
reporting previously-missed broken links is a correctness improvement, not a wire
or tool change).

## v1.83.0 - vectorized query-time semantic scoring (#63)
Companion to #62 from @mmashwani, on the query path he flagged as the separate
deferred item. `DocIndex._semantic_search` and the semantic half of
`_hybrid_search` scored the query against every embedded section with a
per-section pure-Python `cosine_similarity` (O(N*D) per query, ~242 ms on his
10.7k-section corpus, and synchronous on the event loop so it blocked other
calls). Fix: new `DocIndex._ensure_semantic_matrix` builds + caches an
L2-normalized embedding matrix once per index (cached on the instance, which
DocStore already keys by index path + mtime, so a re-index rebuilds it with no
manual invalidation; the cache attr is not a dataclass field so it never
serializes), and `_semantic_scored` replaces both scoring loops with a single
matrix-vector product (`mat @ q`). numpy is imported lazily with the original
per-section loop as the fallback. Same returned tuples, same `(-score, id)`
sort, same `_path_excluded` / no-embedding filtering, same downstream RRF;
float64 keeps `_score` equal to the pure-Python value to fp noise. Clean-room
implementation (not the reporter's patch); parity test `tests/test_v1_83_0.py`
(5) asserts the vectorized `_semantic_search` matches the untouched
`cosine_similarity` reference (top-k order + scores within 1e-9), plus
tie / no-embedding / zero-vector / path_glob / cache / fallback cases. Additive,
1.x-compatible. Deferred (low urgency now the scan is sub-ms): dispatching
`search_sections` via `asyncio.to_thread`, and the same matrix for
`find_similar_sections`.

## v1.82.0 - vectorized related-graph semantic build + core index saved first (#62)
New report from @mmashwani (his first since the 26-issue batch closed at
v1.81.0). `related_persist.build`'s semantic half was an O(N^2) pure-Python
all-pairs cosine (`semantic_neighbors` per section, each a full
`cosine_similarity` scan), so on his ~10.7k-section embedded corpus it pinned a
core for an extrapolated ~45 min and never produced `related.json`; worse, the
sidecar was sequenced BEFORE `save_index` in `index_local`, so the slow build
gated the core index (sections + embeddings, all retrieval needs) from being
written, and the single tool call exceeded the client timeout even after #34's
`asyncio.to_thread`. **Two fixes.** (1) **Vectorized semantic build:** new
`_semantic_edges_matrix` L2-normalizes the embedding set once and computes
cosine as a single chunked numpy matmul (`block @ matrix.T`, 512-row blocks to
bound peak memory), top-N per row via `lexsort` — byte-for-byte identical
output to `semantic_neighbors` (same `>= min_score` threshold, top-5 cap,
self-exclusion, 4dp rounding, score-desc/index-asc tie order). numpy is
imported lazily with a pure-Python fallback, so it stays a soft dep (the
embedding stack already pulls numpy in wherever this path does real work); a
`_PUREPY_SEMANTIC_MAX` (2000) guard skips the semantic half with a logged
warning in the rare numpy-absent-but-large case rather than stalling.
Clean-room implementation (not the reporter's patch); a dedicated parity test
asserts exact equivalence to the untouched reference across the 512-row block
boundary with ties / no-embedding / zero-vector / top-5-cap cases. (2)
**Sidecar ordering:** `index_local` now calls `save_index` BEFORE the
related/boilerplate/dedup sidecars, so the core index always lands first and a
slow or failing sidecar can never block it. Additive, 1.x-compatible (faster
internal algorithm, identical public output). Tests: `tests/test_v1_82_0.py`
(4). Query-time `_semantic_search`/`_hybrid_search` in doc_store stay
per-section pure-Python (a separate deferred path, out of scope here).

## v1.81.0 - structural_integrity health axis (#54) — @mmashwani batch COMPLETE
Last issue of the 26-issue batch (tracking #61). `doc_health_radar` /
`get_doc_health` had no structural axis, so an index that silently lost sections
to a fence accident (unclosed fence at EOF, or a stray early closer) graded
identically to its repair — the documented CI gate gave false assurance on the
worst failure mode. New `structural_integrity` axis, fed entirely from
already-persisted data (no parser change, no reindex beyond what populates
code_blocks): `_structural_signals` counts headings swallowed into stored fenced
bodies (column-0 `^#{2,6}` lines, markdown-variant fences exempt — catches both
the EOF-open shape via the #51 flush and the early-close shape) plus
heading-level skips (consecutive `level` jumps > 1 per doc, the persisted-but-
unread `level` field). `compute_radar` grows a `structural_integrity` axis
(`_score_structural_integrity`, same steep slope as link_integrity); the
warnings flow get_doc_health -> doc_health_radar -> compute_radar. On the report's
repro, repairing the swallowed sections now moves the axis 0 -> 100 and the
composite/grade (was identical before). Deferred sub-signal: empty-link
(`[t]()`) detection needs extraction-layer plumbing; the level-skip already
flags the report's good.md, so coverage holds. Tests: `tests/test_v1_81_0.py`
(6). **All 26 of @mmashwani's issues now shipped** (v1.70.3 -> v1.81.0).

## v1.80.0 - shared prose view for hybrid-search scoring (#58)
Second of the frontmatter/scoring tail (tracking #61). Hybrid `search_sections`
fused a BM25 score (fences stripped by the tokenizer) with an embedding score
(`title + summary + content[:1000]`, fences AND frontmatter raw), so the two
channels scored different texts of the same section; a Jekyll/Hugo page with
long frontmatter got a root embedding of title + pure YAML keys (the prose never
reached the capped window). Fix at the consumption layer (parser content +
content_hash untouched, avoiding the #35 hash-vs-byte-range pathology): new
`prose_view` in `retrieval/tokenize.py` strips top-of-text frontmatter (YAML
`---` / TOML `+++`, same-delimiter backreference) + fences; `tokenize()` applies
the frontmatter strip (so the BM25 content path matches), and
`_section_embed_text` reduces content via `prose_view` BEFORE the 1000-char cap.
Embedding cache is keyed by `content_hash` (unchanged by the text fix), so the
key is now salted with `_EMBED_TEXT_VERSION` ("pv1") — old vectors miss and
re-embed instead of serving stale text. Stale tokenizer docstring corrected.
**Reindex + re-embed** picks it up (cache salt forces it). Tests:
`tests/test_v1_80_0.py` (5). Last in the batch: #54 (structural-integrity axis).

## v1.79.0 - TOML (+++) frontmatter recognition (#60)
First of the frontmatter/scoring tail (tracking #61). `_frontmatter_end_line`
recognized only YAML `---`, so Hugo's TOML `+++ ... +++` block was indexed as
root-section prose and its URLs (e.g. `canonical`) entered the stored
`references` artifact. The detector now accepts a `+++` opener with a matching
`+++` closer (composes with the #56 blank-line discriminator, which stays scoped
to `---` since `+++` has no thematic-break collision). Also routed per-section
`references` through the same frontmatter-free prose view already used for tags
/ inline_code, so frontmatter values (YAML and TOML) and in-code link syntax no
longer become references — this is the #47 HTML/frontmatter masking follow-on
landing for the reference artifact too. `content`/`content_hash` untouched
(frontmatter stays in the byte-accurate content). Deferred: the consumer half
(get_stale_pages/get_tutorial_path parsing `+++` dates via tomllib) needs Python
3.11+ (project floor is 3.10), tracked as a follow-on. **Reindex required**
(rebuilds references). Tests: `tests/test_v1_79_0.py` (4). Next: #58 (shared
prose view for embed/BM25 scoring), then #54 (structural-integrity health axis).

## v1.78.0 - inline_code artifact for the code<->docs bridge (#59)
Final links-group release (tracking #61). The parser extracted only fenced
`code_blocks`, so inline backtick mentions (`name`) — the conventional way prose
names symbols — never reached `link_code_to_symbols` or
`get_undocumented_symbols`; on a prose-heavy corpus the symbol<->docs bridge saw
~2 of 10 symbol-naming files. Three layers: **parser** — new
`extract_inline_code` collects identifier-shaped spans (trailing `()` stripped,
`^[A-Za-z_][A-Za-z0-9_.]*$`, len>=3, deduped, cap 40) from the same prose view
used for tags (so fenced code isn't double-counted), persisted as
`Section.inline_code` (omitted when empty; `from_dict`/`to_dict` round-trip,
no migration). **link_code_to_symbols** — each section's inline spans become a
synthetic `{section_id}::inline` bridge input routed through the same
identifier->search_symbols->by_block/by_symbol path (loop refactored to handle
code blocks + inline uniformly). **get_undocumented_symbols** — inline spans
feed the haystack (recall) and an exact lowercased-span set marks an exact
name/qualified_name as authoritative-documented (precision). **Reindex
required** to populate `inline_code`. Tests: `tests/test_v1_78_0.py` (6).
**Links group complete** (#47, #48, #49, #50, #59). Remaining: frontmatter/
scoring tail (#54, #58, #60) + the #52 get_section_diff follow-on.

## v1.77.0 - get_broken_links: fs existence + GitHub anchors + scheme parsing (#49, #50, #47.6)
Second links-group release (tracking #61); the consumer-layer link-health
fixes, no reindex needed. **#49:** existence was tested only against the indexed
doc set, so links to existing non-doc files (images, LICENSE, source) reported
`file_not_found`. Now the filesystem is consulted against `source_root` before
flagging; with no `source_root` (GitHub indexes) non-doc extensions aren't
claimed missing. **#50:** `#anchor` links were validated against jdocmunch's
private hierarchical slug scheme, which diverges from rendered GitHub anchors on
four axes (underscore flattening, hyphen-run collapse, `-2`-vs-`-1` duplicate
suffix, raw-markup leakage). New `_build_github_anchors` derives the
GitHub-rendered namespace per document (rendered text via `_rendered_text` +
`_github_slug` with github-slugger rules), accepted alongside the existing forms
so valid rendered anchors stop being flagged. (Accepting alongside fixes the
false-positive direction, the dangerous one for a link checker; github-dead but
private-valid anchors like `#install-2` stay unflagged, a documented limit.)
**#47 symptom 6:** the blanket colon-skip silently dropped typo'd/unknown
schemes; a scheme prefix that isn't a known-external scheme now reports
`unknown_scheme`. Bare email autolinks are treated as external. Closes #47
(extraction shipped in 1.76.0). Tests: `tests/test_v1_77_0.py` (7). Next: #59
inline_code bridge, then the frontmatter/scoring tail (#54, #58, #60).

## v1.76.0 - extract_references rewrite: inline grammar + reference defs (#47, #48)
First of three links-group releases (tracking #61); the parser-layer reference
extractor. **#47 (High):** `references` was built by two naive regexes over the
raw section body, so titled links `[t](x "title")` and angle-bracket
destinations `[t](<a b>)` stored the junk verbatim (broken-link false positive
+ backlink-zero false orphan from one defect), images were file-checked,
parenthesized URLs truncated at the first `)`, autolinks/bare URLs kept trailing
`>`/`.`/`,`, and link syntax shown inside code fences/spans became permanent
"broken" links. `extract_references` is now a proper inline pass: scrub fenced
code + inline code spans + HTML comments, then match inline links (images
skipped, titles + `<...>` stripped, one level of balanced parens for wiki URLs),
autolinks (`<https://...>` and `<user@email>`), and bare URLs (trailing
punctuation trimmed); empty `[t]()` destinations skipped. **#48 (High):**
reference-style links (`[t][ref]`, `[t][]`, `[t]`) contributed no edge; the
`[ref]: target` definition targets are now captured (file-level edges for
broken-links / backlinks / orphans / blast-radius). Section-precise resolution
of reference usages is a noted follow-up. **Reindex required** (rebuilds the
stored `references` artifact). Tests: `tests/test_v1_76_0.py` (11). Next:
get_broken_links fixes (#47 symptom-6 scheme parsing + #49 fs-existence + #50
GitHub-anchor namespace), then #59 inline_code bridge.

## v1.75.0 - parse-loop block detection: indentation + HTML blocks (#43, #45)
Second half of the block-detection sub-group (tracking #61); the two
parse-loop-rewriting fixes, landed together since both classify lines before
heading detection. **#43 (High):** every block-start regex ran against the raw
column-0 line, so ATX/setext headings indented 1-3 spaces folded into the
previous section and indented/list-nested fences never opened (their interiors
parsed as markdown, minting phantoms; `find_code_examples` went blind).
parse_markdown now computes leading indent: ATX/setext detection runs on a
dedented view when indent <= 3 (4+ stays indented code, not a heading), and
fence opens are indent-tolerant (`line.lstrip(" ")`, matching the already-loose
close side) so list-nested runbook fences open. **#45 (High):** the loop had no
HTML-block state, so `#`/`---` lines inside `<!-- -->`, `<script>`/`<pre>`/
`<style>`/`<textarea>`, and `<div>`-class blocks became phantom indexed
sections (hidden comment text searchable, raw code as section titles). New
CommonMark HTML-block state machine (`_html_block_start`, types 1-7) suppresses
heading detection inside HTML blocks like the fence machine does; type 1-5 end
inclusively on their close marker, type 6-7 at the first blank line, type 7
can't interrupt a paragraph, single-line blocks never enter the state. Byte
ranges / content_hash unchanged (invariant holds across HTML regions).
**Deferred:** #51's 4-space indented-code extraction stays out, it needs a list/
container model the parser lacks (4-space list-continuation prose would be
misclassified as code); documented limitation. Blockquote/list-item heading
sectioning (`> # X`) is a follow-on per #43 (anchor side is #50). **Reindex
required** (clears phantoms, picks up indented headings/fences). Tests:
`tests/test_v1_75_0.py` (10). Block-detection sub-group complete; remaining:
links (#47-50, #59) + frontmatter/scoring (#54, #58, #60).

## v1.74.0 - markdown block-detection, isolated set (#46, #51, #56, #57)
First half of the block-detection sub-group (tracking #61); the four fixes that
don't rewrite the parse loop. **#46 (High):** `strip_mdx` ran its import/export
+ JSX removers over the whole `.mdx` document, mutilating code inside fences and
storing the corrupted text as the hash-verified mirror. Now fence-aware: split
into `strip_mdx` (frontmatter + mermaid whole-doc, then segment on backtick/
tilde fences) + `_strip_mdx_plain` (the substitution pipeline, applied only to
non-fence regions). **#51 (bug half):** code blocks were emitted only on fence
close, so a fence left open at EOF was buffered then dropped; now flushed before
the final `_finalize_section` (CommonMark closes an unterminated fence at EOF).
The 4-space indented-code enhancement is descoped (documented limitation; lands
with the #43 indentation work). **#56:** a leading `---` thematic break plus a
later bare `---` was read as frontmatter, silently swallowing every heading
between; `_frontmatter_end_line` now rejects the opener when the next line is
blank (pandoc's discriminator). **#57:** `extract_tags` ran over the raw body
incl. fenced code + YAML frontmatter, so `#include`/`#fff`/YAML values became
corpus tags; new `_prose_view` blanks fenced-code byte ranges + the frontmatter
span (via a C-speed translate table) before tag extraction. Content/byte/hash
untouched. **Reindex required** for #46/#57 (changes stored mirror / tags).
Tests: `tests/test_v1_74_0.py` (9). Next: #43 (indentation) + #45 (HTML-block
state machine), the two loop-rewriting fixes.

## v1.73.0 - CLI/config ergonomics batch (#36-41)
Cluster B of @mmashwani's batch (tracking #61); six independent CLI/config gaps,
several with jcodemunch precedent. **#36 (High, operator-control bypass):**
`JDOCMUNCH_DISABLED_TOOLS` was enforced against the literal incoming tool name,
so a deprecated call-time alias (`index_repo`/`list_repos`) reached the disabled
canonical handler unchecked. New module-level `_ALIAS_TO_CANONICAL` map resolved
at the top of `call_tool` BEFORE the gate, so disabling a canonical tool blocks
every spelling. **#37 (storage split-brain):** `DOC_INDEX_PATH` was honored only
on the MCP dispatch path; `DocStore.__init__`'s default branch now consults it,
so CLI + hooks resolve the same root (explicit `base_path` still wins). **#38:**
`index-file` owner detection was folder-name inference only, unusable for a
custom `--name` index or a non-path-safe (spaced) root; new `--name` arg +
`_resolve_named_index` loads the index directly and derives the rel path from
its `source_root`. **#39:** `init --hooks` wrote bare command names that fail
under Claude Code's minimal-PATH `/bin/sh`; ported jcm's `_hook_invocation()` /
`_enforcement_hooks()` (resolve `shutil.which` to an absolute path at install
time, forward-slash on Windows, quote spaces, bare-name fallback). Dedup marker
widened to `jdocmunch-mcp` so bare/absolute/.EXE spellings collapse to one.
**#40:** `-V`/`--version` flag. **#41:** `delete-index --repo` CLI subcommand
(wraps the existing `delete_index` tool). Tests: `tests/test_v1_73_0.py` (9) +
3 updated `test_hooks.py` assertions for the absolute-path hooks. Byte-space +
CLI clusters both done; remaining: block-detection/links/frontmatter/scoring
tail (#43, #45-51, #54, #56-60).

## v1.72.0 - byte-space fidelity: CRLF preservation + BOM strip (#52, #53)
Third drop of @mmashwani's parser batch (tracking #61); the ingestion /
mirror-space pair. **#52 (CRLF):** `index_local`/`index_file` read with
`Path.read_text` under universal newlines, collapsing `\r\n`/`\r` to `\n`
*before* byte offsets were measured, so every published `byte_start`/`byte_end`/
`content_hash` verified only against a hidden LF-normalized mirror, never the
on-disk file, and disagreed byte-for-byte with the GitHub leg (which preserves
CRLF via `response.text`). Local reads now use
`open(..., encoding="utf-8", errors="replace", newline="")` (Path.read_text
lacks `newline` before 3.13), so offsets/hashes address the real on-disk bytes
and the local path converges onto the already-correct GitHub path. **#53 (BOM):**
a leading UTF-8 BOM (U+FEFF) survived both ingestion legs and broke every
first-line detector (the first ATX heading folded into the root, YAML
frontmatter became a phantom `author: Bar` section, setext titles embedded
U+FEFF). `preprocess_content` now strips one leading BOM as its first step,
fixing all formats + both legs at once and keeping the stored mirror aligned
with the parsed string (so the #55 invariant still holds end-to-end:
`sha256(disk[bs:be]) == content_hash`, verify drift 0 on a fresh CRLF index).
**Reindex required** (CRLF corpora get new disk-accurate offsets/hashes/mirror).
Not in scope: `get_section_diff`'s `@disk` label still compares index-vs-mirror
snapshots, so it can't observe a *post-index* on-disk edit — tracked as a
follow-on on #52. Tests: `tests/test_v1_72_0.py` (4, CRLF end-to-end +
BOM survival + preprocess unit).

## v1.71.0 - CommonMark setext/paragraph correctness + byte/hash invariant (#44, #35, #55)
Second drop of @mmashwani's parser batch (tracking #61); the parser-correctness
core. `parse_markdown` was rewritten around the CommonMark paragraph rule.
**#44 (High):** setext underline detection keyed on a prev-line heuristic
(non-blank + no `|`), so `---`/`===` after a list item, blockquote, fence-close,
or ATX heading fabricated phantom H2s, destroyed the real section as a `[0:0]`
range, or mis-titled it; multi-line setext titles kept only the last line; and
single-dash H2 / pipe-bearing H1 underlines were rejected. Now a `para_lines` +
`para_byte_start` block-state tracker arms only on real paragraph text and
clears on every non-paragraph context (blank, ATX, fence, frontmatter, list,
blockquote, thematic break via new `_LIST_ITEM_RE`/`_BLOCKQUOTE_RE`/
`_THEMATIC_BREAK_RE`); narrow `|` guard on H2 only (H1 never collides with
tables) keeps all five GFM pipe-table shapes from becoming headings.
**#35 + #55:** section bodies are now derived from the byte range
(`content_bytes[byte_start:byte_end].decode()`) instead of a separately
bookkept line buffer, so `sha256(raw[byte_start:byte_end]) == content_hash`
holds by construction. The old setext path hashed a subset of its range and
made `verify_index` report false drift on every setext section; a fresh setext
index now verifies `drift: 0` (was 3) end-to-end. Removed the `current_lines`
body buffer and `prev_line` bookkeeping entirely. **Reindex required** to clear
prior false drift and pick up corrected sections. Tests: `tests/test_v1_71_0.py`
(10, incl. #44 families a-d + the 5-shape table gate + the invariant).
Next: #52 (CRLF disk-byte fidelity) + #53 (BOM), the ingestion/mirror-space pair.

## v1.70.3 - fence-open regex accepts arbitrary CommonMark info strings (#42)
First fix of @mmashwani's 26-issue parser-correctness batch (#42-60) + CLI
batch (#35-41). `_FENCE_OPEN_RE` accepted only an empty info string or one bare
`[\w.+-]` token, so attribute-bearing fences (`` ```python title="x" ``,
`` ```js {1,3} ``, RMarkdown `` ```{r} ``, `` ```c# ``) were not recognized as
openers. The fence state machine then inverted: block body parsed as markdown
(phantom `# comment` sections), the block's bare closing ``` opened a phantom
`lang=""` fence that swallowed every real heading after it until the next bare
fence or EOF, and code blocks were lost - and the corruption self-verified
green (`get_section_diff` `identical:true`, `check_section_delete_safe`
`safe_to_delete` over real content). Fix: widen to CommonMark 4.5
(`r"^(`{3,}(?!.*`)|~{3,}).*$"` - backtick fences reject a backtick in the info
string, tilde fences accept anything); strip RMarkdown `{}` from `fence_lang`
so `{r}` filters as `r`. Existing indexes built over such fences carry phantom
+ missing sections and need a reindex. Tests: `tests/test_v1_70_3.py` (4,
incl. the report's fixture + 8 regex unit cases). Next: #55 finalize-time
`sha256(raw[byte_start:byte_end]) == content_hash` invariant, then the #52/#53
byte-space cluster.

## v1.70.2 - search/verify/event-loop fixes (#32, #33, #34)
Closes the rest of @mmashwani's issue batch. **#32**: `path_glob` was a
tool-layer post-filter after the index top-k cut, starving single-document
globs to 0 results on large corpora; now a candidate pre-filter inside
`DocStore.search` (new defaulted `path_glob` kwarg, shared `_path_excluded`
helper across lexical/semantic/hybrid). **#33**: `verify_index` bare-`continue`d
zero-byte-range sections (every structured-OpenAPI section is 0,0; even plain
markdown indexes carry one for the synthetic doc root), so counters didn't sum;
new `skipped_count`/`skipped_sections` (reason `empty_byte_range`), invariant
clean+drift+missing+error+skipped == section_count tested. **#34**: `index_local`
was a sync call inside async `call_tool`, wedging the stdio server past client
timeouts on big embeddings corpora; now `asyncio.to_thread` (write-safety via
the v1.69.2 cross-process lock). Deferred from #34: background-job/progress
architecture + vectorized cosine. Tests: `tests/test_v1_70_2.py` (8).

## v1.70.1 - `paths` subset refresh no longer prunes the index (#31)
Data-loss fix reported by @mmashwani. `index_local(paths=[...])` / CLI
`--paths-from` on an existing incremental index marked every unlisted indexed
file as deleted (`detect_changes` diffed the subset against the whole index),
collapsing a corpus on a 1-file refresh. Now the deletion diff is scoped to
the requested subset: listed files add/update, a listed file missing from disk
deletes, listed dirs diff their subtree, unlisted files are never pruned, and
`paths=["."]` keeps the full-corpus diff. Pure-deletion subset refreshes work
(no more "No documentation files found" early-return when an index exists).
`_resolve_explicit_paths` now returns `(files, warnings, requested)`.
Tests: `tests/test_v1_70_1.py` (7). Related rough edge (index-file owner
detection needing a `--repo` override) deliberately NOT addressed here.

## v1.70.0 - tune_weights recency window (mirrors jcm v1.108.53)
New `max_age_days` param (default 90, `0` = lifetime/pre-1.70 behavior) on
`tune_weights` / `tune_one_repo` / `tune_all_repos`; `ranking_db_query` gained
`window_seconds`. Closes the stale-anchor exposure from the 2026-06-10
memory-degradation research sweep: lifetime ledger events anchored
`semantic_weight` proposals to dead query distributions. `tune_all_repos`
discovery is also windowed, so repos with only aged-out events aren't scanned.
Additive (defaulted kwargs + new response keys) per the 1.x contract. 3 new
tests in `tests/test_v1_23_0.py` (`_backdate` helper shifts ledger ts).
jdata swept clean the same day (no tuner/learned state at all — no change).

## v1.69.2 - serialize concurrent same-repo index writes (PR #28)
Originally contributed by @Chrisr6records; carried across the finish line with
a cross-platform lock + Windows replace-retry. Two processes writing the same
repo's `<name>.json` at once (e.g. scheduled reindex + per-edit hook) could
install corrupt/partial JSON or silently lose an update: both wrote a shared
`<name>.json.tmp` and `os.replace`-d it with no lock. Fix in
`storage/doc_store.py`: per-PID temp name (`<name>.json.<pid>.tmp`); a
cross-process write lock around `save_index`/`incremental_save` (the whole
read-modify-write), `flock` on POSIX and `msvcrt.locking` on Windows via a
per-repo `<name>.json.lock`; and a bounded `_atomic_replace` retry for the
Windows `PermissionError` (WinError 5/32) that a concurrent reader triggers on
`os.replace`. The PR's lock was POSIX-only (`fcntl`) and no-op'd on Windows,
leaving both the lost-update race and the replace error unfixed there; both are
covered now. Fully additive (no `INDEX_VERSION`/tool/response change; the retry
never newly-raises -- 1.x contract). Regression tests in
`tests/test_concurrent_index_writes.py` reproduce both races across real
processes and pass on Windows + POSIX.

## v1.69.1 - git subprocess stdin -> DEVNULL, fixes Windows stdio deadlock (PR #30)
Contributed by @Derjyn. On Windows, every `index_local` over the MCP stdio
transport deadlocked the server permanently. `_git`/`_git_bytes` spawned git
with `stdout=PIPE, stderr=DEVNULL` but no `stdin` redirect, so the git child
inherited the server's stdin (the JSON-RPC pipe) and Git for Windows blocked
on it forever. The `JDOCMUNCH_GIT_TIMEOUT` guard couldn't recover: the timeout
killed the direct child but the post-kill `communicate()` drain wedged joining
the reader thread, because the `cmd\git.exe` wrapper chain still held the
inherited pipe handles. CLI `index-local` never hung (console-handle stdin, not
a pipe), which masked the bug as transport-specific. Fix: `stdin=DEVNULL` in
both helpers. Pure no-op for behavior (none of `rev-parse`/`status`/`ls-files`
read stdin). Diagnosis via faulthandler traceback + raw stdio JSON-RPC harness;
post-fix calls complete in 0.1-0.6s. `_git.py` is the only git-spawning module
on the server tool path, so the two helpers cover every affected call.

## v1.69.0 - GitHub `ref` selection for versioned doc snapshots (PR #27, closes #26)
Contributed by @DevItBetter; completes the index-identity arc (#17 -> #25
-> #27). Adds an optional `ref` arg to `doc_index_repo`/`index_repo` to
index a specific GitHub branch, tag, or commit-ish. Without `ref`,
behavior is unchanged (HEAD).

`ref` is selection input only, never persisted: explicit refs resolve to a
concrete 40-hex commit SHA *before* fetching tree/content, and durable
lookup/citation handles stay commit-SHA based (`repo_at_sha`,
`source_repo_at_sha`). Because the SHA fast-path still gates on
`current_sha == stored head_sha`, a different ref pointing at a different
commit can never trigger a false no-change short-circuit. Refs are
URL-encoded (`quote(ref, safe="")`) so branch names containing `/` (e.g.
`release/1.x`) resolve correctly. Fails closed: an explicit unresolvable
ref errors instead of silently falling back to HEAD, and invalid refs
(empty, whitespace, non-string) are rejected before any network call. The
omitted-ref path keeps the existing uncertified-HEAD fallback. Composes
with PR #25 named indexes.

Fully additive: `INDEX_VERSION` unchanged; omitted-`ref` calls behave
exactly as before. Out of scope (deliberately): `owner/repo@branch|tag`
lookup handles, moving aliases, persisted ref identity, snapshot
retention, changes to strict `repo@sha` semantics.

## v1.68.0 - `doc_index_repo` name override for named GitHub doc indexes (PR #25, closes #24)
Contributed by @DevItBetter; follow-up to #17 and #23. Adds an optional
`name` arg to `doc_index_repo`/`index_repo` so a GitHub doc index can be
stored under a caller-chosen safe handle (`owner/name`) while preserving
the upstream GitHub identity. Without `name`, behavior is unchanged. With
it, the stored index is `owner/name`, the upstream source is persisted on
a new `DocIndex.source_repo` field, and indexing responses + `list_repos`
surface `source_repo` (and `source_repo_at_sha` for certified indexes)
alongside the stored `repo` / `repo_at_sha`.

`name` is a storage-name override, not a moving alias: it must be a single
safe component (`[A-Za-z0-9._-]+`; `/`, `\`, `@`, `.`, `..`, empty, and
non-strings rejected before any network fetch). The SHA fast-path and
incremental paths stay keyed to the stored name while fetching from the
upstream source, and both now guard on `existing_source_repo ==
source_repo_id` so reusing one stored name for a different upstream source
can't serve stale content via the fast path. Legacy indexes (empty
`source_repo`) fall back to `repo` and get backfilled on next touch.

Fully additive: `INDEX_VERSION` stays 3 (new field uses `.get` default on
load + omit-when-empty on write). Out of scope (deliberately): branch/tag/
ref selection, moving aliases, multi-alias, snapshot retention.

## v1.67.0 - certified repo@sha handles for citeable doc snapshots (PR #23, closes #22)
Contributed by @DevItBetter; follow-up to #17. Adds an immutable
`owner/repo@40hexsha` handle so downstream workflows can cite the exact
doc snapshot used for retrieval. New `DocIndex` fields `head_sha`,
`source_dirty`, `sha_certified`, `source_root`; derived `repo_at_sha`
property (never stored, emitted only when SHA is 40-hex AND not dirty
AND certified). Surfaced in `list_repos`, `search_sections`,
`get_doc_health`, `get_index_overview`, `doc_health_radar`, session
snapshot. `search_sections` (and every read tool) accepts a strict
`repo@sha` alias that resolves only when the stored index matches that
commit and is certified clean; a miss resolves to an uncreatable
sentinel name so it cannot collide with a real repo.

Certification rules (conservative by design):
- GitHub: `index_repo` resolves HEAD->SHA once, then fetches tree,
  `.gitignore`, and all content pinned at that SHA (`?ref=<sha>`),
  closing the old per-call HEAD drift window. Certified only when the
  SHA actually resolved.
- Local Git: "clean" means the *indexed corpus* is reproducible at
  HEAD, not that the worktree is pristine. Dirty files outside the
  indexed scope don't block; gitignored-but-explicitly-indexed paths
  do (separate `git ls-files` tracked-ness check). New `tools/_git.py`
  helpers; git probes bounded by `JDOCMUNCH_GIT_TIMEOUT` (default 10s,
  `<=0` disables); a timed-out/failed probe falls to dirty so an
  immutable handle is never emitted for an unknown state.
- `index_file` is sticky: surgical updates never upgrade a dirty,
  moved-HEAD, untracked-path, or no-longer-Git-backed index to
  certified. Run `index_local` to recertify the full corpus.

Fully additive: `INDEX_VERSION` stays 3 (new fields use `.get` defaults
on load + omit-when-empty on write, so old<->new indexes round-trip).
`load_index`/`delete_index` now catch `ValueError` from the sentinel
name. Suite-parity follow-up candidate for jcm/jdata if citeable
handles earn their keep.

## v1.66.3 - openai-compatible: probe actual dim at init (jdoc#20)
Hardens v1.66.0's openai-compatible provider. Backing-model swap
behind the same URL/model env vars (e.g. Ollama retagging) used to
silently mix vectors of different dims in the on-disk cache because
`_provider_identity` returned `dim=None`. Fix:
`_OpenAICompatibleProvider.__init__` now probes the endpoint with a
one-token canary and stores the discovered dim on `self.dim`;
`_provider_identity` reads it from the cached singleton. Cache layer's
strict dim check now engages; a model swap forces clean re-embed.
Probe failure is non-fatal (dim falls back to None → wildcard
behavior of v1.66.0). When jcm/jdata pick up openai-compatible (#302,
jdata#2), this pattern should ship there too.

## v1.66.2 - warm sentence-transformers before stdio (jdoc#19)
Reported by @rknighton. First semantic `search_sections` hung when
sentence-transformers was configured: lazy model load exceeded MCP
timeouts and leaked progress chatter to stdout, corrupting JSON-RPC
framing. Fix: `provider.warmup()` (provider-type-gated; only
sentence-transformers needs warming) runs in `run_server()` before
`stdio_server()` takes over, wrapped in `redirect_stdout(sys.stderr)`
so noisy library writes land safely. Network providers skip warmup
to avoid avoidable startup round-trips. Warmup failure is non-fatal.

## v1.66.1 - `should_embed("false")` parses as False (jdoc#18)
Reported by @rknighton. `should_embed()` ran any non-empty string
through `bool()`, so `use_embeddings="false"` enabled embeddings.
MCP tool inputs hit this whenever a client sent the flag as a JSON
string. Fix: recognise common string booleans before the bool()
fallback (`"true"/"false"`, `"1"/"0"`, `"yes"/"no"`, `"on"/"off"`,
`"t"/"f"`, `"y"/"n"`, case-insensitive, whitespace-trimmed).
Unknown strings still fall through to `bool()` so 1.x compat holds
(`"flase"` typo remains truthy as it did before).

## v1.66.0 - openai-compatible embeddings (PR #17)
Opt-in `openai-compatible` embedding provider for Ollama, vLLM,
LiteLLM, llama.cpp, LM Studio, and any other OpenAI-API-shaped
endpoint. Four env vars: `JDOCMUNCH_OPENAI_COMPAT_{URL,MODEL,API_KEY,
BATCH_SIZE}`. Explicit-only activation (never auto-detected).
Credential isolation: default API key is the literal `"local"`, never
falls through to `OPENAI_API_KEY`. Cache signature includes URL +
model + first-8 of compat key + batch size; ambient `OPENAI_API_KEY`
is excluded. Contributed by @DevItBetter. Follow-ups: jdoc#20
(canary-pin actual dim), jcm#302 + jdata#2 (sibling parity).

## v1.63.3 - `jdocmunch_guide` sibling-parity tool
Adds `jdocmunch_guide` -- doc-MCP sibling of `jcodemunch_guide` (jcm since
v1.84.0). Returns the version-current CLAUDE.md / AGENT.md policy snippet
for jdocmunch-mcp so an agent can keep a one-line CLAUDE.md
(`"Call jdocmunch_guide and strictly follow its instructions."`) instead
of pasting a static block that drifts. Tool count 59 -> 60. Companion
release of jdatamunch-mcp v1.12.2 ships `jdatamunch_guide` on the same
shape. Backstory: issue #296 (Codex compatibility report by @rknighton).

## v1.63.2 - drift-proof __version__ via importlib.metadata
`__version__` is now derived from `importlib.metadata.version("jdocmunch-mcp")`
in `__init__.py`, mirroring jcodemunch-mcp's pattern. pyproject.toml is
the single source of truth; the hardcoded literal can no longer drift.
v1.63.1's `tests/test_version_sync.py` regex guard retired as redundant.
Source-checkout callers without pip install see `__version__ = "unknown"`;
the replay runner's `_resolve_version()` already handles this via pyproject
fallback.

## v1.63.1 - CI green (fixture query rename + full-history checkout)
Patch release. Two independent CI fixes, no installed-user behavior change.
(1) `self_v1_11_0` fixture query renamed `wiki stats` -> `wiki benchmark`:
the old query collided with `### Stats` subheadings added to CHANGELOG.md
in v1.62.0/v1.63.0 and demoted the target wiki-benchmark page from
rank 1 to rank 4 (MRR 1.0 -> 0.925). (2) Both CI workflows now set
`fetch-depth: 0` so `scripts/generate_changelog.py` can walk every
`release:` commit; the shallow default broke `test_runs_against_real_repo`
on any push whose HEAD wasn't a `release:` commit.

## v1.63.0 — `get_doc_pr_risk_profile` (Phase-2 sibling-parity COMPLETE)
Composite doc-PR risk: volume + blast_radius + backlink_burden + tutorial_disruption
+ role_weight → 0-1 risk_score + level (low/medium/high/critical) + top-5 blockers +
recommended_action. Caller supplies the change list (paired with git diff or
get_recent_changes). Mirrors jcm's get_pr_risk_profile. Tool count 58 → 59.

## v1.62.0 — `doc_health_radar` + `diff_doc_health_radar` (Phase-2 sibling-parity)
Six-axis 0-100 doc-index health scorecard: freshness, link_integrity, orphan_health,
embedding_coverage, role_coverage, drift_health (omitted when no canary). Pure-function
diff helper alongside. Third leg of the suite-wide radar pattern (jcm + jData).
Tool count 56 → 58.

## v1.61.0 — explicit-paths indexing
`index_local(paths=[...])` skips the directory walk and indexes exactly the
listed files / subdirs. Each entry is validated against the root the same
way walked files are (path-traversal, symlink-escape, unsupported-extension
all warn-and-skip). CLI: `jdocmunch-mcp index-local --path <dir> --paths-from FILE`
(use `-` for stdin) — composes with `find` / `fd` / `rg`. Additive: omitting
`paths` preserves every existing call shape. Helper `_load_paths_from_arg`
in `server.py` parses the file/stdin (strips blanks + `#` comments).

