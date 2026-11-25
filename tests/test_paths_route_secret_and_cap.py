"""The ``paths`` route of ``index_local`` keeps the walk's guarantees.

jdoc#216: a file listed in ``paths``, or found under a listed directory, is
refused as a secret exactly when the folder walk would refuse it, with the
walk's warning. Directory-scoped patterns (``.docker/config.json``) match on
the ROOT-relative path, not a path relative to the listed directory.

jdoc#217: a ``paths`` call that drops files at ``max_files`` reports
``truncated: true`` with the true discovered count and the cap warning, like
the walk does (jdoc#15).

Both regression tests below are the reporter's repro scripts as assertions.
"""

from pathlib import Path

import pytest

from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.tools.index_local import _resolve_explicit_paths, index_local


def _index(root: Path, store: Path, **kw) -> dict:
    return index_local(
        str(root), name="docs", use_ai_summaries=False, use_embeddings=False,
        storage_path=str(store), **kw,
    )


def _stored(store: Path) -> list:
    index = DocStore(base_path=str(store)).load_index("local", "docs")
    return sorted(index.file_hashes) if index else []


def _cap_warnings(out: dict) -> int:
    return sum("max_files" in w for w in out.get("warnings", []))


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = (tmp_path / "docs").resolve()
    root.mkdir()
    store = (tmp_path / "store").resolve()
    monkeypatch.setenv("DOC_INDEX_PATH", str(store))
    return root, store


# --- jdoc#216: the secret check on the paths route ---------------------------


def test_listed_secret_file_is_refused_like_the_walk(env):
    root, store = env
    (root / "guide.md").write_text("# Guide\n\nPublic.\n", encoding="utf-8")
    (root / "secrets.yaml").write_text("api_key: not-a-real-key\n", encoding="utf-8")

    walk = _index(root, store)
    assert _stored(store) == ["guide.md"]
    assert "Skipped secret file: secrets.yaml" in walk.get("warnings", [])

    # The reporter's repro: the same store, then the file listed by name.
    _index(root, store, paths=["secrets.yaml"])
    assert _stored(store) == ["guide.md"]


def test_listed_secret_file_warns_with_the_walks_message(env, tmp_path):
    root, _store = env
    (root / "guide.md").write_text("# Guide\n\nPublic.\n", encoding="utf-8")
    (root / "secrets.yaml").write_text("api_key: not-a-real-key\n", encoding="utf-8")
    fresh = tmp_path / "fresh"

    listed = _index(root, fresh, paths=["secrets.yaml", "guide.md"])
    assert _stored(fresh) == ["guide.md"]
    assert "Skipped secret file: secrets.yaml" in listed.get("warnings", [])


def test_listed_secret_file_by_absolute_path_is_refused(env):
    root, store = env
    (root / "guide.md").write_text("# Guide\n\nPublic.\n", encoding="utf-8")
    (root / "secrets.yaml").write_text("api_key: not-a-real-key\n", encoding="utf-8")

    out = _index(root, store, paths=[str(root / "secrets.yaml"), "guide.md"])
    assert _stored(store) == ["guide.md"]
    assert "Skipped secret file: secrets.yaml" in out.get("warnings", [])


def test_listed_directory_matches_directory_scoped_secret_patterns(env):
    root, store = env
    (root / "guide.md").write_text("# Guide\n\nPublic.\n", encoding="utf-8")
    docker = root / ".docker"
    docker.mkdir()
    (docker / "config.json").write_text('{"auths": {}}\n', encoding="utf-8")
    (docker / "README.md").write_text("# Docker notes\n\nPublic.\n", encoding="utf-8")

    out = _index(root, store, paths=[".docker"])
    stored = _stored(store)
    assert ".docker/config.json" not in stored
    assert ".docker/README.md" in stored
    assert "Skipped secret file: .docker/config.json" in out.get("warnings", [])


def test_listed_parent_directory_matches_nested_secret_pattern(env):
    root, store = env
    gh = root / ".config" / "gh"
    gh.mkdir(parents=True)
    (gh / "hosts.yml").write_text("github.com:\n  oauth_token: not-real\n", encoding="utf-8")
    (root / ".config" / "notes.md").write_text("# Notes\n\nPublic.\n", encoding="utf-8")

    for i, listed in enumerate(([".config"], [".config/gh"], [".config/gh/hosts.yml"])):
        store = env[1].parent / f"nested{i}"
        out = _index(root, store, paths=listed)
        assert ".config/gh/hosts.yml" not in _stored(store), listed
        assert "Skipped secret file: .config/gh/hosts.yml" in out.get("warnings", []), listed


# --- jdoc#217: truncation on the paths route ----------------------------------


def _five_pages(root: Path) -> None:
    (root / "sub").mkdir()
    for i in range(5):
        (root / "sub" / f"page{i}.md").write_text(f"# Page {i}\n\nText.\n", encoding="utf-8")


def test_paths_call_over_the_cap_reports_truncation_like_the_walk(env, tmp_path):
    root, _store = env
    _five_pages(root)

    walk = _index(root, tmp_path / "walk", max_files=2)
    listed = _index(root, tmp_path / "paths", max_files=2, paths=["sub"])

    for out in (walk, listed):
        assert out.get("truncated") is True
        assert out.get("discovered") == 5
        assert out.get("indexed") == 2
        assert _cap_warnings(out) == 1


def test_count_continues_past_the_cap_across_entries(env):
    root, _store = env
    _five_pages(root)
    (root / "other").mkdir()
    for i in range(3):
        (root / "other" / f"o{i}.md").write_text(f"# O {i}\n", encoding="utf-8")
    (root / "single.md").write_text("# Single\n", encoding="utf-8")

    files, _w, _req, _mt, discovered = _resolve_explicit_paths(
        root, ["sub", "other", "single.md"], max_files=2, follow_symlinks=False,
    )
    assert len(files) == 2
    assert discovered == 9

    out = _index(root, root.parent / "multi", max_files=2,
                 paths=["sub", "other", "single.md"])
    assert out.get("truncated") is True
    assert out.get("discovered") == 9
    assert out.get("indexed") == 2
    assert _cap_warnings(out) == 1


def test_overlapping_entries_are_counted_once(env):
    root, _store = env
    _five_pages(root)

    files, _w, _req, _mt, discovered = _resolve_explicit_paths(
        root, ["sub", "sub/page0.md", "sub"], max_files=100, follow_symlinks=False,
    )
    assert len(files) == 5
    assert discovered == 5

    files, _w, _req, _mt, discovered = _resolve_explicit_paths(
        root, ["sub", "sub/page0.md", "sub"], max_files=2, follow_symlinks=False,
    )
    assert len(files) == 2
    assert discovered == 5


def test_paths_call_under_the_cap_is_not_truncated(env, tmp_path):
    root, _store = env
    _five_pages(root)

    out = _index(root, tmp_path / "paths", max_files=10, paths=["sub"])
    assert not out.get("truncated")
    assert _cap_warnings(out) == 0
