"""jdoc#89 (rknighton pre-production QA): QA-06/QA-07 blockers + QA-08..QA-11.

QA-06: proof, capture, record publication, and guarded delete are one
coordinated lifecycle — the proof/capture gap, missing-fingerprint
authorization, and post-guard peer disappearance all resolve to conflict
with both handles loadable.

QA-07: cleanup requires the durable-record publication receipt; pending
claims are truthful; failed saves preserve the record; same-process
publishers never collide on one temp path.

QA-08..QA-11: stale-record self-heal, retained-side voiding (refresh and
direct delete), fsync durability.
"""

from __future__ import annotations

import os
import stat
import threading

import pytest

from jdocmunch_mcp.storage import doc_store as doc_store_module
from jdocmunch_mcp.storage import retirements
from jdocmunch_mcp.storage.doc_store import DocStore, RetirementConflict
from jdocmunch_mcp.tools import _worktree_corpus as wc
from tests import test_v1_109_0 as modern
from tests import test_v1_110_0 as legacy
from tests.test_v1_115_0 import _corrupt_stored_hash


def _begin_pair_retirement(store):
    ds = DocStore(base_path=str(store))
    fingerprints = {
        "local/old": ds.index_fingerprint("local", "old"),
        "local/modern": ds.index_fingerprint("local", "modern"),
    }
    assert retirements.begin_retirement(
        str(store), "local", "old",
        retained="local/modern", fingerprints=fingerprints,
        family="legacy_reconcile",
    )
    assert retirements.pending_retirement(str(store), "local", "old")
    return ds


# --- QA-06 -------------------------------------------------------------------

def test_qa06_proof_capture_gap_conflicts(tmp_path, monkeypatch):
    """A change landing at first fingerprint capture is re-proved, not
    absorbed into the accepted token."""
    repo, wt, _ = legacy._twin_repo(tmp_path)
    store = tmp_path / "store"
    assert legacy._index(repo / "docs", "established", store)["success"]
    modern._create_provisional(monkeypatch, wt / "docs", store, "provisional")

    real_fingerprint = DocStore.index_fingerprint
    fired = False

    def mutate_then_fingerprint(self, owner, name):
        nonlocal fired
        if not fired:
            fired = True
            _corrupt_stored_hash(store, "established")
        return real_fingerprint(self, owner, name)

    monkeypatch.setattr(DocStore, "index_fingerprint", mutate_then_fingerprint)
    out = legacy._index(wt / "docs", "provisional", store)
    assert out["success"], out
    assert out["reconciliation"]["reason_code"] != wc.REASON_RECONCILED
    assert "removed_handle" not in out["reconciliation"]
    ds = DocStore(base_path=str(store))
    assert ds.load_index("local", "provisional") is not None
    assert ds.load_index("local", "established") is not None


def test_qa06_none_expected_fingerprint_fails_closed(tmp_path, monkeypatch):
    """None never authorizes: expected None conflicts even when the current
    fingerprint is also None (missing handle)."""
    _, _, _, store = legacy._standard_pair(tmp_path, monkeypatch)
    ds = DocStore(base_path=str(store))
    with pytest.raises(RetirementConflict) as exc:
        ds.delete_index(
            "local", "old",
            expected_fingerprints={"local/old": None, "local/ghost": None},
        )
    assert set(exc.value.changed) == {"local/old", "local/ghost"}
    assert ds.load_index("local", "old") is not None


def test_qa06_retained_deleted_mid_cleanup_conflicts(tmp_path, monkeypatch):
    """A direct delete of the retained peer after the entry guard is caught
    by the pre-removal recheck — the retiring handle stays loadable and no
    missing handle is ever returned."""
    repo, wt, _ = legacy._twin_repo(tmp_path)
    store = tmp_path / "store"
    assert legacy._index(repo / "docs", "established", store)["success"]
    modern._create_provisional(monkeypatch, wt / "docs", store, "provisional")

    real_rmtree = doc_store_module.shutil.rmtree
    fired = False

    def remove_retained_then_continue(path, *args, **kwargs):
        nonlocal fired
        if not fired:
            fired = True
            assert DocStore(base_path=str(store)).delete_index(
                "local", "established"
            )
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(
        doc_store_module.shutil, "rmtree", remove_retained_then_continue
    )
    out = legacy._index(wt / "docs", "provisional", store)
    assert out["success"], out
    block = out["reconciliation"]
    assert block["reason_code"] == wc.REASON_GRADUATION_CONFLICT
    assert "removed_handle" not in block
    ds = DocStore(base_path=str(store))
    assert ds.load_index("local", "provisional") is not None
    assert out["repo"] == "local/provisional"


# --- QA-07 -------------------------------------------------------------------

def test_qa07_no_cleanup_without_publication_receipt(tmp_path, monkeypatch):
    """Record publication failure stops the retirement before any removal,
    and the response never claims pending work that has no record."""
    _, wt, _, store = legacy._standard_pair(tmp_path, monkeypatch)
    # Block <owner>/.retirements from ever becoming a directory.
    (store / "local" / ".retirements").write_text("blocked", encoding="utf-8")

    def fail_cleanup(path, *args, **kwargs):  # must never be reached
        raise AssertionError("cleanup started without a publication receipt")

    monkeypatch.setattr(doc_store_module.shutil, "rmtree", fail_cleanup)
    out = legacy._index(wt / "docs", "old", store, legacy_reconcile="apply")
    assert out["success"], out
    block = out["legacy_reconciliation"]
    assert block["reason_code"] == wc.REASON_LEGACY_CLEANUP_INCOMPLETE
    assert "pending_retirement" not in block
    ds = DocStore(base_path=str(store))
    assert ds.load_index("local", "old") is not None
    assert ds.load_index("local", "modern") is not None


def test_qa07_failed_save_preserves_record(tmp_path, monkeypatch):
    """A save that fails before its atomic replace leaves the still-pending
    retirement discoverable instead of erasing its record."""
    _, _, _, store = legacy._standard_pair(tmp_path, monkeypatch)
    ds = _begin_pair_retirement(store)
    before = ds._index_path("local", "old").read_bytes()

    from jdocmunch_mcp.retrieval import bm25, posting_cache

    def fail_before_write(*_args, **_kwargs):
        raise OSError("injected save failure")

    # v1.146.0: save_index derives the stats block from the posting rows when
    # the row cache is on and from compute_corpus_stats when it is not. Both
    # seams are blocked so this stays an assertion about what a FAILED SAVE
    # leaves behind, not about which producer happens to run.
    monkeypatch.setattr(bm25, "compute_corpus_stats", fail_before_write)
    monkeypatch.setattr(posting_cache.RowSet, "build", fail_before_write)
    with pytest.raises(OSError):
        ds.save_index(
            "local", "old",
            sections=[], raw_files={}, doc_types={}, file_hashes={},
        )
    assert ds._index_path("local", "old").read_bytes() == before
    assert retirements.pending_retirement(str(store), "local", "old")


def test_qa07_same_process_publishers_never_share_a_temp_path(tmp_path):
    """Two same-process publishers of one record both succeed — the temp
    name is unique per publication, not per PID."""
    owner_dir = tmp_path / "local"
    owner_dir.mkdir()
    for name in ("same", "first", "second"):
        (owner_dir / f"{name}.json").write_text("{}", encoding="utf-8")
    store = DocStore(base_path=str(tmp_path))
    results = []
    barrier = threading.Barrier(2)

    def publish(label):
        barrier.wait(timeout=10)
        results.append(retirements.begin_retirement(
            str(tmp_path), "local", "same",
            retained=f"local/{label}",
            fingerprints={
                "local/same": store.index_fingerprint("local", "same"),
                f"local/{label}": store.index_fingerprint("local", label),
            },
            family=label,
        ))

    threads = [
        threading.Thread(target=publish, args=(lbl,))
        for lbl in ("first", "second")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert len(results) == 2
    assert all(
        isinstance(publication, str) and publication
        for publication in results
    )
    assert results[0] != results[1]


# --- QA-08..QA-11 ------------------------------------------------------------

def test_qa08_completed_deletion_never_reported_pending(tmp_path, monkeypatch):
    """Failed cleanup stays visible, then a fresh read self-heals it."""
    from pathlib import Path

    _, _, _, store = legacy._standard_pair(tmp_path, monkeypatch)
    ds = _begin_pair_retirement(store)
    record_path = store / "local" / ".retirements" / "old.json"
    real_unlink = Path.unlink

    def fail_record_unlink(path, *args, **kwargs):
        if path == record_path:
            raise OSError("injected finalization failure")
        return real_unlink(path, *args, **kwargs)

    with monkeypatch.context() as cleanup_failure:
        cleanup_failure.setattr(Path, "unlink", fail_record_unlink)
        assert ds.delete_index("local", "old") is True
        assert ds.load_index("local", "old") is None
        pending = retirements.pending_retirement(
            str(store), "local", "old"
        )
        assert pending is not None
        assert pending["publication_id"]

    # A fresh recovery read self-heals once record cleanup is available.
    assert retirements.pending_retirement(str(store), "local", "old") is None


def test_qa09_retained_refresh_voids_record(tmp_path, monkeypatch):
    """Rewriting the retained peer stales the stored proof — the pending
    retirement is voided (fail-visible), not left dangling."""
    repo, _, _, store = legacy._standard_pair(tmp_path, monkeypatch)
    _begin_pair_retirement(store)
    (repo / "docs" / "guide.md").write_bytes(
        legacy.GUIDE_BYTES + b"\nretained refresh\n"
    )
    refreshed = legacy._index(repo / "docs", "modern", store)
    assert refreshed["success"], refreshed
    assert retirements.pending_retirement(str(store), "local", "old") is None


def test_qa10_retained_direct_delete_voids_record(tmp_path, monkeypatch):
    """Directly deleting the retained peer voids the retirement that could
    no longer complete as recorded."""
    _, _, _, store = legacy._standard_pair(tmp_path, monkeypatch)
    ds = _begin_pair_retirement(store)
    assert ds.delete_index("local", "modern") is True
    assert retirements.pending_retirement(str(store), "local", "old") is None
    assert ds.load_index("local", "old") is not None


def test_qa11_record_publication_fsyncs(tmp_path, monkeypatch):
    """The publication receipt promises power-loss durability: the record
    bytes are fsync'd before the atomic replace."""
    _, _, _, store = legacy._standard_pair(tmp_path, monkeypatch)
    fsync_targets = []
    real_fsync = retirements.os.fsync

    def record_fsync(fd):
        mode = os.fstat(fd).st_mode
        fsync_targets.append(
            "directory" if stat.S_ISDIR(mode) else "file"
        )
        return real_fsync(fd)

    monkeypatch.setattr(retirements.os, "fsync", record_fsync)
    ds = _begin_pair_retirement(store)
    assert "file" in fsync_targets
    if os.name != "nt":
        assert "directory" in fsync_targets
    assert ds.load_index("local", "old") is not None


def test_qa11_directory_fsync_failure_returns_no_receipt(
    tmp_path, monkeypatch
):
    _, _, _, store = legacy._standard_pair(tmp_path, monkeypatch)
    ds = DocStore(base_path=str(store))
    fingerprints = {
        "local/old": ds.index_fingerprint("local", "old"),
        "local/modern": ds.index_fingerprint("local", "modern"),
    }
    record_dir = store / "local" / ".retirements"
    real_open = retirements.os.open

    def fail_directory_open(path, flags, *args):
        if os.fspath(path) == os.fspath(record_dir):
            raise OSError("injected directory fsync open failure")
        return real_open(path, flags, *args)

    monkeypatch.setattr(
        retirements, "_POSIX_DIRECTORY_FSYNC", True, raising=False
    )
    monkeypatch.setattr(retirements.os, "open", fail_directory_open)
    publication = retirements.begin_retirement(
        str(store),
        "local",
        "old",
        retained="local/modern",
        fingerprints=fingerprints,
        family="legacy_reconcile",
    )

    assert publication is None
    assert ds.load_index("local", "old") is not None
    assert ds.load_index("local", "modern") is not None
