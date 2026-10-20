"""Run one ``watch`` re-index batch in a child interpreter (jdoc#189).

``incremental_save`` rewrites the whole monolith however few files changed,
so a watch batch's peak memory is the largest index's full rebuild. In a
long-lived watcher that peak never fully returns to the OS (allocator arenas
from the worker-thread pool), and the daemon's RSS climbs toward its cap.
Running each batch here instead pays the peak in a process that exits, which
hands every page back. Diagnosed and measured by @DreDay4u in #189.

Protocol: one JSON object on stdin (``root``, ``name``, ``paths``,
``storage_path``, ``use_ai_summaries``); one JSON line on stdout, the
``index_local`` result. Exit 0 on success, 1 when ``index_local`` returned an
error result, 2 when it raised.
"""

from __future__ import annotations

import contextlib
import json
import sys


def main() -> int:
    try:
        job = json.loads(sys.stdin.read())
    except ValueError as e:
        print(json.dumps({"success": False, "error": f"bad batch input: {e}"}))
        return 2
    from .tools.index_local import index_local

    try:
        # stdout carries the result only; provider or library chatter during
        # the run goes to stderr (same guard as the CLI, jdoc#65).
        with contextlib.redirect_stdout(sys.stderr):
            result = index_local(
                path=job["root"],
                name=job["name"],
                paths=list(job["paths"]),
                storage_path=job.get("storage_path"),
                use_ai_summaries=bool(job.get("use_ai_summaries", True)),
                incremental=True,
            )
    except Exception as e:  # reported to the parent, which logs it
        print(json.dumps({"success": False, "error": f"{type(e).__name__}: {e}"}))
        return 2
    print(json.dumps(result, default=str))
    return 0 if result.get("success", True) else 1


if __name__ == "__main__":
    sys.exit(main())
