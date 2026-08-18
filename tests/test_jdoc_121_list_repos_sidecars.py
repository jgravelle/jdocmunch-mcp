"""jdoc#121 - ``list_repos`` must not parse index-owned sidecars.

Reported by @rknighton against 1.133.0 with a controlled 75-index timing
comparison: adding only the 300 auxiliary JSON sidecars owned by those same
indexes moved the median from 2,044 ms to 3,460 ms (ranges did not overlap)
and added 300 json loads. The candidate glob was ``*/*.json`` with only ``_``
and ``.summary.json`` excluded, so every ``.terms``/``.related``/
``.boilerplate``/``.duplicates`` sidecar was opened, parsed, and discarded.

WARNING These tests observe JSON LOADS, not the returned rows. The
primary-absent case returns zero repositories both before and after the fix,
so a row-count assertion would pass against the unfixed code - the reporter
said so in the acceptance criteria and they were right.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from jdocmunch_mcp.storage.doc_store import (
    INDEX_OWNED_SIDECAR_SUFFIXES,
    DocStore,
)

AUX_SUFFIXES = (
    ".terms.json",
    ".related.json",
    ".boilerplate.json",
    ".duplicates.json",
)


def _write_index(storage: Path, owner: str, name: str) -> Path:
    """A primary monolith, its summary, and all four derived sidecars.

    Written directly rather than through ``index_local`` so the fixture is
    fast and cannot drift with summarizer/embedding defaults. The shape is
    what matters: ``list_repos`` keys on filenames and on two ``len()``s.
    """
    owner_dir = storage / owner
    owner_dir.mkdir(parents=True, exist_ok=True)
    index_path = owner_dir / f"{name}.json"
    index_path.write_text(json.dumps({
        "repo": f"{owner}/{name}",
        "sections": [{"id": "s1"}],
        "doc_paths": {"guide.md": ["s1"]},
        "index_version": 3,
        "corpus_identity_version": 1,
        "indexed_at": "2026-08-18T00:00:00Z",
        "doc_types": {"md": 1},
        "source_path": f"/src/{name}",
    }), encoding="utf-8")
    (owner_dir / f"{name}.summary.json").write_text(json.dumps({
        "repo": f"{owner}/{name}",
        "section_count": 1,
        "doc_count": 1,
        "corpus_identity_version": 1,
        "indexed_at": "2026-08-18T00:00:00Z",
        "doc_types": {"md": 1},
        "source_path": f"/src/{name}",
    }), encoding="utf-8")
    for suffix in AUX_SUFFIXES:
        (owner_dir / f"{name}{suffix}").write_text("[]", encoding="utf-8")
    return index_path


def _list_repos_tracking_loads(storage: Path):
    """Run ``list_repos``, recording the basename of every file json-parsed."""
    real_load = json.load
    parsed: list[str] = []

    def tracked(file_obj, *args, **kwargs):
        parsed.append(Path(file_obj.name).name)
        return real_load(file_obj, *args, **kwargs)

    with patch("jdocmunch_mcp.storage.doc_store.json.load", tracked):
        rows = DocStore(str(storage)).list_repos()
    return parsed, [r.get("repo") for r in rows]


# --- the reporter's two cases ----------------------------------------------

def test_live_index_sidecars_are_not_parsed(tmp_path):
    """Case A: the index is present; its four sidecars must not be opened."""
    storage = tmp_path / "store"
    _write_index(storage, "local", "sidecar-control")

    parsed, repos = _list_repos_tracking_loads(storage)

    assert [n for n in parsed if n.endswith(AUX_SUFFIXES)] == []
    assert repos == ["local/sidecar-control"]
    # The summary is what it SHOULD read, and the monolith is what jdoc#77
    # exists to avoid.
    assert parsed == ["sidecar-control.summary.json"]


def test_orphaned_sidecars_are_not_parsed(tmp_path):
    """Case B: no primary, no summary, four sidecars - and none is parsed.

    This is the case a candidate filter keyed on "does the primary exist"
    would leave untouched. Reachable on any store written before 1.108.0,
    where ``delete_index`` did not yet remove index-owned sidecars.
    """
    storage = tmp_path / "store"
    index_path = _write_index(storage, "local", "orphan-control")
    index_path.unlink()
    index_path.with_name("orphan-control.summary.json").unlink()

    parsed, repos = _list_repos_tracking_loads(storage)

    assert parsed == []
    assert repos == []


def test_a_related_sidecar_is_never_opened_even_if_unreadable(tmp_path):
    """Non-vacuity from the other direction: a sidecar that CANNOT be parsed
    must not change the result, which is only true if it is never opened."""
    storage = tmp_path / "store"
    _write_index(storage, "local", "corrupt-sidecar")
    (storage / "local" / "corrupt-sidecar.related.json").write_text(
        "{ this is not json", encoding="utf-8"
    )

    parsed, repos = _list_repos_tracking_loads(storage)

    assert "corrupt-sidecar.related.json" not in parsed
    assert repos == ["local/corrupt-sidecar"]


# --- the discoverability the fix must not cost ------------------------------

def test_summary_backed_and_legacy_indexes_both_still_listed(tmp_path):
    """Both jdoc#77 paths survive: the summary path and the monolith fallback."""
    storage = tmp_path / "store"
    _write_index(storage, "local", "modern")
    legacy_path = _write_index(storage, "local", "legacy")
    # A summary written before corpus_identity_version existed falls through
    # to the monolith by design (jdoc#85 C1-09).
    legacy_path.with_name("legacy.summary.json").write_text(json.dumps({
        "repo": "local/legacy",
        "section_count": 1,
        "doc_count": 1,
        "indexed_at": "2026-08-18T00:00:00Z",
        "doc_types": {"md": 1},
        "source_path": "/src/legacy",
    }), encoding="utf-8")

    parsed, repos = _list_repos_tracking_loads(storage)

    assert sorted(repos) == ["local/legacy", "local/modern"]
    assert "legacy.json" in parsed, "the legacy fallback must still parse the monolith"
    assert "modern.json" not in parsed
    assert [n for n in parsed if n.endswith(AUX_SUFFIXES)] == []


def test_repo_whose_name_ends_in_a_sidecar_suffix_stays_listed(tmp_path):
    """WARNING Repo names may contain dots, so ``api.related`` writes its
    PRIMARY monolith to ``api.related.json`` - a bare suffix test would delete
    it from the listing. Its own ``.summary.json`` readmits it, at one stat and
    no parse; nothing writes a summary beside a real sidecar."""
    storage = tmp_path / "store"
    _write_index(storage, "local", "api.related")

    parsed, repos = _list_repos_tracking_loads(storage)

    assert repos == ["local/api.related"]
    # Its own derived sidecar (api.related.related.json) is still skipped.
    assert "api.related.related.json" not in parsed


def test_underscore_prefixed_files_are_still_skipped(tmp_path):
    """Control: the pre-existing ``_`` exclusion is unchanged."""
    storage = tmp_path / "store"
    _write_index(storage, "local", "kept")
    (storage / "local" / "_internal.json").write_text("{}", encoding="utf-8")

    parsed, repos = _list_repos_tracking_loads(storage)

    assert repos == ["local/kept"]
    assert "_internal.json" not in parsed


# --- anti-rot: the constant must cover every module that writes a sidecar ---

def test_every_writer_module_suffix_is_in_the_canonical_list():
    """A fifth sidecar cannot be added without joining
    ``INDEX_OWNED_SIDECAR_SUFFIXES``.

    Derived from the modules that WRITE the files, not restated here, so this
    fails when a writer is added or renamed rather than when someone remembers
    to update a list. jdoc#121 existed because the same tuple was hand-copied
    to three places and the copy that mattered was never written at all.
    """
    from jdocmunch_mcp.retrieval import boilerplate, dedup, glossary, related_persist

    writers = {
        "glossary": glossary._terms_path,
        "related_persist": related_persist._path,
        "boilerplate": boilerplate._path,
        "dedup": dedup._path,
    }
    missing = {}
    for module_name, path_fn in writers.items():
        filename = path_fn("/tmp/store", "local", "NAME").name
        assert filename.startswith("NAME"), (module_name, filename)
        suffix = filename[len("NAME"):]
        if suffix not in INDEX_OWNED_SIDECAR_SUFFIXES:
            missing[module_name] = suffix
    assert not missing, f"sidecar suffixes missing from the canonical list: {missing}"


def test_the_other_consumers_read_the_canonical_list():
    """``delete_index`` and ``_leftover_artifacts`` import the constant
    instead of restating it, which is what let jdoc#121 exist."""
    from jdocmunch_mcp.storage import doc_store as doc_store_mod
    from jdocmunch_mcp.tools import index_local as index_local_mod

    index_local_src = Path(index_local_mod.__file__).read_text(encoding="utf-8")
    assert "INDEX_OWNED_SIDECAR_SUFFIXES" in index_local_src
    assert ".boilerplate.json" not in index_local_src, "a hand-copied tuple came back"

    store_src = Path(doc_store_mod.__file__).read_text(encoding="utf-8")
    # Once to define it, once in delete_index, once in the glob-matching subset.
    assert store_src.count("INDEX_OWNED_SIDECAR_SUFFIXES") >= 3


def test_glob_matching_subset_excludes_the_jsonl_sidecar():
    """``list_repos`` globs ``*/*.json``; stat-ing for ``.embeddings.jsonl``
    would be dead work, and including it would be a silent lie about what the
    filter can see."""
    from jdocmunch_mcp.storage.doc_store import _SIDECAR_SUFFIXES_MATCHING_JSON_GLOB

    assert ".embeddings.jsonl" in INDEX_OWNED_SIDECAR_SUFFIXES
    assert ".embeddings.jsonl" not in _SIDECAR_SUFFIXES_MATCHING_JSON_GLOB
    assert all(s.endswith(".json") for s in _SIDECAR_SUFFIXES_MATCHING_JSON_GLOB)


@pytest.mark.parametrize("suffix", AUX_SUFFIXES)
def test_each_suffix_individually(tmp_path, suffix):
    """One sidecar alone, with no siblings to vouch for it, is still skipped.

    WARNING Guards against a filter that reasons from the sidecar SET
    (``.related`` is a sidecar because ``.terms`` sits beside it). The
    reporter's largest single file was 1.2 GB; a set-based rule would parse it
    once its peers were cleaned up by hand.
    """
    storage = tmp_path / "store"
    owner_dir = storage / "local"
    owner_dir.mkdir(parents=True)
    (owner_dir / f"lonely{suffix}").write_text("[]", encoding="utf-8")

    parsed, repos = _list_repos_tracking_loads(storage)

    assert parsed == []
    assert repos == []
