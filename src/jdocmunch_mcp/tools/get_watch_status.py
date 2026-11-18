"""get_watch_status — surface the doc watcher's coverage + service state.

Reports whether the `jdocmunch-watch` login service is installed/active and,
for every locally-indexed doc repo, whether its source_root still exists on
disk (i.e. is watchable). Read-only; safe to call from an agent for a quick
"is my doc index being kept fresh?" check.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def get_watch_status(storage_path: Optional[str] = None) -> dict:
    """Return a summary of doc-watcher coverage and login-service health."""
    from ..watch import discover_local_doc_repos

    # Login-service state (best-effort; never raises out of a status probe).
    try:
        from .. import service_installer
        service = service_installer.service_status()
    except Exception as exc:  # noqa: BLE001 - status must not crash on a probe
        # A probe that raised observed nothing: unknown, never inactive (#206).
        service = {
            "active": None, "status": "unknown", "probe_error": "probe_failed",
            "detail": str(exc), "error": str(exc),
        }
    status = service.get("status", "unknown")
    # True only for a watcher seen running, False only for one seen stopped or
    # absent, None when the probe could not tell (#206).
    installed_active = (
        True if status == "active"
        else False if status in ("inactive", "not_installed")
        else None
    )

    watchable = discover_local_doc_repos(storage_path)
    watchable_roots = {root for root, _name in watchable}

    # Enumerate every local doc repo so an unwatchable one (missing source_root)
    # is visible rather than silently absent.
    from .list_repos import list_repos
    repos_out = []
    local_total = 0
    try:
        rows = list_repos(storage_path=storage_path).get("repos", [])
    except Exception:
        rows = []
    for row in rows:
        src = (row.get("source_root") or "").strip()
        if not src:
            continue  # GitHub index — not a local watch target
        local_total += 1
        try:
            resolved = str(Path(src).expanduser().resolve())
        except OSError:
            resolved = src
        exists = os.path.isdir(src)
        repos_out.append({
            "repo": row.get("repo") or row.get("name"),
            "source_root": src,
            "section_count": row.get("section_count"),
            "doc_count": row.get("doc_count"),
            "watchable": exists and resolved in watchable_roots,
            "source_root_exists": exists,
        })

    return {
        "service": {
            "installed_active": installed_active,
            "status": status,
            "platform": service.get("platform"),
            "detail": service,
        },
        "watchable_repo_count": len(watchable),
        "local_repo_count": local_total,
        "repos": repos_out,
        "hint": (
            "Run `jdocmunch-mcp watch` to keep these fresh in the foreground, or "
            "`jdocmunch-mcp watch-install` to run it as a background login service."
        ),
    }
