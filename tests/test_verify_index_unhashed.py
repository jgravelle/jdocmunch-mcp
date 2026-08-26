"""An unverifiable section is not a verified one (2026-08-25).

`verify_index` compared a section's recomputed sha256 against its stored
`content_hash` like this::

    if expected_hash and actual_hash != expected_hash:
        drift.append(...)
    else:
        clean += 1

A section with NO stored hash has nothing to compare against, so it fell to the
`else` and was counted CLEAN. A caller gating CI on `drift_count == 0` would
read "we checked it and it was fine" where the truth is "we could not check it"
-- inside the one tool whose entire job is to certify integrity.

⚠⚠ The accounting invariant could not catch it. `clean + drift + missing +
error + skipped == section_count` still held, because the section was counted,
just in the wrong bucket. A consistency check over totals is blind to a
misfiled row -- the same reason a benchmark sync test that checks the grand
total misses the per-repo lines.

⚠ LATENT, and recorded as latent. Every shipped producer routes through
`compute_content_hash()`, which returns the sha256 of the empty string rather
than "", so no parser emits this today. But `Section.content_hash` DEFAULTS to
"" and the text parsers assign it at the end of a loop, so a producer that
returns early reintroduces it with no symptom. The fix does not depend on every
producer remembering.

`TestTheGuardIsWhatBlocks` reinstates the old comparison and asserts the false
clean count comes back -- without it, the tests below pass equally well against
a guard that never fires.
"""

import hashlib

import pytest

from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools import verify_index as vi
from jdocmunch_mcp.tools.verify_index import verify_index
from jdocmunch_mcp.storage import DocStore


@pytest.fixture
def indexed(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text(
        "# A\n\nbody of a\n\n## Sub\n\nmore text here\n", encoding="utf-8"
    )
    store = tmp_path / "store"
    out = index_local(
        path=str(src), name="fx", storage_path=str(store),
        use_ai_summaries=False, use_embeddings=False,
    )
    assert out.get("success"), out
    return {"repo": out["repo"], "storage": str(store)}


def _load(indexed):
    st = DocStore(base_path=indexed["storage"])
    owner, name = st._resolve_repo(indexed["repo"])
    return st, st.load_index(owner, name)


def _serve(monkeypatch, index):
    """Serve a mutated index without going through persistence."""
    class _Store(DocStore):
        def load_index(self, owner, name):
            return index
    monkeypatch.setattr(vi, "DocStore", _Store)


def _verifiable(index):
    """A section with a real byte range -- one the tool would actually hash."""
    for s in index.sections:
        if int(s.get("byte_end", 0) or 0) > int(s.get("byte_start", 0) or 0):
            return s
    raise AssertionError("fixture produced no verifiable section")


class TestTheProducerIsCurrentlyClean:
    """⚠ The premise of calling this LATENT. If this ever fails, the defect is
    live and the changelog entry above is understating it."""

    def test_every_ranged_section_ships_with_a_hash(self, indexed):
        _, index = _load(indexed)
        unhashed = [
            s["id"] for s in index.sections
            if int(s.get("byte_end", 0) or 0) > int(s.get("byte_start", 0) or 0)
            and not (s.get("content_hash") or "")
        ]
        assert unhashed == [], (
            f"a shipped parser emits verifiable sections with no hash: {unhashed}"
        )


class TestTheControl:
    """Without this, every assertion below could pass because nothing is ever
    counted clean."""

    def test_an_intact_index_verifies_clean(self, indexed):
        out = verify_index(indexed["repo"], storage_path=indexed["storage"])
        assert out["drift_count"] == 0
        assert out["clean_count"] > 0
        assert (
            out["clean_count"] + out["drift_count"] + out["missing_count"]
            + out["error_count"] + out["skipped_count"] == out["section_count"]
        )


class TestAnUnhashedSectionIsNotClean:
    def test_it_is_skipped_not_counted_clean(self, indexed, monkeypatch):
        _, index = _load(indexed)
        baseline = verify_index(indexed["repo"], storage_path=indexed["storage"])

        target = _verifiable(index)
        target["content_hash"] = ""
        _serve(monkeypatch, index)

        out = verify_index(indexed["repo"], storage_path=indexed["storage"])
        assert out["clean_count"] == baseline["clean_count"] - 1, (
            "a section with nothing to compare against was certified clean"
        )
        assert out["skipped_count"] == baseline["skipped_count"] + 1
        assert out["drift_count"] == 0, "unverifiable is not drift either"

    def test_it_says_which_section_and_why(self, indexed, monkeypatch):
        _, index = _load(indexed)
        target = _verifiable(index)
        target["content_hash"] = ""
        _serve(monkeypatch, index)

        out = verify_index(indexed["repo"], storage_path=indexed["storage"])
        reasons = {s["section_id"]: s["reason"] for s in out["skipped_sections"]}
        assert reasons.get(target["id"]) == "no_stored_hash", reasons

    def test_the_counters_still_sum(self, indexed, monkeypatch):
        """The invariant held while the row was misfiled, so it is not evidence
        on its own -- but it must not break either."""
        _, index = _load(indexed)
        _verifiable(index)["content_hash"] = ""
        _serve(monkeypatch, index)

        out = verify_index(indexed["repo"], storage_path=indexed["storage"])
        assert (
            out["clean_count"] + out["drift_count"] + out["missing_count"]
            + out["error_count"] + out["skipped_count"] == out["section_count"]
        )

    def test_a_real_mismatch_is_still_drift(self, indexed, monkeypatch):
        """The fix must not turn a genuine corruption into a skip."""
        _, index = _load(indexed)
        _verifiable(index)["content_hash"] = hashlib.sha256(b"not the body").hexdigest()
        _serve(monkeypatch, index)

        out = verify_index(indexed["repo"], storage_path=indexed["storage"])
        assert out["drift_count"] == 1, out
        assert out["skipped_count"] == verify_index(
            indexed["repo"], storage_path=indexed["storage"]
        )["skipped_count"]


class TestTheGuardIsWhatBlocks:
    """⚠⚠ NON-VACUITY, and it runs the PRE-FIX SOURCE rather than simulating it.

    An earlier draft of this class monkeypatched `hashlib.sha256` to return ""
    so that nothing would match. That broke every section at once and drove
    `clean_count` to 0 -- it did not reproduce the defect, it manufactured a
    different one, and it would have passed as "the guard fires" for the wrong
    reason. The honest version strips the guard out of the real module text and
    executes that.
    """

    @staticmethod
    def _prefix_module():
        """The module as it was before the fix: no `no_stored_hash` skip, and
        the comparison treating a falsy expected hash as a match."""
        import types
        from pathlib import Path

        src = Path(vi.__file__).read_text(encoding="utf-8")
        start = src.index("        if not expected_hash:")
        end = src.index('"reason": "no_stored_hash"}', start)
        NL = chr(10)
        end = src.index("continue" + NL, end) + len("continue" + NL)
        stripped = src[:start] + src[end:]
        stripped = stripped.replace(
            "        if actual_hash != expected_hash:",
            "        if expected_hash and actual_hash != expected_hash:",
            1,
        )
        # ⚠ Assert on the CODE, not the string: "no_stored_hash" also appears
        # in the module docstring, so a substring check passes vacuously.
        assert "if not expected_hash:" not in stripped, "guard not actually removed"
        assert "if expected_hash and actual_hash" in stripped, "old comparison not restored"

        mod = types.ModuleType("verify_index_prefix")
        # ⚠ `__package__` is what makes `from ..storage import DocStore` resolve;
        # without it the exec'd copy raises ImportError on a relative import.
        mod.__dict__.update(
            __file__=vi.__file__,
            __package__=vi.__package__,
            __name__="jdocmunch_mcp.tools.verify_index_prefix",
        )
        exec(compile(stripped, vi.__file__ + " (pre-fix)", "exec"), mod.__dict__)
        return mod

    def test_without_the_guard_the_defect_returns(self, indexed, monkeypatch):
        _, index = _load(indexed)
        baseline = verify_index(indexed["repo"], storage_path=indexed["storage"])

        target = _verifiable(index)
        target["content_hash"] = ""

        old = self._prefix_module()

        class _Store(DocStore):
            def load_index(self, owner, name):
                return index

        old.DocStore = _Store
        out = old.verify_index(indexed["repo"], storage_path=indexed["storage"])

        assert out["clean_count"] == baseline["clean_count"], (
            "the defect did not reproduce, so the assertions above are not "
            "evidence that the guard fires"
        )
        assert out["skipped_count"] == baseline["skipped_count"]
        assert not any(
            s["reason"] == "no_stored_hash" for s in out["skipped_sections"]
        ), "pre-fix source should not know that reason at all"
