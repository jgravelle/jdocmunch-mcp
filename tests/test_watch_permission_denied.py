"""watch: an unreadable directory under one root must not stop the watch of every root.

Every locally-indexed root goes into ONE awatch call. Before the fix, awatch
raised PermissionError as soon as it met a directory it could not read under
any of them (seen where a link out of an indexed root reached `/boot/efi`), so
no root was watched at all and edits in a readable repo were never re-indexed.

Acceptance: with an unreadable directory under root A, editing a document in
root B updates B's index.

The watcher runs as the real `jdocmunch-mcp watch` in a subprocess and is
killed at the end: before the fix the loop also restarted at once after the
error, which blocks an in-process event loop, so an in-process test hangs
instead of failing.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="needs POSIX permissions that bind (not root)",
)

SRC = Path(__file__).resolve().parents[1] / "src"


def _index(path, name, store):
    from jdocmunch_mcp.tools.index_local import index_local
    return index_local(path=str(path), name=name, use_embeddings=False,
                       use_ai_summaries=False, storage_path=str(store))


def _section_hashes(store, name):
    from jdocmunch_mcp.storage.doc_store import DocStore
    index = DocStore(base_path=str(store)).load_index("local", name)
    return sorted(s["content_hash"] for s in index.sections)


def test_an_unreadable_directory_under_one_root_does_not_stop_the_others(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    a = tmp_path / "a"
    (a / "locked").mkdir(parents=True)
    (a / "README.md").write_text("# A\n\nRoot A.\n", encoding="utf-8")
    b = tmp_path / "b"
    b.mkdir()
    doc = b / "README.md"
    doc.write_text("# B\n\nBefore the edit.\n", encoding="utf-8")
    _index(a, "a", store)
    _index(b, "b", store)
    before = _section_hashes(store, "b")

    (a / "locked").chmod(0)
    env = {**os.environ, "DOC_INDEX_PATH": str(store),
           "PYTHONPATH": os.pathsep.join([str(SRC), os.environ.get("PYTHONPATH", "")])}
    watcher = subprocess.Popen(
        [sys.executable, "-m", "jdocmunch_mcp", "watch", "--no-ai-summaries", "--quiet"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(3.0)  # let the watcher start and set up its watches
        doc.write_text("# B\n\nAfter the edit.\n", encoding="utf-8")
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline and _section_hashes(store, "b") == before:
            time.sleep(0.2)
        assert _section_hashes(store, "b") != before, "the edit in root B was never re-indexed"
    finally:
        watcher.kill()
        watcher.wait(timeout=10)
        (a / "locked").chmod(0o755)
