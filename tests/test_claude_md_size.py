"""`CLAUDE.md` is loaded into every session under this directory, so its size is
a per-turn cost paid by every reader forever.

⚠⚠ **Installed after the sibling repo hit the cliff.** On 2026-08-21
jcodemunch-mcp's `CLAUDE.md` reached 200,543 chars and the harness refused to
load it — while the maintenance practice governing its size was being followed.
That practice named one section, and the growth was in the sections it did not
name. **A rule that names one section licenses every other section to grow**, and
a budget stated only in prose is not a budget.

This repo already rotates into `docs/CLAUDE-history.md` (established
2026-07-25). It had no gate, which is why it is at 101,613 chars with 27
embedded release sections accounting for 96% of the file.

⚠⚠ **The archive path is NOT a free choice here, and that is this file's real
subject.** The replay self-fixture indexes `repo_path: "."` — the whole repo — so
any large markdown file in the tree joins the retrieval corpus and competes with
the goldens. Trimming into an untracked `docs/CLAUDE-history.md` once dropped
nDCG to 0.906 against a 0.95 gate with recall still 1.0: every golden found, just
outranked. `CLAUDE.md` and the archive are both in the fixture's
`extra_ignore_patterns` for exactly that reason, and the fixture's own
description warns the problem "WILL recur for any new large doc added at a new
path."

Failure here means rotate, do not delete — and rotate into the path the fixture
already excludes.
"""
from __future__ import annotations

import glob
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# The harness refuses to load a project instruction file above this. It is not a
# style preference and it is not ours to raise.
HARNESS_LIMIT = 150_000

# Where the gate fires. The gap to HARNESS_LIMIT is deliberate: a ceiling that
# fires exactly at the cliff fires for the first time in the session it breaks.
BUDGET = 130_000

ARCHIVE = "docs/CLAUDE-history.md"


def _claude_md() -> str:
    return (ROOT / "CLAUDE.md").read_text(encoding="utf-8")


def _whole_repo_fixtures() -> "list[tuple[str, dict]]":
    """Every replay fixture whose corpus is the entire repository."""
    out = []
    for path in sorted(glob.glob(str(ROOT / "benchmarks/replay/fixtures/*.json"))):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("repo_path") in (".", "./"):
            out.append((Path(path).name, data))
    return out


def test_claude_md_fits_the_session_budget():
    size = len(_claude_md())
    assert size <= BUDGET, (
        f"CLAUDE.md is {size:,} chars against a {BUDGET:,} budget "
        f"({HARNESS_LIMIT:,} is where the harness stops loading it). Move the "
        f"oldest `## vX.Y.Z` release sections into {ARCHIVE} — that path, not a "
        f"new one; see the replay-corpus test below."
    )


def test_a_pointer_and_an_archive_imply_each_other():
    """Neither may exist without the other.

    ⚠ Stated both ways on purpose. Asserting only that the archive exists cannot
    fire in a repo that has not rotated yet, and says nothing about whether the
    archive is reachable from the file every session actually reads.
    """
    archive_exists = (ROOT / ARCHIVE).is_file()
    pointer_exists = ARCHIVE in _claude_md()
    assert archive_exists == pointer_exists, (
        f"{ARCHIVE} exists={archive_exists} but CLAUDE.md points at it="
        f"{pointer_exists}."
    )


def test_the_archive_is_tracked_by_git_once_it_exists():
    """⚠⚠ Present on disk is not the same as kept — and here it is worse than
    losing history.

    When this archive was UNTRACKED, local replay runs were red while all 8 CI
    jobs were green, because a fresh clone did not have the file. **Any "CI is
    green at the frozen SHA" claim is blind to untracked working-tree files.**
    """
    if not (ROOT / ARCHIVE).is_file():
        pytest.skip(f"{ARCHIVE} does not exist yet; nothing to track")
    try:
        rc = subprocess.run(
            ["git", "ls-files", "--error-unmatch", ARCHIVE],
            cwd=ROOT, capture_output=True, stdin=subprocess.DEVNULL, timeout=10,
        ).returncode
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("no usable git here (sdist checkout or git absent)")
    assert rc == 0, f"{ARCHIVE} is not tracked by git — check .gitignore."


def test_every_whole_repo_fixture_excludes_the_brief_and_its_archive():
    """The rotation target must stay out of the replay retrieval corpus.

    ⚠⚠ **This is the test that stops a well-meaning port from breaking the
    replay gate.** jcodemunch-mcp rotates into a root-level `ISSUE-HISTORY.md`;
    copying that choice here would move 98KB of tool-keyword-dense release prose
    out from under the `CLAUDE.md` exclusion and re-break the same three
    CHANGELOG goldens ('hybrid search', 'broken links', 'openai compatible
    embeddings').

    ⚠ Written over EVERY whole-repo fixture rather than the one that exists
    today, so a second one is covered on the commit that adds it.

    ⚠ Never fix a failure here by moving a golden or lowering the gate. The
    signal is correct; the corpus scope is what went wrong.
    """
    fixtures = _whole_repo_fixtures()
    assert fixtures, (
        "no replay fixture indexes the whole repo any more. If that is "
        "deliberate, this test's premise is gone and the exclusion requirement "
        "should be re-derived rather than deleted."
    )
    for name, data in fixtures:
        patterns = data.get("extra_ignore_patterns") or []
        assert "CLAUDE.md" in patterns, (
            f"{name} indexes the whole repo but does not exclude CLAUDE.md; its "
            f"tool-keyword-dense entries shadow stable CHANGELOG goldens."
        )
        assert ARCHIVE in patterns, (
            f"{name} indexes the whole repo but does not exclude {ARCHIVE}. The "
            f"archive IS the trimmed CLAUDE.md entries, so it inherits the "
            f"exclusion by necessity."
        )
