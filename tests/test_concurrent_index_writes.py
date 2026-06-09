"""Regression tests for concurrent same-repo index writes.

jdocmunch rewrites the whole ``<name>.json`` index on every save. Before the
cross-process lock + per-PID temp name in ``DocStore.save_index`` /
``incremental_save``, two processes writing the same repo shared the
deterministic ``<name>.json.tmp`` with no lock, so ``os.replace`` could install
corrupt/partial JSON (the repo then reads as both "corrupt" -- ``load_index``
raises -- and "absent" -- ``list_repos`` drops it) or silently lose an update
(last-replace-wins).

These tests reproduce that race across real processes and assert it no longer
corrupts the index or drops updates.
"""
from __future__ import annotations

import json
import multiprocessing as mp

import pytest

from jdocmunch_mcp.parser import parse_file
from jdocmunch_mcp.storage.doc_store import DocStore

try:
    import fcntl  # noqa: F401

    _HAS_FLOCK = True
except ImportError:  # pragma: no cover - non-POSIX
    _HAS_FLOCK = False

_OWNER = "local"
_NAME = "concurrent"


def _join_all(procs, timeout: float = 120.0) -> None:
    """Join workers, terminate any straggler, then assert all exited cleanly."""
    for p in procs:
        p.join(timeout=timeout)
    for p in procs:
        if p.is_alive():  # pragma: no cover - only on a hang
            p.terminate()
            p.join(timeout=10)
    for p in procs:
        assert p.exitcode == 0, f"writer process failed (exitcode={p.exitcode})"


def _hammer_save(base_path: str, barrier, iters: int) -> None:
    store = DocStore(base_path=base_path)
    md = "# Root\n\nIntro.\n\n## A\n\nContent A.\n\n## B\n\nContent B.\n"
    sections = parse_file(md, "README.md", f"{_OWNER}/{_NAME}")
    raw_files = {"README.md": md}
    doc_types = {".md": 1}
    barrier.wait()  # start all writers together to maximize contention
    for _ in range(iters):
        store.save_index(_OWNER, _NAME, sections, raw_files, doc_types)


def _add_one_doc(base_path: str, barrier, i: int) -> None:
    store = DocStore(base_path=base_path)
    doc = f"doc{i}.md"
    md = f"# Doc {i}\n\nBody for doc {i} with enough text for a real section.\n"
    new_sections = parse_file(md, doc, f"{_OWNER}/{_NAME}")
    barrier.wait()
    store.incremental_save(
        _OWNER,
        _NAME,
        changed_files=[],
        new_files=[doc],
        deleted_files=[],
        new_sections=new_sections,
        raw_files={doc: md},
        doc_types={".md": 1},
    )


def test_concurrent_save_no_corruption(tmp_path):
    """N processes hammering save_index on the same repo never corrupt it.

    The per-PID temp name fixes this even without the lock, so it runs on every
    platform (including Windows where ``fcntl`` is unavailable).
    """
    base = str(tmp_path)
    n_procs, iters = 8, 15
    barrier = mp.Barrier(n_procs)
    procs = [mp.Process(target=_hammer_save, args=(base, barrier, iters)) for _ in range(n_procs)]
    for p in procs:
        p.start()
    _join_all(procs)

    # The on-disk index must be valid JSON (raises JSONDecodeError if corrupt).
    index_file = tmp_path / _OWNER / f"{_NAME}.json"
    assert index_file.exists()
    json.loads(index_file.read_text())

    # And it must still load + list cleanly.
    store = DocStore(base_path=base)
    assert store.load_index(_OWNER, _NAME) is not None
    assert any(r["repo"] == f"{_OWNER}/{_NAME}" for r in store.list_repos())

    # No leftover temp files after success.
    assert list((tmp_path / _OWNER).glob("*.tmp")) == []


@pytest.mark.skipif(
    not _HAS_FLOCK,
    reason="lost-update prevention requires the cross-process flock (POSIX-only)",
)
def test_concurrent_incremental_no_lost_update(tmp_path):
    """N processes each add a distinct doc via incremental_save; all survive.

    Without the cross-process lock the workers all read the same base index and
    last-replace-wins drops every addition but one. The flock serializes the
    read-modify-write so every doc lands. (POSIX-only: the lock no-ops where
    ``fcntl`` is unavailable, so the guarantee under test does not hold there.)
    """
    base = str(tmp_path)
    base_md = "# Base\n\nBase content.\n"
    DocStore(base_path=base).save_index(
        _OWNER,
        _NAME,
        parse_file(base_md, "base.md", f"{_OWNER}/{_NAME}"),
        {"base.md": base_md},
        {".md": 1},
    )

    n = 8
    barrier = mp.Barrier(n)
    procs = [mp.Process(target=_add_one_doc, args=(base, barrier, i)) for i in range(n)]
    for p in procs:
        p.start()
    _join_all(procs)

    loaded = DocStore(base_path=base).load_index(_OWNER, _NAME)
    assert loaded is not None
    doc_paths = set(loaded.doc_paths)
    expected = {"base.md"} | {f"doc{i}.md" for i in range(n)}
    missing = expected - doc_paths
    assert not missing, f"lost updates (last-writer-wins): missing {sorted(missing)}"
