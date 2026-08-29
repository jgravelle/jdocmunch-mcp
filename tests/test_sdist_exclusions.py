"""The sdist exclude list is proven by BUILDING, not by grepping pyproject.toml.

Ported from jcodemunch-mcp after jcm 1.108.305 shipped `relnotes.md` -- a
scratch copy of release notes -- inside a published sdist, swept up by a
`git add -A` in the release commit. The allowlist below found a second
instance (`suite.log`) minutes later.

⚠⚠ This repo had NEITHER half of that guard, which is the standing "a setting
fixed in one repo of a suite is fixed in one repo" lesson. A defect reportable
in one of three servers is a parity gap until proven otherwise.

⚠ `tests/` is shipped inside this sdist (200 members), so anything dropped
there is distributed too -- see CLAUDE.md on `tests/infographic.png`, a 5.9 MB
promotional image that was 87% of the whole source distribution until 1.123.2.
That one was a TRACKED file, so exclusion rules and untracked-file scans were
both blind to it; `test_no_oversized_member_ships` is the check that would have
seen it.

⚠ `uv.lock` is gitignored here (unlike jcm), so it is never distributed. It is
deliberately absent from ALLOWED_ROOT_FILES; the staleness test would fail if
someone copied jcm's list wholesale.

Failure here means: fix `[tool.hatch.build.targets.sdist] exclude` in
pyproject.toml, or delete the stray file. Do not fix it by deleting the canary.
"""
from __future__ import annotations

import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

CANARY_NAME = "__sdist_exclusion_canary__.txt"

#: Directories that must be excluded from the sdist. The canary is planted
#: inside each, so a rule that stops matching leaks a file with an obvious name
#: rather than silently re-admitting real content.
#:
#: ⚠ `.claude/settings.local.json` is ignored via a GLOBAL gitignore, so
#: `git status` never shows it and a fresh clone never has it. It leaks only
#: from a LOCAL build -- and a local build is how uploads happen.
EXCLUDED_DIRS = [
    ".claude",
]

#: Paths that must SURVIVE the build. Without these the test would pass on a
#: pyproject that excluded far too much.
REQUIRED_PATHS = [
    "pyproject.toml",
    "README.md",
    "src/jdocmunch_mcp/server.py",
    # ⚠ A version pin site (CLAUDE.md names four). `.claude-plugin/` sits one
    # character away from the excluded `.claude/`, so this pins that the
    # exclusion did not widen onto it.
    ".claude-plugin/plugin.json",
    # tests/ ships here; an exclude that dropped it would be a real change.
    "tests/test_find_similar_sections.py",
]

#: No single member may exceed this. ⚠ An empty ratchet: add an entry only
#: with a reason, never raise the cap to make a file fit.
MAX_MEMBER_BYTES = 1_000_000
OVERSIZED_EXEMPT: dict[str, str] = {}


def _build_sdist(outdir: Path) -> subprocess.CompletedProcess:
    """Build an sdist with whatever builder this environment actually has.

    ⚠ `python -m build` is not guaranteed present: CI installs dependencies
    with `uv sync --group dev` and `build` is not in that group. CI does have
    `uv`.

    ⚠⚠ **This deliberately does not skip when no builder is found.** A skip is
    how a guard becomes decorative -- it returns early in exactly the
    environment that matters. If neither builder exists the tests error, which
    is the honest outcome: the exclusion went unverified.
    """
    attempts: list[str] = []
    for argv in (
        [sys.executable, "-m", "build", "--sdist", "--outdir", str(outdir)],
        ["uv", "build", "--sdist", "--out-dir", str(outdir)],
    ):
        try:
            result = subprocess.run(
                argv,
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            attempts.append(f"{argv[0]}: not on PATH")
            continue
        # "No module named build" is an absent builder, not a build failure.
        if result.returncode != 0 and "No module named build" in (result.stderr or ""):
            attempts.append(f"{argv[0]} -m build: module not installed")
            continue
        return result

    raise AssertionError(
        "no sdist builder available, so the exclude rules went unverified: "
        + "; ".join(attempts)
    )


@pytest.fixture(scope="module")
def built_sdist(tmp_path_factory) -> list[tarfile.TarInfo]:
    """Plant the canaries, build one sdist, return its members.

    Module-scoped: a build takes tens of seconds and every assertion below
    reads the same tarball.
    """
    planted: list[Path] = []
    created_dirs: list[Path] = []
    try:
        for rel in EXCLUDED_DIRS:
            directory = REPO_ROOT / rel
            if not directory.exists():
                # A fresh clone has no .claude/; create it so the rule is
                # exercised there too. Tracked back for removal in `finally`.
                directory.mkdir(parents=True)
                created_dirs.append(directory)
            canary = directory / CANARY_NAME
            canary.write_text("planted by test_sdist_exclusions\n", encoding="utf-8")
            planted.append(canary)

        outdir = tmp_path_factory.mktemp("sdist")
        result = _build_sdist(outdir)
        assert result.returncode == 0, (
            "sdist build failed:\n"
            + (result.stdout or "")[-4000:]
            + "\n"
            + (result.stderr or "")[-4000:]
        )

        tarballs = list(outdir.glob("*.tar.gz"))
        assert len(tarballs) == 1, (
            f"expected one sdist, got {[t.name for t in tarballs]}"
        )
        with tarfile.open(tarballs[0], "r:gz") as tf:
            members = tf.getmembers()
    finally:
        for canary in planted:
            canary.unlink(missing_ok=True)
        # Deepest first, and only ones this test created. rmdir refuses a
        # non-empty directory, so a real .claude/ is never removed.
        for directory in sorted(created_dirs, reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass

    assert members, "sdist contained no members"
    return members


def _strip_root(members: list[tarfile.TarInfo]) -> list[str]:
    """Drop the `jdocmunch_mcp-<version>/` prefix every member carries."""
    stripped = []
    for member in members:
        _, _, rest = member.name.partition("/")
        if rest:
            stripped.append(rest)
    return stripped


def _root_files(members: list[tarfile.TarInfo]) -> set[str]:
    return {
        name
        for name in _strip_root(members)
        if name and "/" not in name and not name.endswith("/")
    }


def test_no_canary_survives_the_exclusion(built_sdist):
    """The whole point: a planted file under an excluded path must not ship."""
    leaked = [m.name for m in built_sdist if CANARY_NAME in m.name]
    assert not leaked, (
        "sdist exclude rules did not hold; these canaries shipped:\n"
        + "\n".join(leaked)
        + "\n\nFix [tool.hatch.build.targets.sdist] exclude in pyproject.toml."
    )


@pytest.mark.parametrize("excluded", EXCLUDED_DIRS)
def test_excluded_directory_contributes_nothing(built_sdist, excluded):
    """Beyond the canary, no member may live under an excluded directory."""
    prefix = excluded + "/"
    leaked = [n for n in _strip_root(built_sdist) if n.startswith(prefix)]
    assert not leaked, (
        f"{len(leaked)} member(s) shipped from excluded {excluded!r}, e.g.:\n"
        + "\n".join(leaked[:10])
    )


@pytest.mark.parametrize("required", REQUIRED_PATHS)
def test_sdist_still_contains_what_rebuilds_the_project(built_sdist, required):
    """Non-vacuity in the other direction.

    An over-broad exclude would satisfy every assertion above by shipping
    almost nothing.
    """
    assert required in _strip_root(built_sdist), (
        f"{required!r} is missing from the sdist. An exclude rule is too broad "
        "-- an sdist must be able to rebuild what the repo builds."
    )


# --------------------------------------------------------------------------- #
# Root-level allowlist. The exclusion rules above answer "did a KNOWN bad path
# get in"; this answers "did anything get in that nobody decided on".
# --------------------------------------------------------------------------- #

#: Every file permitted at the sdist ROOT. Adding one is a decision someone
#: writes down -- which is the whole mechanism.
#:
#: ⚠ The canary tests above could not catch a scratch file and never could:
#: they prove that NAMED bad paths are absent, and a scratch file has no name
#: to plant a canary under. An allowlist catches the class, a denylist catches
#: the instance.
#:
#: ⚠ Build release notes OUTSIDE the repository (the scratchpad, `tmp_path`,
#: anywhere `git add -A` cannot reach). A `.gitignore` entry also works and is
#: strictly weaker: it protects only the spelling someone remembered.
ALLOWED_ROOT_FILES = frozenset({
    # Packaging + metadata
    "PKG-INFO", "pyproject.toml", "server.json", "LICENSE",
    ".gitignore", "SECURITY.md", "CONTRIBUTING.md",
    # Documentation shipped to users
    "README.md", "CHANGELOG.md", "USER_GUIDE.md", "ARCHITECTURE.md",
    "ROADMAP.md", "SPEC.md", "TOKEN_SAVINGS.md",
    "jDocMunch_Whitepaper_Final_v2.pdf",
    # Agent-facing
    "CLAUDE.md",
})


def test_no_unexpected_file_at_the_sdist_root(built_sdist):
    """⚠⚠ An allowlist, because a scratch file has no name to plant a canary
    under.

    `relnotes.md` shipped in jcm 1.108.305 this way. The failure mode is a
    stray root file swept up by `git add -A` during a release -- notes, a log,
    a profile dump, a copied config. None of them have a fixed name, so only a
    positive list can see them.
    """
    unexpected = sorted(_root_files(built_sdist) - ALLOWED_ROOT_FILES - {CANARY_NAME})
    assert not unexpected, (
        f"unexpected file(s) at the sdist root: {unexpected}. "
        "If one belongs in the distribution, add it to ALLOWED_ROOT_FILES with "
        "a reason. If it is scratch, delete it and build release notes outside "
        "the repository -- PyPI cannot be re-uploaded."
    )


def test_the_allowlist_is_not_stale(built_sdist):
    """⚠ The reverse: an allowlist naming files that no longer ship rots
    quietly.

    A stale entry re-opens the hole it was meant to close, because the next
    reader trusts the list to describe reality. It is also what catches a
    wholesale copy of a sibling repo's list (jcm's carries `uv.lock`,
    `Dockerfile` and two dozen root docs this repo does not have).
    """
    gone = sorted(ALLOWED_ROOT_FILES - _root_files(built_sdist))
    assert not gone, (
        f"ALLOWED_ROOT_FILES names file(s) the sdist no longer carries: {gone}. "
        "Remove them; a list that does not match the artifact is not a guard."
    )


def test_no_oversized_member_ships(built_sdist):
    """⚠⚠ Inspect the artifact's LARGEST entries, not just its file list.

    `tests/infographic.png` -- 5.9 MB, referenced by nothing -- was 87% of this
    source distribution until 1.123.2. It was a TRACKED file, so exclusion
    rules and untracked-file scans were both blind to it, and nothing asserted
    a size budget. This is that budget.
    """
    oversized = sorted(
        (m.size, m.name.partition("/")[2])
        for m in built_sdist
        if m.isfile()
        and m.size > MAX_MEMBER_BYTES
        and m.name.partition("/")[2] not in OVERSIZED_EXEMPT
    )
    assert not oversized, (
        "sdist member(s) over "
        f"{MAX_MEMBER_BYTES:,} bytes: "
        + ", ".join(f"{name} ({size:,} B)" for size, name in oversized)
        + ". If the file belongs in the distribution, add it to "
        "OVERSIZED_EXEMPT with a reason. Never raise the cap to fit a file."
    )
