"""jdoc#135 + #136: document recency.

#135 `index_repo` returns the change set with the head commit date as `mtime`.
#136 the time is stored in the index and `list_docs` returns it.

Design verdicts taken on the two questions the issues left open:

  - **Format.** `index_local` renders naive local time and shipped that way in
    1.142.0, so it cannot change on 1.x. `index_repo` emits an offset-aware ISO
    8601 string with a NUMERIC offset. ⚠⚠ Never a bare ``Z``:
    ``datetime.fromisoformat`` gained ``Z`` support in 3.11 and this package
    supports >=3.10, so a ``Z`` is unparseable on the oldest matrix job and on no
    developer box newer than it. Measured on 3.10.11.
  - **Stored, not stat.** `list_docs` reads a stored time, so one index has one
    meaning for the key. A stat would be fresher for a local index and would make
    `mtime` mean filesystem-time or commit-time depending on the index.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.tools.list_docs import list_docs


def _section(doc_path: str, title: str = "T"):
    from jdocmunch_mcp.parser.markdown_parser import parse_markdown
    return parse_markdown(f"# {title}\n\nbody text here\n", doc_path, "o/n")


def _save(store: DocStore, owner: str, name: str, files: dict, mtimes=None):
    sections = []
    for dp in files:
        sections += _section(dp)
    return store.save_index(
        owner=owner, name=name, sections=sections, raw_files=files,
        doc_types={".md": len(files)}, file_mtimes=mtimes,
    )


class TestStoredTimeSurvivesTheAllowList:
    """⚠⚠ `_index_to_dict` is an ALLOW-LIST, not asdict().

    A field added to the dataclass AND to every save/load signature still
    round-trips as empty until it is named in that serializer. The repo has
    already paid a debugging cycle for exactly this (jdoc#116), so it gets a
    test that reads the file back off disk rather than the in-memory object.
    """

    def test_file_mtimes_round_trips_through_disk(self, tmp_path):
        store = DocStore(base_path=str(tmp_path))
        mt = {"a.md": "2026-09-18T14:18:02"}
        _save(store, "o", "n", {"a.md": "# A\n\nbody\n"}, mtimes=mt)

        loaded = store.load_index("o", "n")
        assert loaded.file_mtimes == mt, "lost between save and load"

    def test_file_mtimes_is_named_in_the_serializer(self, tmp_path):
        """Read the JSON, not the dataclass: the object can be right and the
        file wrong, which is the whole failure mode."""
        store = DocStore(base_path=str(tmp_path))
        _save(store, "o", "n", {"a.md": "# A\n\nbody\n"},
              mtimes={"a.md": "2026-09-18T14:18:02"})
        path = Path(store._index_path("o", "n")) if hasattr(store, "_index_path") else None
        if path is None:
            path = next(Path(tmp_path).rglob("n.json"))
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "file_mtimes" in data, "not written to the monolith"
        assert data["file_mtimes"] == {"a.md": "2026-09-18T14:18:02"}

    def test_legacy_index_without_the_key_loads(self, tmp_path):
        """An index written before this change reads back as {}, not a failure."""
        store = DocStore(base_path=str(tmp_path))
        _save(store, "o", "n", {"a.md": "# A\n\nbody\n"})
        path = next(Path(tmp_path).rglob("n.json"))
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("file_mtimes", None)
        path.write_text(json.dumps(data), encoding="utf-8")
        store._index_cache_clear() if hasattr(store, "_index_cache_clear") else None
        fresh = DocStore(base_path=str(tmp_path))
        loaded = fresh.load_index("o", "n")
        assert loaded is not None
        assert loaded.file_mtimes == {}


class TestIncrementalSaveMergesTimes:
    def test_changed_updated_deleted_popped_untouched_preserved(self, tmp_path):
        store = DocStore(base_path=str(tmp_path))
        _save(store, "o", "n",
              {"keep.md": "# K\n\nk\n", "gone.md": "# G\n\ng\n", "edit.md": "# E\n\ne\n"},
              mtimes={"keep.md": "2026-01-01T00:00:00",
                      "gone.md": "2026-01-02T00:00:00",
                      "edit.md": "2026-01-03T00:00:00"})

        store.incremental_save(
            owner="o", name="n",
            changed_files=["edit.md"], new_files=["add.md"], deleted_files=["gone.md"],
            new_sections=_section("edit.md") + _section("add.md"),
            raw_files={"edit.md": "# E\n\nedited\n", "add.md": "# A\n\nadded\n"},
            doc_types={".md": 3},
            file_mtimes={"edit.md": "2026-02-01T00:00:00", "add.md": "2026-02-02T00:00:00"},
        )

        got = DocStore(base_path=str(tmp_path)).load_index("o", "n").file_mtimes
        assert got.get("keep.md") == "2026-01-01T00:00:00", "untouched file lost its time"
        assert got.get("edit.md") == "2026-02-01T00:00:00", "changed file kept a stale time"
        assert got.get("add.md") == "2026-02-02T00:00:00", "new file has no time"
        assert "gone.md" not in got, "deleted file kept its time"


class TestListDocsRecency:
    def test_mtime_returned_and_omitted_when_unknown(self, tmp_path):
        store = DocStore(base_path=str(tmp_path))
        _save(store, "o", "n", {"a.md": "# A\n\na\n", "b.md": "# B\n\nb\n"},
              mtimes={"a.md": "2026-09-18T14:18:02"})

        out = list_docs(repo="o/n", storage_path=str(tmp_path))
        by_path = {d["doc_path"]: d for d in out["docs"]}
        assert by_path["a.md"]["mtime"] == "2026-09-18T14:18:02"
        assert "mtime" not in by_path["b.md"], "absent time must omit the key, not emit null"

    def test_meta_discloses_how_many_documents_carry_a_time(self, tmp_path):
        """⚠ Without this a partially filled index, an index predating the
        change, and a complete one are indistinguishable to the caller."""
        store = DocStore(base_path=str(tmp_path))
        _save(store, "o", "n", {"a.md": "# A\n\na\n", "b.md": "# B\n\nb\n"},
              mtimes={"a.md": "2026-09-18T14:18:02"})

        out = list_docs(repo="o/n", storage_path=str(tmp_path))
        assert out["_meta"]["docs_with_mtime"] == 1
        assert out["doc_count"] == 2

    def test_legacy_index_reports_zero_rather_than_omitting_the_count(self, tmp_path):
        store = DocStore(base_path=str(tmp_path))
        _save(store, "o", "n", {"a.md": "# A\n\na\n"})
        out = list_docs(repo="o/n", storage_path=str(tmp_path))
        assert out["_meta"]["docs_with_mtime"] == 0, "a zero must be stated, not implied"


class TestCommitDateFormat:
    """#135's format verdict, pinned as a property rather than a spelling."""

    def test_github_z_is_converted_to_a_numeric_offset(self):
        from jdocmunch_mcp.tools._changes import normalize_commit_date
        got = normalize_commit_date("2026-09-20T13:15:39Z")
        assert got is not None
        assert not got.endswith("Z"), f"bare Z is unparseable on 3.10: {got!r}"
        assert got.endswith("+00:00"), got

    @pytest.mark.parametrize("raw", [
        "2026-09-20T13:15:39Z",
        "2026-09-20T13:15:39+00:00",
        "2026-09-20T13:15:39-05:00",
    ])
    def test_every_emitted_value_parses_with_fromisoformat(self, raw):
        """⚠⚠ The point of the whole format decision. This assertion passes on
        3.11+ even against a bare Z, so it is NOT sufficient on its own — the
        sibling test above pins the absence of Z, which is what fails on 3.10."""
        got = normalize = None
        from jdocmunch_mcp.tools._changes import normalize_commit_date
        got = normalize_commit_date(raw)
        assert datetime.fromisoformat(got) is not None

    def test_garbage_and_absence_yield_none_not_a_guess(self):
        from jdocmunch_mcp.tools._changes import normalize_commit_date
        for raw in (None, "", "not a date", 17):
            assert normalize_commit_date(raw) is None


class TestChangesHelpersAreShared:
    """#135 asks for one implementation, not two that drift."""

    def test_index_local_and_index_repo_import_the_same_helpers(self):
        import ast
        import inspect
        from jdocmunch_mcp.tools import _changes, index_local, index_repo

        assert index_local._build_changes_list is _changes.build_changes_list
        assert index_local._changes_fields is _changes.changes_fields

        # ⚠ A ratchet over the SOURCE, because importing the shared name and
        # then defining a local copy beside it would pass an identity check on
        # the import alone.
        for mod in (index_local, index_repo):
            tree = ast.parse(inspect.getsource(mod))
            defs = [n.name for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef)]
            assert "build_changes_list" not in defs, f"{mod.__name__} redefines it"
            assert "_build_changes_list" not in defs, f"{mod.__name__} redefines it"

    def test_cap_and_disclosure_are_identical_for_both_tools(self):
        from jdocmunch_mcp.tools._changes import CHANGES_CAP, changes_fields
        entries = [{"doc_path": f"{i}.md", "status": "new", "mtime": None}
                   for i in range(CHANGES_CAP + 5)]
        out = changes_fields(entries)
        assert len(out["changes"]) == CHANGES_CAP
        assert out["changes_total"] == CHANGES_CAP + 5
        assert out["changes_truncated"] is True
