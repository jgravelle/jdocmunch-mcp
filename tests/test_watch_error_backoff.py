"""watch: an awatch error that recurs must not restart the loop at once.

Before the fix, the loop logged the error and started the next awatch cycle
immediately. An error that recurs on every attempt (a root awatch cannot set
up, e.g. one holding a directory it may not read) then rebuilt the watcher as
fast as the machine could, using a full core for as long as the watcher ran,
and only a log line at WARNING said so.

Acceptance: with awatch failing on every attempt, the loop backs off
(ERROR_RETRY_INITIAL_S, then twice that, ...), so within 2.5 x the initial
delay it makes at most 3 attempts (at 0, 1 and 3 x the delay); it used to
make thousands.
"""
import asyncio


def test_an_awatch_error_that_recurs_backs_off(tmp_path, monkeypatch):
    import watchfiles
    from jdocmunch_mcp import watch as W
    from jdocmunch_mcp.tools.index_local import index_local

    root = tmp_path / "docs"
    root.mkdir()
    (root / "README.md").write_text("# Docs\n\nSomething to watch.\n", encoding="utf-8")
    store = tmp_path / "store"
    store.mkdir()
    index_local(path=str(root), name="docs", use_embeddings=False, use_ai_summaries=False,
                storage_path=str(store))

    attempts = []

    async def failing_awatch(*paths, **kwargs):
        attempts.append(paths)
        if len(attempts) > 50:
            # A tight loop starves the event loop, so the test's own timer
            # would never fire: end the watcher the way a stop request does.
            raise asyncio.CancelledError
        raise PermissionError(13, "Permission denied")
        yield  # pragma: no cover  (makes this an async generator, like awatch)

    monkeypatch.setattr(watchfiles, "awatch", failing_awatch)
    window = 2.5 * W.ERROR_RETRY_INITIAL_S

    async def scenario():
        task = asyncio.create_task(W.watch_docs(
            storage_path=str(store), debounce_ms=50, rediscover_interval_s=60.0,
            use_ai_summaries=False, quiet=True))
        await asyncio.sleep(window)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, SystemExit):
            pass

    asyncio.run(scenario())
    assert 1 <= len(attempts) <= 3, f"{len(attempts)} awatch attempts in {window} s"
