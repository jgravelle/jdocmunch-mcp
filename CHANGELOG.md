# Changelog

## [Unreleased]

### The aside came out of the LICENSE

`He's kinda full of himself.` sat inside condition 2, in the middle of the
derivation-and-attribution obligation. It stays in jCodeMunch's README, where it
reads as the author's voice; a licence is the document a customer's counsel reads
before allowlisting, and a joke inside an operative clause makes a reader stop
and work out whether it is operative.

⚠ **Editorial, and deliberately so.** It grants and removes nothing, so the
licence version and the identifier are untouched — a downstream allowlist on
this identifier is not churned. jcodemunch-mcp #521 pins that distinction with a
digest; these files state no licence version yet, so the same routing is a
convention here rather than a check.

### PyPI published no license identifier at all (#122, @marcelruhf)

`pyproject.toml` had no `license` key, so PyPI left `info.license` and
`info.license_expression` empty and a commercial user could not allowlist us
by identifier. Packaging metadata is PEP 639 now:
`license = "LicenseRef-jDocMunch-Dual-Use"` plus
`license-files = ["LICENSE"]`.

The LICENSE file is unchanged and still ships.

⚠ **PyPI metadata is immutable per version, so this starts at the next release.**
Every version up to 1.135.0 keeps empty license fields.

⚠⚠ **The report named one surface and the licence is declared on three.**
`.claude-plugin/plugin.json` and the mcpb manifest both said
`LicenseRef-Dual-Use` — no product prefix — so an allowlist keyed on the
identifier still needed two entries. That is the reported defect one surface
over, and fixing only what was reported would have left it. Both now name the
same identifier, and the mcpb generator DERIVES it from `pyproject.toml` rather
than carrying its own copy, which is how the two spellings drifted apart to
begin with.

⚠ **LICENSE has no Version line, so the identifier has no version suffix.**
jcodemunch-mcp #518 pins the suffix to LICENSE's `Version X.Y` so 1.2 cannot
ship under 1.1's identifier. The same ratchet is here: if a Version line
appears without a matching suffix (or the reverse), the build fails.
`tests/test_license_identifier_agreement.py` is that pin.

## [1.135.0] - 2026-08-18 - list_repos stops parsing the files it throws away

`list_repos` globbed `*/*.json` and excluded only `_`-prefixed files and
`.summary.json`, so it opened and json-parsed every `.terms.json`,
`.related.json`, `.boilerplate.json` and `.duplicates.json` sidecar in the
store, then discarded each one for lacking primary-index fields. Reported by
@rknighton ([#121](https://github.com/jgravelle/jdocmunch-mcp/issues/121)) with
a controlled 75-index comparison: adding only the 300 auxiliary sidecars owned
by those same indexes moved the median from 2,044.2 ms to 3,459.5 ms with
non-overlapping ranges, and 300 extra parses. This is a documented first-call
hot path, also hit by the PreCompact snapshot hook.

⚠⚠ **The filter is keyed on the SIDECAR SUFFIX, not on whether a primary index
sits beside it, and the reporter is the reason.** Keying on the primary fixes
the live case and leaves the worse one alone: a store that lost an index to a
pre-1.108.0 `delete_index` still carries all four of its sidecars, and those
were being parsed in full to return no row at all. Their store held 1,093 such
files — **2.0 GB opened on every call to produce nothing**, of which
`.related.json` was 98.3% and a single file was 1.24 GB. Their reproduction
ships both arrangements for exactly this reason.

⚠ **The counts are the assertion, not the rows.** The primary-absent case
returns zero repositories before and after, so a row-count test passes against
the unfixed code. The regression tests patch `json.load` and assert on what was
opened.

⚠ **Repo names may contain dots** (`is_safe_path_component` allows
`[A-Za-z0-9._-]`), so a repo genuinely named `api.related` writes its PRIMARY
monolith to `api.related.json` — a bare suffix test would have quietly removed
it from the listing, trading a performance fix for a repo that stopped
existing. A candidate that looks like a sidecar is readmitted when it has its
own `.summary.json`: every index saved since jdoc#77 writes one, and nothing
anywhere writes a summary beside a real sidecar. One `stat`, no parse.
⚠ A pre-jdoc#77 legacy index whose name ends in a sidecar suffix has no summary
to vouch for it and is not listed — recorded rather than solved, because the
only way to tell it from an orphaned sidecar is to open the file, which is the
cost being removed.

⚠⚠ **The suffix tuple existed in three hand-copied places and the copy that
mattered was never written at all.** `delete_index` had one, `_leftover_artifacts`
had another, `list_repos` had none — which IS #121. Now one
`INDEX_OWNED_SIDECAR_SUFFIXES` in `storage/doc_store.py`, read by all three,
with a test that derives each suffix from the module that WRITES it and fails
if one is missing. A fifth sidecar cannot be added without joining the list.

⚠ One test asserts each suffix **alone**, with no siblings beside it. A filter
that reasoned from the sidecar set (`.related` is a sidecar because `.terms`
sits next to it) would pass every other test here and then parse that 1.24 GB
file once its peers were cleaned up by hand.

Measured locally on a synthetic 40-index store, 23.1 MB of which 22.8 MB is
`.related.json` — **not** the reporter's fixture, which is private: json loads
200 → 40, median 188.9 ms → 8.0 ms over 5 runs. The ratio is a property of that
store's sidecar bytes, so take the load counts as the durable claim and the
milliseconds as an illustration.

Tests `tests/test_jdoc_121_list_repos_sidecars.py` (13). ⚠ The file cannot
IMPORT pre-fix, so non-vacuity used a behaviour-only subset: **9 fail / 1
pass**, the pass being the `_`-prefix control. Suite **2582 / 6**;
`ruff check src/` clean. No tool, schema or INDEX_VERSION change.

## [1.134.1] - 2026-08-16 - The tag attests the artifact

Provenance repair. No code change, no behavior change, nothing to act on if you
are already running 1.134.0.

1.134.0 was built from a working tree that carried uncommitted tool-description
edits from a concurrent editing session, so the published package did not match
the `v1.134.0` tag. **The delta was the `description=` text of 20 tools** — no
schema change, no code. Verified two ways: every shipped module diffed against
the tag, and only `server.py` differed at all; then both versions of `server.py`
compared with every description value stripped, which produced zero differences.

⚠ Corrected after 1.134.1 shipped: this entry first said "12 `description=`
strings across ten tools". That figure came from counting changed LINES — a
multi-line description contributes one changed line and its continuations
contribute none, so it undercounted both numbers. The description-only finding
was re-verified and stands; only the count was wrong.

PyPI cannot be re-uploaded, so the fix is forward: those descriptions are now
committed, and 1.134.1 is built from a clean worktree. **1.134.1 is byte-equal to
the 1.134.0 that shipped**, plus the version bump — the point is that this time
the tag attests it. Nothing an existing user has is revoked, which is why the
descriptions were carried forward rather than reverted to the tag.

The improved descriptions are additive prose on `doc_list_repos`,
`doc_index_repo`, `list_docs`, `get_index_overview`, `get_sections`,
`get_section_excerpts`, `get_document_outline`, `get_backlinks`,
`get_orphan_sections` and `get_watch_status` — each saying what the tool does
*not* cover, so an agent stops inferring coverage the tool never had.

⚠ The build reads the WORKING TREE, not `HEAD`. A shared checkout with a second
session live in it is not a release-safe build directory. This release was built
from a dedicated `git worktree`, which is the standing remedy and was not applied
to 1.134.0.

### Twenty tools that never named a boundary

The descriptions above were not an accident of content, only of timing. They are
the jdocmunch share of a suite-wide pass against the tool-description rubric in
[arXiv:2602.14878](https://arxiv.org/abs/2602.14878) (Hasan, Li, Rajbahadur, Adams
& Hassan, Queen's University), which scored 856 tools across 103 MCP servers and
found 97.1% carrying at least one description smell. Method, both scoring frames,
and the before/after numbers live in jcodemunch-mcp under
`benchmarks/description_smells/`.

Across the three servers, 99 of 194 tools carried the Unstated Limitation smell:
they never said what they do not return, when they refuse, or when an empty result
means "nothing matched" rather than "nothing is indexed". jdocmunch was flagged on
35 tools, of which 19 were real gaps and the rest were phrasing the scanner failed
to match. Those 19 got a clause each, and `doc_list_repos` was rewritten outright
from a single sentence, which is the twenty above.

Every clause is grounded in the tool's own behaviour rather than written to satisfy
the rubric. `delete_index` now says it never touches your source documents and that
there is no undo. `get_section` says nested child sections are not included, which
is the difference between a thin answer and a wrong one. `doc_health_radar` says it
grades the index and not the prose, because none of its six axes read the writing.
`get_orphan_sections` says inbound links are counted across indexed docs only, so a
link from code does not rescue a section.

## [1.134.0] - 2026-08-16 - The installed watcher runs the flags you chose

[#120](https://github.com/jgravelle/jdocmunch-mcp/issues/120), reported by
@williamblair333. `watch --no-ai-summaries` worked in the foreground;
`watch-install` wrapped that same daemon and could not pass it through, because
its parser took no arguments and `_exec_cmd()` was a constant. So a corpus
indexed with `--no-ai-summaries` regained summaries the first time the installed
watcher touched it — no prompt, no log line.

`watch-install` now takes `watch`'s flags, `--no-ai-summaries` and `--quiet`,
under the same spellings, and threads them into the argv on all three platforms.
One source of truth for what the daemon does, whether it runs in the foreground
or at login.

The asymmetry underneath is what made this bite. `index-local`'s file exclusions
persist as `corpus_shape_patterns` and a later refresh that omits them inherits
them ([#116](https://github.com/jgravelle/jdocmunch-mcp/issues/116));
`use_ai_summaries` has no such persistence and is a per-call argument, so the
watcher's default answered a question the user had already answered.

**The hand-edit is still reverted, and that part is deliberate.** Every installer
rewrites its whole service definition, which is why the reporter's `systemctl
edit --full` did not survive an upgrade. Merging a user's argv with a generated
one makes what the service runs unpredictable, so the rewrite stays — but
`watch-install` now reports the definition it replaced, on stdout as
`replaced_exec` and in words on stderr. The revert was the smaller half of the
complaint; the silence was the rest of it.

Also disclosed: the README's background-behavior section now describes the login
service itself — what it runs, which flags it carries, where it logs, and how to
remove it. It previously covered the embedding child process and said only that
`watch-install` registers a service when you run it.

⚠ Reading the installed definition back is comparison-only and fails quiet. A
`schtasks` reading in a non-English display language, an unreadable plist, or a
missing unit all report nothing rather than guessing — a false "we overwrote your
customisation" is worse than no warning.

Tests `tests/test_jdoc_120_watch_install_flags.py` (30; **22 fail pre-fix**, 8
controls pass both sides). One of those controls earned its place: the first cut
split `ExecStart` back into an argv with `shlex.split`, which eats backslashes in
POSIX mode, so an interpreter path containing one never round-tripped and every
re-install claimed a customisation nobody had made. Suite **2569 / 6**;
`ruff check src/` clean. Additive, no INDEX_VERSION or tool-count change.

## [1.133.0] - 2026-08-13 - The embedding import leaves the server, and the choice goes with it

[#118](https://github.com/jgravelle/jdocmunch-mcp/issues/118) closed properly.
`sentence-transformers` now loads in a **child process**, on by default.

v1.132.0 shipped a switch, not a fix, and said so: the import was either on the
main thread (a cold `torch` load measured **73.77 s**, past a client's 30 s
connect timeout) or on a background thread (Windows loader lock, wedged
indefinitely). Both contracts cannot hold in one process — so the process was
the thing to change. The import now runs concurrently with the live server
**and** alone in its own loader, which makes backgrounding safe again.

**The default is the fix.** Leaving the worker opt-in would have added a third
switch to a pile of two and left the user picking between outages, which is the
defect restated as configuration. `JDOCMUNCH_EMBED_WORKER=0` opts out;
`JDOCMUNCH_PRELOAD_EMBEDDINGS=1` still selects v1.132.0's main-thread import for
anyone who chose it.

Measured here (deltas, not absolutes): `initialize` with the worker is
**indistinguishable from the in-process default** — +0.04 s across three runs,
inside run-to-run noise — against **+5.6 s** for the v1.132.0 preload; 1000 sections embed in **1.72 s**
through the pipe against **1.64 s** in-process. The handshake cost is a spawn,
not an import, so it does not grow on a healthy install.

**What crosses the boundary is one method**, `embed_texts`. Provider detection,
the HuggingFace-cache probe, cache keys, the sidecar, identity headers and
rotation detection all need no import and stay in the server. numpy stays too —
`doc_store` and `related_persist` use it to *score*, and a section matrix down a
pipe per query would be absurd; the existing numpy preload still covers them.
The server now loads numpy and nothing else native.

Details that are load-bearing rather than incidental:

- **A spawn failure falls back to the in-process provider.** Without that,
  defaulting the worker on would silently remove semantic search from machines
  with an unusual `sys.executable` — a new defect traded for the old one. A
  child that dies *later* does not fall back, because by then the machine has
  proven it can spawn and importing the stack into a running server would trade
  a degraded feature for the deadlock.
- **The cache identity is unchanged.** `_provider_identity` still reports `None`
  for this provider's dim; adopting the child's reported dim would fail
  `identity_matches` on every existing sidecar and re-embed every corpus.
- **A timeout raises** rather than returning empty vectors, so `embed_sections`
  records `embed_failed` and preserves the sidecar. Requests chunk at 256,
  because `embed_sections` submits every cache miss in one call.
- **A wedged child is killed.** That is the property a thread cannot offer, and
  the reason this is a fix rather than a relocation.
- The child claims a private stdout for its protocol, inherits stderr, and exits
  when the server does. It opens no network connection and registers nothing —
  disclosed in the README's new "Background behavior, fully disclosed" section.

⚠ **The wedge itself still does not reproduce on the machine that reported it**,
so there is no A/B and this does not claim one. What is asserted instead is
stronger and deterministic: after a real embed, the server process's
`sys.modules` contains none of `sentence_transformers`, `transformers`, `torch`,
`scipy` or `sklearn`. The deadlock is unreachable rather than unobserved.

## [1.132.0] - 2026-08-12 - The loader deadlock has a stack, and the fix has a price tag

[#118](https://github.com/jgravelle/jdocmunch-mcp/issues/118) named at last, with
a native stack instead of a hypothesis. `py-spy dump --native` on a wedged
server, twice 25 s apart, identical:

```
ZwWaitForAlertByThreadId (ntdll)
RtlSleepConditionVariableCS
RtlEnterCriticalSection
<libopenblas64_...>            <- DllMain
LdrLoadDll / LoadLibraryExW (KERNELBASE)
```

The Windows **loader lock**, inside OpenBLAS's `DllMain`, reached through
numpy's `_multiarray_umath`. Not the GIL, not CPython's import lock — both
candidates in the report are ruled out, and "both threads idle" is explained: a
thread parked in `ZwWaitForAlertByThreadId` samples as idle. The Python-visible
half is a second thread stuck in `threading.Thread.start()` from
`subprocess.communicate` in `local_git_head`, because a new thread needs that
same loader lock to run its `DLL_THREAD_ATTACH` callbacks and so never sets
`_started`. Reproduced 7 runs in 8; an idle server never wedges, because with
nothing else running there is no second party to deadlock against.

⚠ **NOT the OpenBLAS thread pool**, which was the first explanation offered.
`OPENBLAS_NUM_THREADS=1` was measured against that reproduction and it **still
wedged**; at one thread the pool is never spawned.

**What ships, and what each piece actually covers:**

| | covers | default |
|---|---|---|
| subprocess import probe | an import that RAISES | on |
| `JDOCMUNCH_PRELOAD` (numpy) | paths reaching numpy without sentence-transformers | on, Windows |
| `JDOCMUNCH_PRELOAD_EMBEDDINGS` | the wedge itself, whole chain | **off** |

⚠⚠ **A subprocess probe cannot fix a deadlock, and shipping it as though it
could was the mistake this release corrects.** It answers "would this import
raise?" — a probe subprocess is single-threaded, so on a healthy install it
returns True and `warmup` then runs `embed_query`, which imports
sentence-transformers **on the warmup thread** beside the live server. That is
the wedge condition exactly. It rescued the machine it was written on only
because sentence-transformers genuinely raises there. Kept anyway: a provider
whose import raises is unusable, and learning that up front beats learning it at
the end of a heavyweight import chain.

⚠ Preloading numpy alone is also insufficient — the chain loads scipy, sklearn
and torch, and the issue's FIRST dump is wedged in
`scipy/sparse/linalg/_svdp.py`, a different DLL from the numpy one in the
second.

⚠⚠ **Why the real remedy is OFF by default: it collides with
[#110](https://github.com/jgravelle/jdocmunch-mcp/issues/110) and the collision
is structural.** The import is either on the main thread (slow handshake) or on
a background thread (possible indefinite hang); there is no third option in one
process. Windows nearly took the cost by default on the strength of two
handshake readings, 6.5 s and 11.4 s against a ~1.0 s baseline — then a **cold**
run of the same import took **73.77 s**, against 5.71 / 5.51 / 5.53 s warm.
torch is gigabytes of DLLs and the first import after a boot pays for all of
them, so the slow end of a 13x spread lands on the first server start after a
reboot: past a 30 s connect timeout, i.e. #110's outage verbatim. That trades a
probabilistic hang for a fairly reliable cold-start failure, so the switch goes
to whoever knows which one they have. **Wedged users set
`JDOCMUNCH_PRELOAD_EMBEDDINGS=1`.** Measured end to end: default handshake
1.13 s, opt-in 6.91 s.

⚠ torch/scipy/sklearn are **not** new cost — declared dependencies of
sentence-transformers that `warmup` has always loaded (verified: all five of
numpy/scipy/sklearn/transformers/torch reach `sys.modules` even on the failing
import). Nothing here adds work; it only moves where the work happens.

⚠⚠ **VERIFICATION STATUS, stated because this issue has already produced two
retractions: the mechanism is measured, the remedy is not.** There is no A/B.
The wedge stopped reproducing after ~30 server starts in **both** arms — the
same cold/warm effect the 73.77 s reading later made concrete. A first attempt
at an A/B was invalidated outright when a concurrent editing session changed
`provider.py` mid-experiment, so every later trial in both arms was already
running the other fix. Do not read a green run as proof.

Also fixed: four warmup tests that shelled out to the real sentence-transformers
and so asserted a property of the developer's site-packages. `#110`'s handshake
guard is intact and unchanged by default, with a note on why #118 must not
quietly relax it, plus a separate banner-asserting test for the opt-in path — a
ceiling in seconds is the runner-speed assertion #114 warned about.

Suite **2495 passed / 6 skipped**; `ruff check src/` clean.

## [1.131.1] - 2026-08-11 - A lexical corpus stops paying for numpy

Follow-up to 1.131.0, found by running it.

`_semantic_edges_matrix` imported numpy as its **first statement**, then returned
`{}` a few lines later whenever no section carried an embedding. For a corpus
indexed with `use_embeddings=False` that import is pure cost inside a function
guaranteed to produce an empty map.

⚠ **1.131.0 turned it into a PER-REFRESH cost.** Putting the sidecar rebuild on
the incremental path (#117) was right, but it also put this import on the path a
watch/refresh loop takes every single time. Previously only a full re-index paid
it. Fixing #117 without this is trading unbounded sidecar staleness for a
recurring import.

The early-out now runs first; numpy is imported only when there is a matrix to
build. Output is unchanged, and the reorder's safety is asserted rather than
argued: with no vectors the function returns `{}`, and `build` maps an absent id
to `[]` — exactly what the numpy-missing `None` fallback produces for the same
corpus. Both paths are pinned in the new tests.

⚠ **This is a real cost fix, but on the machine where it was found it is a
WORKAROUND for something else.** There, `import numpy` inside the running server
does not merely run slowly — it wedges: the same C-extension frame
(`numpy/core/overrides.py:8`) across dumps 30 and 50 minutes apart, while the
identical import takes 0.10 s standalone, 0.10 s on a worker thread, and 0.10 s
with the #110 fd swap replayed. That is
[#118](https://github.com/jgravelle/jdocmunch-mcp/issues/118), it is not
explained, and this release does not fix it — it only stops the lexical path
from reaching the import.

Tests `tests/test_jdoc_119_no_numpy_when_lexical.py` (6; **4 fail pre-fix**, 2
controls pass both sides). Suite **2439 passed / 6 skipped**; CI-equivalent
**2436 / 9**; `ruff check src/` clean.

## [1.131.0] - 2026-08-11 - The derived sidecars refresh on every path, and say when they don't

Found in-house on a 253-file corpus whose `.related.json`, `.terms.json`,
`.boilerplate.json` and `.duplicates.json` were three days older than the index
beside them, despite many refreshes in between.

### An incremental refresh never rebuilt them ([#117](https://github.com/jgravelle/jdocmunch-mcp/issues/117))

All four sidecar writes lived after `save_index` on the **full-index** path. The
incremental path returns before reaching them, so any index kept alive by
incremental refreshes served sidecars from whenever the last *full* index ran.
`get_related_sections` reads that file through `related_persist.lookup`, so its
answers described an arbitrarily old corpus. Staleness was unbounded, and
silent: the path never attempted the write, so there was no failure to see.

⚠⚠ **The naive fix is a silent WIPE, and it is worse than the staleness.**
Persisted section dicts carry no body text — `Section.to_dict` drops `content`
to keep the monolith small, and search re-reads it by byte range at query time.
Rebuilding the sidecars from those dicts would hand all four builders empty
strings: the glossary empties, boilerplate finds nothing, dedup finds nothing,
and every one of them reports success. Sections are therefore **hydrated**
before the rebuild — through the store's byte-range loader for untouched
documents, shadowed by this run's in-memory text for the files it just changed,
whose new bytes are not yet on disk under the old offsets.

⚠ The regression test for this is not the mtime check, which an empty rebuild
would also pass. It asserts that a term belonging to an **untouched** document
survives an incremental refresh, which is only possible if the body was re-read.

### A failed sidecar looked exactly like a clean one

Three of the four were wrapped in a bare `except Exception: pass`. #103 already
made this argument for the near-duplicate sidecar — "a silent skip is
indistinguishable from *no duplicates found*" — and it was never generalised.
All four now report through a new `sidecars_skipped` block on both paths, naming
the sidecar and the reason.

⚠ `dedup_skipped` is **retained** alongside it rather than folded in: it already
ships, and 1.x forbids removing a response key.

Both sidecar writes now go through one `_write_sidecars` helper, so the two
paths cannot drift apart again. Tests `tests/test_jdoc_117_sidecar_refresh.py`
(10). ⚠ The file cannot import pre-fix, so non-vacuity was proven with a
behaviour-only subset: **2 fail / 2 pass** across the fix. Suite **2433 passed /
6 skipped**; CI-equivalent (`uv run --python 3.13`) **2430 / 9**.

⚠ Fixtures are sized at 12 documents on purpose. Under ~5 the incremental path
re-materializes everything and the defect hides — the jdoc#107 lesson.

### Not fixed here, and not this repo's bug

#117 was filed claiming `index_local` takes 40–70 minutes on that corpus. It
does not: measured in-process it is **3 seconds**, on the same corpus, arguments
and index. The stall is real but lives in the MCP transport path between client
and server, not in this tool. Recorded on the issue rather than left implied.

## [1.130.0] - 2026-08-10 - A corpus exclusion survives every re-entry point

Two independently reported defects with one shape: a narrower corpus established
at index time was silently widened by a later entry point that said nothing
about corpus shape.

### The CLI destroyed a stored exclusion it could not express ([#116](https://github.com/jgravelle/jdocmunch-mcp/issues/116))

Reported by @pnm-jgb. The MCP `index_local` tool accepts `extra_ignore_patterns`;
the `index-local` CLI did not. Passing no patterns computed a `full` corpus
selection that **overwrote** the stored `full+shape:<hash>`, re-admitting every
deliberately excluded file. Third and last member of the
[#108](https://github.com/jgravelle/jdocmunch-mcp/issues/108) set, and worse than
the two fixed there: the CLI did not merely fail to *express* the setting, it
*destroyed* one already persisted.

⚠⚠ **The reported remedy would have been worse on its own.** "Preserve the
stored selection" was the right target, but only the **digest** was persisted,
never the patterns: `corpus_selection` records *that* a corpus was shaped and
never *how*. An inherited descriptor would therefore assert an exclusion the walk
could not reapply, giving an index that claims `full+shape:...` while containing
the excluded file. The pre-fix behaviour at least **disclosed** the widening.
Persisting the patterns is what makes inheritance honest.

Three parts:

1. **`corpus_shape_patterns` is persisted** beside the descriptor, through all
   five persistence paths. Written only when non-empty, so unshaped and legacy
   indexes gain no key and legacy files stay byte-identical.
2. **`None` and `[]` now mean different things.** `None` ("the caller said
   nothing" — the CLI, a watch refresh, every silent re-entry point) inherits.
   `[]` ("explicitly none") widens with the `corpus_selection_changed`
   disclosure. Resolved *before* discovery, because inheritance has to change
   which files the walk visits, not merely which descriptor is stored.
3. **`--extra-ignore-pattern`** (repeatable) and **`--no-extra-ignore-patterns`**.
   The clear flag maps to `[]` rather than `None`, because mapping it to `None`
   would mean "inherit" and do the opposite of its name.

⚠⚠ **This reverses a deliberate earlier decision.** jdoc#82's
`test_changed_ignore_selection_reconciles_and_discloses` asserted that a silent
refresh widens *and* discloses — the behaviour #116 reports as the bug. jdoc#82's
stated rule is "stored coverage never shifts under an unchanged identity", and
inheritance satisfies it more strongly: neither side moves, so there is nothing
to disclose. The old test pinned one *instance* of the rule, not the rule. It has
been rewritten to assert the invariant, with the disclosure path moved onto the
explicit `[]` branch. **Disclosure is not a safeguard when the entry point cannot
avoid triggering it.**

⚠ Indexes created before this release carry `full+shape:<hash>` with no stored
patterns, so there is nothing to reapply and they still widen on the next
refresh — but they now **warn** that the shape is unrecoverable and name the
remedy. Re-run once with the patterns to make them durable.

⚠ `_index_to_dict` is an explicit **allow-list**, not `asdict()`. The new field
round-tripped as empty through the dataclass, `save_index`, `update_index` and
`load` until it was named there. Any future field-adder hits this.

### The watcher re-admitted what discovery excluded ([#115](https://github.com/jgravelle/jdocmunch-mcp/issues/115))

Reported by @MotoMato85 with a complete two-session reproduction and acceptance
criteria adopted verbatim. After a full index excluded a file via the source
root's `.gitignore`, editing that file made `watch` add it, and its sections
became retrieval candidates.

⚠⚠ **The fix is in `watch.py`, not in `index_local`'s `paths=` branch, and the
reporter said so first.** A caller naming a file explicitly and bypassing
`.gitignore` is intentional and documented (SPEC.md, the 1.61.0 changelog): a
human asking for a specific generated file should get it. The watcher is not that
caller — it manufactures its path list from filesystem events, so the bypass
fires for files nobody asked for. jCodeMunch splits the same way for
`CACHEDIR.TAG`: explicit paths opt past the rules, the watcher fast path applies
them. A test guards that contract and passes on both sides of this change.

⚠ **The two fixes interlock**, and neither issue could have seen it because
neither fix existed yet. Once #116 made exclusion patterns durable, a watcher
ignoring them would reinstate pattern-excluded files. The filter applies both the
source root's `.gitignore` and the stored `corpus_shape_patterns`.

Filtering happens before `index_local` is called, so a batch of only-ignored
edits cannot log "re-indexed 1 file(s)" for work that did not happen.

### Tests

`tests/test_jdoc_116_corpus_shape_inheritance.py` (10; 7 fail pre-fix) and
`tests/test_jdoc_115_watch_respects_gitignore.py` (6; 4 fail pre-fix). Suite
2423 passed, 6 skipped, 0 failed.

## [1.129.0] - 2026-08-09 - JSON-RPC owns a private stdout, so provider init leaves the startup path

Closes [#110](https://github.com/jgravelle/jdocmunch-mcp/issues/110), reported
by [@pnm-jgb](https://github.com/pnm-jgb). 1.128.0 fixed only the outage half;
this is the rest, and it removes the reason the limitation existed.

### The real constraint

Warmup was never an optimization. It existed so a model load finished *before*
`stdio_server` owned stdout, because any other write to that stream breaks a
framed response. That is what pinned provider init to the startup path and cost
~7.6 s on every launch.

⚠⚠ `contextlib.redirect_stdout` could not lift the constraint: it rebinds
`sys.stdout` and nothing more. It cannot catch a C extension calling
`write(1, ...)` (tqdm, tokenizers, torch), a subprocess that inherited fd 1, or
another thread — and those are exactly the writers a model download produces.

### The fix

`stdio_guard.claim_stdout()` duplicates the real stdout, points file descriptor
1 at stderr, and hands the duplicate to the transport, which already accepts an
explicit `stdout`. Afterwards fd 1 **is** stderr for the whole process — Python,
native code and child processes alike — and the JSON-RPC stream is reachable
only through the handle the transport holds. The guarantee is structural rather
than a rule somebody has to remember at each new call site.

Warmup then moves to a background daemon thread. It still declines an uncached
model: downloading hundreds of megabytes unasked at every start, for a feature
the session may never use, is not something to do in the background either.

**Measured, same tree, provider vs `none`:**

| | `none` | `sentence-transformers` | provider cost |
|---|---:|---:|---:|
| v1.128.0 | 6062 ms | 13109 ms | **+7047 ms** |
| v1.129.0 | 4339 ms | 4245 ms | **-94 ms** |

The +7.0 s matches the reporter's independently measured ~7.6 s. (Baselines
differ between trees from cold-cache variance; the comparison that counts is
provider-vs-`none` within each tree.)

⚠ `_get_provider` is now lock-guarded. Construction became reachable from two
threads at once — the warmup and a tool call arriving before it finishes — and
without the lock both would build a provider, meaning two simultaneous model
loads with one silently discarded.

⚠ The existing guards stay. The jdoc#19 warmup ordering and the jdoc#65 CLI
`redirect_stdout` wrappers are now belt-and-braces; removing them in the same
pass is how a safety improvement becomes an incident.

⚠ Fails open. Under pythonw, or a harness that replaced `sys.stderr` with a
non-file object, the swap is skipped, the server starts as before, and it says
so on stderr. A server that starts with the old hazard beats one that will not
start.

### Verification

`tests/test_stdio_guard.py`, 10 tests, deliberately driven through real
subprocesses — an in-process test of a descriptor-level swap would be testing
the mock. Coverage includes the native `os.write(1, ...)` case, an inherited
child process, a background thread, unbuffered delivery, buffered-output
ordering, UTF-8, and both fail-open paths. The handshake test asserts the
*delta* between providers rather than an absolute time, since an absolute bound
would be a runner-speed assertion — the mistake jdoc#114 was about.

## [1.128.0] - 2026-08-09 - Every open issue closed: private corpora, startup cost, CLI opt-outs

Closes [#108](https://github.com/jgravelle/jdocmunch-mcp/issues/108),
[#110](https://github.com/jgravelle/jdocmunch-mcp/issues/110),
[#112](https://github.com/jgravelle/jdocmunch-mcp/issues/112) and
[#114](https://github.com/jgravelle/jdocmunch-mcp/issues/114). #110 and #112
were reported by [@pnm-jgb](https://github.com/pnm-jgb).

### #112 — a local summarizer target

Every valid `JDOCMUNCH_SUMMARIZER_PROVIDER` was remote cloud, so a private
corpus could have AI summaries or privacy, never both. This is not cosmetic:
the summary is embedded alongside the section, and the content itself is capped,
so for a long-section corpus the summary is the only channel through which
anything past that cap reaches the vector — retrieval quality was gated behind
exporting the corpus to a third party.

`openai-compatible` now joins the summarizer providers, configured with
`JDOCMUNCH_SUMMARIZER_URL` + `_MODEL` (+ optional `_API_KEY`), mirroring the
embedding side. The client machinery already existed — `_make_openai_compat`
serves openai, minimax and glm — only a configurable endpoint was missing.

It is deliberately **not** in `_PAID_CLOUD_PROVIDERS`, matching the embedding
precedent: requiring an explicit URL *and* model is itself the opt-in, and it
cannot be reached by a stray ambient key. A configured local target now
**outranks every cloud key** in auto-detect — falling through to a billed remote
provider while a local model sits configured would be the wrong default. An
explicit `none`, or an explicitly named cloud provider, still wins.

⚠ Naming the provider bypasses the configured-check, so a half-configured setup
fails at construction with a message naming the missing variable, rather than
later as an opaque connection error mid-index. Indexing continues with heuristic
summaries.

### #108 — the CLI can decline

`index-local` gained `--no-ai-summaries` (same spelling as `watch`) and
`--embeddings auto|on|off`, with `--no-embeddings` as an alias. The MCP tool
could express both per call and the CLI could express neither, so the documented
route sent section text to whatever summarizer the environment exposed with
nothing in `--help` saying so. Purely additive; no default changed.

### #110 — an uncached model no longer blocks the handshake

`serve` initialized the sentence-transformers provider before answering
`initialize`: ~7.6 s on every start with a cached model, and with an **uncached**
one the download landed in the same window. A 440 MB model pushed past the
client's 30 s connect timeout, so the server never registered and the error said
only "connection timed out" — naming neither models nor downloads. Changing one
env var became a one-cycle outage.

Warmup is now skipped when the model is not already in the local HuggingFace
cache, so the load moves to first use where it can report a real error.
`JDOCMUNCH_EMBED_WARMUP=0` skips it entirely.

⚠⚠ **Not** done as background warming, which is what the report asked for.
Warmup exists so the model load completes *before* `stdio_server` owns stdout;
`contextlib.redirect_stdout` is process-global, so a load running concurrently
with JSON-RPC cannot be redirected and its progress chatter would corrupt
framing for every request. Skipping is safe, backgrounding is not, and the
failure mode of getting that wrong is worse than the bug. The cached ~7.6 s
therefore remains by default — the outage is fixed, the fixed cost is opt-out.

⚠ Two bugs in the cache probe, both caught before release: a bare model name is
not the cache key (`all-MiniLM-L6-v2` lives under
`models--sentence-transformers--all-MiniLM-L6-v2`, so checking the literal name
reported the *default* model uncached on every machine that has it), and
`os.altsep` is `/` on Windows, so every org-qualified hub id was being probed as
a filesystem path — uncached on Windows and nowhere else. The probe fails open.

### #114 — a test asserted against a budget it did not own

`test_internal_record_lock_coordination_keeps_bounded_wait` asserted the whole
`delete_index` round trip finished in under 1.0 s while the step it waits on is
permitted exactly 1.0 s — unsatisfiable at the boundary, not merely tight. It
went red at 1.588 s on a Windows runner during the 1.127.0 release. The budget is
now the named `RECORD_LOCK_WAIT_SECONDS`, imported by the tests instead of
restated, and the upper bound distinguishes bounded from unbounded while the
behavioural asserts carry the rest.

## [1.127.0] - 2026-08-09 - An embedding-model rotation no longer leaves the index unqueryable

Closes [#109](https://github.com/jgravelle/jdocmunch-mcp/issues/109) and
[#111](https://github.com/jgravelle/jdocmunch-mcp/issues/111), both reported by
[@pnm-jgb](https://github.com/pnm-jgb) with root-cause analysis read off the
source and, for #111, measurements over a real 1,992-section corpus.

### The defect

Change the embedding model, re-index a corpus whose files have not changed, and
the run reported `success: true` while leaving the old vectors on disk. Every
search afterwards failed:

```
matmul: Input operand 1 has a mismatch in its core dimension 0 (size 768 is different from 384)
```

The index was unqueryable, not merely stale, and the only documented recovery
was `delete-index` plus a full rebuild.

⚠ **The reported line was not the one that fired.** The report locates the bug
at the incremental path's `embed_sections` call, where `entries` ends up empty
and an `if entries:` guard skips the write. That mechanism is real, but with
zero changed files `index_local` returns from the **"No changes detected"**
branch further up and never calls `embed_sections` at all. Fixing only the
guard would have left the reported repro broken. Detection now sits before the
incremental branch, so both paths are covered.

Two further failure sites turned up while confirming it, neither of them filed:

- **A sidecar can hold two widths at once**, after a rotation that touched some
  files. The old single-matrix build called `np.asarray` on ragged rows, which
  raised before any query was scored.
- ⚠⚠ **Without numpy there was no error at all.** `cosine_similarity` zips the
  two vectors, so a 768-dim query against a 384-dim vector truncates to the
  shorter and returns `0.707` — an ordinary-looking similarity. The numpy path
  failed loudly; this one returned confident garbage and recorded nothing. That
  is the worse of the two, and it was invisible.

### The fix

**Indexing escalates.** `cache.identity()` reports the sidecar's stored
`{provider, model, dim}`, or `None` when there is no sidecar. `load()` returned
`{}` for both cases, which is precisely why nothing could act on a rotation —
no caller could tell a first index from a model change. When the stored
identity does not match the active one, the run re-embeds the whole corpus
rather than the changed subset.

**The escalation is disclosed**, as `embedding_rotation: {from, to, action}`. A
caller who asked for an incremental refresh and got a full corpus re-embed is
owed the reason.

⚠⚠ **A paid cloud provider is never auto-escalated.** `watch.py` calls
`index_local` from a long-running daemon on every file-change batch and prints
only `re-indexed N file(s)`, so an unattended service would re-send the entire
corpus to a billed third party with the disclosure reaching nobody. Identity
can also change with no user action at all — an `openai-compatible` endpoint
restarted on a different model reprobes a new dimension — so "they changed the
model, so they want this" does not hold. Same reasoning as jcodemunch's
`refresh` forcing summaries off: a scheduled job must not bill unasked.

On `openai` or `gemini` the run instead returns
`action: "rebuild_required"` with the old vectors left intact, and search
degrades and discloses rather than failing. `JDOCMUNCH_ALLOW_PAID_EMBEDDINGS=1`
— the consent signal that already existed for this exact hazard, rather than a
second knob — restores automatic escalation; `--rebuild` is the explicit
one-shot override. Local providers are never gated.

⚠ **A provider outage during a rotation must not destroy the vector store.**
Purging on an empty pass is right when the corpus produced no vectors and is
data loss when `embed_texts` merely threw: the sidecar would be emptied and the
*new* header written over it, so the next run saw a matching identity and never
re-embedded — permanent and silent, jdoc#107's exact shape. Found while
reviewing the paid-provider question; the purge is now conditional on the embed
pass having actually succeeded.

**Querying degrades instead of dying.** Embedding matrices are bucketed by
vector width, and a query is scored only against the bucket it fits. A width
mismatch now yields lexical results plus `_meta.embedding_stale`, naming both
dimensions and the command that fixes it. ⚠ Degrading quietly would have traded
a loud failure for a silent one, which is the same defect wearing a hat.

**`semantic_search` is reported on the no-change payload too.** It was present
on the full-rebuild path and absent on the incremental ones, so a caller could
not distinguish "embeddings are healthy" from "embeddings were never looked
at". Absence is not a status.

**`index-local --rebuild`** forces a full pass from the CLI, which previously
had no way to express it short of `delete-index`.

### #111 — the embed char cap is configurable, and part of the identity

`_section_embed_text` truncated section prose at a hardcoded 1000 characters.
Measured over 1,992 sections, that withheld **41.2%** of available prose
(778,236 → 457,284 tokens), and the median section already exceeded the cap.
Because 1000 characters is roughly 250 tokens — just under all-MiniLM-L6-v2's
256-token window — the cap, not the model, was the binding constraint: moving
to a longer-context embedding model recovered almost nothing, because the text
never reached its window.

**`JDOCMUNCH_EMBED_CHARS`**, default `1000`. The default is unchanged
deliberately: raising it would invalidate every existing index and shift recall
for users who never asked.

**The cap is part of the embedding identity**, not merely a cache-key salt. The
report proposed salting the per-section key and noted that adding the cap to
the header would be more robust — it is in fact the part that works. Salting
alone leaves the header matching, so the old entries still load and merge with
the new ones and the sidecar accumulates *both* derivations. And on an
unchanged corpus, nothing reaches the embedder at all, which is #109 again one
layer down. Both are in: the cap salts the key *and* sits in the header, so a
cap change escalates and discloses exactly like a model rotation.

⚠⚠ **The migration rule is the risky part, and it bites twice.** Sidecars
written before this release have no `embed_chars` field and were all built at
1000, so absence means 1000, not "unknown". Reading absence as a mismatch would
have escalated *every existing index* to a full re-embed on its next run — a
corpus-wide bill for users who changed nothing.

The same trap sits in the key salt, and the report's patch sketch walks into
it: salting unconditionally makes the new `h#pv1-1000` miss against the `h#pv1`
already on disk, so every user on the default re-embeds their entire corpus on
upgrade to arrive at byte-identical vectors. **The default cap adds no salt.**
Four tests pin both halves in both directions, including an end-to-end upgrade
from a sidecar written exactly as 1.126.1 wrote it, asserting zero re-embeds.

### Verification

`tests/test_embedding_rotation.py`, 52 tests. Run against the unmodified
v1.126.1 tree in a throwaway worktree, 19 fail — including the reporter's exact
error string and payload shape. The three end-to-end indexing tests fail there
on behavior, not on a missing helper, which is the point of writing them that
way.

Still open from these two reports: nothing. #109's four suggestions and #111's
proposal are all in.

## [1.126.1] - 2026-08-09 - Dotted directories are skipped by rule, not by a list of twelve

Closes [#113](https://github.com/jgravelle/jdocmunch-mcp/issues/113). Found
in-house when an index of 243 notes reported 486 documents.

### The defect

`SKIP_PATTERNS` was a twelve-entry denylist, so the walk skipped exactly the
dotted directories somebody had thought of in advance and descended into every
other one. Any tool writing a dotfile cache into a corpus had that cache
ingested as documentation.

⚠ **This is not [#102](https://github.com/jgravelle/jdocmunch-mcp/issues/102).**
That was `lstrip("./")` eating the leading dot of a *gitignored* path. Here
nothing is gitignored and the fixtures are not git repos, so there was no
pattern to miss: the directories were never pruning candidates.

**What it cost.** A sibling tool wrote a projection into `.jmemorymunch/` inside
an indexed corpus. Every note was then indexed twice, and the second copy was
not a copy: a lossy condensation roughly a fifth the size, frozen at the moment
the projection was built. A section search could answer from the condensation
with nothing marking it as a summary or as stale. `.claude/` is the same hazard
in a code repo, where agent instructions come back as project documentation.

### The rule

`is_skipped_dot_dir()` in `tools/_constants.py`, called by both walkers.
Directories whose name begins with a dot are pruned, which inverts the failure
mode: the next tool to write a dotfile cache into an indexed tree needs no
change here.

- **`.github` is allowlisted.** It is dotted and legitimately holds
  `CONTRIBUTING.md`, issue templates and often a docs tree. Skipping it would
  trade one silent omission for another.
- **`include_dot_dirs` is the opt-back-in**, on `index_local` and on the MCP
  tool schema, taking directory names rather than paths.
- **Pruned directories are counted** as `dot_directory` in `skip_counts`. The
  silence was the reportable half: a walk that quietly drops a subtree looks
  exactly like a corpus that never had one.
- ⚠ **Both walkers, one rule.** `index_local` and `index_repo` each carried
  their own `_should_skip`; `index_repo` filters a flat GitHub tree with no
  `os.walk` to prune, so it checks every leading component instead.

⚠ **`SKIP_PATTERNS` keeps its dotted members deliberately.** Removing `.venv/`
and `.git/` looks redundant once the rule exists and is a regression: the list
is matched as a path substring by callers this change does not touch.

### Three cases pinned by test, because reasoning gets them wrong

- **A corpus whose root is itself dotted** is unaffected. Matching on the
  absolute path, or on the root's own name, empties such a corpus and reports
  success.
- **A dotted directory named in `paths` is still indexed.** Naming it is a
  request, not a stray cache; only dotted directories below it prune.
- **`.gitignore` is kept.** A dotfile file is not a directory.

New `tests/test_dot_directory_pruning.py` (30). The file cannot import against
pre-fix HEAD, so non-vacuity was proven with a behaviour-only subset there:
**6 fail, 2 pass**, and both passing cases are the controls. Suite
**2295 passed / 6 skipped**, `ruff check src/` clean. No INDEX_VERSION or
tool-count change; `include_dot_dirs` is an additive optional argument.

## [1.126.0] - 2026-08-08 - Retrieval confidence is scored on the right scale

Follow-up to [#106](https://github.com/jgravelle/jdocmunch-mcp/issues/106). We
told the reporter the weight tuner exists precisely to move the ranking weight
off its default. Checking whether that was true found something worse than the
slowness we had already disclosed.

### The confidence signal was scale-corrupted

The `strength` sub-signal of retrieval confidence reads a raw top-1 score
through a curve that was hardcoded to the BM25 scale. BM25 tops out in the
tens. A fused reciprocal-rank score tops out at `1/(k+1)`, about 0.0164. A
cosine tops out at 1.0. All three were scored on the same curve.

Measured on identical relative separation between the top two results:

| scorer | top-1 | confidence |
|---|---|---|
| BM25 | 20.0 | 0.6205 |
| fused | 0.0146 | 0.0872 |

Same ranking quality, seven times the confidence, entirely from the units.

Two consumers read that number, so the damage was not cosmetic:

- The `low_confidence` verdict refuses to mint a citable absence claim.
  Searches using the semantic channel were being disqualified from evidence
  they had earned.
- The weight tuner compares mean confidence with and without the semantic
  channel. The scale gap swamped the real signal: it read `semantic_hurts`
  with a delta of −0.53 and stepped the weight down every round, to the 0.10
  floor, on data where the semantic channel was answering the queries. Not
  slow to reach a better weight — actively walking away from one. Measured
  over seven consecutive rounds before the fix, one round after.

Each scorer now declares the score at which `strength` saturates. The BM25
curve is unchanged: `1 - exp(-3t/12)` is algebraically identical to the old
`1 - exp(-t/4)`, so lexical confidences are byte-identical to 1.125.0 and only
the modes that were wrong move. An unknown mode falls back to the BM25 ceiling,
so a caller that does not pass one is unchanged rather than newly wrong.

**This changes `_meta.confidence` values upward for searches that use the
semantic channel, and with them some `low_confidence` verdicts.** If you gate
CI on a confidence threshold, re-check it.

### The tuner takes a step proportional to the evidence

A flat ±0.05 meant crossing the useful range took nine successful rounds of 50
or more qualifying events each. The step now scales with the measured gap, from
0.05 at the decision threshold up to a bounded 0.20 when the evidence is
decisive. It stays bounded on purpose: the sign is what the ledger tells us,
and the magnitude of a confidence delta is not a calibrated distance to the
right answer.

### A single-mode workload can now set the weight instead of waiting

The tuner learns by comparing modes. A user who always runs one mode produces
no signal at all, however long they run, and the old response said only
`no_signal_split`. It now explains that the workload is single-mode, that
waiting will not help, and what to do instead.

New `tune_weights(repo=..., set_weight=...)` persists a measured value
directly. It deliberately does not require telemetry — that gate exists because
there is nothing to learn from without a ledger, and writing down a value you
have already measured needs no ledger. Out-of-range values are clamped and the
response reports it.

Tests: `test_tuner_reachability.py` (20, 18 fail before the fix). Suite 2265
passed / 6 skipped. No index version change.

## [1.125.0] - 2026-08-08 - A partial embed pass no longer discards saved vectors

Two reports from @faxik ([#106](https://github.com/jgravelle/jdocmunch-mcp/issues/106),
[#107](https://github.com/jgravelle/jdocmunch-mcp/issues/107)), both measured on
a 5,300-section corpus.

### #107 — incremental indexing dropped vectors for untouched documents

`embed_sections` finished by calling `cache.write`, which is documented as an
atomic rewrite of the whole sidecar. That is correct on a full index, where the
sections it was handed are the entire corpus. On an incremental refresh it was
handed only the changed documents, and every vector belonging to an untouched
document was discarded.

Observed three times on the same corpus: **5,316 vectors → 21**, then 224, then
48. Each run exited 0 with no warning.

Since v1.75 the sidecar is not a cache. Vectors are stripped from the index
monolith at save time and read back from `<name>.embeddings.jsonl`, so losing
an entry loses the vector. Queries kept returning results; the semantic channel
ranked almost nothing.

It does not reproduce on a small corpus — under about five documents the
incremental path re-materializes everything anyway, so the rewrite happens to
contain the corpus.

- `embed_sections` now merges into what is already on disk. New `prune=True`
  argument requests the old rewrite and is passed only from the two
  full-corpus call sites, where stale vectors for removed sections should go.
- Merging is the default, so a caller that does not opt in cannot lose data.
- Provider or model rotation still purges: the identity check already returns
  an empty set on a mismatch, so the merge has nothing to carry forward.
- Two further call sites were passing no index identity at all, which disabled
  the cache outright: `index_file` (the auto-reindex hook path, whose vectors
  were therefore never persisted) and `index_repo` (which re-embedded from
  scratch on every refresh). Both now use it.
- New `cache.append_entries` lets the save-time safety net extend an existing
  sidecar. It never rewrites rows and never replaces an existing identity
  header.

### #107 — indexing now reports embedding coverage

The write bug was invisible from outside: exit 0, no warning. The reporter
ended up instrumenting it themselves, comparing sidecar rows against
`section_count` after every reindex.

`index_local`, `index_repo` and `index_file` now return `embedded_sections` and
`embedding_coverage`, and emit a warning below 50%. Both keys are omitted when
the index has no sidecar, so a lexical-only index does not report `0.0`.
Coverage is counted from sidecar keys, never its vectors.

### #106 — the effective ranking weight now says where it came from

With the stock `semantic_weight` of 0.5, none of 15 paraphrased queries were
answered in the top 5 — worse than turning the semantic channel off entirely —
while ranking the same stored vectors by cosine alone answered 5. Keyword
recall was flat at 93.3% from 0.0 all the way through 0.95.

Reciprocal rank fusion at `k=60` structurally penalises a result that is strong
in one channel and absent from the other, which is the shape of a paraphrase
answer. Nothing in the response indicated a weight was involved, so the failure
read as broken vectors.

- `_meta.semantic_weight_source` reports `caller`, `tuning.jsonc` or `default`.
  The weight's value was already reported; its origin was not.
- `_meta.semantic_weight_clamped_to` appears when a hand-written override was
  out of bounds, instead of clamping silently.
- The tuner's ceiling moves from 0.85 to 0.95. It was below the measured
  optimum, and it was enforced inconsistently: a value in `tuning.jsonc` was
  clamped, an explicit call argument was not. 1.0 is the value worth excluding
  — keyword recall dropped only there.
- Omitting the argument is now the only way to request the tuned weight.
  Passing 0.5 explicitly used to be indistinguishable from omitting it and was
  silently overridden. The tool schema no longer declares a default, so a
  client that fills defaults in cannot mute the tuner.
- A `repo_group` search reports the weight and its source per member, since
  each member resolves its own.

Not changed, deliberately: the 0.5 default itself and the `k=60` fusion
constant. One corpus is not enough to move either, which the reporter said
first.

Tests: `test_embedding_sidecar_preservation.py` (18), 
`test_semantic_weight_provenance.py` (22). Suite 2245 passed / 6 skipped. No
INDEX_VERSION or tool-count change.

## [1.124.3] - 2026-08-07 - A lint gate, and the NameError it found

CI had no lint job. It has one now, and adding it immediately surfaced a latent
defect shipped in v1.124.0.

### The defect

```
src/jdocmunch_mcp/server.py:2003  F821 Undefined name `logger`
```

`_declared_properties` logged through a module-level `logger` that this file has
**never defined**. The call sits inside `except Exception:` -- the handler whose
job is to swallow a failure -- so any real failure of `_all_tools()` raised
`NameError` **out of** the handler and turned a handled error into a crash.

`logging` was not imported at module scope either; the one other logging call in
this file imports it locally inside its function. Both fixed, with four
regression tests that force the error path and assert it survives.

The other two F821s were `OrderedDict` inside **string annotations**, which are
never evaluated at runtime and had a real local import. Not bugs, but
unresolvable for `typing.get_type_hints`, so they now import under
`TYPE_CHECKING`.

### The gate

`ruff check src/` runs once on Linux, outside the test matrix.

⚠ **`select` is explicit**: `["E4", "E7", "E9", "F"]`. Ruff's DEFAULT rule set is
not stable across versions, and this repo has no `uv.lock` to pin ruff with.
Measured in a clean environment: an `ignore`-only config under ruff 0.16.2
resolved **446 findings** where the intended set gives a handful. Widening the
set is now a decision someone makes rather than something a ruff upgrade does.

⚠ `ruff` is in the dev group, because `uv run ruff` fetching it on demand locally
is precisely what hides its absence from CI.

Fixed to make the gate green: 14 unused imports (each verified to have no
importers elsewhere, since an unused import can still be a re-export).
Grandfathered with counts and reasons: `E402` (65 deliberate lazy imports) and
`F841` (2 dead locals).

⚠ Not copied from jcodemunch-mcp: its lint job runs `uv sync --locked`, which
would fail here because `uv.lock` is gitignored. This job uses plain
`uv sync --group dev`, matching this repo's own test job.

### The lesson is not "add a linter"

jcodemunch-mcp HAD this check. It failed on four consecutive releases and nobody
read it. A gate is worth exactly as much as the habit of reading it, which is why
the release checklist now names both the lint step and reading the CI run.

## [1.124.2] - 2026-08-07 - Text-mode IO and CLI output declare their encoding

Suite parity with jcodemunch-mcp, which swept three directions of the same cp1252
hazard. This repo was scanned separately; nothing about a defect living in one
server implies it lives in its siblings, and nothing implies it does not.

| Direction | Found here |
|-----------|------------|
| subprocess **input** | 0, already clean since the 2026-08-03 sweep |
| our own **output** | 7 lines in `cli/init.py` |
| **file IO** | 4 sites |

### File IO

`open()`, `Path.read_text()` and `Path.write_text()` use the platform default
when no encoding is given, which is cp1252 on Windows. Reading a UTF-8 file then
raises on the five bytes cp1252 leaves undefined (`81 8D 8F 90 9D`) and silently
mangles everything else. Fixed at the savings tracker (3) and the WSL `/proc/version` probe.

⚠ Every one of these holds ASCII-only content today (JSON with ASCII keys, and `/proc/version`), so nothing
was corrupt. The exposure is a future non-ASCII value landing in one of them.
Stated plainly rather than dressed up as a bug fix.

### Output

`cli/init.py` prints `—` and `•`. Both **are** cp1252-encodable, so nothing
crashed -- piped output simply went out as cp1252 bytes and a UTF-8 consumer got
mojibake, with nothing raised on our side. jcm had a character cp1252 cannot
encode at all and died outright. Same defect, different symptom, and the quiet
one is harder to notice.

`_force_utf8_stdio()` now runs at the top of `main()`, before any subcommand can
write, so the next character added does not decide which symptom this repo gets.
`PYTHONIOENCODING` is honoured as an opt-out, `errors="replace"` guards against
surrogates from path decoding, and the MCP stdio transport is unaffected because
it wraps `sys.stdout.buffer` -- asserted by a test, not remembered.

### Guards

Two AST guards ported from jcodemunch-mcp, each with an **empty ratchet**: a new
unencoded call fails, and a listed exemption that gets fixed must be deleted so
the set cannot decay into a permanent excuse.

The file-IO scanner matches the file mode **by value** rather than by argument
position, because `open(file, mode)`, `path.open(mode)` and `wave.open(file,
mode)` put it in three different slots -- and two earlier position-based versions
each produced a different class of false positive. It is tested in both
directions: correct code must not be flagged, broken code must be. A guard with
false positives is one nobody believes, and a ratchet nobody believes collects
exemptions.

⚠ The non-vacuity floor is sized to THIS repo's tree (100+ files), not copied
from jcm. A floor larger than the tree fails forever; a floor of 1 passes over a
scan that collapsed to nothing.

### A finding that turned out not to be one

While bumping version pins I noticed `uv.lock` recording an older version than
`pyproject.toml`, and started writing it up as drift that jcodemunch-mcp's
`test_lockfile_version_sync.py` gate would have caught.

It is not drift. **`uv.lock` is gitignored in this repo, deliberately** -- CI runs
plain `uv sync`, not `uv sync --locked`, so a committed lock would silently change
dependency resolution. The file is a local artifact and cannot drift across
releases because it was never in a release.

Recorded here because the near-miss is the useful part: porting jcm's lockfile
gate would have shipped a test that fails on a fresh clone, where no `uv.lock`
exists. jcm tracks its lock and this repo does not, and that asymmetry is a
decision, not an oversight. Suite parity is for behaviour contracts, not for
whatever the other repo happens to have in `tests/`.

## [1.124.1] - 2026-08-07 - an ignored argument must not be able to back an absence claim

Completes #104. v1.124.0 disclosed ignored arguments and stopped there. That is
the smaller half.

jDocMunch mints citable `absent:<sha>` references (v1.117.0). A call whose
scoping argument was silently dropped could still reach `state: "absent"` and be
cited as proof the target is not there -- and the whole point of #104 is that
such a call returns a WIDER result than requested, so "not found" is exactly the
claim it is least entitled to make. The call that ran is not the call that was
requested; it cannot prove anything about what was asked for.

**This contract already existed in the other two servers.** jcodemunch shipped
it in v1.108.175 after finding the defect live (a `search_text` call passing
`regex=true` when the parameter is `is_regex`), and jdatamunch carries the port.
jDocMunch was the one server without it, which is why #104 was reportable here
and not there. The v1.124.0 fix was written from the report rather than from the
sibling implementations, so it reproduced the disclosure and missed the refusal.

`tools/_arg_contract.py` is now present in all three, with the shared note text:

> The call that ran is not the call that was requested, so this result cannot be
> read as evidence the target is absent.

- `degrade_absent_verdict` downgrades `absent` to `degraded` and records why.
  It runs BEFORE the absence-evidence block reads the verdict, so `note_absence`
  refuses to mint rather than minting and retracting. Ordering is the fix; a
  test asserts the call order in the dispatcher source.
- Only the absence CLAIM is refused. `ok`, `degraded` and `low_confidence` are
  untouched, so a caller does not lose a usable result over a typo'd flag.
- Disclosure is now also TOP-LEVEL (`ignored_arguments` +
  `ignored_arguments_note`), matching jdatamunch, which documented that shape
  for the same reason it applies here: this server strips `_meta` by default.
  `_meta.ignored_arguments` is retained because v1.124.0 shipped it and the 1.x
  contract forbids removing a response key -- it is also what jcodemunch emits,
  so a cross-server consumer reading either spelling works.

Tests: `tests/test_unknown_arguments.py` grows to 23.

## [1.124.0] - 2026-08-07 - four reported defects: ignored dot-directories, an unbounded sidecar, silent argument drops, and a verification that verified the wrong bytes

Closes #102, #103, #104, #105.

### #102 - gitignored dot-directories were walked and indexed (@faxik)

`str.lstrip` takes a character SET, not a prefix, so
`f"{dir_rel}/{d}/".lstrip("./")` ate the leading dot of the first path
component:

```
"./.worktrees/".lstrip("./")   ->  "worktrees/"    # pattern no longer matches
```

`.venv/`, `.tox/`, `.next/`, `.cache/` and `.worktrees/` were therefore walked
despite `.gitignore` listing them and `git check-ignore` agreeing. Undotted
directories were pruned correctly, which is exactly why this read as working.
The per-file fallback at the same site was corrupted the same way, so files
nested under a leaked directory did not get caught there either.

Reported impact: 9,813 documents indexed where ~3,100 exist, roughly 48 copies
of one corpus, because git worktrees lived under a gitignored `.worktrees/`.
Duplicates are not only a cost - near-identical sections from stale branches
compete with the live one, so retrieval quality degrades too.

Both sites now use a shared `_walk_rel` helper with prefix semantics.

The idiom was swept across the suite before fixing: 14 occurrences in
jcodemunch, 5 here. **jcodemunch's indexer does not share this defect** - it
prunes on the bare directory name (`skip_dirs_regex.match(dir)`) rather than a
constructed relative path. Its remaining occurrences are path normalizers, a
separate and lower-severity class, deliberately not folded into this release.

### #103 - the near-duplicate sidecar was unbounded and its skips were silent (@faxik)

`retrieval/dedup.py::detect_clusters` is all-pairs Jaccard with a length
pre-filter. The pre-filter prunes to ~30% of the space, which is a constant and
not a change of asymptote. Measured wall clock grows ~O(n^2.3):

| Sections | Wall clock |
|---------:|-----------:|
|    5,931 |      7.4 s |
|   12,493 |     42.2 s |
|   25,329 |    206.7 s |

The code comment already said "fine up to a few thousand sections" and was
right. Nothing enforced or surfaced that ceiling: the sidecar ran on every
index with no flag and no size guard, and the caller's bare
`except Exception: pass` meant a skip would have been silent too.

**The silence was the defect; the runtime was its symptom.** A skipped sidecar
was indistinguishable from one that found no duplicates.

Now: a section ceiling (default 20,000, `JDOCMUNCH_DEDUP_MAX_SECTIONS`, `0`
disables, an invalid value falls back to the default so a typo cannot uncap an
O(n^2.3) loop), an `enabled` opt-out, and a `dedup_skipped` block in the
`index_local` response naming the count, the ceiling and the knob that moves it.
The default sits above every published benchmark corpus (largest 10.4k), so the
guard fires where the curve has turned over rather than where it is merely
non-linear.

MinHash + LSH banding is the real fix and is deliberately NOT in this release.

### #104 - unknown tool arguments were silently ignored (@faxik)

`get_toc{doc_path: "CLAUDE.md"}` returned the entire corpus TOC. `doc_path` is
`get_document_outline`'s parameter; `get_toc` scopes with `path_glob`.

The direction is what makes it worth fixing: an agent that means to SCOPE a call
and misnames the parameter silently gets a much LARGER response than it asked
for, which is the failure this server exists to prevent.

Rejecting unknown properties would be the stricter answer and is not available
here - the 1.x contract does not permit a previously-accepted call to start
raising. So the response now carries `_meta.ignored_arguments`, additive and
omitted when empty.

Two details that decide whether it works at all: the property sets are built
from the UNFILTERED catalog, since a tool hidden by a tier filter or
`JDOCMUNCH_DISABLED_TOOLS` still has a schema; and the block is attached AFTER
meta_fields filtering, because the default config strips `_meta` entirely and a
warning the default deletes is no warning at all.

`get_toc`'s description now points at `get_document_outline` for the
single-document case, per the reporter's docs-only suggestion.

### #105 - verify_index verified the cached mirror while its docs promised the live source (@T0R0-xp)

Confirmed. `verify_index` reads `store._content_dir()`, so both sides of the
comparison come from the index and a source file edited, truncated or deleted
after indexing still verifies clean. The reporter demonstrated this with a
same-length modification, which rules out a size check passing for the wrong
reason.

The module docstring said it "byte-range-reads its current on-disk content",
which reads as the workspace. **The description was wrong, not the behaviour.**
Cache verification is a real check - corruption of `~/.doc-index` is what B1/B2
of the v1.10 audit were about - and flipping the default would silently change
what existing CI gates on.

So the default is unchanged and now honest, and the other question is finally
askable:

- `source="cache"` (default): index integrity. `_meta.verifies` states in words
  that a clean result is NOT proof the source is current.
- `source="live"`: workspace integrity under the index's `source_root`. An
  edited source drifts, a deleted one goes missing.
- `_meta.verify_layer` names which one ran, on every call.

A live check with no recorded `source_root` reports `no_source_root` for every
section rather than falling back to the cache. Falling back would answer the
cache question under the live label, which is the confusion being fixed.

Same disclosure discipline as the v1.122.0 content tools, which hit this exact
cached-mirror-vs-live-source split and resolved it the same way.

**Open question for the maintainer**, recorded rather than decided: whether
`live` should become the default. v1.122.0 flipped the content tools to
live-when-available, which argues yes; it would change counts for anyone gating
CI on `drift_count == 0`, which argues for doing it deliberately.

### Tests

2,144 passed, 6 skipped, 0 failed. New `tests/test_gitignore_dot_directories.py`
(20; under the old semantics 10 fail and 10 pass, the latter being controls),
`tests/test_dedup_ceiling.py` (17), `tests/test_unknown_arguments.py` (15,
including a whole-catalog round-trip proving no tool flags its own declared
arguments), `tests/test_verify_index_source_layer.py` (19, built on the
reporter's four-file fixture).

No INDEX_VERSION change. All additions are additive per the 1.x contract.

## [1.123.2] - 2026-08-05 - a 5.9 MB marketing image was 87% of the source distribution

`tests/infographic.png` was a promotional graphic that had been sitting in the
test directory since the initial commit. Nothing referenced it: no test, no
module, no documentation, no workflow. At 5,895,033 bytes it accounted for
**87% of the 6,769,591-byte sdist** across only 607 entries, so every install
from source paid for it.

It was also stale collateral rather than evidence. The figures on it were
generated for an early v1.x and were never traceable to anything in
`benchmarks/`, which is where reproducible numbers for this project belong.
Removing it and keeping the measured results is the same correction 1.123.1
applied to TOKEN_SAVINGS.md.

Measured on the built artifacts: **6,769,591 bytes -> ~903 KB (-86.7%),
607 -> 606 entries.** (Approximate on the new side deliberately: stating the
exact byte count inside this file changes it.) The largest remaining entry is the whitepaper PDF at
193,417 bytes. No code change, no test change, no wire-format, tool-count or
INDEX_VERSION change; suite 2063 passed / 6 skipped.

## [1.123.1] - 2026-08-05 - README rewritten as a landing page; TOKEN_SAVINGS.md rebuilt from measured runs

- README restructured to identity -> value -> evidence -> install -> quickstart -> capabilities -> architecture -> security -> limitations -> licensing; 793 -> 226 lines. New Limitations section.
- TOKEN_SAVINGS.md: three of four tables were illustrative figures presented as measurements and are removed. Replaced with results traceable to benchmarks/ (Kubernetes, wiki, SciPy, LangChain) including the 5,987-31,757 per-query spread.
- TOKEN_SAVINGS.md cost model corrected: `claude_opus` was documented at $15.00/1M, the retired Opus 4.0/4.1 rate. The shipped code uses $5.00/1M, so the published avoided-cost figure overstated threefold. `claude_sonnet` and `claude_haiku` were undocumented; all four now match token_tracker.py.
- No code change. Docs only.

## [1.123.0] - 2026-08-04 - offloadable-work annotation, off by default

`get_section` and `get_sections` can now tell you whether the work their payload
enables is grunt-work a cheaper model can do. Set `JMUNCH_OFFLOADABLE=1` (or
`JDOCMUNCH_OFFLOADABLE=1` for this server alone) and each reply carries an
advisory `_meta.offloadable` block. Off by default.

**We label. We never route, execute, or hold model credentials.** No new
process, no network call, no new tool, no model of ours runs.

Routers classify the *prompt*, because that is all they can see. This sits
downstream of retrieval and classifies *the evidence just assembled*: whether
the section content is actually in the payload, how many documents it spans,
whether anything was truncated, and whether freshness came back unknown.

Tri-state and reason-coded, never a bare score, and it fails closed — every
unknown bearing on the answer disqualifies. `verify_with` names the call that
would adjudicate a cheaper model's answer over the payload.

⚠ **This is why v1.122.0 shipped first.** A section whose source cannot be
checked, or that comes back stale, is refused rather than labelled. Before the
identity tools disclosed freshness there was nothing to refuse on, and every
payload would have been rejected on `TRI_STATE_UNKNOWN`.

Identical field contract in jcodemunch-mcp (symbols/files) and jdatamunch-mcp
(columns/datasets): the vocabulary is *units* and *containers*, and a pinned
`CONTRACT_DIGEST` plus a generated contract test fails the build in any of the
three that drifts.

Additive: one new `_meta` key, emitted only when gated on. No tool, schema or
`INDEX_VERSION` change. Tests `tests/test_offload_contract.py` (23).

## [1.122.0] - 2026-08-04 - a content read discloses its freshness, and `fresh` means proven

`get_section` and `get_sections` returned bytes with no indication of whether
the file they came from had changed — or been deleted — since indexing.
`search_sections` has carried per-section freshness since v1.16.0; the tools
that hand a caller actual content carried none. A content read is a claim about
what a file holds right now, so both now emit `_meta.freshness`, `_meta.verdict`
and `_meta.drift_layer`.

⚠ **Two over-claims were fixed in the probe itself, and without them the new
disclosure would have been worse than none.**

`FreshnessProbe._classify` answered `fresh` when it had compared nothing: a
section carrying no `doc_path`, and a file that exists but whose bytes could not
be read (`_file_hash` returns `(None, True)` on `OSError`) both fell through
every comparison to a closing `return "fresh"`. Both now return `unknown` — a
comparison that could not be made is not the same fact as one that succeeded.

`summary()` compounded it by tallying three buckets and silently dropping
anything else, so such a section vanished from the aggregate entirely and the
counts could sum to fewer than the sections they described. It now counts
`unknown`, and an absent or unrecognised bucket counts as `unknown` rather than
disappearing.

⚠ **The default probe compares against jdoc's cached content mirror, which does
not change when the workspace file does.** Wired that way, the new reading
answered `fresh` for a file that had been edited AND for one that had been
deleted — a disclosure that discloses nothing. The content tools now use the
jdoc#71 live-source layer when the index records a usable `source_root`, and
report which layer answered in `_meta.drift_layer` rather than leaving it to be
assumed.

`build_verdict` gained the same tri-state treatment its siblings received: the
`"stale" if index_stale else "fresh"` expression is extracted as `index_channel`
and takes an optional richer reading. Callers passing only the Boolean keep
their previous behaviour exactly.

New `section_verdict_for_index` backs the identity tools. A batch verdict takes
the **worst** section reading, never the first or an average — otherwise one
stale section rides out under an `ok` covering the others, and the caller may
never see the per-section flag.

Additive: new `_meta` keys only, no tool, schema or INDEX_VERSION change. A
section whose freshness cannot be established now reads `unknown` where it read
`fresh`, which is the correction, and per-row `_freshness` is retained for it
because only `fresh` rows are dropped as noise.

Tests `tests/test_identity_freshness.py` (20), including a non-vacuity test
reproducing the old expression. Suite 2063 passed / 6 skipped.

## [1.121.1] - 2026-08-03 - git output is decoded as UTF-8, not as cp1252

In-house, found by an AST sweep across the suite after the same defect was
reproduced in jcodemunch-mcp. Reproduced here before it was fixed, and verified
at `index-local` rather than at the function that was edited.

### Fixed

- **`index-local` failed outright for any corpus checked out under a path with
  a non-ASCII character in it.** `subprocess.run(..., text=True)` with no
  `encoding=` decodes the child's output with `locale.getpreferredencoding()`,
  which on a stock Windows box is **cp1252**. Git emits UTF-8.

  ⚠ **The trigger is `git rev-parse --show-toplevel`, which prints the
  repository's absolute path raw and unquoted** — unlike `status` and
  `ls-files`, whose paths pass through `core.quotepath` and come back ASCII.
  `_git_root` is the gateway every git-aware path in the package goes through,
  so this was not local to one feature. On Windows the most common way to have
  a non-ASCII character in your checkout path is your own user name.

  Measured on the reproduction: a corpus at `...\Ýcorpus` (UTF-8 `c3 9d`; `9d`
  is undefined in cp1252) returned
  `{"success": false, "error": "Indexing failed: 'NoneType' object has no
  attribute 'strip'"}`. **Not a degraded index — no index at all.** After the
  fix the same corpus indexes with `head_sha` populated and
  `sha_certified: true`.

  ⚠ The error message named neither git nor encoding, because
  `UnicodeDecodeError` is raised inside `subprocess`'s **reader thread**: no
  `try/except` around the call can catch it, `proc.stdout` comes back `None`,
  and the caller dies later on `.strip()`. `_git`'s three carefully-separated
  except clauses (`CalledProcessError` / `TimeoutExpired` / `Exception`) were
  all bypassed.

  **9 call sites** fixed — `tools/_git.py` ×2, `service_installer.py` ×5,
  `cli/init.py` ×1, `scripts/evidence_receipt.py` ×1 — all now passing
  `encoding="utf-8", errors="replace"`.

  ⚠ **`local_git_paths_tracked` was already correct and is untouched**: it uses
  `_git_bytes` with an explicit `.decode("utf-8", errors="surrogateescape")`.
  The hazard had been recognised for `ls-files` and not generalised, which is
  the exact shape of gap a convention-without-a-test produces.

### Added

- `tests/test_subprocess_encoding_guard.py` — an AST guard failing on any
  `run`/`Popen`/`check_output`/`check_call`/`call` that passes
  `text=`/`universal_newlines=` without `encoding=`. Proven non-vacuous both
  ways: the detector is parametrized over known-good and known-bad snippets
  through the **same function** the repo-wide check uses, and stashing this
  release's `src/` and `scripts/` changes fails it with all 9 sites named.

  `tests/` and `unused/` are **exempt by name with reasons on the record**, not
  skipped — `tests/` drives git against fixture repos this suite creates with
  ASCII authors and paths, `unused/` ships to nobody. `KNOWN_UNENCODED` is an
  empty ratchet with its own anti-rot test, so a listed gap that gets fixed must
  be removed rather than decaying into a permanent exemption.

⚠ **Invisible to CI and to any UTF-8 development box.** CI is Linux. The only
population that hits this is Windows users, which is why the convention could
not be maintained by noticing and why the sweep found 9 sites rather than 1.

No INDEX_VERSION, tool-count, or wire-format change.

## [1.121.0] - 2026-08-03 - search_sections stops paying for bytes the caller can't use (#101)

Reported by @vondecron, with a per-field byte table that made the fix
mechanical: a default result row spends most of its bytes on fields an agent
cannot act on, and carries no body text, so even a perfect top hit costs a
second `get_section` call.

Three opt-in knobs on `search_sections`, all defaulting to today's behavior:

- **`compact=true`** drops `repo` (already in the envelope), `parent_id`
  (derivable from `id`), `children`, `byte_start`/`byte_end`, `content_hash`,
  `inline_code`, `references` and `code_blocks`; drops `tags` when empty; drops
  `summary` only when it is byte-identical to `title`. Per-row `_freshness`
  survives only when it is NOT `fresh` -- an all-fresh result set is what
  `_meta.freshness` already reports, but a single stale row is a signal the
  caller needs.
- **`fields=[...]`** is an explicit whitelist and wins over `compact`. `id`
  always survives; a row nothing can follow up on is not a saving.
- **`snippet_bytes=N`** inlines the first N bytes of each section body as
  `snippet`, so a confident top hit needs no round-trip. UTF-8 safe (never
  splits a codepoint, which matters for the CJK corpora jdoc indexes) and sets
  `snippet_truncated: true` when it cuts.

Measured on this repo's own docs at `max_results=10`: **1,989 chars/row
default, 319 compact (-84%), 431 compact + `snippet_bytes=200` (-78%, and the
`get_section` hop is gone)**.

Two ordering constraints the implementation is built around. Projection runs
**after** every filter and after scoring, because those consumers read fields
compact drops (`min_byte_length` reads `byte_start`/`byte_end`, and it still
works under `compact=true` -- there is a test). And in a `repo_group` fan-out
`compact` deliberately **keeps `repo`**: dead weight in a single-repo response,
the only thing telling two members' rows apart in a fused one.

`_meta.tokens_saved` is now computed on the **served** payload rather than on
the pre-projection rows, so the number describes what the caller received.

Not adopted: jcodemunch's interned `#MUNCH/1` wire format, which the reporter
raised as prior art. That is a wire-format change, and the 1.x contract forbids
changing a tool response's JSON shape for existing consumers. `compact`/`fields`
reach the same saving additively.

New `retrieval/projection.py`. Tests `tests/test_v1_121_0.py` (20, including a
guard that the default response stays byte-identical). Suite 2008 passed / 6
skipped. Additive/1.x, no INDEX_VERSION or tool-count change.

## [1.120.0] - 2026-07-29 - retirement is commit-scoped, and the proof is independent (#95)

Closes the coordinated-retirement arc that ran through #80, #88, #89, #90, #93
and #95, all of it originated by @rknighton. The `[1.115.0]` entry below records
the QA-01/QA-03 work and the #89 corrections; this entry covers what landed after
it, and the independent verification the release was held for.

**Independently re-verified, not self-certified.** QA-15 and QA-17 were run
together against a frozen commit on Linux by the reviewer who wrote the harness
and found the original defects, in a clean detached checkout inside an isolated
container (no network, capabilities dropped, `no-new-privileges`), with the
post-test tree identical and `git status --porcelain` empty. Result: **10 passed,
2 skipped, 0 failed**, the full collection of both files. The 2 skips are the
`_PAIR_LOCK_API` cases, which apply only to the canonical-order pair-lock design
and stay inapplicable while Path A stands. `test_three_processes_keep_one_lock_inode`
**executed rather than skipping**, which is the reason Linux was the platform
that mattered. His pre-registered harness passed **7 of 7** at sha256
`88381e18f8463617349df506cc2569d54968938b0904d73f56077473d33a5f6`, byte-identical
to the copy we downloaded and ran ourselves.

The acceptance criteria predated any implementation, so the gate could not be
reshaped to fit the fix.

### Fixed

- **Final retirement authority was not commit-scoped (QA-19, High).** Retirement
  could commit using a retained-handle proof that had gone stale after it was
  taken, record-path existence could be mistaken for authority from the same
  publication, and record cleanup could remove a *different* publication than
  the one it proved.

  At every successful commit the retained handle is now loadable and matches the
  authoritative final proof, and a retained-handle change before that commit
  forces re-proof or a fail-closed non-`retired` result. The final gate is
  authorized by the exact current retirement publication rather than by a record
  path existing. Every retirement-record mutation is lock-coordinated,
  revalidated, and conditional on the intended publication, so an older
  completion cannot remove a newer one. Authoritative record-lock failure fails
  closed and can neither enter an unlocked critical section nor permit a
  deletion. Every non-`retired` reconciliation outcome leaves the retiring index
  loadable.

- **Public deletion could wait about a second on a retirement-record lock
  (QA-23).** Public target-lock and record-lock contention now return a prompt,
  typed, retryable `index_lifecycle_busy`. An MCP call that blocks on a lock is
  indistinguishable from a hang to its caller. Internal coordinated retirement
  keeps its bounded wait, where waiting is mid-protocol and correct. The public
  path never enters the guarded route: it supplies neither
  `retirement_publication` nor `expected_fingerprints`, and its record-lock
  coordination runs at `timeout_seconds=0.0`.

- **Normal deletion did not preserve the index lockfile (QA-21)**, now fixed with
  a non-vacuous Linux proof.

- **Contention intent was inferred rather than stated (QA-25).** Two tests
  required opposite behaviour on the same lock while leaving `lock_wait` to the
  default, so no default could satisfy both. Every contention-sensitive caller
  now states whether it waits or refuses. The default stays `False`, which is a
  data-loss argument rather than a preference: a caller that forgets to say gets
  the refusing behaviour, preserving the guarantee that both participating
  indexes are never simultaneously absent.

- **A missing or unreadable entry record could be treated as authority.** It now
  never is: no primary unlink, a non-`retired` result, and a loadable retiring
  index.

### Added

- **A public `reason_code` vocabulary**, documented in `SPEC.md` and protected by
  a runtime drift test, so the codes a caller branches on are a contract rather
  than an implementation detail.

- **Durable, recoverable retirement records.** Interrupted retirements are proven
  recoverable by **real-process interruption** rather than mocked exceptions:
  a child process exits inside the record lock and a fresh process acquires the
  same lock and completes recovery.

- **`package-smoke` CI job** (#99). Every other test runs against an editable
  source tree, so a packaging fault could not fail them. This builds the wheel,
  installs it into a clean environment, and exercises public delete against the
  installed artifact from a directory with no `src/` reachable. The smoke script
  refuses to run if it imported from a source tree, because without that guard it
  could pass by testing `src/` again.

- **Machine-generated evidence receipts** (#100). Each matrix job emits a receipt
  from the run itself, so the commit named in a summary is the commit the numbers
  came from, and summaries are generated rather than transcribed. Counts come
  from `pytest --junitxml`, which is built in, so producing evidence adds no
  dependency. Two honesty guards: a dirty working tree is recorded and surfaced,
  and a summary spanning more than one commit says so instead of averaging runs
  into a figure that describes no candidate.

- **An exhaustive wait-policy guard** (#98). Every production `delete_index` call
  must pass `lock_wait` explicitly or be named with the reason it cannot matter,
  so a newly added caller cannot arrive with no stated policy.

### Changed

- **CI reports each matrix job independently** (QA-24). A single ubuntu failure
  was cancelling all four Windows jobs, so a frozen review commit carried no
  Windows result at all while the checks panel showed eight failures where there
  was one. An external reviewer cannot verify a per-platform claim against a
  cancellation.

### Compatibility

Additive under the 1.x contract. No tool added, removed, or renamed. No wire
format change. `INDEX_VERSION` stays at **3**, so **no reindex is required**.

### Known and deliberate

Two behaviours are documented decisions rather than defects, both recorded on
`ROADMAP.md` with close conditions and the reviewer's credit:

- The internal record lock acquires without a deadline. This is **not
  Windows-only**: POSIX `flock(fd, LOCK_EX)` without `LOCK_NB` blocks
  indefinitely too, so any bound would have to cover both platforms. The exposure
  is availability rather than authority, since a blocked caller cannot authorize
  a deletion while it waits, and legitimate holds measured 3.02 ms at 2 MB.
- An unreadable retirement record is inert but persists. If the record's identity
  cannot be established there is no verified publication to act on, and unlinking
  anyway would mutate retirement state without proving it is the intended
  publication, which is the class of problem QA-19 exists to prevent. It is
  disclosed as `{"record_state": "unreadable"}` rather than hidden, and a later
  valid `begin_retirement` for the same slot replaces it.

### Suite

1972 passed / 9 skipped on Linux, 1967 passed / 14 skipped on Windows, across
Python 3.10 through 3.13. The five-test difference is POSIX-only coordination
tests, which is exactly why a Windows pass is never read as verifying a locking
contract.

⚠ **Version note.** This work was tracked throughout as the `[1.115.0]` CHANGELOG
heading, which master deliberately skipped to reserve the number for this branch.
It **ships as 1.120.0**: `pip install jdocmunch-mcp` resolves to the highest
version, so publishing 1.115.0 after 1.119.0 would ship it into a version nobody
receives by default. The `[1.115.0]` entry stays as the historical record of the
branch's work.

## [1.119.0] - 2026-07-24 - a rebuild underneath a scan cannot prove absence (5th refusal rule)

Suite parity with jcodemunch-mcp v1.108.168.

### Fixed

- **Absence evidence could be minted over an index that was being rewritten.**
  v1.117.0 shipped four refusal rules for absence proofs — only `absent` proves
  absence; `low_confidence`/`degraded` do not; a stale index does not; a
  truncated index does not. None covered an index being **rewritten while the
  scan reads it**.

  Index staleness here is `source_dirty`, which reports that the *source* moved.
  It is blind to a reindex that rewrites sections under an unchanged tree, so
  such a scan reported `index: "fresh"`, reached `absent`, and handed back a
  citable `absent:<sha>` ref. That matters more in jdoc than in its siblings:
  sections are scored through a lazy content loader that reads body text from
  disk at scan time, so a rebuild mid-scan can move the very bytes being ranked.

  Zero results plus a detected rewrite now yields `degraded` instead of
  `absent`. Because `degraded` already cannot prove absence, the fifth rule
  falls out of the existing "only `absent` proves absence" check — there is no
  parallel rule to drift. `absence_refusal` names the rebuild rather than the
  generic state.

- **`channels.index` gains `"rebuilding"`**, disclosed on **every** state, not
  only the refused one: a caller reading an `ok` result still deserves to know
  the index moved under it. Only the absence *claim* is withheld — a scan that
  returned sections still returns them.

### Notes

- Detection is a filesystem signal (`DocStore._stamp_load_provenance` stamps the
  monolith path + mtime at both load return points;
  `retrieval.verdict.index_changed_since_load` re-stats it), deliberately **not**
  in-process reindex state, which cannot see a rebuild driven by a separate
  watcher process.
- **Unknown is not changed**: an index with no stamped provenance (a test
  double, a hand-built `DocIndex`) reports unchanged rather than degrading every
  verdict.
- Byte-identical for existing callers when nothing is rebuilding. NO new tool,
  NO tool-count or `INDEX_VERSION` change. New `tests/test_v1_119_0.py` (13).

## [1.118.0] - 2026-07-24 - lexical query no longer lowercased before tokenizing (#91 follow-up)

Reported by @tetiz123 while validating the v1.114.1 CJK tokenizer on a real
111-document / 2,053-section Korean corpus (the fix held: no reindex, and the
lexical channel went from returning nothing to being their best ranker). While
measuring, they found a second, unrelated defect with a one-line cause.

`DocIndex._lexical_search` computed `query.lower()` and handed that to the
scorer. But `bm25.tokenize` inserts CamelCase boundaries BEFORE it lowercases,
so the two sides of the match disagreed for any identifier that carries case:

    document side  tokenize("OvertimeService")  -> ['overtime', 'service']
    query side     tokenize("overtimeservice")  -> ['overtimeservice']

Every code-identifier query — the one thing a keyword ranker should be
unbeatable at — scored 0.0 and returned a silent empty list. It is silent
rather than an error because the Stage-A posting prune tokenizes the ORIGINAL
query, so candidates survive the prune and then each one scores 0. CamelCase
(`OvertimeService`) and acronym-plus-suffix (`HCA060T`) identifiers were hit;
underscore-separated names (`SPM_NOTIFICATION`) were not, because the delimiter
is case-independent.

Fix: pass the raw query to the scorer. `tokenize` lowercases internally after
the de-camel step, so feeding it the original text is both correct and
redundant-work-free. `_score_section`'s first argument is renamed accordingly;
`query_words` (the tag kicker) still uses the lowercased set, since tags are
matched case-folded. Consumer-layer only, no reindex: `tokenize` runs over
stored content at scoring time.

New `tests/test_v1_118_0.py` (7): the tokenizer asymmetry at the root, plus
end-to-end lexical retrieval for CamelCase / acronym / repository-suffix
identifiers, an underscore control, and a lowercase-prose control that must not
regress. Additive/1.x, no INDEX_VERSION or tool-count change; suite 1831.

Shipped from MASTER as a patch (like 1.114.1 / 1.114.2 / 1.116.0 / 1.117.0)
while `coordinated-retirement` (1.115.0) stays HELD for rknighton's
re-verification; on merge, resolve version conflicts to the higher number and
keep all CHANGELOG entries.

## [1.117.0] - 2026-07-24 - absence evidence (handoff/v2 phase 3, suite parity)

Suite parity with jcodemunch-mcp v1.108.166 (jcodemunch-mcp#377 phase 3, design
by @mightydanp). A zero-result section search can now be cited as evidence in a
handoff. Under v1/v2 it could not: nothing was served, so there was no id to
reference. But "we searched the complete, fresh, non-truncated index and it is
not there" is exactly the claim an audit most needs attested.

`retrieval/verdict.build_verdict` already reports state (`ok` / `low_confidence`
/ `absent` / `degraded`), scan counts, per-channel status, coverage, and a
scorer pin. A search whose verdict is `absent` now surfaces a citable ref;
passing it to `finalize_handoff` attests the absence. Because jdoc's default
`meta_fields` strips `_meta` entirely (the v1.104.0 lesson), the ref rides in
`_meta.absence_evidence`, re-attached AFTER filtering so the token-efficient
default cannot delete a token the agent needs to cite.

The refusal rules are the feature, adopted as proposed: only `absent` proves
absence; `low_confidence` and `degraded` do not; a stale index does not; a
truncated index does not. A refused scan is still recorded, so citing one
returns the reason (`refused_absence`, or `refused_absence_claims` naming the
claim) rather than a bare unknown-ref error. The rendered proof carries the
tool and query, the scope it was not found in, sections and documents scanned,
channel status, coverage with exclusion counts, and the scorer. Unknown
coverage is disclosed as unknown, never rendered as a complete scope.

Refs are content-addressed over `(tool, repo, query, scope)`. Session-scoped,
in-memory, capped, never on disk. Receipt gains `absence_attested` when cited.
Additive/1.x, no INDEX_VERSION or tool-count change. Tests
`tests/test_v1_117_0.py` (23, one per refusal rule); suite 1824.

## [1.116.0] - 2026-07-23 - claim-scoped evidence (handoff/v2 phase 1, suite parity)

Claim-scoped evidence, suite parity with jcodemunch-mcp v1.108.165
(jcodemunch-mcp#377 phase 1, design by @mightydanp). A handoff section may now
carry caller-authored `claims`, each with its own `evidence_refs`. v1 proved a
cited ref was retrieved this session but never bound it to a sentence: refs
landed in one global block at the end of the body.

New `_validate_claims` takes `{id, statement, evidence_refs, classification?}`.
Ids are unique across the WHOLE handoff, not per section, since the id is the
citation anchor and two sections owning one id would make a citation ambiguous.
Statements and classifications are preserved verbatim; the server never
rewrites one. Each claim's refs are attested separately through the unchanged
`_validate_evidence`, so an unknown ref returns `invalid_claims:
[{claim_id, unknown_refs}]` and names the claim that cited it instead of
vanishing into one global failure list. `render_handoff` prints the claim as a
`###` heading with its evidence indented beneath.

Three decisions carried from the jcm implementation:

- The input picks the contract. No claims anywhere means the schema string
  stays `jdocmunch.handoff/v1` and the body is byte-identical to what v1 rendered;
  `claims_attested` is omitted from the receipt rather than reported as `0`.
  Any claim promotes the handoff to `jdocmunch.handoff/v2`.
- Claims can satisfy `evidence_refs`: the top-level list may be empty when
  claims carry refs, so a caller who scoped everything to claims need not
  restate it. Strictly more permissive; no existing call changes.
- Claim refs join the canonical evidence index, caller order first, so a v1
  consumer reading a v2 handoff still sees every reference where it expects.

Section `content` becomes optional only for a section carrying claims.
Additive/1.x, no INDEX_VERSION or tool-count change.

Known limit, disclosed on the tracking issue before anyone builds against it:
phase 1 does not narrow what counts as a match. Attestation still accepts a
broader reference than the claim, so citing a whole document attests
even when only one unrelated member of it was served. Narrowing that is phase
2 (evidence receipts), which is deferred.

Tests `tests/test_v1_116_0.py` (18, incl. the byte-identical v1 guard); suite 1801.
**Shipped from MASTER as a patch (like 1.114.1 / 1.114.2) while
`coordinated-retirement` (1.115.0) stays HELD for rknighton's re-verification.
Version 1.115.0 is deliberately skipped here so the held branch keeps it; on
merge, resolve version conflicts to the higher number and keep all CHANGELOG
entries.**

## [1.115.0] - 2026-07-22 - QA-01/QA-03: coordinated & recoverable retirement, truly read-only report (#88)

The remaining two findings from @rknighton's #88 adversarial QA.

**QA-01 (High):** every retirement path proved the relationship, rechecked it,
then deleted — and an index that changed between the recheck and the physical
removal was still removed under the stale decision. `DocStore.delete_index`
now accepts proof-time `expected_fingerprints` (sha256 of each handle's
monolith, retiring AND retained) and re-verifies them inside the deletion
boundary, before any removal. A mismatch raises `RetirementConflict` with
nothing touched; the three retirement sites (legacy apply, supersession,
exact-dedup graduation) report it as `legacy_reconcile_conflict`,
`supersession_conflict`, and the new `graduation_conflict`, each with a
`changed_handles` list and both indexes kept.

**Recoverability (QA-01/QA-02):** a durable retiring record
(`<owner>/.retirements/<name>.json` — retiring + retained handles,
fingerprints, family, start time) is written before the destructive step.
Removed on success and on conflict; kept when cleanup fails, so pending work
survives a crash as a discoverable fact (`pending_retirement: true` in
cleanup-incomplete responses). A refresh of a retiring handle cancels the
pending retirement rather than racing it, and `delete_index` clears the
record once the primary record is gone.

**QA-03 (Medium):** `legacy_reconcile="report"` is documented as proof-only
but ran the full refresh first, rewriting the legacy index whenever source
files changed. Report now diverts before the refresh and proves from stored
snapshots plus live Git evidence: both indexes certified clean at one SHA and
the live checkout clean at that same SHA — three clean legs at one commit
mean the stored snapshots describe the live tree, no refresh needed. Zero
writes on every outcome; responses carry `_meta.read_only: true`. A genuinely
uncertified legacy index reports `legacy_reconcile_uncertified` honestly
(apply, which refreshes under C.2 intent, remains the certify-and-retire
path).

**Pre-production corrections (#89, @rknighton):** the branch QA pass found
the coordination above still left a proof-to-capture gap and an
unverified recovery record; both fixed before this release shipped.

- **QA-06 (High):** the three retirement paths now run one coordinated
  destructive step — fingerprints captured FIRST, decisive proof re-run on a
  reload the token covers, then the guarded delete verifies the fingerprints
  at entry AND immediately before the primary record is removed (a concurrent
  save or direct delete of the retained peer mid-cleanup now conflicts with
  every index still loadable). A missing/unreadable fingerprint fails closed
  (`None` never authorizes), and `delete_index` holds the same cross-process
  write lock as the save paths — every writer and direct delete of a handle
  joins one lifecycle coordinator. Only the target handle is ever locked, so
  cross-handle lock ordering (and its deadlock surface) never arises.
- **QA-07 (Medium):** `begin_retirement` returns a durable publication
  receipt (fsync'd record, per-publication-unique temp name — two
  same-process publishers can no longer collide on one PID-based temp path)
  and cleanup never starts without it; a failed publication reports
  cleanup-incomplete with nothing removed. `pending_retirement: true` is
  claimed only when the record actually exists, and the save paths cancel a
  pending record only AFTER their atomic replace lands (a failed save
  preserves the record).
- **QA-08:** a record whose retiring index no longer exists is a completed
  retirement — self-healed, never reported pending. **QA-09/QA-10** (policy):
  a rewrite or direct delete of the RETAINED handle voids any record naming
  it as retained (fail-visible; the next reconcile re-proves). **QA-11**
  (contract): record publication is fsync'd, so the receipt survives sudden
  power loss. **QA-15:** the POSIX write lock re-verifies its inode after
  acquisition, so lockfile deletion can't split coordination across two
  inodes.

**Completion QA (#90, @rknighton):**

- **QA-17 (High):** pair coordination previously ended at the final
  fingerprint check — a retained-peer delete landing between that check and
  the primary unlink could still leave both indexes absent. The retirement
  record is now the PAIR coordination point: the guarded delete executes its
  final gate (fingerprint re-verify, record-existence check, primary unlink,
  record removal) under a lock on its own retirement record, and any delete
  first voids the records naming its target as retained THROUGH that same
  lock (bounded wait) before touching anything. Void lands before the gate →
  the gate finds the record gone and conflicts, keeping the retiring handle.
  Gate already closed → the retained-peer delete is refused (returns False;
  a retry succeeds as soon as the gate opens, normally milliseconds). No
  interleaving finishes with both participating indexes absent, and no
  caller ever blocks on two locks — the single-handle lock design (and its
  absent deadlock surface) is preserved.
- **QA-18:** corrected below — the #89 harness claim now states the exact
  results instead of "pass in full."

**Issue #95 merge-closure candidate:** retirement publication is now
commit-scoped: record creation, replacement, and removal are coordinated, final
authorization requires the exact current publication, and fingerprints are
re-proved after retained-handle coordination. Public `delete_index` makes one
nonblocking lifecycle-coordination attempt while internal retirement keeps its
bounded wait; successful deletion preserves the stable per-index lockfile.
The public deleted, missing, and lifecycle-busy results have one authoritative
runtime vocabulary, with a `SPEC.md` table and drift guard. QA-25's explicit
caller wait policy and nonblocking default were preserved and independently
verified, not redesigned.

Additive and 1.x-compatible: new defaulted kwarg, new exception only raised
when that kwarg is passed, new response keys, no INDEX_VERSION bump. Tests:
`tests/test_v1_115_0.py` (10) + `tests/test_v1_115_0_qa89.py` (10) +
`tests/test_v1_115_0_qa90.py` (4); @rknighton's `qa_adversarial_test.py`
passes 8/8 verbatim. His #89 `qa_blockers.py` passes 9/9; `qa_process.py`
passes 5/6 — the one flip is `test_observation_direct_retained_delete`, his
explicitly-labeled current-behavior observation, which now asserts pre-#89
behavior by design (the QA-10 policy voids the record on a retained-handle
direct delete). His #90 `qa_atomic_gap.py` both-indexes-absent state is
unreachable: the mid-gate retained delete is refused, so the harness stops
at its delete-returns-True assert while the invariant it protects holds.
## [1.114.2] - 2026-07-23 - canonical handoff contract: finalize_handoff + munch://handoff/<id> (suite parity, jcodemunch-mcp #374)

New tool `finalize_handoff` (`jdocmunch.handoff/v1`) + resource
`munch://handoff/<id>` — suite parity with jcodemunch-mcp v1.108.162. A
multi-step documentation audit ends with one authoritative, server-owned
Markdown handoff: the assistant authors the analysis; the server
deterministically assembles the caller's sections plus optional named
appendices (each exactly once, duplicates rejected), validates every
`evidence_refs` entry against the session's actual retrieval record
(section ids and doc paths served by `search_sections` / `search_titles` /
`get_section` / `get_sections`, recorded at the response chokepoint; unknown
refs fail closed with an `unknown_refs` list), persists session-scoped in
memory, and returns a compact receipt `{handoff_id, resource_uri, sha256,
length, canonical: true}`. The resource serves the immutable body with
byte-identical repeated reads; `canonical: true` is advisory metadata only.
No character limit; never writes to the documentation corpus; standard tier;
`readOnlyHint: false`. Tool count 63 → 64. Additive/1.x, no INDEX_VERSION
bump. Tests `tests/test_v1_114_2.py` (15).

## [1.114.1] - 2026-07-23 - BM25 tokenizer: Unicode word splitting + CJK character bigrams (#91)

Reported by @tetiz123. The BM25 split regex was `[^a-z0-9]+`, so every
non-ASCII character acted as a separator: Korean/Japanese/Chinese content
produced zero tokens (the lexical channel contributed nothing, and installs
without an embedding provider had no working search at all for those corpora),
and accented Latin was mangled (`café` → `caf`). The module docstring claimed
Unicode word boundaries; the implementation now actually delivers them.

The tokenizer splits on Unicode word boundaries (`[\W_]+`), keeps accented
Latin intact, and expands CJK runs (Hangul, Hiragana/Katakana, Han) into
overlapping character bigrams — CJK has no whitespace word boundaries, and
since index time and query time share the same expansion, bigram overlap is
the match signal. Mixed-script tokens (`초과근무OvertimeService`) split
cleanly; CamelCase/snake_case handling, URL expansion, frontmatter/fence
scrubbing, and English stop-words are unchanged, and pure-ASCII corpora
tokenize exactly as before. `search_titles`'s private ASCII-only tokenizer
gets the same treatment via a new shared `word_tokens` helper.

No reindex needed: BM25 tokenizes stored section content at scoring time, so
existing indexes pick up CJK-capable lexical scoring immediately.

## [1.114.0] - 2026-07-22 - QA-04/QA-05: disclose failed Git verification, complete the result-code contract (#88)

@rknighton's follow-up QA on #88.

**QA-04 (Medium):** `doc_resolve_repo` returned the same ordinary not-found
response whether Git confirmed a path as non-Git or Git verification failed
outright (missing binary, timeout, permissions). A failed verification now adds
a structured `git_verification` block (`verified: false`, reason code
`git_verification_unavailable`) and a hint explaining that worktree-based
canonical-index discovery was skipped. The provisional-creation path is
unchanged: `index_local` still works in this state and quarantines the new
index as provisional.

**QA-05 (Low):** SPEC.md claimed a complete, drift-guarded result vocabulary,
but the read-time resolver emitted reason codes written as inline string
literals (e.g. `unique_location_candidate`) that bypassed the guard's
STATUS_*/REASON_* attribute scan. All twelve resolver codes are now module
constants, documented in a new `worktree_resolution.reason_code` table, and a
new AST guard rejects any future inline `reason_code` literal in src.
Related corrections in the same pass:

- `provisional_cap_exceeded` and `legacy_reconcile_not_applicable` are
  documented as top-level `error` codes — the field where they are actually
  returned — instead of reason-code table rows.
- USER_GUIDE.md documents the `legacy_reconcile="report"|"apply"` workflow.
- The v1.108.0 changelog date is corrected (2026-07-28 → 2026-07-20).

Additive and 1.x-compatible: one new response block on an existing error path,
no tool or schema change, no INDEX_VERSION bump. Tests:
`tests/test_v1_114_0.py` (7); the attached `test_remaining_qa_findings.py`
harness passes 3/3 without weakened assertions.

## [1.113.0] - 2026-07-22 - QA-02 contained fixes: retirement delete result is authoritative (#88)

@rknighton's adversarial QA on the reconciliation lifecycle (#88) found three
reproducible gaps. This release ships the two contained QA-02 fixes; the
QA-01 refresh/retirement coordination and QA-03 read-only report follow in a
dedicated coordinated-retirement release.

- **Exact-duplicate graduation honors the delete result.** The
  identity-plus-hash-proven duplicate path in `_resolve_graduation`
  (`tools/index_local.py`) called `store.delete_index(...)` and ignored the
  return, reporting `reconciled` + `removed_handle` even when removal returned
  `False`. It now checks the result: on a failed removal it reports the new
  recoverable `graduation_cleanup_incomplete` reason code, keeps both indexes
  discoverable, and never emits a `removed_handle` for a loser that still
  exists. The happy path is unchanged.
- **Partial cleanup stays discoverable and retryable.** `DocStore.delete_index`
  (`storage/doc_store.py`) unlinked the primary `<name>.json` record FIRST,
  then removed the content cache and sidecars. If a later removal raised, the
  index was already un-loadable, so the documented retry could not find the
  handle. The primary record is now removed LAST — content cache, summary, and
  sidecars go first — so any mid-cleanup failure leaves the index fully
  loadable and the retry succeeds.

New `REASON_GRADUATION_CLEANUP_INCOMPLETE` in `_worktree_corpus.py`, added to
the B4 vocabulary drift-guard (`test_v1_106_0.py`) and the published
`SPEC.md` status/reason_code table. Additive/1.x — no tool add/rename, no wire
break (the failed-delete case previously mis-reported success), no
`INDEX_VERSION` bump. Tests: `tests/test_v1_113_0.py` (4). QA-01/QA-03 remain
open on the #88 tracker for the coordinated-retirement build.

## [1.112.0] - 2026-07-21 - tool-surface schema receipt in session stats (suite parity, jcodemunch-mcp v1.108.153)

`get_session_stats` now carries an advisory `tool_surface` block: visible vs
catalog tool counts (after `JDOCMUNCH_TOOL_PROFILE` + `JDOCMUNCH_DISABLED_TOOLS`
filtering), estimated schema tokens for each, `schema_tokens_avoided` by the
active profile, and the top-15 heaviest tool schemas. Estimated at the meter's
bytes/4 scale over the `{name, description, inputSchema}` serialization. jDoc
has no Counter surface, so the block carries `profile` but no `surface` key.
Read-only, computed inline on the stats call only, nothing persisted; a probe
failure omits the block rather than failing the call. Additive/1.x — no new
tool, no schema change, no INDEX_VERSION bump. Tests: `tests/test_v1_112_0.py`
(6).

## [1.111.0] - 2026-07-21 - runtime identity resource (suite parity, jcodemunch-mcp#371)

New MCP resource `munch://runtime/identity` — a read-only
`munch.runtime.identity/v1` JSON document giving multi-agent harnesses process
provenance for this server instance: `schema`, `product`, `version`,
`transport`, `pid`, `process_start {value, source}`, `instance_id`, and an
optional `launch_id` echo. `process_start` is OS-derived when obtainable
(Windows `GetProcessTimes`; Linux `/proc/self/stat` starttime + btime) with
`source: "os"`; when the OS probe is unavailable the value is the module's own
first-read clock, disclosed as `source: "self_recorded"` — never presented as
OS evidence. `instance_id` is a uuid4 minted once per process lifetime, so a
restart (even with a reused PID) yields a new identity. `launch_id` echoes
`JDOCMUNCH_LAUNCH_ID` (fallback `MUNCH_LAUNCH_ID`) and is omitted when unset.
Deliberately excluded: command lines, env, cwd, hostnames, corpus paths, task
data. Delivered as a resource, not a tool — no tool-count or schema change and
zero cost when unused; on-demand read only, no background or network behavior.
Additive/1.x. New module `runtime_identity.py`; tests `tests/test_v1_111_0.py`
(11). Same contract ships in jcodemunch-mcp v1.108.152 and jdatamunch-mcp
v1.22.0.

## [1.110.0] - 2026-07-21 - Part C.2: explicit-intent legacy reconciliation (#87)

The final build of the #80 identity arc: proving a genuine pre-1.102
fieldless legacy index redundant against its modern peer, and retiring it —
only ever under explicit caller intent, behind the same hard Git proof gates
as the modern supersession path.

- New `index_local(legacy_reconcile="report"|"apply")`. `report` proves
  readiness without changing anything; `apply` repeats the proof immediately
  before the only destructive step. Omitted (the default), an ordinary
  refresh stays exactly what it was: backfill-only, never retires.
- The explicitly selected legacy handle is the only possible loser. The
  operation requires an explicit `name=`, a handle that is fieldless at call
  start, a full refresh, default `worktree_mode`, and confirmed Git lineage;
  any miss refuses fail-closed (`legacy_reconcile_not_applicable`) with no
  write.
- Retirement proof: exactly one non-provisional modern peer matching the
  verified corpus identity (lineage + relative root + durable selection),
  both indexes clean and certified at the same commit, and every
  selected-handle path present in the peer with the same stored hash — a
  missing hash is unproven, never assumed. Zero peers reports
  `legacy_reconcile_no_modern_peer`; several report
  `legacy_reconcile_ambiguous`; nothing is ever removed on a failed proof.
- Basename disclosure (`legacy_index_present`) never enters the proof set
  (LC2-02): identity is matched on verified lineage evidence only.
- `apply` reuses the #86 retirement primitive: final recheck immediately
  before deletion (`legacy_reconcile_conflict` on drift, nothing removed),
  loud + idempotent cleanup failure (`legacy_reconcile_cleanup_incomplete`),
  leftover sidecars disclosed. The peer is never touched; the success
  response returns its handle with `removed_handle`/`removed_file_count`.
- Under C.2 intent the selected handle deliberately stays fieldless (no
  identity backfill), so a retry after any mid-flight failure still passes
  the fieldless-at-call-start gate. Backfill remains the ordinary refresh's
  job.
- Nine new reason codes, all additive, in the B4 drift guard and the
  published SPEC.md vocabulary table (the #84 contract).
- Tests: `tests/test_v1_110_0.py` (12; real Git repos + linked worktrees,
  peer verified byte-for-byte across apply, drift + cleanup-failure rows).

Additive/1.x: new optional kwarg + new response keys only; calls without
`legacy_reconcile` are byte-identical. No INDEX_VERSION bump.

## [1.109.0] - 2026-07-21 - modern verified-snapshot supersession (#86)

The dedicated modern-snapshot follow-up to the #80 identity arc. When a
provisional and an established index represent the same verified corpus at
different certified commits, Git ancestry now resolves them — behind proof
gates that keep every other pair untouched.

- **MS-02 — strict-ancestor provisional retires.** When the provisional's
  snapshot is certified clean at a valid commit, still current in its
  checkout, and Git proves it a strict ancestor of the established index's
  certified snapshot, the older provisional is retired (loser only; the
  established index is byte-for-byte unchanged) and the established handle
  returned. Reason code `superseded_by_established` carries both SHAs, the
  relationship, and `removed_handle`/`removed_file_count`. A final
  identity/candidate/generation recheck runs immediately before the one
  destructive step; any drift returns `supersession_conflict` with nothing
  removed.
- **MS-01 — descendant provisional never supersedes.** The established index
  is never replaced, retargeted, or aliased automatically. Reason code
  `provisional_newer_than_established` reports both SHAs and the explicit
  completion path: refresh the established handle from this checkout, then
  re-run — exact deduplication (v1.108.0) finishes the job (MS-03).
- **MS-04 — every negative boundary preserved.** Equal hashes still use exact
  dedup; diverged, unordered (`ancestry` reported), unproven, dirty,
  uncertified, stale-snapshot, provisional-peer, and multiple-peer pairs keep
  both indexes. A cleanup failure is visible
  (`supersession_cleanup_incomplete`, or `cleanup_incomplete` +
  `leftover_files` on the success disclosure) and retries idempotently.
- **New `commit_ancestry` helper** (`tools/_git.py`): fail-closed, bounded
  git probes; any non-determination is `unproven`, never an ordering.
- **Published vocabulary table.** SPEC.md now carries the complete
  runtime-matched status/reason-code table (the #84 item-4 contract), with a
  test that fails if the runtime can emit an undocumented value.

Additive/1.x: four new reason codes (drift-guarded + published), new response
keys, no tool/schema change, no INDEX_VERSION bump. Tests:
`tests/test_v1_109_0.py` (11, real Git repos + linked worktrees). Full suite
1716 passed.

## [1.108.0] - 2026-07-20 - C.1 hardening: hash-proven duplicates, complete cleanup, honest listings (#85)

rknighton's focused QA pass on the 1.107.0 graduation work reproduced four
gaps; all closed, his harness passing 4/4.

- **C1-01/C1-02 — exact-duplicate cleanup now requires content proof.** The
  reconcile auto-cleanup gated only on Git-verified identity plus path
  coverage, so a provisional index holding DIFFERENT content for the same
  paths could be removed as though it were a duplicate. A second destructive-
  safety gate now requires every retired file to exist in the surviving index
  with the same stored hash; a mismatch (or an unprovable hash) keeps both
  indexes and reports the new additive reason code
  `graduation_content_differs` with the differing files (capped at 20),
  the established handle, and a suggested next action. A successful
  reconcile response now also confirms what was removed (`removed_handle`,
  `removed_file_count`).
- **C1-05 — dirty state.** Exact path/hash equality still proves duplication
  regardless of Git cleanliness; differing dirty content is never removed
  (Git ancestry cannot order uncommitted snapshots).
- **C1-06 — hashes never replace the identity gate.** Hash equality without
  verified lineage never reconciles (explicit negative test).
- **C1-03 — controlled supersession: decided and deferred.** Supersession
  between fully certified, ancestry-ordered modern snapshots is accepted in
  principle but is NOT in this hardening pass; until it ships as its own
  focused build with atomic-failure coverage, different-content pairs remain
  separate and visible.
- **C1-07/C1-08 — complete cleanup of a retired index.** `delete_index` (and
  therefore reconcile auto-cleanup) now removes every index-owned auxiliary
  sidecar: `.embeddings.jsonl`, `.terms.json`, `.related.json`,
  `.boilerplate.json`, `.duplicates.json`.
- **C1-09 — identity version survives listings.** The summary sidecar and
  `list_repos` rows now carry `corpus_identity_version`, so a modern index is
  never presented as pre-1.102 legacy. A summary written before the key
  existed falls back to the monolith and self-heals on the next save.

Additive/1.x: one new reason code (covered by the vocabulary drift guard),
new response keys, no tool/schema change, no INDEX_VERSION bump. Tests:
`tests/test_v1_108_0.py` (7, adapted from the attached harness plus the
C1-05/C1-06 decided cases). Full suite 1705 passed.

## [1.107.0] - 2026-07-20 - provisional-index graduation (#80 Part C)

Part C of the local-index reconciliation arc: a provisional index (created
under failed Git verification in Part B) can now graduate when the proof
arrives, behind six security invariants so promotion can never be manufactured.

When a provisional index is FULLY refreshed (`index_local`, no `paths` subset)
and Git lineage is now CONFIRMED, one of:

- **Graduate in place.** No established index shares the identity, so the
  provisional is promoted: identity fields written, provisional flag cleared,
  it becomes a normal established index.
- **Reconcile (auto-cleanup).** An established index already holds this
  identity, so the provisional is the loser: it is removed and the established
  handle is returned, but ONLY after confirming the provisional's documents are
  a subset of the established index (no document loss). The established index is
  never modified.
- **Fail closed and stay provisional** when it would not be safe: more than one
  established index matches the identity (ambiguous), or the provisional holds
  documents the established index lacks (diverged, so it is never deleted).

The gate is the same positive proof #83 requires (confirmed lineage), never
weaker and never an accumulation of grey-area signals; provisional indexes are
excluded from the candidate set, so they never vouch for each other; promotion
is event-driven (a verifying refresh), never time-driven; and conflicts never
touch the established index. A subset refresh, a still-unverifiable refresh, and
any volume of provisional accretion never produce a graduation.

New pure `classify_graduation` helper plus graduation outcome vocabulary
(`graduated_verified`, `reconciled_to_established`, `graduation_ambiguous`,
`graduation_content_diverged`), all covered by the drift-guard. Tests:
`tests/test_v1_107_0.py` (13, including the adversarial invariant gate).
Additive / 1.x, no INDEX_VERSION bump. Deferred to a follow-on: pre-1.102
legacy physical-index merge (§4.3); Part B already discloses that case
(`legacy_index_present`).

## [1.106.0] - 2026-07-20 - reconciliation quarantine, quarantine-only (#80 Part B)

Part B of the local-index reconciliation arc (#80), the safe foundation the
Part C reconciler is built on. **Quarantine only, with no graduation path** —
a quarantined index stays quarantined until Part C.

- **Provisional stamp on failed Git verification.** When both git
  common-directory probes are *unavailable* (timeout, missing binary, or OS
  error — not a clean not-a-repository answer), `index_local` still creates the
  index (availability over a transient git failure) but stamps it
  `reconciliation_state = "provisional"` and discloses a structured
  `reconciliation` block on the response. A new `_git_probe` classifies the
  failure so a genuine non-Git corpus (git ran and said no) stays normal while
  only an unanswerable probe quarantines. New additive `DocIndex.reconciliation_state`
  (omit-when-empty; carried through save / incremental / summary sidecar /
  list_repos row; no INDEX_VERSION bump).
- **Authority-free while provisional.** A provisional index is excluded from
  worktree reuse candidates, so it can never become an `established_handle` or
  suppress creation of the real corpus. It carries no lineage key and never
  graduates in Part B — a refresh preserves the provisional state (no silent
  promotion via reindex).
- **Per-source_root provisional cap.** Creation beyond a small per-root ceiling
  fails closed with `provisional_cap_exceeded` rather than letting many
  marginal provisional indexes accrue silently.
- **`legacy_index_present` disclosure.** When a fresh index is created and an
  older (pre-1.102, identity-fieldless) index for a plausibly equivalent corpus
  exists, the response flags it so the duplicate is not silent and the caller
  can reindex the older corpus into the lineage system.
- **Vocabulary drift-guard.** A test asserts every Part B status / reason_code
  the runtime can emit is documented, keeping the public vocabulary honest
  until Part C publishes the complete runtime-matched table.

Graduation and reconciliation (promoting a provisional index to established,
merging pre-1.102 duplicates) are **Part C**, gated behind a single hard proof
gate — a genuine git-verified identity match, never an accumulation of
grey-area signals. Tests: `tests/test_v1_106_0.py` (10). Additive / 1.x.

## [1.105.1] - 2026-07-20 - consistent candidate-list bound on doc_resolve_repo (#84)

QA follow-up (@rknighton) on the 1.102.0 worktree-reuse work. On the
`doc_resolve_repo` not-found worktree path, the nested
`worktree_resolution.candidates` list correctly capped at five records with
`total_candidates` reporting the true count, but the top-level
`canonical_candidates` list returned every record found. Both public lists now
share the same five-record bound; the full number found stays reported via
`worktree_resolution.total_candidates`. One-line assembly fix in
`tools/resolve_repo.py` (reusing the shared `MAX_CANDIDATES` constant); the
resolver and every other response field are unchanged. Boundary regression
coverage added at exactly five and six records, plus the eight-record
reproduction and zero/one sanity cases (`tests/test_v1_105_1.py`, 5).
Additive/1.x; no INDEX_VERSION bump. Items 2 through 4 of the report are
policy decisions parked for the Part C reconciliation phase.

## [1.105.0] - 2026-07-19 - office document ingestion (.pdf/.docx/.pptx/.epub)

New optional extra: `pip install jdocmunch-mcp[office]` teaches local
indexing (`index_local` / `index-file` / the `watch` daemon) to ingest
PDF, Word, PowerPoint, and EPUB documents. Files are converted to
Markdown on-machine at read time via Microsoft's MIT-licensed markitdown
(local converters only — the cloud converters it offers are never
enabled, so no network request originates from conversion), then
sectioned, searched, and health-checked like any other doc. Converted
output is cached under the storage root (`.office_cache/`, keyed by
file-content hash + converter version) so refreshes never re-convert
unchanged documents; discovery applies a 25MB office-specific size cap
(binary sources run large while their extracted Markdown stays small).
Without the extra, office files are skipped at discovery with a distinct
coverage-report reason (`office_extra_not_installed`); `index-file`
returns a clean install hint. Live-source freshness reproduces the
conversion leg (cache hit for unchanged files) and falls back to the
stored mirror when conversion is unavailable. Deliberate boundaries:
tabular formats (`.csv`/`.xlsx`) stay with jdatamunch-mcp, and the
GitHub remote leg does not fetch office files — local-only. Additive/
1.x: base install is byte-identical, no INDEX_VERSION bump (office docs
enter an index on the next refresh like any newly-discovered file).
Tests: `tests/test_v1_105_0.py` (10).

## [1.104.0] - 2026-07-19 - advisory session token budget

Suite parity with jcodemunch-mcp v1.108.146. Set
`JDOCMUNCH_SESSION_TOKEN_BUDGET` to an advisory ceiling over response
tokens served (the context this server injects into the agent, counted at
the response chokepoint with the same bytes/4 estimate the savings meter
uses). Once the session crosses 80% of the limit, every response carries
`_meta.budget = {limit, spent, state}` (`approaching` at >=80%, `over` at
>=100%) — attached AFTER meta_fields filtering, so the warning survives
the token-efficient default that strips `_meta` (an advisory the default
config silently deletes would be no advisory at all). `get_session_stats`
gains `session_response_tokens` and, when configured, the `budget` block
in all three states. Never blocks, throttles, or truncates — awareness
only; hard caps belong to the gateway layer. Unset/`0` disables and the
wire is byte-identical. Additive/1.x; inline compute, no new background
or network behavior, no INDEX_VERSION bump. Tests:
`tests/test_v1_104_0.py` (9).

## [1.103.0] - 2026-07-19 - coverage contract on absence claims

Prompted by community feedback on the retrieval-verdict article: an
`absent` verdict backed only by scan counts lies by omission when files
were excluded at index time. A doc index now remembers what its discovery
walk left out, and an absence claim discloses it.

Index time: every full discovery walk of `index_local` (no explicit
`paths`) persists a coverage block on the index (`DocIndex.coverage`):
walk kind, files indexed, per-reason skip counts tallied at the existing
discovery skip sites (unsupported extension, oversize, gitignored, skip
patterns, secret files, symlink and traversal guards, stat/read errors),
the count of files that parsed to zero sections, and a UTC timestamp.
Incremental and subset (`paths`) saves carry the block forward unchanged;
the next full re-walk overwrites it (self-heals). No index-format version
bump: the field follows the established omit-when-empty convention, and
legacy indexes load with an empty block.

Query time: `search_sections` and `find_endpoint` attach a `coverage`
block to `absent`/`degraded` verdicts only, via the new
`index_coverage_meta` helper: generation metadata (`indexed_at`,
`index_version`, `git_head` first 12 when tracked), `files_indexed`,
`excluded` per-reason counts, and `no_sections_files`. When the index
predates the contract the block is omitted entirely; empty coverage means
unknown, never fabricated. `ok`/`low_confidence` verdicts stay lean.
`build_verdict` also gains a `scorer` integer version pin (starts at 1)
so a stated confidence ties to the scorer that produced it.

Suite parity with jCodeMunch v1.108.145; clean-room jDoc shape (sections,
not symbols). Additive, 1.x-compatible: new persisted field, new response
keys on negative verdicts only. Tests: `tests/test_v1_103_0.py` (9).

## [1.102.0] - 2026-07-18 - reuse an established corpus index across linked Git worktrees (#83)

Item B of the #80 identity meta-issue, PRD by @rknighton. The same
documentation corpus checked out in two linked Git worktrees used to
produce two duplicate physical indexes and a not-found resolution from the
second worktree. One side-effect-free resolver, shared by both public
tools, now translates identity across worktrees: logical identity =
linked-worktree lineage (Git common directory, never inferred from remote
URL, commit, folder name, or content) + repository-relative corpus
location + the Item A durable selection. doc_resolve_repo additively
returns established handles as bounded canonical_candidates with a
worktree_resolution evidence object, read-only, with selection reported
unavailable. index_local reuses a proven-fresh equivalent (same certified
revision, no relevant uncommitted docs) by returning the established
handle with no write; stale, dirty, ambiguous, legacy-unresolved, or
evidence-incomplete outcomes return bounded decisions with no write; new
worktree_mode="branch_local" intentionally creates an exact-path index.
Cross-worktree creation races contend on one lineage-keyed claim with an
under-claim recheck, extending the #82 single-winner rule; the #82
adversarial harness stays 4/4. Additive, 1.x-compatible: optional persisted
identity fields, one new tool parameter, new response objects only.

## [1.101.0] - 2026-07-18 - Item A hardening: single winner, true ambiguity, order-independent identity (#82)

Adversarial QA by @rknighton reproduced four gaps in the 1.100.0 corpus
identity guarantees; all four are closed, verified by his supplied harness
(4/4). Creation claims now publish their ownership payload atomically (a
private temp file hardlinked into place), and a claim that exists without a
readable payload blocks creation with a new corpus_creation_in_progress
error instead of racing to a second physical index. Several equivalent
matches now always return bounded ambiguity with no established handle;
registry order never promotes a winner. Durable-selection identity is a
symmetric relation independent of creation order: an intentional named
subset and a full index are distinct corpora in either order, while a
temporary paths refresh remains a directional refresh rule only. Corpus-
shaping inputs (extra_ignore_patterns, follow_symlinks) are folded into the
durable-selection descriptor, and a refresh that changes coverage updates
identity and discloses it via corpus_selection_changed rather than
retargeting silently. Additive, 1.x-compatible.

## [1.100.0] - 2026-07-18 - corpus identity: index_local won't duplicate an equivalent source (#81)

Item A of the #80 identity meta-issue, spec by @rknighton. index_local now
resolves a structured corpus identity before choosing physical storage: the
normalized local root (the same resolve+normcase comparison doc_resolve_repo
uses) plus a durable documentation selection ("full", or a subset descriptor;
paths=["."] counts as full), persisted as DocIndex.corpus_selection. An
equivalent source with no conflicting explicit name reuses the established
handle; an explicit different name returns a corpus_already_indexed conflict
with no persistent write; several equivalent legacy indexes return bounded
ambiguity instead of guessing. A subset paths refresh never redefines the
durable selection, containment alone never establishes identity, and creation
sits behind an atomic claim so overlapping creations converge on one physical
index. Additive, 1.x-compatible, INDEX_VERSION unchanged.

## [1.99.0] - 2026-07-18 - doc_resolve_repo: path to doc-index handle lookup (#79)

Requested by @rknighton. New read-only doc_resolve_repo(path) answers "which
doc index covers this path?" via stored source_root metadata in an O(1)-sized
response: exact root match first, then the most specific containing root;
equally-specific duplicates return bounded ambiguity instead of guessing;
GitHub corpora (no source_root) never match. Suite parity with jCodeMunch's
resolve_repo; the doc_ prefix keeps the two servers collision-free. Tool
count 62 to 63. Additive, 1.x-compatible.

## [1.98.0] - 2026-07-16 - `watch` daemon: keep doc indexes fresh on any on-disk change (#78)

Reported by @oderwat. jDocMunch's index freshness rode entirely on the
PostToolUse hook, which only fires when the *agent* edits a doc file. Docs
changed outside the agent (a git pull, an editor, a build step, a teammate) went
stale until the agent happened to touch that file again. jCodeMunch has had a
`watch-all` daemon for this; jDocMunch now gets the equivalent, scoped to
documentation file types.

New foreground daemon `jdocmunch-mcp watch`: auto-discovers every locally-indexed
doc repo (registry-driven, via `list_repos`), watches each `source_root` with
`watchfiles`, filters to documentation extensions (`.md`/`.rst`/`.txt`/`.adoc`/
`.ipynb`/`.html`/... — the same `_DOC_EXTENSIONS` set the reindex hook uses), and
on any change re-indexes the owning index **incrementally** through the existing
`index_local(paths=[...])` subset path (jdoc#31 semantics: adds/updates listed
files, deletes a listed-but-missing file, never prunes unlisted docs). Repos
indexed while it runs are picked up on the next discovery pass; GitHub-sourced
indexes (no local source_root) are skipped. Clean SIGINT/SIGTERM shutdown; WSL
polling awareness (`JDOCMUNCH_WATCH_POLL_DELAY_MS`, mirrors jcm #356).

Background login service, same as jCodeMunch's: `jdocmunch-mcp watch-install`
registers the daemon as a per-user systemd unit / launchd LaunchAgent / Task
Scheduler task (`jdocmunch-watch`); `watch-uninstall` removes it; `watch-status`
prints service state + per-repo watch coverage. The daemon launches via
`sys.executable -m jdocmunch_mcp watch`, so a `__main__.py` entry point was added.

New agent-facing MCP tool `get_watch_status` (standard tier, read-only): reports
whether the login service is active and, per local doc repo, whether its
source_root still exists on disk (watchable). Tool count 61 -> 62.

New dependency `watchfiles>=0.21.0` (the only new runtime dep; the daemon fails
with a clear message if it's somehow absent).

**Disclosure:** README gains a "Background behavior, fully disclosed" section
(the file watcher, the opt-in login service, the existing hooks, telemetry, and
the local store) and drops "real-time file watching" from "Not intended for."
Additive, 1.x-compatible: no tool rename/removal, no `INDEX_VERSION` bump, no
wire change to existing tools. Tests: `tests/test_v1_98_0.py` (15) +
`tests/test_server.py` count/name updates.

### Added
- `jdocmunch-mcp watch` foreground daemon (`src/jdocmunch_mcp/watch.py`).
- `jdocmunch-mcp watch-install` / `watch-uninstall` / `watch-status` login-service
  commands (`src/jdocmunch_mcp/service_installer.py`).
- `get_watch_status` MCP tool (`src/jdocmunch_mcp/tools/get_watch_status.py`).
- `src/jdocmunch_mcp/__main__.py` so `python -m jdocmunch_mcp` works.
- `watchfiles>=0.21.0` runtime dependency; `JDOCMUNCH_WATCH_POLL_DELAY_MS` env var.

## [1.97.1] - 2026-07-16 - docs only

Documentation wording only. No code, wire-format, or behavior change from 1.97.0.

## [1.97.0] - 2026-07-16 - update model price constants to current Anthropic pricing

Updates the model input-price constants used by the `cost_avoided` dollar
estimate to Anthropic's current published rates: Opus $5/MTok, Sonnet $3/MTok,
Haiku $1/MTok. Anthropic has reduced input pricing across the Opus line since
these models launched, so the constants now track current pricing.

Token savings are measured in tokens and valued at the applicable model rate,
so the underlying savings are unchanged; only the price constants now reflect
current pricing.

### Changed
- `claude_opus` input rate set to the current $5/MTok (comment cites the dated
  source, anthropic.com/pricing 2026-06-24).

### Added
- `claude_sonnet` ($3/MTok) and `claude_haiku` ($1/MTok) entries, so
  `cost_avoided` / `total_cost_avoided` show the full current model set (parity
  with the sibling code MCP's price table). Additive keys only; the existing
  `claude_opus` and `gpt5_latest` keys are unchanged in name, so the wire shape
  stays 1.x-compatible.

`cost_avoided` does not touch the public token counter (which stores tokens and
values them at display time). No INDEX_VERSION bump, no tool add/rename. Suite
parity: jcm v1.108.130 (receipt table) + jdata v1.19.0 (same constants).

## [1.95.0] - 2026-07-10 - suite-parity retrieval verdict (`_meta.verdict` on search_sections + find_endpoint)

### Added

- **`search_sections` and `find_endpoint` now emit `_meta.verdict`** — the same
  agent-facing honesty contract the sibling code MCP ships on its search tools. An
  empty or weak result is positive, token-saving evidence: the index can attest
  "this topic is not documented here" instead of leaving the agent to reformulate
  a query for something that provably isn't present. Taxonomy: `ok` /
  `low_confidence` / `absent` / `degraded`.
- **`degraded`** fires when a caller requests semantic search on an index with no
  embeddings — results are lexical-only, so absence is not proven (re-index with
  embeddings for semantic recall). It takes precedence over `absent`.
- **`low_confidence`** keys off the existing retrieval confidence score (the
  documented < 0.4 ambiguity floor), so a returned-but-shaky top hit is flagged.
- **`absent`** carries a `did_you_mean` list of documents whose path or title
  contains a query term, so a miss redirects the agent instead of repeating.
  `find_endpoint` suggests existing endpoint paths that share a segment with a
  missed glob.

Clean-room jDoc implementation (new `retrieval/verdict.py`); only the wire shape
is shared with the sibling MCPs — no cross-suite import. Additive and
1.x-compatible: `_meta.verdict` is a new key, every existing response field is
unchanged, no `INDEX_VERSION` bump, inline compute (no new background or network
behavior). Tests: `tests/test_v1_95_0.py` (13).

## [1.94.0] - 2026-07-08 - large-corpus stability: vectors out of the monolith, throttled reindex hook, cheap list_repos (#75, #76, #77)

Reported by @floke75 (three linked issues, confirmed on two machines; a 16 GB
box suffered cascading jetsam kills / swap storm / WindowServer watchdog restarts
from the interaction of all three). Additive and 1.x-compatible: `INDEX_VERSION`
stays 3, no forced reindex — existing on-disk indexes keep working and drop their
inline vectors on the next save.

### Changed

- **#75 — embedding vectors live only in the sidecar, never inline in the index
  monolith.** `doc_store` persisted every section's vector inline in
  `~/.doc-index/<owner>/<name>.json`, pretty-printed at `indent=2` (~26 KB of JSON
  per 1024-dim section). On a broadly-indexed repo the monolith reached multiple
  GB and every `load_index` parsed the whole thing into Python lists — ~8 GB RSS
  and ~60 s on a 175k-section corpus, and the vectors were already duplicated in
  the `.embeddings.jsonl` cache. Fix: `_index_to_dict` strips the `embedding` key
  non-mutatingly (in-memory sections keep their vectors for the related/
  boilerplate/dedup sidecars built right after `save_index`), the monolith is
  written with compact `separators=(",", ":")` instead of `indent=2`, and vectors
  rehydrate lazily from the sidecar as `array('f')` (~4 KB per section, not ~70 KB
  as float lists) the first time a semantic code path needs them
  (`DocIndex._rehydrate_embeddings`, called from `_ensure_semantic_matrix`,
  `find_similar_sections`, `get_related_sections`, and the `get_doc_health`
  embedding count). `_has_embeddings` treats a present sidecar as "embeddings
  exist". A save-time safety net writes the sidecar first when sections carry
  vectors but none exists yet, so the strip is always lossless. `load_index` on a
  corpus this size drops from ~60 s / ~8 GB to sub-second / <0.5 GB with unchanged
  ranking (float32 shifts cosine ~1e-7, ordering unaffected outside exact ties).

- **#77 — `list_repos` no longer json-parses every monolith to take two
  `len()`s.** `DocStore.list_repos` (the documented first call of a session, also
  hit by the PreCompact snapshot hook) loaded every index monolith just to read
  `repo`/`indexed_at`/`doc_types` and `len(sections)`/`len(doc_paths)`. Each save
  now writes a tiny `<name>.summary.json` sidecar (atomically, inside the same
  per-repo write lock as the monolith), and `list_repos` reads it instead —
  falling back to the full parse for legacy indexes that predate the sidecar, and
  robust against a single corrupt monolith taking the whole listing down.
  `delete_index` removes the sidecar.

- **#76 — the PostToolUse auto-reindex hook is throttled.** `run_posttooluse`
  spawned one fire-and-forget `index-file` per Edit/Write with no lock, debounce,
  or spawn cap, so a burst of N edits fanned out into N concurrent full-index
  loads (the memory amplifier behind the crash). Now: a per-file leading-edge
  **debounce** (`JDOCMUNCH_HOOK_DEBOUNCE_SECONDS`, default 3 s) coalesces rapid
  repeat edits before anything spawns; the hook spawns a new throttled
  `hook-reindex` worker that acquires one of N cross-process **slot locks**
  (`JDOCMUNCH_HOOK_MAX_REINDEX`, default 2) *before* it loads the index and exits
  if the cap is saturated (the next edit reindexes — correctness holds); and an
  opt-in **breadcrumb log** (`JDOCMUNCH_HOOK_LOG=1` → `_hooks/reindex.log`) makes
  pile-ups/skips observable instead of silently discarded to `DEVNULL`. New
  `hook-reindex` CLI subcommand.

Tests: `tests/test_v1_94_0.py` (21); `tests/test_hooks.py` updated for the new
`hook-reindex` spawn target + a debounce-coalesce case.

## [1.93.0] - 2026-07-07 - MCP readOnlyHint annotations (suite parity with jcodemunch PR #361)

### Added

- **Every tool advertises `ToolAnnotations(readOnlyHint=...)`.** MCP clients that
  gate execution (Claude Code plan mode) prompted for approval on every jDoc
  call because tools carried no annotations. Read tools are now
  `readOnlyHint=True` (plan mode runs them silently) and the write-set is
  `False`. Applied at the `list_tools` chokepoint via a non-mutating
  `model_copy`. The write-set (`index_local`, `doc_index_repo`, `delete_index`,
  `define_repo_group`, `tune_weights`, `check_embedding_drift`) is any tool that
  can mutate persistent state under any argument — biased conservative, since
  mislabeling a writer as read-only is the harmful direction. Suite parity with
  jcodemunch-mcp (PR #361) and jdatamunch-mcp. Additive, 1.x-compatible (new
  `tools/list` field only). Tests: `tests/test_v1_93_0.py` (4).

## [1.70.2] - 2026-06-12 - search/verify/event-loop fixes (#32, #33, #34)

Patch release closing the remaining three issues from @mmashwani's
2026-06-11/12 report batch (the first, #31, shipped in v1.70.1).

**#32 - `search_sections` `path_glob` now pre-filters candidates.**
The glob was a tool-layer post-filter applied AFTER the index-layer top-k
cut (only `role` triggered candidate over-fetch), so a glob naming a single
document returned 0 results with confidence 0.0 whenever that document
didn't rank in the corpus-wide top k - near-certain on large corpora.
`DocStore.search` gains a `path_glob` parameter applied as a candidate
pre-filter in all three modes (lexical, semantic, hybrid) alongside the
existing `doc_path` equality check, via a shared `_path_excluded` helper;
the tool-layer post-filter is removed. Ranking now happens within the
glob-matched set, as documented.

**#33 - `verify_index` accounts for unverifiable sections.**
Sections persisted with an empty byte range (`byte_end <= byte_start`) were
skipped with a bare `continue`, so the failure counters didn't sum to
`section_count` and hundreds of unverifiable sections (e.g. every section
from the structured OpenAPI parser) read as a fully clean index. New
`skipped_count` and `skipped_sections` (reason `"empty_byte_range"`) close
the arithmetic; the invariant `clean + drift + missing + error + skipped ==
section_count` is now tested. The docstring's stale promise to route these
into `missing_sections` is corrected: unverifiable-by-design is a distinct
signal from corruption.

**#34 - `index_local` no longer blocks the MCP event loop.**
The full index + embed pipeline ran synchronously inside the async
`call_tool` handler, monopolizing the server's single asyncio loop past
client tool timeouts; once the client timed out, every subsequent call also
timed out while the server kept working. `index_local` now dispatches via
`asyncio.to_thread`, so cheap tools (`doc_list_repos`, `search_sections`)
stay responsive during long indexing runs. The v1.69.2 cross-process
index-write lock already serializes concurrent same-repo writes. The
larger suggestions from #34 (background job + progress polling, vectorized
cosine scoring) are acknowledged and deliberately deferred.

Additive per the 1.x contract: new defaulted kwarg on `DocStore.search`,
new response keys on `verify_index`, no tool or wire-shape removals.
Regression tests in `tests/test_v1_70_2.py`.

## [1.70.1] - 2026-06-12 - `paths` subset refresh no longer prunes the rest of the index (#31)

Patch release. Fixes a data-loss bug reported by @mmashwani in #31:
`index_local(paths=[...])` (and CLI `index-local --paths-from FILE`) on an
existing incremental index treated every indexed file NOT in the list as
deleted. A refresh of 1-3 changed files collapsed a whole corpus — e.g.
176 files / ~2957 sections reduced to the listed file's 11 sections —
directly contradicting the documented intent of `paths` ("batch-indexing
exactly the files an agent already knows about").

Root cause: `paths` only narrowed which files were read into
`current_files`; `DocStore.detect_changes` then computed deletions as
`old_set - new_set` against the full existing index, so every unlisted
file was pruned by `incremental_save`.

Fix, in `tools/index_local.py`: when `paths` is provided on the
incremental path, the deletion diff is scoped to the requested subset.
Listed files are added/updated; a listed file that no longer exists on
disk is removed; files under a listed directory are diffed against that
subtree; indexed files outside the listed subset are never touched.
Listing the corpus root (`.`) keeps the full-corpus diff, and the
walk-based (no-`paths`) path is unchanged. A subset refresh can now also
process pure deletions (every listed file gone from disk) instead of
failing with "No documentation files found"; that error still applies
when there is no existing index to update.

Additive per the 1.x contract: no tool/response shape changes; the only
behavioral change removes an undocumented destructive side effect.
Regression tests in `tests/test_v1_70_1.py`.

## [1.70.0] - 2026-06-11 - recency window on weight tuning

`tune_weights` now learns from a recency window of the ranking ledger
instead of the lifetime history. New `max_age_days` parameter (default 90;
`0` restores the lifetime read) on the MCP tool, `tune_one_repo`, and
`tune_all_repos`; `ranking_db_query` gains a `window_seconds` filter to
support it.

Previously every ranking event ever recorded for a repo fed the
`semantic_weight` proposal, so as a doc corpus and its query patterns
drifted, stale events kept anchoring the learned weight to a distribution
that no longer exists. Recent research on memory systems documents exactly
this failure mode: accumulated context that can't distinguish current from
stale signal degrades retrieval quality over time. Mirrors jcodemunch-mcp
v1.108.53.

Additive per the 1.x contract: new defaulted kwargs and new response keys
(`max_age_days` on tuner results) only; existing call shapes keep working.

## [1.69.2] - 2026-06-10 - serialize concurrent same-repo index writes (PR #28)

Patch release. Fixes a data race when two processes write the same repo's
index at once (e.g. a scheduled reindex and a per-edit hook). Originally
contributed by @Chrisr6records; the cross-platform lock and the Windows
replace-retry were added here to carry it across the finish line.

jdocmunch rewrites the whole `<name>.json` on every save. The two writers
both wrote a shared deterministic `<name>.json.tmp` and then `os.replace`-d
it into place with no lock, so concurrent writers could install corrupt or
partial JSON (the repo then reads as both "corrupt" and "absent") or silently
lose an update (last-replace-wins on the read-modify-write in
`incremental_save`).

Fix, in `storage/doc_store.py`:
- **Per-PID temp name** (`<name>.json.<pid>.tmp`) so concurrent writers never
  share, and clobber, one temp file.
- **Cross-process write lock** around `save_index` / `incremental_save` (the
  whole read-modify-write), backed by `flock` on POSIX and `msvcrt.locking`
  on Windows, on a per-repo `<name>.json.lock`. This is what closes the
  lost-update window, on both platforms.
- **Bounded replace-retry** (`_atomic_replace`): on Windows a concurrent
  reader holding the destination open makes `os.replace` raise
  `PermissionError` (WinError 5/32) transiently; a brief backoff rides it out,
  then re-raises the original error if it never clears. POSIX `rename` is
  atomic and never hits this.
- `delete_index` cleans up the per-repo `.lock` file.

The PR's original lock was POSIX-only (`fcntl`), which no-op'd on Windows and
left both the lost-update race and the `os.replace` `WinError 5` unfixed
there; both are now covered. Fully additive: no `INDEX_VERSION` change, no
tool/response change, and the default failure mode is unchanged (the retry
never introduces a new raise -- 1.x contract). New regression tests in
`tests/test_concurrent_index_writes.py` reproduce both races across real
processes and pass on Windows and POSIX.

## [1.69.1] - 2026-06-10 - redirect git subprocess stdin to DEVNULL (PR #30)

Patch release. Fixes a Windows-only deadlock that wedged the MCP stdio
server permanently on any `index_local` call. Contributed by @Derjyn.

`_git` and `_git_bytes` spawned git with `stdout=PIPE, stderr=DEVNULL`
but left `stdin` un-redirected. Under the MCP stdio transport the git
child inherited the server's stdin, which is the JSON-RPC pipe from the
client; Git for Windows blocked on that inherited handle and never
exited. The `JDOCMUNCH_GIT_TIMEOUT` guard couldn't recover: the timeout
killed the direct child, but the post-kill `communicate()` drain then
blocked forever joining the reader thread because the `cmd\git.exe`
wrapper chain still held the inherited pipe handles. The event loop
wedged inside the synchronous tool call.

The CLI `index-local` path never hung because its stdin is a console
handle, not a pipe, which made the bug look transport-specific. Both git
and non-git target folders hung (a non-git folder still calls
`local_git_head` -> `git rev-parse --is-inside-work-tree`).

Fix: pass `stdin=subprocess.DEVNULL` in both helpers. Pure no-op for
behavior; none of the spawned git commands (`rev-parse`,
`status --porcelain`, `ls-files`) read stdin. With the patch the same
stdio harness calls complete in 0.1-0.6s.

## [1.66.3] - 2026-05-16 - openai-compatible: probe actual dim at init (jdoc#20)

Patch release. Hardens the openai-compatible provider added in v1.66.0.

## The silent-corruption window

`_OpenAICompatibleProvider` returned `(f"{url}::{model}", None)` from
`_provider_identity()` because the embedding dim was unknown without
calling the endpoint. The on-disk cache (`embeddings/cache.py`) handles
`dim=None` by relaxing the strict dim check to a wildcard.

That composes correctly when the backing model stays put. But if a user
keeps the URL/model env vars constant and swaps the backing model behind
the endpoint -- a realistic Ollama scenario, retagging
`nomic-embed-text` to point at `all-minilm` -- the cache identity still
matches, and old 768-dim vectors get mixed with fresh 384-dim vectors.
Downstream cosine math either crashes on shape mismatch or silently
returns garbage similarity scores.

## The fix

`_OpenAICompatibleProvider.__init__` now embeds a one-token canary at
construction time to discover the endpoint's actual embedding dim, and
stores it on `self.dim`. `_provider_identity("openai-compatible")` reads
that dim out of the cached provider singleton. The cache layer's strict
dim check now engages and a silent backing-model swap forces a clean
re-embed.

Probe failure is non-fatal: `self.dim` stays `None`, the cache layer
falls back to its wildcard-dim behavior (v1.66.0 semantics). Network
outage, misbehaved endpoint, or any other probe error degrades
gracefully.

Cost: one extra round-trip on provider init (per process), once per
session. Cheap.

## Tests

3 new regression tests in `tests/test_openai_compatible_embeddings.py`:
probe-discovers-actual-dim, probe-failure-sets-dim-none, and
identity-uses-probed-dim-when-instance-cached. The four existing tests
that assert on the fake client's `.calls` list were updated to account
for the probe as the first recorded call (skipping `calls[0]` or
asserting the probe explicitly).

Full suite: 1254 passing.

## Cross-suite

When jcm and jdata pick up the openai-compatible provider (jcm#302,
jdata#2), the same probe-at-init pattern should ship in those ports.

## [1.66.2] - 2026-05-16 - warm sentence-transformers before stdio (jdoc#19)

Patch release. Reported by @rknighton on jdoc#19.

The first semantic `search_sections` call hung when sentence-transformers
was the configured provider. The model lazy-loads on first `embed_query`,
and that load (a) can exceed the MCP client's tool-call timeout and
(b) writes progress/download chatter to stdout, corrupting MCP JSON-RPC
framing. Same call worked from a direct Python entry point because
nothing was contending for stdout.

Fix: warm the active embedding provider in `run_server()` before
entering `stdio_server()`. `provider.warmup()` is gated on provider
type -- only sentence-transformers gets warmed (it's the only one
with significant cold-start latency and stdout-leak risk); network
providers (gemini, openai, openai-compatible) are skipped to avoid
an avoidable startup round-trip. The warmup runs inside a
`contextlib.redirect_stdout(sys.stderr)` so any noisy library writes
land somewhere safe.

Warmup failure is non-fatal: server still starts, the first real
embed call retries cleanly.

Regression coverage in `tests/test_hybrid_search.py` (4 new tests):
network providers skip warmup, unconfigured provider skips warmup,
sentence-transformers gets warmed, exception during warmup is
swallowed. Full suite: 1251 passing.

## [1.66.1] - 2026-05-16 - `should_embed("false")` now parses as False (jdoc#18)

Patch release. Reported by @rknighton on jdoc#18.

`should_embed(flag)` resolved any non-empty string via `bool(flag)`, so
`use_embeddings="false"` evaluated to `True` and silently turned
embeddings on. MCP tool inputs that arrive over the wire as JSON strings
(`"false"`, `"0"`, `"no"`) all hit this path.

Fix: recognise common string booleans (case-insensitive, whitespace-
trimmed) before the `bool()` fallback. Recognised truthy: `"true"`,
`"1"`, `"yes"`, `"on"`, `"t"`, `"y"`. Recognised falsy: `"false"`,
`"0"`, `"no"`, `"off"`, `"f"`, `"n"`, `""`. `"auto"` behavior preserved.

Unknown strings still fall through to `bool(flag)` to preserve 1.x
compatibility: a typo like `"flase"` remains truthy as it did before,
rather than silently disabling embeddings. The contract is "we
recognise the obvious cases; we don't change behavior for inputs we
don't recognise."

Regression coverage in `tests/test_hybrid_search.py` (3 new tests, 19
total in that file). Full suite: 1247 passing.

## [1.66.0] - 2026-05-16 - openai-compatible embeddings (PR #17)

Adds opt-in `openai-compatible` embedding provider for any
OpenAI-API-shaped endpoint (Ollama, vLLM, LiteLLM, llama.cpp,
LM Studio, etc.). Contributed by @DevItBetter via PR #17.

Four new env vars, all opt-in, no default-behavior change:

- `JDOCMUNCH_EMBEDDING_PROVIDER=openai-compatible` (required to activate)
- `JDOCMUNCH_OPENAI_COMPAT_URL` (required when active)
- `JDOCMUNCH_OPENAI_COMPAT_MODEL` (required when active)
- `JDOCMUNCH_OPENAI_COMPAT_API_KEY` (optional, defaults to literal
  `"local"`; never falls back to `OPENAI_API_KEY`)
- `JDOCMUNCH_OPENAI_COMPAT_BATCH_SIZE` (optional, default 32)

Design highlights worth preserving:

1. **Explicit-only activation.** Never auto-detected. Setting only the
   URL/model without the provider env var returns `None` from
   `get_provider_name()`.
2. **Credential isolation.** Default API key is the literal `"local"`,
   never falls through to `OPENAI_API_KEY`. Closes the bug class where
   a real OpenAI key could leak to a localhost endpoint.
3. **Cache signature** includes URL, model, first-8 of compat key, and
   batch size. Ambient `OPENAI_API_KEY` is excluded.
4. **Provider identity** returns `(f"{url}::{model}", None)`; cache
   layer relaxes the dim check when dim is unknown.

Test coverage in `tests/test_openai_compatible_embeddings.py` (16 tests,
304 lines): provider selection, missing-config handling,
non-auto-detection, credential isolation, batch-size defaults +
overrides + invalid fallback, signature variance, identity, and both
query-cache and section-cache invalidation on model change.

Follow-ups filed: jdoc#20 (pin actual dim via canary at provider init
to close a silent backing-model-swap corruption window),
jcm#302 + jdata#2 (sibling-parity ports).

## [1.65.0] - 2026-05-14 - prefer-newest walk order on truncation (jdoc#16)

Follow-up to jdoc#15 (@LuigiNicaPRO). When the corpus exceeds `max_files`
and truncation kicks in, the previous walker took the first `max_files`
in filesystem-walk order -- non-deterministic from the user's
perspective. A file edited 4 minutes before the index call could be
silently dropped while older files made the cut. Reported by
@LuigiNicaPRO as suggestion #4 on jdoc#15; deferred to its own ship.

New `sort_by` kwarg on `index_local` and `discover_doc_files`:

- `sort_by="newest"` **(new default):** when `discovered > max_files`,
  sorts by mtime descending so the indexed subset is always the N
  most recently-edited files. Recent edits are always in the index
  regardless of filesystem-walk position.
- `sort_by="walk_order"`: pre-1.65 behavior. Useful for deterministic
  reproducible builds where mtimes shift but content doesn't.

The sort only runs on the truncation path (`discovered > max_files`),
so corpora under the cap pay zero cost. mtime is captured in the same
`stat()` call that already does the size check, so no extra syscalls
either.

Regression coverage in `tests/test_index_local_sort_by.py` (6 tests).

## [1.64.2] - 2026-05-14 - silent truncation footgun in `index_local` (jdoc#15)

Reported by @LuigiNicaPRO: `index_local()` on a 5,705-file Obsidian Vault
returned `success: true` and `file_count: 498` with no programmatic
signal that ~90% of the corpus had been silently dropped. Default
`max_files=500` was buried in the schema; the cap-hit hint was a
free-text `note` string in the response.

Four fixes:

1. **Default `max_files` raised from 500 to 10,000.** Modern doc repos
   and Obsidian Vaults routinely exceed 500.
2. **Walker counts past the cap** (up to a 20x safety ceiling) so
   `discovered` reflects the true corpus size, not the cap. Returns a
   new tuple shape `(files, warnings, discovered_count)`.
3. **Structured top-level truncation fields**: when the cap is hit, the
   response now includes `truncated: true`, `discovered: <total>`,
   `indexed: <max_files>`. Programmatic detection is trivial:
   `if result.get("truncated"):`. When the corpus fits, `truncated:
   false` is set explicitly.
4. **Structured warning entry** in the existing `warnings` array
   alongside the legacy `note` string (kept for back-compat).

Both the full-index and incremental code paths surface the new fields.

Walker order is still filesystem order -- prefer-newest is a useful
future enhancement (@LuigiNicaPRO's suggestion #4) but lands cleanest
in a separate ship since it changes which subset gets indexed, not
just how truncation is reported.

Regression coverage in `tests/test_index_local_truncation.py` (6 tests).

## [1.64.1] - 2026-05-14 - O(N^2) hang in `related_persist.build()` (jdoc#14)

Reported by @LuigiNicaPRO with a py-spy backtrace and a working local
patch in hand: `index_local` on a 10-20k-section repo hung at 100% CPU
on a single thread. The docstring claimed `build()` was O(N) on
structural edges; it was actually O(N^2) on two stacked patterns:

1. `section_dicts` was rebuilt inside the per-section loop on every
   iteration -- O(N) work x N iterations = O(N^2) before any neighbor
   computation began.
2. `structural_neighbors()` rebuilt its by-id map and called
   `_children_of(parent_id, sections)` up to 4 times per section, each
   a linear scan -- another O(N) per outer iteration.

Fix: precompute `section_dicts`, the by-id map, and a new
parent->children map once before the loop and thread them into the
per-section calls via two new optional kwargs on `structural_neighbors`
and `semantic_neighbors`. External callers ignore the new kwargs and
keep the original behavior bit-for-bit -- the cache parameters are
prefixed `_` to mark them as internal hot-path use only.

Bench (Windows / Python 3.14): the fixed path indexes 10k sections in
~0.6s. The pre-fix path on the same input ran for minutes before being
killed.

Regression coverage in `tests/test_related_persist_perf.py`: asserts
the build scales linearly between 2k and 4k sections (ratio <3.5x) and
that 15k completes in <5s.

## [1.64.0] - 2026-05-14 - `tool_profile` + `disabled_tools` config (#297)

Reported by @AlexJ-StL in #297: Google Antigravity caps MCP-server tool
counts at 50, but jdocmunch shipped 60 tools with no way to trim them
short of disabling the whole server. Sibling-parity gap with jcm, which
has had `tool_profile` and `disabled_tools` since v1.78.

Two new env-var-driven knobs in `server.py`:

- `JDOCMUNCH_TOOL_PROFILE=core|standard|full` (default `full`).
  - `core` (13 tools): index + the navigation/search essentials.
  - `standard` (~50 tools): core + analysis/cross-reference tools.
  - `full` (60 tools): everything, current behavior.
- `JDOCMUNCH_DISABLED_TOOLS=tool1,tool2,...` removes named tools from
  both the listed schema and the call dispatcher. Composes with
  `tool_profile`.

Filtering is enforced in `list_tools()` (schema visibility) AND
`call_tool()` (call-time rejection) so a client that cached the schema
gets a clear error if it invokes a disabled tool. `jdocmunch_guide`
survives tier filtering (so a one-line CLAUDE.md keeps working at any
tier) but honors `disabled_tools` (it's documentation, not a control
surface) -- mirrors jcm v1.108.8's issue-#298 resolution.

Antigravity users with the full munch suite can now run:

```jsonc
// per-server env vars
"jdocmunch": { "env": { "JDOCMUNCH_TOOL_PROFILE": "core" } }
```

to fit under the 50-tool cap.

## [1.63.3] - 2026-05-13 - `jdocmunch_guide` sibling-parity tool

Adds `jdocmunch_guide` -- the doc-MCP sibling of `jcodemunch_guide` (jcm
since v1.84.0). Returns the version-current CLAUDE.md / AGENT.md policy
snippet for jdocmunch-mcp so an agent can keep a one-line CLAUDE.md
(`"Call jdocmunch_guide and strictly follow its instructions."`) instead
of pasting a static block that drifts from the installed version.

Backstory: GitHub issue #296 (Codex Desktop compatibility report by
@rknighton) noted that jcodemunch-mcp ships a guide tool but jdocmunch-mcp
doesn't, leaving agents told to call `<pkg>_guide first` without an
onboarding entry point for the doc surface. Sibling parity closes the
gap. Companion v1.12.2 release of jdatamunch-mcp ships `jdatamunch_guide`
on the same shape.

Tool count 59 -> 60. No tool, schema, or wire-format change for existing
tools. 1205 tests pass (1199 + 6 new in `test_v1_63_3.py`).

## [1.63.2] - 2026-05-12 - drift-proof __version__ via importlib.metadata

`src/jdocmunch_mcp/__init__.py` now derives `__version__` from
`importlib.metadata.version("jdocmunch-mcp")` instead of a hardcoded
literal. Reads the wheel's metadata at import time, so pyproject.toml
and the runtime version string can no longer disagree by construction.

Backstory: v1.63.0 shipped with the hardcoded `__version__` stuck at
1.60.0 (three minors stale) because nothing cross-checked it against
pyproject. v1.63.1 added a `tests/test_version_sync.py` regex guard,
but jcodemunch-mcp already had a better pattern. This release ports
that pattern over and retires the test (no longer reachable code).

When run from a source checkout without pip install, `__version__`
resolves to `"unknown"`. The replay-runner's `_resolve_version()`
already falls back to parsing `pyproject.toml` in that case, so
baseline-result filenames stay correct on source builds.

No tool, schema, or wire-format changes.

## [1.63.1] - 2026-05-12 - CI green: fixture query rename + full-history checkout

Patch release that turns master green again. Two independent CI fixes,
no behavior change for installed users.

1. Replay fixture: the `wiki stats` query in `self_v1_11_0.json` collided
   with the `### Stats` H3 subheadings that v1.62.0 and v1.63.0 hand-added
   to CHANGELOG.md. BM25 ranked those short, dense sections above the
   target wiki-benchmark page, dropping MRR from 1.0 to 0.925 (over the
   0.06 gate). Renamed to `wiki benchmark`. Expected target returns to
   rank 1 with a clean margin and the slug `jdocmunch-mcp-wiki-benchmark`
   is the unambiguous lexical anchor for it.
2. Workflow checkout: both `test.yml` and `replay.yml` now set
   `fetch-depth: 0` on `actions/checkout@v5`. The shallow default broke
   `tests/test_v1_35_0.py::TestChangelogGenerator::test_runs_against_real_repo`
   on any push whose HEAD wasn't itself a `release:` commit, because
   `scripts/generate_changelog.py` walks `git log` for release subjects
   and a depth-1 clone had none to match.

No tool, schema, or wire-format changes. v1.63.1 baseline result captured
at `benchmarks/replay/results/self_v1_11_0-v1.63.1.json` (1.0 / 1.0 / 1.0).

## [1.63.0] — 2026-05-12 — `get_doc_pr_risk_profile` (Phase-2 sibling-parity COMPLETE)

Composite doc-PR risk profile. Fuses five orthogonal signals over a
caller-supplied list of changed sections into a 0-1 `risk_score` with
overall `risk_level` (low / medium / high / critical), a ranked top-5
list of blockers, and a one-line `recommended_action`. Mirrors jcm's
`get_pr_risk_profile`.

### Signals

| Signal                | Source                                              |
|-----------------------|-----------------------------------------------------|
| `volume`              | changed sections / total sections (×10 cap)         |
| `blast_radius`        | mean blast_score for modified + deleted sections    |
| `backlink_burden`     | avg inbound references per changed section / 5     |
| `tutorial_disruption` | % of changes on tutorial chains                     |
| `role_weight`         | % of changes hitting tutorial/reference/guide roles |

Weights: `volume 0.15 + blast 0.30 + backlinks 0.20 + tutorial 0.20 + role 0.15`.
Thresholds: `≤0.25 low / ≤0.50 medium / ≤0.75 high / >0.75 critical`.

### Input shape

Caller passes `changed_sections` as either bare section IDs (str,
defaults to `kind=modified`) or `{section_id, kind}` dicts where
`kind ∈ {added, modified, deleted}`. Added sections skip backlink
lookup since they cannot have inbound refs yet.

The tool does **not** diff anything itself — pair with `get_recent_changes`
or compute the list from `git diff` in your CI step.

### Stats

- Tool count: 59 (+ `get_doc_pr_risk_profile`)
- Tests: 1196 passed (+12 new — 5 pure-function + 7 integration)

This completes Phase 2 of the sibling-parity PRD across all three munches.

---

## [1.62.0] — 2026-05-12 — `doc_health_radar` + `diff_doc_health_radar`

Six-axis health radar for documentation indexes, plus a pure-function
diff helper. Third leg of the suite-wide radar pattern (jcm's
`health_radar.py` + jData's `data_health_radar`).

### Axes

Each axis scores 0-100 (higher = healthier):

| Axis                | Source                                              |
|---------------------|-----------------------------------------------------|
| `freshness`           | fresh / (fresh + edited + stale) × 100            |
| `link_integrity`      | linear penalty per broken link (relative to sections) |
| `orphan_health`       | linear penalty per orphan section                 |
| `embedding_coverage`  | embedded sections / total sections × 100          |
| `role_coverage`       | sections with non-unknown role / total × 100      |
| `drift_health`        | canary clean → 100; alarm → 0; no canary → omitted|

`freshness` is omitted when section_count is zero. `drift_health` is
omitted when no embedding-drift canary has been captured. Omitted axes
appear in `omitted_axes` and never silently penalise the composite —
radars stay comparable across repos with different setup states.

### `diff_doc_health_radar`

Pure function: takes two radar payloads, returns per-axis deltas,
composite delta, grade change, regression + improvement lists at a
3-point threshold, and a one-line verdict. No I/O.

### Stats

- Tool count: 58 (+ `doc_health_radar`, `diff_doc_health_radar`)
- Tests: 1184 passed (+12 new; 12 pre-existing baseline-gate failures unaffected)

---

## [1.61.0] — 2026-05-12

### New: explicit-paths indexing

`index_local` gains a `paths=[...]` parameter that bypasses the directory
walk and indexes only the listed files / subdirs. Each entry can be
absolute or relative to the `path` root. Useful for batch-indexing
exactly the doc files an agent already knows about — e.g. *the docs git
just touched*, *the pages in this PR's diff*, *the markdown matched by
fd / rg* — without the cost (or surprise) of a full-tree walk.

Security: explicit paths are validated the same way as walk-discovered
files — entries outside the root, path-traversal attempts, and symlink
escapes are rejected with per-entry `warnings`. Unsupported extensions
are warned-and-skipped rather than silently passed.

CLI: new `--paths-from FILE` flag on `jdocmunch-mcp index-local`. Use
`-` for stdin to make the command pipe-friendly with `find`, `fd`,
`fzf`, and `rg`:

```bash
git diff --name-only HEAD~5 -- '*.md' \
  | jdocmunch-mcp index-local --path docs/ --paths-from -
```

Empty input is treated as an error so the command doesn't silently fall
through to a full-tree index. Lines beginning with `#` are skipped.

### Notes
- Fully additive — `paths` defaults to `None`, preserving every existing
  call shape. The MCP `index_local` tool's `inputSchema` gains an
  optional `paths: list[string]` field with the same semantics.
- 10 new tests in `test_v1_61_0.py`. 1174 passed.

## [1.60.0] — 2026-05-11

### New: `find_similar_sections` — multi-signal dedup detection

Every wiki of size accumulates "three pages that all say the same
thing." This tool surfaces them. Multi-signal scoring fuses embedding
cosine (when the index has embeddings) with title + body lexical
Jaccard, gated by a cheap title-token pre-filter to keep cost bounded
on large wikis.

Output is cluster-shaped: one entry per group of overlapping sections,
each with a `canonical` (recommended keeper, ranked by backlink_count +
byte_length) and `variants` to fold in. Verdict tiers per cluster:

- `near_duplicate` — combined score ≥ `near_duplicate_threshold` (0.92)
- `overlapping_topic` — combined score ∈ `[min_score, threshold)`
- `parallel_tutorial` — cluster members live in different doc
  directories (suggests parallel guides that should cross-reference
  rather than be merged)

Defaults: `min_score=0.7`, `max_clusters=50`, `max_sections=1000`.
Parser-artifact filter drops zero-byte-range wrapper sections so they
don't cluster with their own heading-level twins.

Read-only. Inspired by `find_similar_symbols` in jcodemunch-mcp (see
`C:/MCPs/PRD_sibling_parity_v1.md` §6.2). **Completes the jDoc Phase-1
batch from the sibling-parity PRD** (joins `check_section_delete_safe`
+ `get_section_blast_radius`).

### New: `get_section_blast_radius` — transitive impact of a section change

Companion to `get_backlinks` (which is depth 1 only). Walks the inbound
reference graph to `max_depth` (default 3) and classifies each hit as
`anchor` (link targets this section's slug), `doc` (link targets the
enclosing doc), or `tutorial` (section appears in a Next/Prev / toctree
chain).

Returns `direct_impact` (depth 1), `transitive_impact` (depth ≥ 2), a
`summary` of counts, and a normalised `blast_score` in [0, 1] so blast
radius is comparable across sections of different size.

Read-only. Inspired by `get_blast_radius` in jcodemunch-mcp (see
`C:/MCPs/PRD_sibling_parity_v1.md` §6.3).

### New: `check_section_delete_safe` — composite deletion preflight

First Phase-1 deliverable from the sibling-parity PRD. Answers the
question every wiki maintainer asks every week: *can I safely remove
this section?*

Fuses four channels into a single verdict plus up to five ranked
blockers and a one-line `recommended_action`:

1. **Tutorial-path membership** — section is part of a Next/Prev chain,
   Sphinx toctree, VuePress sidebar, or ordered-filename sequence. High
   severity — deleting breaks readers walking the chain.
2. **Anchor-specific backlinks** — other sections link to `doc#slug`.
   High severity — those anchored links 404 once the section is gone.
3. **Transitive doc-level backlinks** — BFS over inbound refs to
   `transitive_depth` (default 3). Medium severity above a threshold of
   3 referers.
4. **Recent-edit recency** — section's source touched within
   `recent_edit_days` (default 14), or sits in FreshnessProbe's
   `edited_uncommitted` bucket. Low severity — defer deletion.

Verdict tiers (highest first): `tutorial_path_blocking`,
`anchor_referenced`, `backlinks_blocking`, `recently_edited_blocking`,
`safe_to_delete`.

Read-only. Composes existing primitives (`get_tutorial_path`,
`get_backlinks`, `FreshnessProbe`) — no new persisted state, no
INDEX_VERSION bump.

Inspired by `check_delete_safe` in jcodemunch-mcp (see
`C:/MCPs/PRD_sibling_parity_v1.md` §6.1).

## [1.9.0] — 2026-04-19

### New: Hybrid BM25 + semantic search

- **`search_sections` now fuses lexical and semantic scores** when the index has embeddings. New parameters match jcodemunch-mcp's shape:
  - `semantic` — `null`/omit (auto — hybrid when embeddings exist), `true` (force hybrid), `false` (force lexical-only)
  - `semantic_only` — skip lexical entirely, rank purely by embedding cosine
  - `semantic_weight` — 0.0–1.0 weight of the semantic channel in fusion (default 0.5)
- Each channel min-max-normalized to [0,1] within the candidate set, then weighted sum. When `embed_query` returns `None` (provider disabled at query time), hybrid gracefully degrades to lexical. Zero performance impact when the index has no embeddings.
- `_meta.search_mode` now reports one of `hybrid`, `semantic_only`, or `lexical` (replacing the previous binary `semantic`/`lexical`). `_meta.semantic_weight` is surfaced on hybrid calls.

### New: `use_embeddings="auto"` default

- `index_local` and `doc_index_repo` now default `use_embeddings` to `"auto"` — embeddings are generated automatically whenever an embedding provider is configured (`GOOGLE_API_KEY`, `OPENAI_API_KEY`, or sentence-transformers installed). Explicit `true`/`false` still honored.
- `index-file` now preserves embedding parity: when re-indexing a single file into an index that already has embeddings, the new sections get embedded too (previously left empty).

### Tests

- 16 new tests covering `should_embed` flag resolution, hybrid fusion ranking, `semantic=False` short-circuit, semantic-only, `semantic_weight=0` reduction to lexical, graceful degradation, and search_mode reporting (400 total).

## [1.8.1] — 2026-04-15

### Documentation
- **Hermes Agent integration** — added "Works with" section to README with Hermes Agent config example; submitted optional skill PR to [NousResearch/hermes-agent#10413](https://github.com/NousResearch/hermes-agent/pull/10413)

## [1.7.1] — 2026-04-09

### New features

- **`meta_fields` support** — control which `_meta` fields appear in tool responses via `JDOCMUNCH_META_FIELDS` env var. Matches jcodemunch-mcp's `meta_fields` affordance. Values: unset/`[]` = strip `_meta` entirely (default, maximum token savings), `null`/`all`/`*` = include all fields, comma-separated list = include only those fields (e.g. `timing_ms,powered_by`).

### Tests

- 11 new tests for meta_fields config parsing and filtering (358 total)

## [1.7.0] — 2026-04-09

### New: Full `init` onboarding

- **`jdocmunch-mcp init`** — One-command setup matching jcodemunch-mcp's UX:
  - Detects installed MCP clients (Claude Code CLI, Claude Desktop, Cursor, Windsurf, Continue)
  - Patches each client's config JSON to add jdocmunch as an MCP server
  - Installs a Doc Exploration Policy into CLAUDE.md (global or project scope)
  - Installs Cursor rules (`.cursor/rules/jdocmunch.mdc`) and Windsurf rules (`.windsurfrules`)
  - Installs enforcement hooks (PreToolUse, PostToolUse, PreCompact)
  - Indexes the current working directory
  - Supports `--dry-run`, `--demo`, `--yes`, `--no-backup`, `--client`, `--claude-md`, `--hooks`, `--index`
  - Interactive prompts for scope selection when run in a terminal

### New: `claude-md` subcommand

- **`jdocmunch-mcp claude-md`** — Print the Doc Exploration Policy to stdout
- **`jdocmunch-mcp claude-md --install global|project`** — Append policy to CLAUDE.md (idempotent)

### New: `index-file` single-file re-index

- **`jdocmunch-mcp index-file <path>`** — Re-index a single doc file within an existing index without re-walking the entire folder. Finds the owning index automatically, re-parses, and updates in place via incremental_save.
- PostToolUse hook now spawns `index-file <path>` instead of `index-local --path <dir>` for faster, more targeted re-indexing after edits.

### Tests

- 20 new tests for client detection, config patching, CLAUDE.md injection, Cursor/Windsurf rules, claude-md command, index-file tool, CLI dispatch (347 total)

## [1.6.0] — 2026-04-09

### New: CLI hook system for Claude Code

- **`hook-pretooluse`** — PreToolUse hook that intercepts `Read` on large doc files (.md, .rst, .adoc, .txt, etc.) and suggests `search_sections` + `get_section` instead. Warns via stderr; allows the read to proceed (Edit workflow requires Read first).
- **`hook-posttooluse`** — PostToolUse hook that auto-reindexes after `Edit`/`Write` on doc files. Spawns `jdocmunch-mcp index-local` as a fire-and-forget background process.
- **`hook-precompact`** — PreCompact hook that generates a session snapshot (indexed repos, doc/section counts) before Claude Code context compaction, injected as `systemMessage`.
- **`index-local --path <dir>`** — CLI equivalent of the MCP `index_local` tool, callable from shell hooks without a live MCP session.
- **`init --hooks`** — One-command installer that merges all three enforcement hooks into `~/.claude/settings.json`. Additive (preserves existing hooks), creates `.bak` backup by default. Supports `--dry-run`.

### Fixed

- Version mismatch between `__init__.py` and `pyproject.toml` — both now track 1.6.0.

### Tests

- 29 new tests for hooks + init (327 total)

Closes [#8](https://github.com/jgravelle/jdocmunch-mcp/issues/8). Thanks @Will-Luck for the detailed feature request.

## [1.5.3] — 2026-04-07

### Changed
- Switch MCP tool responses from pretty-printed JSON to compact JSON — saves 30-40% tokens per response (jcodemunch-mcp#219)

## [1.5.2] — 2026-04-06

### Added
- **`contrib/build-deb.sh`** — Community-contributed Debian/Ubuntu packaging script for Proxmox and other Linux deployments. Includes venv isolation, systemd unit, and streamable HTTP wrapper. Contributed by @Tikilou. Closes #7.

## [1.5.0] — 2026-04-01

### New tools

- **`get_broken_links(repo)`** — scan all indexed doc sections for internal cross-references that no longer resolve. Checks markdown `[text](target)` links, RST `:ref:`/`:doc:` directives, and anchor-only links (`#heading`). External links (http/https/mailto) are skipped. Each broken entry reports `source_file`, `source_section`, `target`, and `reason` (`file_not_found` | `section_not_found` | `anchor_not_found`). Pure index scan — no re-reading source files.
- **`get_doc_coverage(repo, symbol_ids)`** — given a list of jcodemunch symbol IDs, reports which symbols are mentioned in section titles (documented) vs absent (undocumented). Bridges jcodemunch ↔ jdocmunch. `symbol_ids` capped at 200. Output: `{documented, undocumented, coverage_pct}`.

### Tests

- 26 new tests (298 total)

## [1.4.6] — 2026-03-31

### Housekeeping

- Added `LICENSE` file (dual-use: free for non-commercial, paid for commercial)

## [1.4.0] — 2026-03-13

### New features

- **`get_section_context` tool** — returns a target section's full content alongside its ancestor heading chain (root→parent) and immediate child summaries, all under a configurable `max_tokens` budget. Eliminates the need for whole-file reads when a section alone is too thin to answer a question.
- **sentence-transformers embedding backend** — fully offline embeddings via `sentence-transformers` (default model `all-MiniLM-L6-v2`, override with `JDOCMUNCH_ST_MODEL`). Auto-detected as fallback after Gemini/OpenAI. Nothing leaves the machine.
- **tiktoken-aware token counting** — `count_tokens()` in `storage/token_tracker.py` uses `tiktoken` when installed (cl100k_base), falling back to bytes/4 when not present. Opt-in: no new required dependency.
- **`incremental` parameter on `index_local` and `index_repo`** — callers can now pass `incremental: false` to force a full re-index without deleting the existing index first.

### Performance and correctness

- **In-memory index cache** — `load_index()` now caches parsed `DocIndex` objects keyed by path + `mtime_ns`. Zero `json.load()` calls on repeated tool calls against the same unchanged repo.
- **True incremental GitHub indexing** — `index_repo(incremental=True)` now fetches the HEAD commit SHA first and exits immediately (no tree or file fetches) when the SHA matches the stored value. HEAD SHA stored in the index.
- **Hierarchical section IDs** — slugs are now prefixed with the ancestor heading chain (e.g. `installation/prerequisites` instead of bare `prerequisites`). A new heading inserted in one branch no longer renumbers IDs in other branches. `INDEX_VERSION` bumped to `2` — existing indexes are automatically re-indexed on first access.

### Documentation

- SPEC, ARCHITECTURE, USER_GUIDE, and README audited and reconciled against code reality
- `verify` parameter correctly described as cache integrity verification, not live-source drift detection
- Section ID format updated to show hierarchical slug paths
- Embedding environment variables (`OPENAI_API_KEY`, `JDOCMUNCH_EMBEDDING_PROVIDER`, `JDOCMUNCH_ST_MODEL`) documented throughout

### Tests

- 8 new `get_section_context` tests (248 → 256 total)

---

## [1.1.0] — 2026-03-08

- OpenAPI 3.x / Swagger 2.x parser (`parser/openapi_parser.py`)
- `.yaml`, `.yml`, `.json` files content-sniffed: indexed when spec contains `openapi:` or `swagger:` key; skipped otherwise
- Operations grouped by tag → `## Tag` sections; each endpoint becomes a `### METHOD /path` subsection with parameters, request body, and responses rendered
- Schemas / Definitions section appended with property types and required markers
- `pyyaml>=6.0` already a hard dependency (no new deps)
- 25 new tests (176 → 201 total)

---

## [1.0.0] — 2026-03-07

First stable release. API is now frozen under semantic versioning — no breaking
changes without a major version bump.

### Stable feature set

**Document formats** (11 formats, 14 extensions):
- `.md`, `.markdown`, `.mdx` — Markdown (ATX + setext headings, MDX preprocessing)
- `.txt` — plain text paragraph splitting
- `.rst` — RST heading/adornment parser
- `.adoc`, `.asciidoc`, `.asc` — AsciiDoc `=` heading parser
- `.ipynb` — Jupyter notebook JSON → Markdown conversion
- `.html`, `.htm` — HTML → text conversion, chrome stripped
- `.yaml`, `.yml`, `.json` — OpenAPI 3.x / Swagger 2.x specs (content-sniffed)

**Indexing**
- Incremental indexing: hash-based change detection, only changed/new files re-parsed, atomic save
- Full indexing with gitignore-aware file discovery and security filtering

**Retrieval**
- O(1) section lookup via `__post_init__` id→section dict
- Byte-offset content retrieval with SHA-256 content hash verification
- Token savings tracking (raw file size vs. section response size)

**AI summaries**
- Claude Haiku (`ANTHROPIC_API_KEY`) or Gemini Flash (`GOOGLE_API_KEY`) for section summaries
- Graceful fallback to heading text when no AI key is set

**Security**
- Path traversal protection on all file I/O
- Secret file detection (`.env`, `.pem`, credentials, keys)
- Binary file filtering
- Max file size enforcement

**Test coverage**: 201 tests passing.

### Breaking changes from 0.x
None — the index schema and MCP tool interface are unchanged from 0.1.x.

---

## [0.1.5] — 2026-03-07

- OpenAPI/Swagger parser (`parser/openapi_parser.py`)
- `.yaml`, `.yml`, `.json` added to `ALL_EXTENSIONS` with content sniffing
- `pyyaml>=6.0` added as a hard dependency
- 25 new tests (176 → 201)

## [0.1.4] — 2026-03-07

- Incremental indexing for both `index_local` and `index_repo`
- `DocStore.detect_changes()` and `DocStore.incremental_save()`
- O(1) section lookup via `DocIndex.__post_init__`
- `time.time()` → `time.perf_counter()` across all tools
- 7 new incremental indexing tests (169 → 176)

## [0.1.3] — 2026-03-06

- HTML parser (`parser/html_parser.py`): `<h1>`–`<h6>` → Markdown headings, chrome stripped
- Double `load_index()` fix: `_index` parameter on `get_section_content`
- Token savings: `os.path.getsize()` replaces per-section content summing

## [0.1.2] — 2026-03-05

- Jupyter notebook parser (`parser/notebook_parser.py`)
- AsciiDoc parser (`parser/asciidoc_parser.py`)
- RST parser (`parser/rst_parser.py`)
- Plain text paragraph parser (`parser/text_parser.py`)

## [0.1.1] — 2026-03-04

- Markdown parser with ATX + setext heading support
- Section hierarchy wiring (`parser/hierarchy.py`)
- `DocStore` with atomic save, path traversal protection, secret file detection
- MCP tools: `index_local`, `index_repo`, `get_section`, `get_sections`, `get_toc`,
  `get_toc_tree`, `get_document_outline`, `search_sections`, `list_repos`, `delete_index`
