# jdocmunch-mcp

**Version:** 1.150.0 |
**Tests:** `PYTHONPATH=src python -m pytest tests/ -q`

⚠ **`python -m pytest`, not bare `pytest`**, matching the rest of
the jMunch suite. The `-m` form guarantees the interpreter that receives
`PYTHONPATH=src` is the one running the tests; a bare `pytest` shim can resolve
into a different environment, and without `PYTHONPATH` the INSTALLED package
shadows `src/`. ⚠⚠ **Neither form reproduces CI** — see "reproduce CI" under
Standing operational notes; this one is the edit loop, not the gate.

Maintainers may also have a `CLAUDE.local.md` here (gitignored), which loads
private working notes. Nothing in this file depends on it.

## Engineering lessons

Rules that outlived the releases that produced them. Each line names the
version it came from; `CHANGELOG.md` has that release's full entry.

**Releasing**

- ⚠ **One shared TOKEN with a fixture query is enough to fail the replay
  gate.** A CHANGELOG draft saying "directory links" demoted the 'broken
  links' golden (nDCG 0.926 against 0.95). Reworded; goldens and gate
  untouched. Run `tests/test_replay_metrics.py` after every CHANGELOG edit.
  (v1.145.2)
- ⚠ **A PR reading `BLOCKED` with every job green may have NO `license/cla`
  status.** A GET to
  `https://cla-assistant.io/check/jgravelle/jdocmunch-mcp?pullRequest=<n>`
  posted it within seconds on our own #162. For a contributor who hasn't
  signed, absent still means NOT SIGNED. (v1.145.2)
- ⚠⚠ **A docs-only commit after a build still stales the artifacts.**
  `ROADMAP.md` and `CHANGELOG.md` ship in the sdist, so 1.143.0 was rebuilt
  and CI re-read on the later SHA before upload. Release from the SHA the
  artifacts were built at, and build last. (v1.143.0)
- ⚠⚠ **An unbounded list field on 1.x cannot be capped later, so the cap
  and its `*_total` / `*_truncated` keys arrive WITH the field.** Measure
  the response in bytes on a real repo and read the sibling keys: #132's
  `changes` was 39,755 of 41,129 bytes while the same response already
  capped `files`. A head cut drops whatever sorts last (deletions there), so
  the counts are the authority, never `len(list)`. (v1.142.0)
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

- ⚠⚠ **A measurement needs a control saying the thing ran.** The first
  FastEmbed timing for a watch child (1.4 s) came from a child whose model
  file was missing: `embedding_provider_error` was set and nothing was
  embedded. The valid figure (1.7 s) reported `embedded_sections: 41`.
  (v1.150.0)
- ⚠ **A byte total over a cache is summed from the keys still cached,
  never kept as a running counter.** Tests call `_INDEX_CACHE.clear()`
  directly, and puts can race; a counter drifts under both. (v1.150.0)
- ⚠ **A watch batch runs in a child process by default**, where a test's
  stub of `index_local` can't reach. Tests about grouping or filtering set
  `JDOCMUNCH_WATCH_INPROCESS=1`. (v1.150.0)
- ⚠⚠ **The OCR prose bar was moved once, after seeing data**: from 8 pages
  to the 6 without type specimens (original Tesseract 0.9489, CER 0.069 /
  0.116). Never restate the moved bar as the original, and never add a
  per-page table flag without a new pre-registered test. The README's
  statement that a 20-page scan can exceed the 60 s budget with Tesseract
  is what makes that budget acceptable; keep it. Run
  `scripts/ocr_regression/ocr_regression.py` before releasing a change to
  `parser/ocr.py`. (v1.149.0)
- ⚠ **`mark_sections` must find the root note by the first `## Page`
  heading**, not by the first OCR span: a text-layer page 1 sits before the
  first span. The mixed-PDF test pins it. (v1.149.0)
- ⚠⚠ **An empty conversion is not an error, so nothing downstream sees
  it.** markitdown turns a PDF with no text layer into `''` with no error.
  Check a converter's OUTPUT, not just whether it raised. OCR confidence is
  not a guard on its own either. (v1.148.1)
- ⚠⚠ **`semantic_search` means "a provider is configured", not "vectors
  exist".** A bare `except` hid a missing `model.onnx`; `embedding_provider_error`
  is the key that says so. Profile without cProfile first: it inflated
  pure-Python code about 7x. (v1.148.0)
- ⚠⚠ **A check that guards a READ belongs after the cheapest test that
  rules the read out.** `discover_doc_files` ran lstats, `resolve()`s and
  `fnmatch`es before its extension gate, on the 84% of files it never
  opens (80.5 s -> 18.5 s). Moving a gate can shift skip attribution; check
  that none of the shifted reasons is actionable. (v1.147.1)
- ⚠⚠ **Sort before you cap a cross-document list** (#174). Its capped
  candidate list followed `os.walk` order, so only the Ubuntu jobs went red:
  Windows sorted by accident. ⚠ **Read two compared values through ONE
  producer** (#175): globbing dist-info while `__version__` came from
  `importlib.metadata` read a leftover dist-info as drift forever. (v1.147.0)
- ⚠⚠ **A test can pin a defect as the contract** (#172). `test_v1_73_0.py`
  asserted a custom-named index failed without `--name`, which kept the bug
  green for 73 minor versions. When a fix turns an old test red, read what
  it was protecting before you edit it. ⚠ `_hook_probe` keeps its own copy of
  the store-root rule so hooks import nothing heavy; a test pins it against
  `storage/paths.py`. (v1.146.0)
- ⚠⚠ **A path-check memo's LIFETIME is its security argument** (#169).
  The posting build's per-document verdict is per `load_index` call,
  thread-local and cleared in `finally`. Kept on `DocStore` or the cached
  index, a check made before a symlink appeared would authorise a read
  after it. #168's `RowSet.build` runs outside that sweep, so no memoized
  verdict feeds a persisted row. (v1.145.5)
- ⚠ **`index-file <path>` keeps the explicit-path bypass of the corpus
  rules on purpose** (#115's split). A person naming a file gets it; an
  edit event from the PostToolUse hook is not that, so only the hook
  passes `apply_corpus_rules=True`. (v1.145.4)
- ⚠⚠ **Read a combinatorial helper for its cost at n = 16, and then read
  your own replacement the same way.** #163's `path_glob` form made 2**n
  `fnmatch` calls per section; our first regex replacement backtracked
  worse (2.3 s at 8 globstars). `path_glob_matches` walks segment
  boundaries, and its test counts regex calls, not time. A `**/` glob is
  an ADDITIVE result change on 1.x: plain `fnmatch` decides alone when it
  matches. ⚠ jcodemunch-mcp's `file_pattern` likely has the same gap
  (unverified). (v1.145.3)
- ⚠⚠ **The watcher's directory census prunes by ONE rule, "not a directory
  link"** (#154). Discovery also skips dot-directories, SKIP_PATTERNS and
  ignored paths. Pruning more than the census loses updates; jcm's first
  draft would have blinded its watcher to `.github/`. Don't move it toward
  discovery's rules without a test that every discovered directory stays.
  (v1.145.2)
- ⚠⚠ **A non-recursive watch doesn't know about a new directory, and a file
  written before its watch exists raises NO event.** Three parts, each
  tested: a directory add/remove re-arms, directories new since the last
  ARMED set are read once after the first yield, and rediscovery compares
  the directory set. `armed_census` moves only at that first yield.
  (v1.145.2)
- ⚠⚠ **A cap applied AFTER the arithmetic does not bound the arithmetic.**
  #158 computed `1.0 * 2 ** (n - 1)` and then took `min()` with the ceiling.
  At n = 1025 the int can't convert to float, and the `OverflowError` came
  from inside the `except Exception` handler, so the watcher exited (#159).
  **Read a backoff for its 1,000th iteration, not its third.** The delay
  lives in `watch.py::_error_retry_delay`, callable at any streak length.
  (v1.145.1)
- ⚠⚠ **Every root shares ONE `awatch` call, so one root's failure is every
  root's** (#155, #160). A cycle that fails at once cancels `_monitor`
  before its first rediscovery, so a vanished root was retried forever. One
  discovery pass now follows each retry wait. (v1.145.1)
- ⚠⚠ **Measure what dominates BEFORE designing the clever fix.** #142
  proposed a BM25 delta. The profile put 25.6 s of 48.5 s in per-section
  `open`s, so reading each file once took the cost and the delta was SHELVED:
  it can't be exact without storing the uncapped `df` plus a total tie-break,
  which moves ranking. `df` is capped at 5,000 and on this repo the cut
  falls inside a 1,848-term tie at count 1, so a full and an incremental
  build of the same files could differ. **Settled by @outoftheblue9's #168
  (`f3f29a6`):** `bm25.cap_df` breaks the tie by term. Measured 2026-10-14,
  an incremental save's `bm25_stats` now equals a fresh full index. (v1.145.0)
- ⚠⚠ **A memo of what is on disk is trusted only on (size, `mtime_ns`)**
  (`embeddings/cache.py::_KEY_MEMO`, #140). A size-only check misses a
  same-size rewrite. ⚠ A cache on the long-lived `_content_loader` could
  serve old bytes after a save, so the #142 whole-file cache is scoped to the
  stats pass, where kept files cannot change. (v1.145.0)
- ⚠⚠ **An append equals a rewrite ONLY on a matching identity with no
  prune** (#141). The rewrite is how rotation (#109) and `prune` (#107)
  retract rows. (v1.145.0)
- ⚠⚠ **A path fix is a MIGRATION** (#146). Every root resolves through
  `storage/paths.py`, with a ratchet against a new `".doc-index"` literal.
  `cache.load` reads the old home-root copy so no corpus is re-embedded.
  ⚠⚠ **`cache.identity` must NOT fall back:** it would let #141's append
  path match the home header and append headerless rows to a new file.
  (v1.145.0)
- ⚠⚠ **Two producers of one key that render it differently: resolve it on
  the NEW producer.** `index_local` emits `mtime` as naive local time and has
  since 1.142.0, so that format is a promise. `index_repo` emits an
  offset-aware string, and both tool descriptions say so. A caller comparing
  the two without reading the offset is wrong by the local UTC offset.
  (v1.144.0)
- ⚠⚠ **Never write a bare `Z`.** `datetime.fromisoformat` gained `Z` in 3.11
  and this package supports 3.10, where it raises `ValueError`. No developer
  box newer than 3.10 can see it; only the oldest matrix job fails.
  `normalize_commit_date` converts to `+00:00`. (v1.144.0)
- ⚠⚠ **`_index_to_dict` is an explicit ALLOW-LIST, not `asdict()`.** A field
  added to the dataclass and to every save/load signature still round-trips
  as empty until it is named there, and nothing raises. Test a new field by
  reading the JSON on disk, not the dataclass. (v1.144.0)
- ⚠ **One index, one meaning for a field.** A repository has no per-file
  modification time, so `mtime` there is the commit date. `list_docs` reads
  the STORED time even though it stats every document, because a live time
  would mean filesystem-time on a local index and commit-time on a
  repository one. (v1.144.0)
- ⚠⚠ **A flag that pre-approves config edits must not also pre-approve a
  download.** `init --yes` does NOT opt in to the ~250 MB embeddings install;
  `--with-embeddings` does. `cli/embeddings_offer.py` is the ONE module that
  runs pip or starts a download, and a ratchet fails if another module under
  `src/` names pip. (v1.143.0)
- ⚠ **After a step fails, pass the next step the explicit OFF value, not
  `"auto"`.** With fastembed installed and its model download failed,
  `"auto"` would retry the download silently inside the index step.
  (v1.143.0)
- ⚠ **Commit a checkpoint before mutating a file you have uncommitted work
  in.** A mutation check reverted with `git checkout -- <file>` took
  uncommitted `init.py` edits with it. Or mutate a copy. (v1.143.0)
- ⚠⚠ **A two-channel verdict reported over one channel is a different defect
  from a wrong score**, and the tool's own diff output (`body_unique_a: []` on
  both sides) was the tell it could not read. ⚠ An "all pairs were title_only"
  cap does not close it: union-find merges transitively, so a cluster can pass
  an all-pairs test on a score from the pair that read nothing. Rest the
  verdict on the best pair that actually compared bodies. (v1.138.0)
- ⚠⚠ **A count computed and withheld at the moment the caller could act on it
  is the same defect as not computing it.** `truncated` answered a different
  question truthfully while being read as "did I get everything"; the fix is a
  NEW key (`coverage_complete`), never a changed meaning on 1.x, and it is keyed
  on ACTIONABLE skips only, since a signal that always fires hides the case it
  exists for. Disclose on ALL response paths, including the error one.
  (v1.138.0)
- ⚠⚠ **An asymmetry is what makes a limit indefensible, not its value.** The
  walk accepted a 25 MB `.pptx` and refused a 600 KB `.md`. Measure before
  raising (1.25 MB parsed in 1.03 s / 8.3 MB peak); resolve env overrides at
  CALL time, since a default argument binds at import. (v1.138.0)
- ⚠ **The obvious remedy can be the wrong axis.** A `benchmarks/` skip would
  drop 20 genuine docs to remove 37 data files; the split was by file KIND.
  `SKIP_PATTERNS` matches path substrings. (v1.138.0)
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
- ⚠⚠ **A CLI subcommand that an installed config file invokes is the same
  kind of 1.x promise as an MCP tool.** `hook-precompact` became a silent no-op
  rather than being deleted: every installed `settings.json` names it, so
  removing it turns every compaction into a hook error on every existing
  install. ⚠ The corollary is that existing installs do NOT get a newly added
  hook EVENT until `init` is re-run. (v1.141.0)
- ⚠⚠ **A ratchet catches the shape it NAMES and nothing adjacent.** The #129
  ratchet was scoped to `run_pretooluse`, so it walked straight past the same
  defect one handler over in `run_precompact` — which is why each fix gets its
  own ratchet instead of trusting the neighbouring one. (v1.141.0)
- ⚠ **A claim about the HOST cannot be tested from this suite.** Nothing here
  can prove Claude Code delivers `additionalContext` on SessionStart; that rests
  on jcm's measured hooks and Claude Code's docs, and the brief must say which.
  (v1.141.0)
- ⚠⚠ **An exit-0 hook has ONE model-facing channel per event, and it differs
  by event.** stderr goes to the debug log. Plain stdout reaches the model only
  on UserPromptSubmit / SessionStart-class events. Top-level `systemMessage`
  goes to the user. PreToolUse needs `hookSpecificOutput.additionalContext`.
  PreCompact has NO channel. **Output on the wrong channel is indistinguishable
  from a hook that fired and was ignored**, which is how a hint sat undelivered
  for 73 minor versions. (v1.140.0)
- ⚠ **Raising a candidate cap is the wrong lever when admission order is the
  defect.** In query order the cap must exceed the corpus's highest document
  frequency, which grows with the corpus. Admit rarest-first with a total
  tiebreak. (v1.140.0)
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

- ⚠⚠ **A test that builds its own input skips the producer that broke.**
  The #115 tests handed `_handle_changes` a bare index name that discovery
  never produces, so the watcher's stored corpus patterns loaded as nothing
  (#165) while the tests stayed green. Feed a consumer from its real
  producer. (v1.145.4)
- ⚠ **A generator that concatenates atoms can spell the token it was told
  to leave out.** A "no globstar" fuzz drew `*` then `*/` and made `**/` in
  90 of 4,000 globs. (v1.145.3)
- ⚠ **WSL Ubuntu on a Windows dev box runs Linux tests.** Sync a Linux-side venv
  (`UV_PROJECT_ENVIRONMENT=$HOME/.venvs/<name> uv sync --group dev --python
  3.12`) and set `WATCHFILES_FORCE_POLLING=false`, since watchfiles polls
  under WSL by default. Two git-pointer tests fail there because a Windows
  worktree's `.git` is a `C:/` path. (v1.145.2)
- ⚠ `tests/test_watch_permission_denied.py` SKIPS on Windows (mode 000 does
  not bind), so the #155 fix rests on Linux CI here. It writes its edit
  after a fixed 3 s sleep; if it flakes on a slow runner, move the write
  inside the poll loop. (v1.145.1)
- ⚠⚠ **A profile names FUNCTIONS, not passes. Count the reads yourself.**
  73,814 `_loader` calls read as one cost and were two full passes through
  the same loader. A spy on `Path.open` / `builtins.open` that records the
  caller settles it in one run. (v1.145.0)
- ⚠ **When a seam moves, the test stubs must follow it.** 16 patch sites
  stubbed `fetch_head_commit_sha` after it stopped issuing the request, so
  the real HTTP call was live and those tests passed for the wrong reason.
  Stub the name that does the I/O. (v1.144.0)
- ⚠ **Verify an install flow by a REAL run, not only recorder tests.**
  1.143.0's was a throwaway venv with isolated home, store and model cache.
  ⚠ Seen there and NOT fixed: stdin from `/dev/null` reads as a TTY on
  Windows, and fastembed's default model cache is `<tempdir>/fastembed_cache`,
  which an OS cleanup can empty. (v1.143.0)
- ⚠⚠ **Adding a second member to an auto-detect chain invalidates every test
  that pinned the first one.** Installing fastembed turned FIVE existing tests
  red; they stubbed one provider probe and read the developer's site-packages
  for the other. CI saw none of it — CI installs neither provider. ⚠ The AST
  ratchet is SCOPED to functions that actually reach the fallback: unscoped it
  flagged every import-probe test, and **a guard with false positives is one
  nobody believes.** (v1.137.1)
- ⚠ **A gate cannot express a defect its fixture is too small to reach.** The
  replay self-fixture's commonest term is in 105 of 570 sections, under the
  200-candidate cap, so the broken and fixed pruning admit identical candidates
  there. Size the test corpus past the threshold (500 sections). (v1.140.0)
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

- ⚠ **#152 is closed as not planned; reopening is welcome.**
  `related_persist.lookup` parses the whole `related.json` (1.99 s, 220 MB
  resident if memoized), but only `get_related_sections` calls it and there
  is no record of that tool being called. **Reopen on a user's latency
  report, not on this evidence.** The related graph's semantic half measured
  86 s at 36k sections with random vectors; that is unverified on real ones
  and is NOT a claim. (v1.145.0)
- ⚠⚠ **A count with no time basis gets one supplied by the reader, and the
  reader picks "per request."** A stable schema block is paid at cache-read
  rates after the first hit, so a bare `schema_tokens_avoided` overstated the
  saving by about an order of magnitude in the direction that flatters us. The
  fix for an unstated basis is a LABEL (`*_basis`), never a silently scaled
  number. (v1.139.0)
- ⚠ **Weigh what the client RECEIVES, never the catalog filtered by a tier
  bundle** — force-included and disabled tools both change the answer. One
  producer of the published surface, one estimator. (v1.139.0)
- ⚠ **A setting that implies a saving it does not deliver is the same defect
  as an unstated basis.** `standard` measured 9.39% and stays as a SCOPE
  choice; the config comment says so. Do not port a gate for a mechanism this
  repo does not have (no runtime tier switch, so no invalidation to price); a
  ratchet names the module to port from if it ever arrives. (v1.139.0)
- ⚠⚠ **A constant written for a FUTURE date is wrong for the whole interval
  before it, and reads identically to a stale one.** A date on a table is
  evidence of when someone looked, never of what they saw. Sonnet 5 was $2.00,
  never the $3.00 scheduled for 2026-09-01 and cancelled. (v1.139.1)
- ⚠⚠ **A key that names a FAMILY inherits whichever member's price someone
  last looked at.** Name the ONE model in the comment; the KEYS (`claude_sonnet`
  etc.) are wire format in `cost_avoided` and never rename on 1.x. (v1.139.1)
- ⚠⚠ **Copies that agree with each other prove nothing.** Four copies of the
  rate across the suite were wrong together. Verify against the source page,
  and search for DERIVED literals (`TOKEN_SAVINGS.md` carried two) — they move
  with the rate and are invisible to a search for its name. (v1.139.1)
- ⚠ **Pinning a number is not vouching for it, and the pin must say which.**
  `gpt5_latest` is pinned unverified. `tests/test_pricing_rates.py` RESTATES
  prices rather than importing them — a pin that reads the value it checks
  asserts nothing. (v1.139.1)
- ⚠ **Two trackers share issue numbers. Read the author, not the number.**
  jdoc's own #129/#130 and the jcodemunch-side findings #129/#130 both appear in
  this repo's history. (v1.140.0)
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
CI** — a dev box may have numpy from other packages, so the gap was invisible locally;
reproduce CI by SYNCING FIRST, with the flags the workflow itself uses:
`uv sync --group dev --python 3.13` then `uv run --python 3.13 pytest tests/ -q`
.
⚠⚠ **A `uv run` with no `uv sync` in front of it inherits a `.venv` it did not
create**, so it silently tests whatever happens to be installed. jcm shipped
that exact command and measured the cost: the run came back **exit 0 with the
totals reconciling exactly** while `passed` fell 8,721 to 8,634 and `skipped`
rose 19 to 124 — **105 tests did not execute.** Exit code and total were both
"green". **Read the SKIP count**; jdoc's baseline on a Windows dev box is 6 POSIX-only
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

## Reranking

A local cross-encoder rerank stage was evaluated in a pre-registered study and
not adopted: it improved nDCG@5 but made more queries worse than the limit set
before the data. `ROADMAP.md` ("A rerank stage") is the public record. Hybrid
search already beat lexical by +0.09 to +0.18 nDCG in the same study, so
getting users onto an embedding provider is the bigger lever.

⚠ `semantic` already means embedding fusion in `search_sections`; any future
rerank parameter is named `rerank`. ⚠ A rerank stage must leave
`attach_confidence`, `build_verdict` and `record_ranking_event` reading the
RETRIEVAL order (the jdoc#106 defect class). ⚠ int8 cross-encoder scores depend
on batch composition; one passage per call.

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
⚠⚠ **Those sums were ALSO zero the whole time, and this audit could not see
it** (#138, @sdjrdriver, fixed in 1.145.0).
`Section.to_dict` drops `content` for byte-addressed sections, so on any index
READ BACK FROM DISK the sum is 0 and `tokens_saved` floors to 0. The 1.0000
ratio was measured on freshly parsed sections, which still carry content.
**A property checked on an in-memory object says nothing about the reloaded
one.** Those tools now use `DocStore.raw_doc_bytes` (cached file sizes, distinct
paths). The partition property above is now the reason that substitution is
exact, so it is still load-bearing.

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

⚠⚠ **The full checklist lives in the `release` skill, which is now TRACKED
HERE at `.claude/skills/release/SKILL.md` (2026-09-01).** It was copied from
jcodemunch-mcp — verbatim, plus a marked delta block, because two copies that
drift silently are worse than one copy with an explicit delta list.
⚠⚠ **It drifted silently anyway, within a day.** jcm rewrote step 7 on
2026-09-02 and nobody ported it; 1.142.0 (2026-09-19) handed over the stale
line. Re-synced that day. ⚠ jcm now TRACKS its copy (2026-09-04) and its
publish steps are superseded by its `release.yml`. **No test binds the copies**
— diff this file's body against jcm's before a release rather than trusting
"verbatim".

⚠⚠ **The two items below STAY restated here, and that is not redundancy.**
Each has already cost a real incident, and a skill file is loaded only when
something loads it — the brief is loaded every session. **This section is what
made the 1.139.1 release recoverable when the skill turned out to be absent
from this repo entirely**, so do not delete it as duplicated now that the skill
is tracked. ⚠ `pyproject.toml`'s `exclude = [".claude/"]` keeps the skill out
of the sdist; the CI guard at `.github/workflows/test.yml:80` matches
`/.claude/` with a trailing slash, so `.claude-plugin/` does not false-positive
and the guard can now actually fire — its own comment records that it never
could, because nothing under `.claude/` had ever been tracked.

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
safe and does NOT need a version bump to avoid a failed tag.

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

**3a. NO TIMEBOX WE OFFER RUNS LONGER THAN 24 HOURS — absolute, no exceptions.**
It covers every shape:
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
.

Surfaces: `CONTRIBUTING.md` + `.github/ISSUE_TEMPLATE/`.

## Index locking and the QA evidence tools

- **Every contention-sensitive `delete_index` caller states `lock_wait`**;
  the lock never infers intent. The default stays `False` (refuse) because a
  caller that forgets then keeps both indexes present, where blocking would
  cost one. `UNCONTENDED_EXEMPT` is empty on purpose: add a site with its
  reason, never loosen the rule. `tests/test_v1_115_0_qa25.py` pins it.
- **The behavioural locking tests are POSIX-only**, so a Windows pass never
  verifies the locking contract; the 5-test skip gap between Linux and
  Windows runs is that mechanism showing.
- **`package-smoke` CI** builds the wheel, installs it in a clean venv and
  runs `scripts/smoke_installed.py`, which refuses to run from a source tree.
- **`scripts/evidence_receipt.py`** writes one receipt per CI job from
  `pytest --junitxml`. On a `pull_request` event `GITHUB_SHA` is the merge
  commit; the head comes from `PR_HEAD_SHA`. A summary spanning more than one
  SHA says so instead of averaging.

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
 class).

## Replay-corpus warning: a large new doc can break the replay gate

Same class as the CHANGELOG incident above, hit 2026-07-26 while tracking
a large archive of release notes. ⚠ **The replay self-fixture indexes `repo_path: "."` —
the WHOLE REPO — so any large markdown file added to the tree joins the retrieval
corpus and competes with the goldens.** Tracking 115 KB of trimmed release-brief
prose dropped nDCG to **0.906 against a 0.95 gate with recall still 1.0** (every
golden found, just outranked), failing
`test_replay_metrics.py::TestGate::test_pass_when_within_gate` and
`TestBaselineLock::test_self_fixture_meets_lock`.

⚠ **It was INVISIBLE to CI because the file was UNTRACKED — a fresh clone did not
have it.** Local runs were red while all 8 CI jobs at `69c91c4` were green. Any
"CI is green at the frozen SHA" claim is blind to untracked working-tree files.

`CLAUDE.md` is in the fixture's `extra_ignore_patterns` for exactly this
reason: its keyword-dense entries shadow stable CHANGELOG goldens (for
example 'broken links'). ⚠ **Never fix this class by moving a golden or
lowering the gate; the signal is correct, the corpus scope was wrong.** It
WILL recur for any new large doc added at a new path.

## Release notes

Per-release notes live in `CHANGELOG.md`.

## Purpose
Documentation section indexing for the jMunch suite. Companion to jcodemunch-mcp (which owns code symbols). Do NOT add code/docstring parsing here.

## Supported Formats
`.md/.mdx`, `.rst`, `.adoc`, `.ipynb`, `.html`, `.txt`, `.yaml/.yml` (OpenAPI only), `.json/.jsonc`, `.xml/.svg/.xhtml`, `.tscn/.tres` (Godot scenes/resources), `.pdf/.docx/.pptx/.epub` (optional `[office]` extra, local indexing only, markitdown conversion)

## Key Modules
- `storage/doc_store.py` — DocIndex, DocStore, detect_changes, incremental_save
- `parser/` — one file per format (markdown, rst, asciidoc, notebook, html, text, openapi, json, xml)
- `tools/` — index_local, index_repo, index_file, get_toc, get_toc_tree, search_sections, get_section, get_sections, list_repos, delete_index, get_broken_links, get_doc_coverage, get_backlinks, get_stale_pages, get_wiki_stats, check_section_delete_safe, get_section_blast_radius, find_similar_sections
- `cli/hooks.py` — PreToolUse (Read interceptor) + PostToolUse (auto-reindex) + SessionStart (session snapshot on compact/resume/fork, #131) hook handlers for Claude Code; PreCompact kept as a no-op; owns `_DOC_EXTENSIONS`
- `watch.py` — (#78) `watch` daemon: `discover_local_doc_repos` + `watch_docs` (watchfiles-based, incremental `index_local` refresh, rediscover loop)
- `service_installer.py` — (#78) cross-platform login-service installer for `watch` (`jdocmunch-watch`; systemd/launchd/Task Scheduler)
- `cli/embeddings_offer.py` — (1.143.0) `init`'s offline-embeddings offer: the ONLY module that runs pip or starts a download; owns the benchmark figures the prompt quotes
- `cli/init.py` — `jdocmunch-mcp init` full onboarding: client detection, config patching, CLAUDE.md policy, Cursor/Windsurf rules, hooks, index; `claude-md` subcommand
- `watch_batch.py` — (1.150.0, #189) one watch batch per child interpreter; `watch._reindex_batch` launches it
- `parser/ocr.py` — (1.149.0) `[ocr]` extra: two-key gate, Tesseract/Windows OCR detection, PDF page reader, section marking; `index_local._OcrState` holds per-call state
- `embeddings/` — provider.py (Gemini + OpenAI), cosine_similarity, embed_sections, embed_query

## CLI Subcommands
| Subcommand | Purpose |
|------------|---------|
| `serve` (default) | Run the MCP server (stdio) |
| `init` | One-command onboarding: detect clients, write config, install policy, hooks, index. Asks once about offline embeddings, default No; `--with-embeddings` opts in for scripts, `--yes` never does (1.143.0) |
| `claude-md` | Print or install the Doc Exploration Policy (`--install global\|project`) |
| `index-local --path <dir>` | Index a local folder (CLI, no MCP session needed) |
| `index-file <path>` | Re-index a single file within an existing index |
| `hook-pretooluse` | PreToolUse hook: intercept Read on large doc files (reads stdin) |
| `hook-posttooluse` | PostToolUse hook: auto-reindex doc files after Edit/Write (reads stdin) |
| `hook-precompact` | PreCompact hook: silent no-op kept for installed settings.json entries (#131; reads stdin) |
| `hook-sessionstart` | (#131) SessionStart hook: restore the doc session snapshot after compact/resume/fork as `additionalContext` (reads stdin) |
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
