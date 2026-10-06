"""watch: a long error streak must neither end the watcher nor pin a dead root.

Two defects in the retry path added for #156, both found in review.

1. (#159) The delay was `ERROR_RETRY_INITIAL_S * 2 ** (n - 1)`. At n = 1025 the int
   no longer converts to float and the multiplication raises OverflowError
   from inside the `except` handler, so the watcher exits. At the default
   30 s ceiling that is reached after about 8.5 hours of one recurring error.

2. (#160) A cycle that fails at once cancels the rediscovery monitor before its first
   pass. So while awatch kept failing, discovery never ran, and a root that
   vanished (awatch raises FileNotFoundError for it) was retried forever with
   every other root unwatched.
"""
import asyncio
import os
import shutil


def _index(path, name, store):
    from jdocmunch_mcp.tools.index_local import index_local
    return index_local(path=str(path), name=name, use_embeddings=False,
                       use_ai_summaries=False, storage_path=str(store))


def _root(tmp_path, name):
    root = tmp_path / name
    root.mkdir()
    (root / "README.md").write_text(f"# {name}\n\nSomething to watch.\n", encoding="utf-8")
    return root


def _run(coro_factory, seconds):
    async def scenario():
        task = asyncio.create_task(coro_factory())
        done, _ = await asyncio.wait({task}, timeout=seconds)
        if done:
            return task.exception()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, SystemExit):
            pass
        return None

    return asyncio.run(scenario())


def test_the_retry_delay_holds_past_the_point_a_float_can_double():
    from jdocmunch_mcp import watch as W

    assert W._error_retry_delay(1, 30.0) == W.ERROR_RETRY_INITIAL_S
    assert W._error_retry_delay(2, 30.0) == 2 * W.ERROR_RETRY_INITIAL_S
    assert W._error_retry_delay(3, 30.0) == 4 * W.ERROR_RETRY_INITIAL_S
    # 2 ** 1024 no longer converts to float; these raised OverflowError.
    for streak in (1025, 1026, 10 ** 6):
        assert W._error_retry_delay(streak, 30.0) == 30.0


def test_a_root_that_vanishes_is_dropped_while_awatch_keeps_failing(tmp_path, monkeypatch):
    import watchfiles
    from jdocmunch_mcp import watch as W

    store = tmp_path / "store"
    store.mkdir()
    a = _root(tmp_path, "a")
    b = _root(tmp_path, "b")
    _index(a, "a", store)
    _index(b, "b", store)

    watched = []

    async def awatch_like(*paths, stop_event=None, **kwargs):
        watched.append(paths)
        if len(watched) == 1:
            shutil.rmtree(a)  # root A vanishes under the watcher
        if any(not os.path.isdir(p) for p in paths):
            raise FileNotFoundError("Input watch path is neither a file nor a directory.")
        await stop_event.wait()  # every root is there: watch until the cycle ends
        return
        yield  # pragma: no cover

    monkeypatch.setattr(watchfiles, "awatch", awatch_like)

    _run(lambda: W.watch_docs(
        storage_path=str(store), debounce_ms=50, rediscover_interval_s=0.3,
        use_ai_summaries=False, quiet=True), seconds=5.0)

    assert len(watched[0]) == 2
    assert [str(b.resolve())] == [str(p) for p in watched[-1]], (
        f"root A was never dropped: {len(watched)} attempts, all over {len(watched[-1])} roots")
