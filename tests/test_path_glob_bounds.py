"""path_glob: the zero-directory ``**/`` match is bounded, exact and additive.

Follow-up to #163 (@vishalhabib99), which made ``**/`` match zero directories
by trying every combination of kept and dropped ``**/``. Review found three
things in that form, and this file pins the replacement against each:

1. 2**n ``fnmatch`` calls per section. 16 globstars measured 173 ms for one
   path that did not match, and the filter runs once per section.
2. Any ``**/`` was droppable, so ``api**/*.md`` began to match ``apix.md``.
   Only a ``**/`` that is a whole path segment is a globstar.
3. A pattern with no ``**/`` that did not match paid for a second, identical
   ``fnmatch``.
"""
from __future__ import annotations

import fnmatch
import itertools
import random
import re

import pytest

from jdocmunch_mcp.storage import doc_store as D
from jdocmunch_mcp.storage.doc_store import path_glob_matches


def _every_combination(doc_path: str, path_glob: str) -> bool:
    """#163's first form, kept here as the oracle for what a globstar means."""
    parts = path_glob.split("**/")
    for keep in itertools.product(("**/", ""), repeat=len(parts) - 1):
        candidate = parts[0] + "".join(sep + part for sep, part in zip(keep, parts[1:]))
        if fnmatch.fnmatch(doc_path, candidate):
            return True
    return False


_NOT_A_WHOLE_SEGMENT = re.compile(r"(?<!^)(?<!/)\*\*/")
_SEGMENTS = ["api", "v2", "x", "docs", "a", "auth.md", "users.md", "readme.md", "b.txt"]
_ATOMS = ["api/", "v2/", "x/", "docs/", "*/", "**/", "**/", "*.md", "*", "a?/",
          "auth.md", "[ab]/", "v*/"]


def _cases(count: int):
    rng = random.Random(163)
    produced = 0
    while produced < count:
        doc_path = "/".join(rng.choice(_SEGMENTS) for _ in range(rng.randint(1, 5)))
        path_glob = "".join(rng.choice(_ATOMS) for _ in range(rng.randint(1, 6)))
        if path_glob.count("**/") > 5 or _NOT_A_WHOLE_SEGMENT.search(path_glob):
            continue
        produced += 1
        yield doc_path, path_glob


def test_it_agrees_with_trying_every_combination():
    wrong = [(d, g) for d, g in _cases(8000)
             if path_glob_matches(d, g) != _every_combination(d, g)]
    assert wrong == []


def test_nothing_that_matched_plain_fnmatch_stops_matching():
    """The 1.x half: a glob can only gain paths, never lose one."""
    lost = [(d, g) for d, g in _cases(8000)
            if fnmatch.fnmatch(d, g) and not path_glob_matches(d, g)]
    assert lost == []


def test_a_glob_with_no_globstar_is_plain_fnmatch():
    rng = random.Random(1630)
    atoms = [a for a in _ATOMS if a != "**/"]
    for _ in range(4000):
        doc_path = "/".join(rng.choice(_SEGMENTS) for _ in range(rng.randint(1, 5)))
        path_glob = "".join(rng.choice(atoms) for _ in range(rng.randint(1, 6)))
        if "**/" in path_glob:
            continue  # two adjacent atoms ("*" then "*/") can spell one
        assert path_glob_matches(doc_path, path_glob) is fnmatch.fnmatch(doc_path, path_glob)


@pytest.mark.parametrize("doc_path,path_glob", [
    ("apix.md", "api**/*.md"),
    ("api.md", "api**/*.md"),
    ("docsx.md", "docs**/x.md"),
])
def test_a_globstar_must_be_a_whole_segment(doc_path, path_glob):
    assert _every_combination(doc_path, path_glob) is True  # what #163 first did
    assert fnmatch.fnmatch(doc_path, path_glob) is False    # what 1.145.2 did
    assert path_glob_matches(doc_path, path_glob) is False


class _Counting:
    def __init__(self, pattern, calls):
        self._pattern, self._calls = pattern, calls

    def match(self, *args):
        self._calls.append(1)
        return self._pattern.match(*args)


@pytest.mark.parametrize("path_glob", [
    "d0/" + "**/" * 16 + "zzz.md",
    "**/*/" * 8 + "zzz.md",
    "**/*/" * 40 + "zzz.md",
])
def test_the_work_is_bounded_by_pieces_and_segment_boundaries(monkeypatch, path_glob):
    """No timing here: count the regex calls against the documented bound."""
    doc_path = "/".join(f"d{i}" for i in range(20)) + "/page.md"
    calls: list = []
    real_compile = D._compile_path_glob.__wrapped__

    def counting_compile(glob):
        whole, pieces = real_compile(glob)
        return _Counting(whole, calls), tuple(_Counting(p, calls) for p in pieces)

    monkeypatch.setattr(D, "_compile_path_glob", counting_compile)
    D._path_glob_matches.cache_clear()

    assert path_glob_matches(doc_path, path_glob) is False

    pieces = len(D._GLOBSTAR_SEGMENT.split(path_glob))
    boundaries = doc_path.count("/") + 1
    assert len(calls) <= 1 + pieces * boundaries ** 2
    assert 2 ** path_glob.count("**/") > len(calls), "the combination form's cost"
    D._path_glob_matches.cache_clear()


def test_a_non_matching_plain_glob_costs_one_match(monkeypatch):
    calls: list = []
    real_compile = D._compile_path_glob.__wrapped__

    def counting_compile(glob):
        whole, pieces = real_compile(glob)
        assert pieces is None
        return _Counting(whole, calls), None

    monkeypatch.setattr(D, "_compile_path_glob", counting_compile)
    D._path_glob_matches.cache_clear()

    assert path_glob_matches("guide/intro.md", "api/*.md") is False
    assert len(calls) == 1
    D._path_glob_matches.cache_clear()
