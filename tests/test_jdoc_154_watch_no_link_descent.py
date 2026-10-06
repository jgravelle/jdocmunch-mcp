"""watch: the watcher must not walk through a directory link out of a root.

jdoc#154 (@Indie-Siggi). `awatch(*roots)` in recursive mode follows directory
links on Linux inotify and under polling, before any filter runs, while
`discover_doc_files` walks with ``followlinks=False``. A root holding a link
to ``/`` made the watcher walk the readable filesystem. Measured on inotify
with watchfiles 1.3.0: a root of 2 directories with one link to
``/usr/share`` held 2,476 watches.

The fix hands awatch an enumerated set of real directories, non-recursively,
on those platforms. That changes how a NEW directory is noticed, so the
second half of this file is about that: a directory created under a root is
picked up, and a file written into it before its watch exists is indexed.
"""
import asyncio
import contextlib
import glob
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"


def _index(path, name, store):
    from jdocmunch_mcp.tools.index_local import index_local
    return index_local(path=str(path), name=name, use_embeddings=False,
                       use_ai_summaries=False, storage_path=str(store))


def _load(store, name):
    from jdocmunch_mcp.storage.doc_store import DocStore
    return DocStore(base_path=str(store)).load_index("local", name)


def _doc_paths(store, name):
    return {s["doc_path"] for s in _load(store, name).sections}


def _hashes(store, name):
    return sorted(s["content_hash"] for s in _load(store, name).sections)


def _link(target, link):
    """Make a directory link, or skip where this account may not."""
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("cannot create a directory symlink here")


def _corpus(tmp_path):
    """A root with one real subdirectory, and a tree outside it."""
    store = tmp_path / "store"
    store.mkdir()
    root = (tmp_path / "docs").resolve()
    (root / "sub").mkdir(parents=True)
    (root / "README.md").write_text("# Docs\n\nBefore.\n", encoding="utf-8")
    outside = (tmp_path / "outside").resolve()
    for i in range(25):
        (outside / f"d{i}").mkdir(parents=True)
    (outside / "d0" / "stray.md").write_text("# Stray\n\nNot ours.\n", encoding="utf-8")
    _index(root, "docs", store)
    return store, root, outside


# ── the enumeration ─────────────────────────────────────────────────────────


def test_the_census_never_enters_a_directory_link(tmp_path):
    from jdocmunch_mcp import watch as W

    _store, root, outside = _corpus(tmp_path)
    _link(outside, root / "outside")

    census = W._watch_directories([str(root)])

    assert set(census) == {str(root), str(root / "sub")}


def test_the_census_keeps_what_discovery_prunes(tmp_path):
    """Pruning less than discovery costs watches; pruning more loses updates."""
    from jdocmunch_mcp import watch as W

    _store, root, _outside = _corpus(tmp_path)
    (root / ".hidden").mkdir()
    (root / "node_modules").mkdir()

    census = W._watch_directories([str(root)])

    assert {str(root / ".hidden"), str(root / "node_modules")} <= set(census)


def test_the_census_sees_a_directory_replaced_at_the_same_path(tmp_path):
    from jdocmunch_mcp import watch as W

    _store, root, _outside = _corpus(tmp_path)
    before = W._watch_directories([str(root)])
    (root / "sub").rmdir()
    (root / "keep-the-inode-busy").mkdir()
    (root / "sub").mkdir()
    after = W._watch_directories([str(root)])

    assert before[str(root / "sub")] != after[str(root / "sub")]


@pytest.mark.parametrize("platform,polling,expected", [
    ("linux", False, False),
    ("linux", True, False),
    ("darwin", False, True),
    ("win32", False, True),
    ("darwin", True, False),
    ("win32", True, False),
])
def test_recursion_is_used_only_where_it_does_not_walk_links(
        monkeypatch, platform, polling, expected):
    from jdocmunch_mcp import watch as W

    monkeypatch.setattr(sys, "platform", platform)
    assert W._native_recursion_is_safe(polling) is expected


# ── what awatch is handed ───────────────────────────────────────────────────


def _watch_with(monkeypatch, store, fake_awatch, *, recursion_safe, seconds=20.0,
                until=lambda: False, rediscover=60.0):
    import watchfiles
    from jdocmunch_mcp import watch as W

    monkeypatch.setattr(watchfiles, "awatch", fake_awatch)
    monkeypatch.setattr(W, "_native_recursion_is_safe", lambda _polling: recursion_safe)

    async def scenario():
        task = asyncio.create_task(W.watch_docs(
            storage_path=str(store), debounce_ms=50, rediscover_interval_s=rediscover,
            use_ai_summaries=False, quiet=True))
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not until() and not task.done():
            await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, SystemExit):
            await task

    asyncio.run(scenario())


def test_enumerated_mode_hands_awatch_the_real_directories_only(tmp_path, monkeypatch):
    store, root, outside = _corpus(tmp_path)
    _link(outside, root / "outside")
    calls = []

    async def fake_awatch(*paths, stop_event=None, **kwargs):
        calls.append((paths, kwargs))
        await stop_event.wait()
        return
        yield  # pragma: no cover  (makes this an async generator, like awatch)

    _watch_with(monkeypatch, store, fake_awatch, recursion_safe=False,
                until=lambda: bool(calls))

    paths, kwargs = calls[0]
    assert set(paths) == {str(root), str(root / "sub")}
    assert kwargs["recursive"] is False


def test_recursive_mode_still_hands_awatch_the_roots(tmp_path, monkeypatch):
    store, root, _outside = _corpus(tmp_path)
    calls = []

    async def fake_awatch(*paths, stop_event=None, **kwargs):
        calls.append((paths, kwargs))
        await stop_event.wait()
        return
        yield  # pragma: no cover

    _watch_with(monkeypatch, store, fake_awatch, recursion_safe=True,
                until=lambda: bool(calls))

    paths, kwargs = calls[0]
    assert list(paths) == [str(root)]
    assert kwargs["recursive"] is True
    assert "yield_on_timeout" not in kwargs


# ── a new directory ─────────────────────────────────────────────────────────


def test_a_file_written_into_a_new_directory_before_its_watch_exists_is_indexed(
        tmp_path, monkeypatch):
    """The directory event arrives; the file inside it raised none.

    A non-recursive watch has nothing on a directory until the next cycle arms
    it, so a file written in between is silent. The loop reads each directory
    that is new since the last armed set, once, after the first yield.
    """
    from watchfiles import Change

    store, root, _outside = _corpus(tmp_path)
    new = root / "guides"
    calls = []

    async def fake_awatch(*paths, stop_event=None, **kwargs):
        calls.append(paths)
        if len(calls) == 1:
            yield set()  # armed, nothing changed
            new.mkdir()
            (new / "setup.md").write_text("# Setup\n\nWritten early.\n", encoding="utf-8")
            yield {(Change.added, str(new))}  # the directory only
        else:
            yield set()
        await stop_event.wait()

    _watch_with(monkeypatch, store, fake_awatch, recursion_safe=False,
                until=lambda: "guides/setup.md" in _doc_paths(store, "docs"))

    assert len(calls) >= 2, "the loop never re-armed after the directory appeared"
    assert str(new) in calls[1]
    assert "guides/setup.md" in _doc_paths(store, "docs")


def test_a_directory_event_that_never_came_is_caught_by_the_periodic_census(
        tmp_path, monkeypatch):
    store, root, _outside = _corpus(tmp_path)
    new = root / "guides"
    calls = []

    async def fake_awatch(*paths, stop_event=None, **kwargs):
        calls.append(paths)
        yield set()
        if len(calls) == 1:
            new.mkdir()
            (new / "setup.md").write_text("# Setup\n\nNo event at all.\n", encoding="utf-8")
        await stop_event.wait()

    _watch_with(monkeypatch, store, fake_awatch, recursion_safe=False, rediscover=0.3,
                until=lambda: "guides/setup.md" in _doc_paths(store, "docs"))

    assert "guides/setup.md" in _doc_paths(store, "docs")


def test_a_newly_discovered_root_is_not_read_as_a_set_of_new_directories(
        tmp_path, monkeypatch):
    """Catch-up is for directories that appeared under a root already watched."""
    from jdocmunch_mcp import watch as W

    store, root, _outside = _corpus(tmp_path)
    other = (tmp_path / "other").resolve()
    other.mkdir()
    (other / "README.md").write_text("# Other\n\nIndexed by someone else.\n", encoding="utf-8")
    calls = []
    handled = []
    real_handle = W._handle_changes

    async def spy_handle(changes, *args, **kwargs):
        handled.append(set(changes))
        return await real_handle(changes, *args, **kwargs)

    async def fake_awatch(*paths, stop_event=None, **kwargs):
        calls.append(paths)
        yield set()
        # Resumed only once the loop is done with that first yield.
        if len(calls) == 1:
            _index(other, "other", store)
        else:
            second_cycle_caught_up.append(True)
        await stop_event.wait()

    second_cycle_caught_up = []
    monkeypatch.setattr(W, "_handle_changes", spy_handle)
    _watch_with(monkeypatch, store, fake_awatch, recursion_safe=False, rediscover=0.3,
                seconds=15.0, until=lambda: bool(second_cycle_caught_up))

    assert len(calls) >= 2 and str(other) in calls[-1]
    assert handled == []


# ── the real watcher ────────────────────────────────────────────────────────


@contextlib.contextmanager
def _real_watcher(store, **env_overrides):
    env = {**os.environ, "DOC_INDEX_PATH": str(store),
           "PYTHONPATH": os.pathsep.join([str(SRC), os.environ.get("PYTHONPATH", "")]),
           **env_overrides}
    watcher = subprocess.Popen(
        [sys.executable, "-m", "jdocmunch_mcp", "watch", "--no-ai-summaries", "--quiet"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        yield watcher
    finally:
        watcher.kill()
        watcher.wait(timeout=10)


def _edit_until_reindexed(store, doc, seconds=40.0):
    """Rewrite ``doc`` until the index follows: proof the watches are armed."""
    before = _hashes(store, "docs")
    deadline = time.monotonic() + seconds
    n = 0
    while time.monotonic() < deadline:
        n += 1
        doc.write_text(f"# Docs\n\nEdit {n}.\n", encoding="utf-8")
        for _ in range(15):
            time.sleep(0.2)
            if _hashes(store, "docs") != before:
                return True
    return False


def _wait_for(predicate, seconds=40.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return predicate()


def _inotify_watches(pid):
    count = 0
    for info in glob.glob(f"/proc/{pid}/fdinfo/*"):
        with contextlib.suppress(OSError):
            with open(info, encoding="utf-8") as handle:
                count += sum(1 for line in handle if line.startswith("inotify"))
    return count


@pytest.mark.skipif(sys.platform != "linux", reason="counts inotify watches in /proc")
def test_the_real_watcher_holds_no_inotify_watch_beyond_the_link(tmp_path):
    """The issue's own measurement: watches held by the running watcher."""
    store, root, outside = _corpus(tmp_path)
    os.symlink(str(outside), str(root / "outside"), target_is_directory=True)

    with _real_watcher(store, WATCHFILES_FORCE_POLLING="false") as watcher:
        assert _edit_until_reindexed(store, root / "README.md"), "the watcher never armed"
        held = _inotify_watches(watcher.pid)

        # 2 real directories. Through the link there are 26 more.
        assert held == 2, f"{held} inotify watches for a root of 2 real directories"

        (root / "guides").mkdir()
        (root / "guides" / "setup.md").write_text("# Setup\n\nNew.\n", encoding="utf-8")
        assert _wait_for(lambda: "guides/setup.md" in _doc_paths(store, "docs")), (
            "a doc in a directory created after start was never indexed")
        assert _wait_for(lambda: _inotify_watches(watcher.pid) == 3)


def test_the_real_watcher_under_polling_picks_up_a_new_directory(tmp_path):
    """Polling takes the enumerated path on every platform, Windows included."""
    store, root, _outside = _corpus(tmp_path)

    with _real_watcher(store, WATCHFILES_FORCE_POLLING="true",
                       JDOCMUNCH_WATCH_POLL_DELAY_MS="200"):
        assert _edit_until_reindexed(store, root / "README.md"), "the watcher never armed"

        (root / "guides").mkdir()
        (root / "guides" / "setup.md").write_text("# Setup\n\nNew.\n", encoding="utf-8")
        assert _wait_for(lambda: "guides/setup.md" in _doc_paths(store, "docs")), (
            "a doc in a directory created after start was never indexed")
        before = _hashes(store, "docs")
        (root / "guides" / "setup.md").write_text("# Setup\n\nEdited.\n", encoding="utf-8")
        assert _wait_for(lambda: _hashes(store, "docs") != before), (
            "an edit in the new directory was never indexed: it has no watch")
