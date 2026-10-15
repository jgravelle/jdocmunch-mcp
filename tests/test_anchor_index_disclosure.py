"""Duplicate explicit anchors, reported by the indexing entry points (v1.147.0).

``test_explicit_anchors.py`` pins ``index_local``'s full path. This file covers
the other return statements that index or re-check a corpus: ``index_local``'s
incremental and nothing-changed ones, every success shape of ``index_repo``, and
``index_file``. Each is a separate return statement, so each gets its own test:
a key missing from one fails nobody until a caller reads that shape.

``index_local`` has success responses that report an existing handle without
reading its index (worktree reuse, legacy-reconcile report). They carry no
duplicate keys, and nothing here claims they do.

``index_repo`` is exercised with the network names stubbed (the functions that
do the I/O, not a wrapper above them), so no HTTP request runs.
"""

import importlib

import pytest

from jdocmunch_mcp.tools.index_file import index_file
from jdocmunch_mcp.tools.index_local import index_local

DUP = "# Doc\n\n## First {#foo}\n\nA\n\n## Second {#foo}\n\nB\n"
CLEAN = "# Doc\n\n## First {#foo}\n\nA\n\n## Second {#bar}\n\nB\n"
OTHER = "# Other\n\nplain body text\n"
KEYS = {"duplicate_anchors", "duplicate_anchors_total", "duplicate_anchors_truncated",
        "duplicate_anchor_warning"}


def _assert_duplicate(out, doc_path="d.md"):
    assert KEYS <= set(out), sorted(out)
    assert len(out["duplicate_anchors"]) == 1
    dup = out["duplicate_anchors"][0]
    assert (dup["doc_path"], dup["anchor"], dup["count"]) == (doc_path, "foo", 2)
    assert "foo" in out["duplicate_anchor_warning"]
    assert doc_path in out["duplicate_anchor_warning"]


def _assert_clean(out):
    assert KEYS <= set(out), sorted(out)
    assert out["duplicate_anchors"] == []
    assert out["duplicate_anchor_warning"] == ""


# ---------------------------------------------------------------------------
# index_local: full, incremental and nothing-changed shapes
# ---------------------------------------------------------------------------


def _local(tmp_path, files):
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    for fname, body in files.items():
        (src / fname).write_text(body, encoding="utf-8")
    return src, str(tmp_path / "store")


def _run_local(src, store):
    return index_local(path=str(src), name="anchorinc", use_ai_summaries=False,
                       use_embeddings=False, storage_path=store)


def test_index_local_full_path_reports_both_cases(tmp_path):
    src, store = _local(tmp_path, {"d.md": DUP})
    out = _run_local(src, store)
    assert not out.get("incremental")
    _assert_duplicate(out)

    clean_src, clean_store = _local(tmp_path / "c", {"d.md": CLEAN})
    clean = _run_local(clean_src, clean_store)
    assert not clean.get("incremental")
    _assert_clean(clean)


def test_index_local_incremental_path_reports_the_whole_corpus(tmp_path):
    """The duplicate sits in a file this run does NOT touch. The incremental
    return statement reports the stored corpus, not just the files it parsed."""
    src, store = _local(tmp_path, {"d.md": DUP, "o.md": OTHER})
    first = _run_local(src, store)
    _assert_duplicate(first)

    (src / "o.md").write_text(OTHER + "\nmore\n", encoding="utf-8")
    out = _run_local(src, store)
    assert out["incremental"] is True
    assert (out["changed"], out["new"], out["deleted"]) == (1, 0, 0)
    _assert_duplicate(out)


def test_index_local_incremental_path_reports_clean_and_a_newly_fixed_file(tmp_path):
    src, store = _local(tmp_path, {"d.md": DUP, "o.md": OTHER})
    _run_local(src, store)

    (src / "d.md").write_text(CLEAN, encoding="utf-8")
    out = _run_local(src, store)
    assert out["incremental"] is True and out["changed"] == 1
    _assert_clean(out)


def test_index_local_incremental_path_reports_a_duplicate_it_just_introduced(tmp_path):
    src, store = _local(tmp_path, {"d.md": CLEAN, "o.md": OTHER})
    _assert_clean(_run_local(src, store))

    (src / "d.md").write_text(DUP, encoding="utf-8")
    out = _run_local(src, store)
    assert out["incremental"] is True and out["changed"] == 1
    _assert_duplicate(out)


def test_index_local_nothing_changed_still_reports_a_stored_duplicate(tmp_path):
    """A duplicate is as present on a run that changes nothing as on the run
    that found it, and absent keys would read as 'clean'."""
    src, store = _local(tmp_path, {"d.md": DUP, "o.md": OTHER})
    _run_local(src, store)
    out = _run_local(src, store)
    assert out["message"] == "No changes detected"
    _assert_duplicate(out)


def test_index_local_nothing_changed_clean_carries_empty_keys(tmp_path):
    src, store = _local(tmp_path, {"d.md": CLEAN, "o.md": OTHER})
    _run_local(src, store)
    out = _run_local(src, store)
    assert out["message"] == "No changes detected"
    _assert_clean(out)


# ---------------------------------------------------------------------------
# index_file: the single-file re-index behind `index-file` and the PostToolUse hook
# ---------------------------------------------------------------------------


def _indexed_folder(tmp_path, files):
    src = tmp_path / "anchorfile"
    src.mkdir()
    for fname, body in files.items():
        (src / fname).write_text(body, encoding="utf-8")
    store = str(tmp_path / "store")
    out = index_local(path=str(src), name="anchorfile", use_ai_summaries=False,
                      use_embeddings=False, storage_path=store)
    assert out["success"], out
    return src, store


def test_index_file_reports_a_duplicate_in_the_file_it_wrote(tmp_path):
    src, store = _indexed_folder(tmp_path, {"d.md": CLEAN, "o.md": OTHER})
    (src / "d.md").write_text(DUP, encoding="utf-8")
    out = index_file(str(src / "d.md"), storage_path=store, use_ai_summaries=False)
    assert out["success"], out
    _assert_duplicate(out)


def test_index_file_clean_carries_empty_keys(tmp_path):
    src, store = _indexed_folder(tmp_path, {"d.md": DUP, "o.md": OTHER})
    (src / "d.md").write_text(CLEAN, encoding="utf-8")
    out = index_file(str(src / "d.md"), storage_path=store, use_ai_summaries=False)
    assert out["success"], out
    _assert_clean(out)


def test_index_file_scopes_the_report_to_the_file_it_wrote(tmp_path):
    """A single-file re-index reports the file it wrote, so a duplicate elsewhere
    in the corpus is not repeated on every unrelated edit. Pinned so the scope is
    a decision."""
    src, store = _indexed_folder(tmp_path, {"d.md": DUP, "o.md": OTHER})
    (src / "o.md").write_text(OTHER + "\nmore\n", encoding="utf-8")
    out = index_file(str(src / "o.md"), storage_path=store, use_ai_summaries=False)
    assert out["success"], out
    _assert_clean(out)


# ---------------------------------------------------------------------------
# doc_index_repo: full, incremental and both no-change shapes
# ---------------------------------------------------------------------------

HEAD_A = "a" * 40
HEAD_B = "b" * 40
HEAD_C = "c" * 40


@pytest.fixture
def repo_env(tmp_path, monkeypatch):
    """Stub the network functions index_repo calls. ``state`` is mutated between
    runs to change what the 'repository' holds and which commit is HEAD."""
    mod = importlib.import_module("jdocmunch_mcp.tools.index_repo")
    state = {"sha": HEAD_A, "files": {"d.md": CLEAN, "o.md": OTHER}}

    async def fake_sha(owner, repo, token=None, client=None, ref="HEAD"):
        return state["sha"]

    async def fake_commit(owner, repo, token=None, client=None, ref="HEAD"):
        return state["sha"], "2026-09-20T13:15:39+00:00"

    async def fake_tree(owner, repo, token=None, client=None, ref="HEAD"):
        return [{"type": "blob", "path": p, "size": 64} for p in state["files"]]

    async def fake_gitignore(owner, repo, token=None, client=None, ref="HEAD"):
        return None

    async def fake_content(owner, repo, path, token=None, client=None, ref="HEAD"):
        return state["files"][path]

    monkeypatch.setattr(mod, "fetch_head_commit_sha", fake_sha)
    monkeypatch.setattr(mod, "fetch_head_commit", fake_commit)
    monkeypatch.setattr(mod, "fetch_repo_tree", fake_tree)
    monkeypatch.setattr(mod, "fetch_gitignore", fake_gitignore)
    monkeypatch.setattr(mod, "fetch_file_content", fake_content)

    async def run():
        return await mod.index_repo(
            "octo/docs", use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path / "store"),
        )

    return state, run


@pytest.mark.asyncio
async def test_repo_full_path_reports_a_duplicate(repo_env):
    state, run = repo_env
    state["files"] = {"d.md": DUP, "o.md": OTHER}
    out = await run()
    assert out["success"] and not out.get("incremental")
    _assert_duplicate(out)


@pytest.mark.asyncio
async def test_repo_full_path_clean_carries_empty_keys(repo_env):
    _, run = repo_env
    out = await run()
    assert out["success"] and not out.get("incremental")
    _assert_clean(out)


@pytest.mark.asyncio
async def test_repo_incremental_path_reports_the_whole_corpus(repo_env):
    state, run = repo_env
    state["files"] = {"d.md": DUP, "o.md": OTHER}
    _assert_duplicate(await run())

    state["sha"] = HEAD_B
    state["files"] = {"d.md": DUP, "o.md": OTHER + "\nmore\n"}
    out = await run()
    assert out["incremental"] is True and out["changed"] == 1
    _assert_duplicate(out)


@pytest.mark.asyncio
async def test_repo_incremental_path_clean_carries_empty_keys(repo_env):
    state, run = repo_env
    state["files"] = {"d.md": DUP, "o.md": OTHER}
    await run()

    state["sha"] = HEAD_B
    state["files"] = {"d.md": CLEAN, "o.md": OTHER}
    out = await run()
    assert out["incremental"] is True and out["changed"] == 1
    _assert_clean(out)


@pytest.mark.asyncio
async def test_repo_sha_unchanged_fast_path_still_reports_a_stored_duplicate(repo_env):
    state, run = repo_env
    state["files"] = {"d.md": DUP, "o.md": OTHER}
    await run()
    out = await run()
    assert out["message"].startswith("No changes detected (HEAD SHA unchanged)")
    _assert_duplicate(out)


@pytest.mark.asyncio
async def test_repo_sha_unchanged_fast_path_clean_carries_empty_keys(repo_env):
    _, run = repo_env
    await run()
    out = await run()
    assert out["message"].startswith("No changes detected (HEAD SHA unchanged)")
    _assert_clean(out)


@pytest.mark.asyncio
async def test_repo_new_commit_same_content_still_reports_a_stored_duplicate(repo_env):
    """HEAD moved but no document changed: the other no-change return statement."""
    state, run = repo_env
    state["files"] = {"d.md": DUP, "o.md": OTHER}
    await run()
    state["sha"] = HEAD_C
    out = await run()
    assert out["message"] == "No changes detected"
    _assert_duplicate(out)


@pytest.mark.asyncio
async def test_repo_new_commit_same_content_clean_carries_empty_keys(repo_env):
    state, run = repo_env
    await run()
    state["sha"] = HEAD_C
    out = await run()
    assert out["message"] == "No changes detected"
    _assert_clean(out)
