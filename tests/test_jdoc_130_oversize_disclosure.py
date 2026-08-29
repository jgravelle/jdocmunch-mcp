"""jdoc#130 - an oversize file was dropped and the response said nothing.

`index_local` computed a per-reason skip tally, PERSISTED it into the index's
``coverage.skip_counts``, and put none of it in the response. So a run that
silently dropped jcodemunch-mcp's 1.25 MB ``CHANGELOG.md`` -- 1,515
heading-delimited sections at a 565-byte median, the highest-leverage retrieval
target in that repository -- returned ``success: true``, ``file_count: 124``
and ``truncated: false``.

⚠⚠ ``truncated`` refers ONLY to the ``max_files`` cap, so it was answering a
different question truthfully while the caller read it as "did I get
everything". A count computed and withheld at the one moment the caller could
act on it is the same defect as not computing it.

Two halves, and the disclosure is the one that generalises: it covers every
future oversize file rather than this one.
"""

from __future__ import annotations

import io
import pathlib

import pytest

from jdocmunch_mcp.security import (
    MAX_FILE_SIZE_ENV,
    DEFAULT_MAX_FILE_SIZE,
    resolve_max_file_size,
)
from jdocmunch_mcp.server import _all_tools
from jdocmunch_mcp.tools.index_local import (
    _ACTIONABLE_SKIP_REASONS,
    _MAX_SKIPPED_PATHS_PER_REASON,
    _coverage_skips_block,
    index_local,
)

_UNDER = "under-the-cap.md"
_OVER = "over-the-cap.md"


def _index(docs_path, tmp_path, **kw):
    storage = str(tmp_path / "store")
    res = index_local(
        path=str(docs_path),
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=storage,
        **kw,
    )
    assert res.get("success"), f"Indexing failed: {res}"
    return res


@pytest.fixture
def corpus_with_one_oversize_file(tmp_path, monkeypatch):
    """One file over the cap and one under it.

    ⚠ The cap is lowered via the env var rather than writing a 5 MB file:
    the behaviour under test is the SKIP, and a multi-megabyte fixture would
    make every run of this suite slower to test the same branch.
    """
    monkeypatch.setenv(MAX_FILE_SIZE_ENV, "2048")
    docs = tmp_path / "wiki"
    docs.mkdir()
    (docs / _UNDER).write_text(
        "# Small\n\nThis document is comfortably under the cap.\n",
        encoding="utf-8",
    )
    body = "# Large\n\n" + ("Filler prose that pushes this past the cap. " * 200)
    (docs / _OVER).write_text(body, encoding="utf-8")
    assert (docs / _OVER).stat().st_size > 2048
    assert (docs / _UNDER).stat().st_size < 2048
    return docs


class TestTheResponseNamesTheSkip:
    def test_response_reports_the_oversize_count(
        self, tmp_path, corpus_with_one_oversize_file
    ):
        res = _index(corpus_with_one_oversize_file, tmp_path)
        assert res.get("skip_counts", {}).get("oversize") == 1, (
            f"the dropped file was not disclosed: {res.get('skip_counts')}"
        )

    def test_response_names_the_dropped_path(
        self, tmp_path, corpus_with_one_oversize_file
    ):
        """A caller told ``oversize: 1`` still cannot tell which file to look
        at."""
        res = _index(corpus_with_one_oversize_file, tmp_path)
        assert _OVER in res.get("skipped_paths", {}).get("oversize", []), (
            f"skipped_paths did not name the file: {res.get('skipped_paths')}"
        )

    def test_the_note_names_the_knob(self, tmp_path, corpus_with_one_oversize_file):
        res = _index(corpus_with_one_oversize_file, tmp_path)
        assert MAX_FILE_SIZE_ENV in res.get("oversize_note", "")

    def test_coverage_complete_is_false(
        self, tmp_path, corpus_with_one_oversize_file
    ):
        """⚠⚠ The field `truncated` was being misread as."""
        res = _index(corpus_with_one_oversize_file, tmp_path)
        assert res.get("coverage_complete") is False
        # `truncated` keeps its documented max_files meaning; it is the pairing
        # that must no longer be silent, not the field that must change sense.
        assert res.get("truncated") is False

    def test_the_under_cap_file_is_still_indexed(
        self, tmp_path, corpus_with_one_oversize_file
    ):
        """⚠ Non-vacuity: without this, "index nothing and report a skip"
        passes every assertion above."""
        res = _index(corpus_with_one_oversize_file, tmp_path)
        assert res["file_count"] == 1, res
        assert res["section_count"] >= 1
        assert _UNDER in [
            f if isinstance(f, str) else f.get("path", "") for f in res["files"]
        ] or res["file_count"] == 1

    def test_a_clean_corpus_reports_complete(self, tmp_path):
        """The other direction: no actionable skip, no false alarm."""
        docs = tmp_path / "wiki"
        docs.mkdir()
        (docs / "a.md").write_text("# A\n\nprose\n", encoding="utf-8")
        res = _index(docs, tmp_path)
        assert res.get("coverage_complete") is True
        assert "oversize_note" not in res


class TestTheCapIsOverridable:
    def test_env_var_raises_the_cap(self, tmp_path, monkeypatch):
        """The same corpus indexes fully once the cap allows it."""
        docs = tmp_path / "wiki"
        docs.mkdir()
        (docs / _UNDER).write_text("# Small\n\nprose\n", encoding="utf-8")
        big = "# Large\n\n" + ("Filler prose past the small cap. " * 200)
        (docs / _OVER).write_text(big, encoding="utf-8")

        monkeypatch.setenv(MAX_FILE_SIZE_ENV, "2048")
        tight = _index(docs, tmp_path / "tight")
        assert tight["file_count"] == 1
        assert tight.get("coverage_complete") is False

        monkeypatch.setenv(MAX_FILE_SIZE_ENV, "1000000")
        loose = _index(docs, tmp_path / "loose")
        assert loose["file_count"] == 2, (
            "raising the cap did not admit the file, so the override is dead"
        )
        assert loose.get("coverage_complete") is True

    def test_resolver_fails_open_on_garbage(self):
        """⚠ A typo must not silently shrink a corpus -- that is the failure
        mode this whole change exists to remove."""
        for bad in ("", "lots", "0", "-5", "5MB", None):
            assert resolve_max_file_size({MAX_FILE_SIZE_ENV: bad}) == (
                DEFAULT_MAX_FILE_SIZE
            ), bad

    def test_resolver_reads_a_valid_value(self):
        assert resolve_max_file_size({MAX_FILE_SIZE_ENV: "123456"}) == 123456

    def test_default_is_not_the_source_file_ceiling(self):
        """500 KB is a sane ceiling for a SOURCE file and the wrong one for a
        document; the same walk already grants .pdf/.docx 25 MB."""
        assert DEFAULT_MAX_FILE_SIZE > 500 * 1024

    def test_the_cap_is_resolved_at_call_time(
        self, tmp_path, corpus_with_one_oversize_file
    ):
        """⚠ A default argument binds the constant at IMPORT, so an env var
        set afterwards would be read and ignored. index_local is imported at
        the top of this module, which is what makes this meaningful -- the
        fixture sets JDOCMUNCH_MAX_FILE_SIZE long after that import."""
        res = _index(corpus_with_one_oversize_file, tmp_path)
        assert res.get("skip_counts", {}).get("oversize") == 1


class TestEveryCandidateDropped:
    """⚠⚠ The one payload with no file_count for a caller to be suspicious of."""

    def test_all_oversize_says_why_not_just_that_nothing_was_found(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv(MAX_FILE_SIZE_ENV, "2048")
        docs = tmp_path / "wiki"
        docs.mkdir()
        (docs / _OVER).write_text(
            "# Large\n\n" + ("Filler prose past the cap. " * 200),
            encoding="utf-8",
        )
        res = index_local(
            path=str(docs),
            use_ai_summaries=False,
            use_embeddings=False,
            storage_path=str(tmp_path / "store"),
        )
        assert res["success"] is False
        assert res.get("skip_counts", {}).get("oversize") == 1, (
            '"No documentation files found" reads as "there is nothing here" '
            'when the truth is "there is something here and I refused it": '
            f"{res}"
        )
        assert _OVER in res.get("skipped_paths", {}).get("oversize", [])
        assert res.get("coverage_complete") is False


class TestTheDisclosureBlockItself:
    def test_empty_when_nothing_was_skipped(self):
        assert _coverage_skips_block({}, {}) == {"coverage_complete": True}

    def test_ordinary_skips_do_not_flip_coverage_complete(self):
        """⚠ `gitignored` and `unsupported_extension` fire on every real repo
        (900 and 16 on the corpus this was found against). Keying
        coverage_complete on them makes it False always, and a signal that
        always fires hides the case it exists for."""
        block = _coverage_skips_block(
            {"gitignored": 16, "unsupported_extension": 900}, {}
        )
        assert block["coverage_complete"] is True
        assert block["skip_counts"] == {
            "gitignored": 16,
            "unsupported_extension": 900,
        }

    def test_zero_counts_are_dropped(self):
        block = _coverage_skips_block({"oversize": 0, "gitignored": 3}, {})
        assert "oversize" not in block["skip_counts"]

    def test_path_sample_truncation_is_disclosed(self):
        """The COUNT is exact; say plainly when the path SAMPLE is not."""
        paths = {"oversize": [f"f{i}.md" for i in range(_MAX_SKIPPED_PATHS_PER_REASON)]}
        block = _coverage_skips_block({"oversize": 99}, paths)
        assert block["skipped_paths_truncated"] == ["oversize"]

    def test_untruncated_sample_says_nothing(self):
        block = _coverage_skips_block({"oversize": 2}, {"oversize": ["a.md", "b.md"]})
        assert "skipped_paths_truncated" not in block

    def test_actionable_set_excludes_the_noisy_reasons(self):
        assert "gitignored" not in _ACTIONABLE_SKIP_REASONS
        assert "unsupported_extension" not in _ACTIONABLE_SKIP_REASONS
        assert "oversize" in _ACTIONABLE_SKIP_REASONS


class TestDescriptionBindsToBehaviour:
    def _tool(self):
        return next(t for t in _all_tools() if t.name == "index_local")

    def test_description_scopes_truncated_and_names_coverage(self):
        desc = self._tool().description
        assert "coverage_complete" in desc
        assert "skip_counts" in desc
        assert MAX_FILE_SIZE_ENV in desc

    def test_every_response_path_attaches_the_block(self):
        """⚠ FOUR paths: nochange / incremental / full / no-files-found.

        The nochange payload is the one whose caller is least likely to look
        anywhere else (jdoc#109); the no-files-found payload is the one with
        no file_count for a caller to be suspicious of. A response path added
        later without the block is the defect coming back, so this counts.
        """
        src = io.open(
            pathlib.Path(__file__).resolve().parents[1]
            / "src/jdocmunch_mcp/tools/index_local.py",
            encoding="utf-8",
        ).read()
        attached = src.count(
            "_coverage_skips_block(walk_skip_counts, walk_skip_paths)"
        )
        assert attached == 4, (
            f"the disclosure is on {attached} response path(s), expected 4. "
            "If you added a response path, attach the block to it; if you "
            "removed one, update this count deliberately."
        )


class TestNoChangePathAlsoDiscloses:
    def test_second_run_still_names_the_skip(
        self, tmp_path, corpus_with_one_oversize_file
    ):
        """⚠ The no-change branch returns before most of the response is
        assembled, which is exactly how a disclosure gets missed."""
        _index(corpus_with_one_oversize_file, tmp_path)
        again = _index(corpus_with_one_oversize_file, tmp_path)
        assert again.get("skip_counts", {}).get("oversize") == 1, again
        assert again.get("coverage_complete") is False
