---
name: release
description: Publishing a jdocmunch-mcp release and reviewing, merging or closing PRs. Load before any version bump, PyPI upload, tag, GitHub release, MCP registry publish, or PR merge.
---

# Release & PR workflow (jdocmunch-mcp)

jdocmunch-mcp has **no release workflow**: `.github/workflows/` holds `test.yml`
and `replay.yml`, so every irreversible step (PyPI upload, tag, GitHub release,
registry publish) is taken by a person or a script, and can be taken against a
red build. The order below exists to stop that. The default branch is `master`.

⚠⚠ **Do not hold a release for something that is not ours** (a signature, a
contributor PR, a reply). Done, tested and green ships; others ride the next one.

## Publishing a release

1. **Bump every pin site.** There are four: `pyproject.toml`, `server.json`
   (`version` AND `packages[].version`) and `.claude-plugin/plugin.json`. Find
   them with `grep -rn "<old-version>" --include=*.json --include=*.toml .`;
   `mcpb/manifest.json` is generated and gitignored, so skip it. `uv.lock` is
   gitignored here and is never a pin site.
2. **Test the edit loop:** `PYTHONPATH=src python -m pytest tests/ -q`.
3. **Lint:** `uv run ruff check src/`. CI lints and the local suite does not; a
   green suite is not a green build.
4. **Reproduce CI's environment** with the workflow's own flags, sync first:
   ```bash
   uv sync --group dev --python 3.13
   uv run --python 3.13 pytest tests/ -q
   ```
   ⚠ Read the SKIP count, not only the exit code: a run against an environment
   missing a dependency can exit 0 with the totals reconciling while tests did
   not execute. ⚠ Never pass `--locked` (the lock is gitignored) or
   `--extra watch` (no such extra).
5. **Commit, push, and READ CI for the pushed SHA** before anything
   irreversible. Read check runs by the FULL 40-character SHA; a short SHA
   returns zero rows with exit 0.
   ```bash
   GITHUB_TOKEN="" gh api "repos/jgravelle/jdocmunch-mcp/commits/$(git rev-parse HEAD)/check-runs" \
     --jq '.check_runs[] | "\(.name): \(.conclusion)"'
   ```
   Read the JOBS, not just a run's conclusion: a matrix still in progress reports
   neither.
6. **Build from a dedicated `git worktree`** at the merged commit, never the
   main checkout: the build reads the working tree, not HEAD. Then check, then
   upload:
   ```bash
   uvx --from build pyproject-build
   uvx --from twine twine check  dist/*X.Y.Z*   # gate: fails BEFORE anything ships
   uvx --from twine twine upload dist/*X.Y.Z*
   ```
   ⚠ Use `uvx`, not a global `python -m twine`: an always-latest hatchling emits
   a `Metadata-Version` an older global `packaging` rejects. Upgrading the
   global `packaging` is the wrong fix. ⚠ `twine check` is the load-bearing
   line: without it a metadata failure surfaces mid-upload, possibly after the
   wheel is on PyPI and the sdist is not. PyPI cannot be re-uploaded, so the
   remedy for a bad artifact is the next version.
   ⚠ A docs-only commit after the build still stales the artifacts
   (`CHANGELOG.md` ships in the sdist). Build last.
7. **Tag and push**, after resolving the default branch rather than typing it:
   ```bash
   BR=$(GITHUB_TOKEN="" gh repo view jgravelle/jdocmunch-mcp --json defaultBranchRef -q .defaultBranchRef.name)
   git tag vX.Y.Z && GITHUB_TOKEN="" git push origin vX.Y.Z
   ```
8. **GitHub release** with both artifacts attached:
   `GITHUB_TOKEN="" gh release create vX.Y.Z dist/*X.Y.Z* --repo jgravelle/jdocmunch-mcp --title "..." --notes "..."`
9. **MCP registry publish.** Run login and publish as ONE command from the repo
   root (`publish` reads `server.json` from the working directory); the registry
   JWT lives five minutes. Log in with the method `github` (`github-oidc` has no
   `--token` flag). A connection timeout is not an expired token: check the host
   is reachable before re-authenticating.
10. **Verify the registry against the live API**, not the CLI checkmark:
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
    ⚠⚠ Rows are nested `{server: {...}, _meta: {...}}`; a flat `row["name"]`
    read returns zero rows on a good publish. `&limit=100` matters: the default
    response is a page. A good publish can read back absent for about 90 seconds.
    **A zero-row read is never grounds to re-publish; fix the parse first.**
    Confirm `server.packages[].version` advanced too.

⚠ Write `reg.json` and release notes OUTSIDE the repository: a stray `.md` or
`.json` in the root ships in the sdist, and `tests/test_sdist_exclusions.py` is
the allowlist that catches it.

## PR workflow

- ⚠⚠ **A fork PR showing only `license/cla` has not been tested; its runs may be
  held.** `gh pr checks` lists only checks that ran.
  ```bash
  GITHUB_TOKEN="" gh api "repos/jgravelle/jdocmunch-mcp/actions/runs?status=action_required&per_page=30" \
    --jq '.workflow_runs[] | "\(.id)|\(.name)|\(.head_branch)|\(.head_sha[0:7])"'
  GITHUB_TOKEN="" gh api --method POST "repos/jgravelle/jdocmunch-mcp/actions/runs/<id>/approve"
  git ls-remote origin "refs/pull/<n>/merge"   # no merge ref = no pull_request run
  ```
  A CONFLICTING PR has no `refs/pull/N/merge`, so it gets no run at all until it
  is rebased. Closing and reopening does not re-provoke a run; a push does.
- ⚠⚠ **A missing `license/cla` status counts as NOT SIGNED.** Our own push to a
  contributor's branch moves the head, and commit statuses do not follow a new
  SHA, so the PR can read `CLEAN` with the CLA absent. Read the status by head
  SHA (`count=0` means not signed):
  ```bash
  GITHUB_TOKEN="" gh api "repos/jgravelle/jdocmunch-mcp/commits/<head-sha>/status" \
    --jq '"state=\(.state) count=\(.statuses|length)"'
  ```
  For our own PR, a GET of
  `https://cla-assistant.io/check/jgravelle/jdocmunch-mcp?pullRequest=<n>` makes
  the bot post its status.
- **An organization-owned fork cannot be pushed to**, even when the PR says
  `maintainerCanModify: true`. Check the owner type before promising to fix a
  contributor's branch:
  `GITHUB_TOKEN="" gh api users/<owner> --jq .type`. When the conflict is ours
  and we can't push, merge their head on a branch of ours
  (`contrib/<n>-merged`) so their commits stay on their record, and say on the
  thread why.
- ⚠⚠ **A mergeable contributor PR merges before any change of ours that
  touches `CHANGELOG.md`**, release commits included:
  ```bash
  GITHUB_TOKEN="" gh pr list --state open --json number,author,mergeable,mergeStateStatus \
    --jq '.[] | select(.author.login != "jgravelle") | "#\(.number) \(.author.login) \(.mergeable) \(.mergeStateStatus)"'
  ```
  A BLOCKED PR cannot go first; then we ship anyway and own the conflict
  resolution, saying on the thread that the conflict was ours.
- Until a fork PR shows a green matrix, a local trial merge onto current
  `master` is the substitute; branch-green is not merged-green.
- Review the diff before approving. Close without merging with a comment that
  says why.
