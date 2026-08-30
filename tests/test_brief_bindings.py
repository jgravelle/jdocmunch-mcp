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
ARCHIVE = "docs/CLAUDE-history.md"

#: How many dated release sections `CLAUDE.md` may carry. Everything older
#: rotates into ARCHIVE. ⚠ The number is a practice, not a measurement -- raise
#: it only with the size budget in mind, never to avoid a rotation.
MAX_DATED_SECTIONS = 3


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
# 4. The rotation actually happened, and nothing was lost.
# --------------------------------------------------------------------------- #


def _dated_sections(text: str) -> "list[str]":
    return re.findall(r"^## v\d+\.\d+\.\d+.*$", text, re.M)


def test_claude_md_keeps_only_the_newest_dated_sections():
    kept = _dated_sections(_claude_md())
    assert len(kept) <= MAX_DATED_SECTIONS, (
        f"CLAUDE.md carries {len(kept)} dated release sections, over the "
        f"{MAX_DATED_SECTIONS} this repo keeps. Rotate the oldest into "
        f"{ARCHIVE} -- and ONLY into {ARCHIVE}; see test_claude_md_size.py for "
        f"why a new path re-breaks the replay gate."
    )


def test_the_archive_exists_and_holds_the_rotated_sections():
    archive = ROOT / ARCHIVE
    assert archive.exists(), f"{ARCHIVE} is missing"
    assert len(_dated_sections(archive.read_text(encoding="utf-8"))) > MAX_DATED_SECTIONS, (
        f"{ARCHIVE} holds almost no dated sections, so the rotation either did "
        f"not happen or went somewhere else"
    )


def test_claude_md_points_at_the_archive():
    """⚠ A split with no pointer is a deletion."""
    assert ARCHIVE in _claude_md(), (
        f"CLAUDE.md does not say where the history went. {ARCHIVE} is not "
        f"loaded into a session, so a reader who is not told it exists has "
        f"simply lost the content."
    )


def test_no_dated_section_was_lost_or_duplicated_by_the_rotation():
    """⚠ A rotation reviewed by eye is how an entry goes missing.

    Every dated heading that CLAUDE.md carried at the previous commit must now
    live in EXACTLY ONE of the two files -- not zero, not both.
    """
    import subprocess

    try:
        prev = subprocess.run(
            ["git", "show", "HEAD:CLAUDE.md"],
            cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        pytest.skip("git absent (sdist checkout)")
    if prev.returncode != 0:
        pytest.skip("no usable git here")

    before = set(_dated_sections(prev.stdout))
    if not before:
        pytest.skip("HEAD:CLAUDE.md carries no dated sections to account for")

    now = set(_dated_sections(_claude_md()))
    archived = set(_dated_sections((ROOT / ARCHIVE).read_text(encoding="utf-8")))

    lost = sorted(h for h in before if h not in now and h not in archived)
    assert not lost, (
        f"{len(lost)} dated section(s) present at HEAD are now in NEITHER "
        f"CLAUDE.md nor {ARCHIVE}:\n" + "\n".join(lost[:10])
    )
    both = sorted(h for h in before if h in now and h in archived)
    assert not both, (
        f"{len(both)} dated section(s) are in BOTH files; the archive is the "
        f"single home for a rotated entry:\n" + "\n".join(both[:10])
    )
