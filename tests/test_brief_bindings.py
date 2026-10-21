"""The brief's load-bearing claims are BOUND to the things they describe.

`CLAUDE.md` is prose, and prose drifts silently. Three facts in it are not
opinions -- they describe a workflow file, a release procedure whose steps are
irreversible, and a registry API whose rows are shaped a particular way. Each
one below fails in CI when the brief and the world disagree, instead of waiting
for a human to notice.

⚠⚠ **Assert the PROPERTY, not the sentence.** A check for an exact string fails
on the next typo fix and gets deleted rather than repaired, which leaves the
hole it was guarding. Every assertion here is written to survive a rewording.

Companion to `tests/test_claude_md_size.py`, which owns the size budget and the
replay-corpus exclusion. This file owns the CONTENT claims.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
#: Strings that mark maintainer-internal content. The public brief is
#: engineering rules only; maintainer notes load from a gitignored
#: `CLAUDE.local.md`. Matched case-sensitively, on whole words where it matters.
INTERNAL_MARKERS = [
    r"\bjjg\b", r"\bJ's\b", r"Chief of Staff", r"Engineering-Doc",
    r"side-session-notes", r"\bCEO\b", r"\bruling\b", r"C:\\Apps",
    r"C:\\Users", r"\[\[",
]


def _claude_md() -> str:
    return (ROOT / "CLAUDE.md").read_text(encoding="utf-8")


def _reproduce_windows() -> "list[str]":
    """Every passage that mentions reproducing CI, longest-lived form.

    ⚠⚠ Two earlier drafts of this were both wrong, in opposite directions, and
    both were caught by these tests rather than by review:

    1. It matched the literal `reproduce CI with ...`, and broke the moment the
       fix reworded that to `reproduce CI by SYNCING FIRST`. A binding that
       fails on a rewording gets deleted rather than repaired.
    2. It then took the FIRST `reproduce CI` in the file, which a
       cross-reference added in the header promptly stole from the real
       passage -- so the test reported the command as missing when it was
       there.

    Returning every window and asserting over the set survives both: a
    cross-reference is simply a window that does not contain the command.
    """
    brief = _claude_md()
    return [brief[m.start():m.start() + 1400]
            for m in re.finditer(r"reproduce CI", brief)]


def _reproduce_passage() -> str:
    """The window that actually carries the command."""
    windows = _reproduce_windows()
    assert windows, "CLAUDE.md no longer tells anyone how to reproduce CI"
    for w in windows:
        if "uv sync" in w:
            return w
    return windows[0]


def _workflow() -> str:
    return (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")


def _sync_flags(command: str) -> set:
    """Flag set of a `uv sync ...` invocation, order-insensitive.

    ``--group dev`` is normalised to a single token so a reordering or an
    ``=``-spelling does not read as a difference.
    """
    m = re.search(r"uv sync([^\n&|;]*)", command)
    if not m:
        return set()
    body = m.group(1).replace("=", " ").split()
    flags, i = set(), 0
    while i < len(body):
        tok = body[i]
        if not tok.startswith("--"):
            i += 1
            continue
        if i + 1 < len(body) and not body[i + 1].startswith("--"):
            flags.add(f"{tok} {body[i + 1]}")
            i += 2
        else:
            flags.add(tok)
            i += 1
    return flags


# --------------------------------------------------------------------------- #
# 1. The documented CI-reproduce command must build CI's environment.
# --------------------------------------------------------------------------- #


def test_workflow_still_installs_before_it_tests():
    """Premise check. If CI stops syncing, this file's whole argument moves."""
    assert re.search(r"run:\s*uv sync", _workflow()), (
        "test.yml no longer runs `uv sync`. The reproduce-command binding "
        "below is derived from that line; re-derive it rather than deleting it."
    )


def test_documented_reproduce_command_syncs_at_all():
    """⚠⚠ The defect this binding exists for.

    `uv run --python 3.13 python -m pytest` with NO sync runs against whatever
    `.venv` happens to hold -- it inherits a state it did not create. jcm
    shipped exactly this: the run came back exit 0 with the totals reconciling
    exactly while 105 tests silently did not execute, because a dependency CI
    installs by name was absent. Exit code and total were both "green".
    """
    windows = _reproduce_windows()
    assert windows, "CLAUDE.md no longer tells anyone how to reproduce CI"

    def syncs_first(w: str) -> bool:
        """⚠ ORDER is the property, not presence.

        A first draft asserted `"uv sync" in w`, which the fixed text satisfies
        from a sentence that says *do NOT* copy jcm's `uv sync --locked`. A
        warning about a command is not the same as documenting one, so assert
        that a sync appears BEFORE the run it is supposed to precede.
        """
        sync = w.find("uv sync")
        run = w.find("uv run")
        return sync != -1 and (run == -1 or sync < run)

    assert any(syncs_first(w) for w in windows), (
        "no passage mentioning `reproduce CI` runs `uv sync` BEFORE `uv run`, "
        "so the documented command inherits whatever .venv already holds: "
        f"{[w[:140] for w in windows]!r}"
    )


def test_documented_reproduce_command_matches_the_workflow():
    """The property: the brief names the same sync flags the workflow uses.

    Subset, not equality -- a local reproduction may legitimately add
    `--python <ver>` to pin one matrix leg, which CI expresses separately via
    `uv python install`.
    """
    wf_flags = _sync_flags(_workflow())
    doc_flags = _sync_flags(_reproduce_passage())
    assert wf_flags <= doc_flags, (
        f"test.yml syncs with {sorted(wf_flags)} but CLAUDE.md documents "
        f"{sorted(doc_flags)}. The workflow is the authority; update the brief."
    )


def test_documented_reproduce_command_does_not_use_locked():
    """⚠ jdoc's `uv.lock` is GITIGNORED, so `--locked` fails here.

    This is where copying jcm's command (`uv sync --locked --group dev --extra
    watch`) breaks: jcm has a tracked lock and a `watch` extra; jdoc has
    neither. CLAUDE.md already records this at the v1.124.3 entry.
    """
    doc_flags = _sync_flags(_reproduce_passage())
    assert "--locked" not in doc_flags, (
        "the documented reproduce command uses --locked, which cannot work "
        "with a gitignored uv.lock. This is jcm's command, not jdoc's."
    )


def test_header_test_command_uses_the_module_form():
    """⚠ The header disagreed with the suite brief until 2026-08-29.

    `python -m pytest` guarantees the interpreter receiving `PYTHONPATH=src` is
    the one running the tests; a bare `pytest` shim can resolve into a different
    environment. The suite brief (`C:\\MCPs\\CLAUDE.md`) states the `-m` form as
    the rule for all three repos.

    ⚠ Bound to the property rather than to the suite brief's file, which lives
    outside the repo and is absent from a fresh clone and from the sdist.
    """
    header = _claude_md()[:1200]
    m = re.search(r"\*\*Tests:\*\*\s*`([^`]+)`", header)
    assert m, "CLAUDE.md's header no longer states a test command"
    cmd = m.group(1)
    assert "PYTHONPATH=src" in cmd, (
        f"the header test command drops PYTHONPATH, so the installed package "
        f"shadows src/: {cmd!r}"
    )
    assert "python -m pytest" in cmd, (
        f"the header test command uses a bare pytest shim rather than the "
        f"module form the suite brief specifies: {cmd!r}"
    )


# --------------------------------------------------------------------------- #
# 2. Read CI before the irreversible steps.
# --------------------------------------------------------------------------- #


def test_brief_says_to_read_ci_before_the_irreversible_steps():
    """⚠⚠ It matters more here than anywhere else in the suite.

    **jdoc has no release workflow at all** -- `.github/workflows/` holds only
    test.yml and replay.yml -- so every irreversible step is taken by a human
    who can take it against a red build. (The contrast is jdatamunch, whose
    release.yml gates on a successful Tests run; ⚠ that is a claim about
    ANOTHER repo and nothing here can bind it -- CLAUDE.md carries the command
    to re-read it.) Four consecutive jcm releases
    were published, tagged and PyPI-uploaded on a RED build because the local
    suite was green and nobody read the check.
    """
    brief = _claude_md()
    assert "gh run list" in brief, (
        "CLAUDE.md does not carry the command for reading CI on a pushed SHA"
    )
    window = brief[brief.index("gh run list") - 1500: brief.index("gh run list") + 500]
    assert re.search(r"irreversible|cannot be re-uploaded|before .{0,40}(upload|publish|tag)",
                     window, re.I), (
        "the `gh run list` command appears with no statement of WHY it comes "
        "first. The reason is the part that survives a skim."
    )


def test_brief_records_that_jdoc_has_no_release_workflow():
    """The reason the human is the only gate. If a release workflow is ever
    added, this assertion is what makes someone revisit the claim."""
    present = {p.name for p in (ROOT / ".github/workflows").glob("*.yml")}
    assert present == {"test.yml", "replay.yml"}, (
        f"the workflow set changed to {sorted(present)}. CLAUDE.md states that "
        "jdoc has no release workflow and that a human is therefore the only "
        "gate on the irreversible steps -- re-check that claim."
    )


# --------------------------------------------------------------------------- #
# 3. The MCP registry's nested-row trap.
# --------------------------------------------------------------------------- #


def test_brief_warns_about_the_nested_registry_row():
    """⚠⚠ A flat `row["name"]` read returns ZERO rows on a publish that
    completely succeeded, and it SURVIVES `&limit=100`, so the documented
    paging remedy does not help and the symptom is indistinguishable from a
    failed publish."""
    brief = _claude_md()
    assert "registry.modelcontextprotocol.io" in brief or "mcp-publisher" in brief, (
        "CLAUDE.md does not mention the MCP registry at all, yet this server "
        "is published to it"
    )
    assert re.search(r'\bserver\b.{0,80}\b_meta\b|\b_meta\b.{0,80}\bserver\b',
                     brief, re.S), (
        "CLAUDE.md does not describe the nested {server, _meta} row shape"
    )


def test_brief_says_a_zero_row_read_is_not_grounds_to_republish():
    brief = _claude_md()
    assert re.search(r"(zero|0)[- ]row.{0,200}(re-?publish|republish)"
                     r"|re-?publish.{0,200}(zero|0)[- ]row",
                     brief, re.I | re.S), (
        "CLAUDE.md does not say that a zero-row read is not grounds to "
        "re-publish. Re-publishing on a bad parse is the harmful action the "
        "warning exists to prevent."
    )


def test_brief_says_to_confirm_the_package_version_advanced():
    """An entry can move `server.version` and not `server.packages[].version`.

    ⚠ The first draft of this asserted `"packages" in brief`, which passed
    against the UNFIXED file because it matched `site-packages` five times
    over. A binding that passes for the wrong reason is worse than none: it
    reports the hole as covered. Assert the registry spelling.
    """
    brief = _claude_md()
    assert re.search(r"packages\[\]", brief), (
        "CLAUDE.md does not name `packages[]`, so it cannot be telling anyone "
        "to confirm the package version advanced alongside isLatest"
    )
    assert "isLatest" in brief


# --------------------------------------------------------------------------- #
# 4. The public brief carries engineering rules, nothing internal.
# --------------------------------------------------------------------------- #


def test_public_brief_carries_no_dated_release_narrative():
    """Release narrative lives in CHANGELOG.md; the brief keeps the rules."""
    dated = re.findall(r"^## v\d+\.\d+\.\d+.*$", _claude_md(), re.M)
    assert not dated, (
        "CLAUDE.md is public and carries dated release sections again; put the "
        f"lesson in 'Engineering lessons' and the narrative elsewhere: {dated[:3]}"
    )


@pytest.mark.parametrize("marker", INTERNAL_MARKERS)
def test_public_brief_carries_no_internal_markers(marker):
    hits = [ln for ln in _claude_md().splitlines() if re.search(marker, ln)]
    assert not hits, (
        f"CLAUDE.md is public, but {marker!r} marks maintainer-internal content. "
        f"Move it to the private notes CLAUDE.local.md loads: {hits[:3]}"
    )


def test_the_marker_scan_fires_on_internal_text():
    """Non-vacuity: every marker matches a line it exists to catch."""
    samples = ["per jjg on the thread", "J's call", "ask the Chief of Staff",
               "Engineering-Doc wrote", "in side-session-notes", "the CEO said",
               "a ruling of 2026", "C:\\Apps\\hq", "C:\\Users\\j", "see [[memo]]"]
    for marker, sample in zip(INTERNAL_MARKERS, samples):
        assert re.search(marker, sample), (marker, sample)


def test_claude_local_md_is_gitignored():
    """The maintainer-notes loader must never be committed or shipped."""
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"^CLAUDE\.local\.md\s*$", gitignore, re.M), (
        ".gitignore no longer lists CLAUDE.local.md"
    )
