"""Recurrence guard: a text-mode subprocess call must name its encoding.

``subprocess.run(..., text=True)`` with no ``encoding=`` decodes the child's
output with ``locale.getpreferredencoding()``. On a stock Windows box that is
**cp1252**. Git emits UTF-8 unconditionally.

The trigger here is not an exotic one. ``git rev-parse --show-toplevel`` prints
the repository's absolute path **raw and unquoted** — unlike ``status`` and
``ls-files``, which pass paths through ``core.quotepath`` and come back ASCII.
So the whole module fails for anyone whose checkout sits under a directory with
a non-ASCII character in it, which on Windows most often means their own user
name.

The failure mode is worse than a normal exception, which is why this needs a
test rather than a habit:

* ``UnicodeDecodeError`` is raised inside ``subprocess``'s **reader thread**,
  so it prints an unhandled thread traceback that no ``try/except`` around the
  call can catch.
* ``proc.stdout`` then comes back ``None`` and the caller dies on
  ``.strip()`` with ``'NoneType' object has no attribute 'strip'`` — a message
  naming neither git nor encoding.
* ``_git_root`` is the gateway every git-aware path in this package goes
  through, so the failure is not local to one feature.

⚠ **Measured on the reproduction**: a corpus checked out at
``...\\Ýcorpus`` (UTF-8 ``c3 9d``; ``9d`` is undefined in cp1252) made
``index-local`` return ``{"success": false, "error": "Indexing failed:
'NoneType' object has no attribute 'strip'"}``. Not a degraded index — no
index at all.

The rule: any call to ``run``/``Popen``/``check_output``/``check_call``/``call``
that passes ``text=`` or ``universal_newlines=`` must also pass **both**
``encoding=`` and ``errors=``.

⚠⚠ **``encoding="utf-8"`` ALONE DOES NOT CLOSE THIS, and until now the
assertion accepted it while the failure message asked for both.** Naming the
encoding only changes *which* codec raises. A child that emits a byte that is
not valid UTF-8 — any tool passing through a path it did not decode — produces
the identical failure described above. Measured on this box against a child
writing ``b"ok-\\x9d-end\\n"``::

    text=True                                   stdout=None, UnicodeDecodeError
                                                ('charmap') in Thread-3
    text=True, encoding="utf-8"                 stdout=None, UnicodeDecodeError
                                                ('utf-8') in Thread-3
    text=True, encoding="utf-8", errors="replace"   stdout='ok-\\ufffd-end\\n'

Only ``errors=`` keeps the reader thread alive and ``stdout`` non-``None``. So
the kwarg that actually prevents the incident was the one not being enforced.

⚠ **This is invisible to CI and to any UTF-8 development box.** CI is Linux.
The convention cannot be maintained by noticing, which is what the sweep that
produced this test demonstrated: it found 9 sites, not 1.
"""

import ast
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

# Trees that ship or run against a real user's repository.
GUARDED_TREES = ("src", "scripts", "benchmarks", "contrib")

# EXEMPT BY NAME, with a reason — not a blanket skip.
#
# `tests/` drives git against fixture repositories this suite creates itself,
# with ASCII authors and ASCII paths, so the kwarg would assert nothing about
# any code a user runs. `unused/` is retired code that ships to nobody. Both
# are listed here rather than merely left out of GUARDED_TREES so that adding
# a tree is a decision and not an oversight.
EXEMPT_TREES = ("tests", "unused")

# `unused/` is not tracked: it does not exist on a fresh clone, and the one
# thing that creates it is a side effect of another test
# (`DocStore(base_path="unused")` in test_issue95_qa19). An exempt tree is
# therefore allowed to be absent only if it is named here, so this file passes
# alone, in any order, and a tracked tree that disappears still fails.
EXEMPT_MAY_BE_ABSENT = ("unused",)

SUBPROCESS_FUNCS = {"run", "Popen", "check_output", "check_call", "call"}
TEXT_KWARGS = {"text", "universal_newlines"}

# Both are required. `encoding` alone leaves the reader thread able to raise —
# see the measurement in the module docstring. Order is the order they are
# reported in, so a message reads the way the fix should be typed.
REQUIRED_KWARGS = ("encoding", "errors")

# KNOWN GAP — a ratchet, not an exemption. Empty as of v1.121.1, when all 9
# offending call sites were fixed. Kept (empty) rather than deleted: it is the
# mechanism that forces a future gap to be declared here instead of shipping
# silently, and `test_known_gap_does_not_rot` fails if a listed entry turns out
# to be compliant, so a stale entry cannot decay into a permanent excuse.
KNOWN_UNENCODED: set[str] = set()


def _call_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def _offenders_in_source(source: str, label: str) -> list[tuple[str, tuple[str, ...]]]:
    """Return ``(site, missing)`` for each text-mode call missing a required kwarg.

    ``site`` stays the bare ``"label:lineno"`` that ``KNOWN_UNENCODED`` is keyed
    on, so declaring a gap does not mean transcribing which kwargs it was
    missing — that detail belongs in the failure message, not in the key.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) not in SUBPROCESS_FUNCS:
            continue
        kwargs = {k.arg for k in node.keywords if k.arg}
        if not (kwargs & TEXT_KWARGS):
            continue
        missing = tuple(k for k in REQUIRED_KWARGS if k not in kwargs)
        if not missing:
            continue
        out.append((f"{label}:{node.lineno}", missing))
    return out


def _offenders_in(path: pathlib.Path) -> list[tuple[str, tuple[str, ...]]]:
    return _offenders_in_source(
        path.read_text(encoding="utf-8", errors="replace"),
        path.relative_to(REPO_ROOT).as_posix(),
    )


def _guarded_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for tree in GUARDED_TREES:
        root = REPO_ROOT / tree
        if root.is_dir():
            files.extend(sorted(root.rglob("*.py")))
    return files


def _all_offenders() -> list[tuple[str, tuple[str, ...]]]:
    found: list[tuple[str, tuple[str, ...]]] = []
    for path in _guarded_files():
        found.extend(_offenders_in(path))
    return found


def test_guarded_trees_exist():
    """A renamed tree must break this test, not silently stop being scanned."""
    present = [t for t in GUARDED_TREES if (REPO_ROOT / t).is_dir()]
    assert "src" in present, "src/ must be scanned — it is the shipped package"
    assert _guarded_files(), "the guard scanned zero files; its scope is wrong"


def test_no_text_mode_subprocess_without_encoding():
    offenders = [o for o in _all_offenders() if o[0] not in KNOWN_UNENCODED]
    assert not offenders, (
        "text-mode subprocess call missing a required kwarg — decodes the "
        "child's output with the platform default (cp1252 on Windows), and "
        "raises in subprocess's reader thread on any byte the codec rejects, "
        "where no try/except around the call can catch it. stdout then comes "
        "back None. ⚠ encoding= alone is NOT enough: it only changes which "
        'codec raises. Add encoding="utf-8", errors="replace":\n  '
        + "\n  ".join(f"{site}  missing {', '.join(missing)}"
                      for site, missing in offenders)
    )


def test_known_gap_does_not_rot():
    """A listed gap that is actually fixed must be removed from the list."""
    live = {site for site, _ in _all_offenders()}
    stale = KNOWN_UNENCODED - live
    assert not stale, (
        "KNOWN_UNENCODED lists call sites that are now compliant; delete them "
        "so the set cannot decay into a permanent exemption: " + ", ".join(sorted(stale))
    )


def test_exempt_trees_are_named_not_forgotten():
    """The exemption is a decision on the record, and it must not overlap scope."""
    assert not (set(EXEMPT_TREES) & set(GUARDED_TREES))
    assert set(EXEMPT_MAY_BE_ABSENT) <= set(EXEMPT_TREES)
    for tree in EXEMPT_TREES:
        if tree in EXEMPT_MAY_BE_ABSENT:
            continue
        assert (REPO_ROOT / tree).is_dir(), f"exempt tree {tree!r} does not exist"


@pytest.mark.parametrize(
    "source,expected_hits",
    [
        ('subprocess.run(["git"], text=True)', 1),
        ('subprocess.run(["git"], universal_newlines=True)', 1),
        # ⚠ WAS 0 UNTIL THE errors= TIGHTENING. This row is the whole change:
        # the guard used to accept the form that still crashes.
        ('subprocess.run(["git"], text=True, encoding="utf-8")', 1),
        ('subprocess.run(["git"], text=True, errors="replace")', 1),
        ('subprocess.run(["git"], text=True, encoding="utf-8", errors="replace")', 0),
        ('subprocess.Popen(["git"], text=True)', 1),
        ('subprocess.check_output(["git"], text=True)', 1),
        ('run(["git"], text=True)', 1),
        ('subprocess.run(["git"], capture_output=True)', 0),  # bytes mode is safe
        ('other.run(["git"], text=True)', 1),  # attribute match is deliberate
    ],
)
def test_detector_is_not_vacuous(source, expected_hits):
    """The scanner itself has to actually fire — a guard that finds nothing on
    a known-bad input passes forever. This drives the SAME function the
    repo-wide check uses, so the two cannot diverge."""
    assert len(_offenders_in_source(source, "sample.py")) == expected_hits


@pytest.mark.parametrize(
    "source,missing",
    [
        ('subprocess.run(["git"], text=True)', ("encoding", "errors")),
        ('subprocess.run(["git"], text=True, encoding="utf-8")', ("errors",)),
        ('subprocess.run(["git"], text=True, errors="replace")', ("encoding",)),
    ],
)
def test_the_message_names_which_kwarg_is_missing(source, missing):
    """A guard that says "something is wrong here" costs a reader the diagnosis.

    Pinned separately from the hit COUNT because the count cannot distinguish
    "missing both" from "missing errors", which is exactly the distinction the
    tightening introduced.
    """
    found = _offenders_in_source(source, "sample.py")
    assert [m for _, m in found] == [missing]
