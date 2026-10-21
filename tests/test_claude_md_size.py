"""`CLAUDE.md` is loaded into every session under this directory, so its size is
a per-turn cost paid by every reader forever.

⚠⚠ **Installed after the sibling repo hit the cliff.** On 2026-08-21
jcodemunch-mcp's `CLAUDE.md` reached 200,543 chars and the harness refused to
load it — while the maintenance practice governing its size was being followed.
That practice named one section, and the growth was in the sections it did not
name. **A rule that names one section licenses every other section to grow**, and
a budget stated only in prose is not a budget.

The brief keeps engineering rules only; release narrative lives in
`CHANGELOG.md`, and maintainer notes load from a gitignored `CLAUDE.local.md`.

⚠⚠ **Any new large doc in the tree is a replay-corpus hazard, and that is this
file's second subject.** The replay self-fixture indexes `repo_path: "."` — the
whole repo — so a large markdown file joins the retrieval corpus and competes
with the goldens. An untracked archive of release notes once dropped nDCG to
0.906 against a 0.95 gate with recall still 1.0: every golden found, just
outranked. `CLAUDE.md` is in the fixture's `extra_ignore_patterns` for that
reason.

Failure here means cut narrative, not rules.
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The harness refuses to load a project instruction file above this. It is not a
# style preference and it is not ours to raise.
HARNESS_LIMIT = 150_000

# Where the gate fires. The gap to HARNESS_LIMIT is deliberate: a ceiling that
# fires exactly at the cliff fires for the first time in the session it breaks.
BUDGET = 130_000



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
        f"({HARNESS_LIMIT:,} is where the harness stops loading it). Cut "
        f"narrative; keep rules, and keep any new large doc out of the replay "
        f"corpus (see the test below)."
    )


def test_every_whole_repo_fixture_excludes_the_brief():
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
