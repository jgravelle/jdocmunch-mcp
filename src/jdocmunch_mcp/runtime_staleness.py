"""Detect that this long-lived server is running code the disk has moved past.

An MCP stdio server cannot restart itself and cannot ask to be restarted:

* the client owns the process lifecycle; the protocol has no "please respawn";
* the tool-list-changed notification makes the client re-fetch the TOOL LIST,
  not reload Python code (spelled out rather than quoted verbatim on purpose:
  ``tests/test_schema_tokens_basis.py`` text-scans ``src/`` for that exact
  marker to catch a runtime tool-tier switch arriving unpriced, and a docstring
  saying we do NOT use it would trip that guard. Do not "fix" this wording);
* a server that exits is reported as a failed connection and is not respawned
  (observed 2026-09-15: "Failed to reconnect to jdocmunch");
* ``os.execv`` keeps the file descriptors but not the completed ``initialize``
  handshake, so the client would wait forever for one it already performed.

So the achievable design is DETECT AND DISCLOSE, never self-restart. This module
is the detect half. It scales without any broadcaster: every server notices
independently and tells its own session, so nothing needs to know how many
sessions exist.

⚠⚠ **A VERSION MATCH IS NOT PROOF THE INSTALL TOOK, and this module cannot see
the case that proves it.** A reinstall can rebuild the environment yet fail to
replace the console-script shim, which Windows locks while a process is running.
``--version`` then reports the new number while the shim still starts the old
entry point. That failure lives in the shim, a file this process never imported,
and it affects future spawns, so it is documented rather than detected: when an
install appears to have had no effect, check the entry point, not the version.
"""

import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Files whose change means this process is running superseded code.
#
# ⚠ The package DIRECTORY is deliberately NOT watched, though its mtime does
# move when a reinstall adds or removes files. Python writes ``__pycache__/``
# into that directory on the first import after an install, which bumps the same
# mtime -- so watching it would report every freshly started server as stale.
# A signal that always fires hides the case it exists for. A reinstall that
# hardlinks from a cache can leave a file's mtime older than this process, so the
# mtime signal can miss such an install; the version signal still catches a
# version change.
_WATCH = ("__init__.py", "server.py")

def _process_loaded_at() -> float:
    """When this process started, as epoch seconds.

    Prefer the OS process start from ``runtime_identity``. Where the platform
    gives none (the value is only self-recorded) or it cannot be parsed, fall
    back to this module's import time. ``server.py`` imports this module when it
    loads, so that fallback is the server's load time, not its first tool call.
    """
    try:
        from .runtime_identity import get_process_start

        start = get_process_start()
        if start.get("source") == "os":
            return datetime.fromisoformat(start["value"]).timestamp()
    except Exception:
        logger.debug("process start unavailable; using import time", exc_info=True)
    return time.time()


# Anything on disk newer than this is code we are NOT running.
_LOADED_AT = _process_loaded_at()

# Linux derives the OS start from the boot time, which is whole seconds, so the
# computed start can be up to a second early. Without slack, a file written in
# that second just before launch would read as superseded for the whole life of
# the process.
_MTIME_SLACK_SECONDS = 2.0

# A reinstall is a human-scale event; re-statting on every tool call would be
# pure waste. Five seconds keeps the disclosure prompt without measurable cost.
_TTL_SECONDS = 5.0

_cache: dict = {"checked_at": 0.0, "report": None}
_failure_logged = False
_version_failure_logged = False


def _package_dir() -> Path:
    return Path(__file__).resolve().parent


def _running_version() -> str:
    try:
        from . import __version__

        return __version__
    except Exception:
        return "unknown"


def _installed_version() -> Optional[str]:
    """The version the environment holds right now, or ``None`` if it has none.

    The running version is ``jdocmunch_mcp.__version__``, resolved once and kept
    for the life of the process, so after a reinstall it still holds the old
    value. This asks the SAME resolver, ``importlib.metadata.version``, again.

    Why the resolver and not a glob of ``jdocmunch_mcp-*.dist-info`` beside the
    package: the running value comes from that resolver, so two readings of one
    unchanged environment always agree. A glob can pick a different dist-info
    than the resolver did (a leftover from an interrupted upgrade or a failed
    uninstall, or a dist-info on another path entry) and would then report
    drift on every tool call for the life of the process, which a restart does
    not clear.

    ``invalidate_caches()`` first, as a defensive measure: ``importlib.metadata``
    already keys its directory cache on the directory's mtime, so a reinstall
    is normally seen without it, and no test pins the call. It covers a
    filesystem whose directory mtime is too coarse to show a quick replace. The
    cost is bounded by ``_TTL_SECONDS``, since ``staleness_report`` calls this
    at most once per TTL.

    Any other failure of the lookup (a METADATA that is not valid UTF-8 raises
    ``UnicodeDecodeError``, for one) also reads as ``None``. The version and
    file-mtime signals are independent, and a version probe that raised would
    take ``files_changed`` down with it. The failure is logged once, at debug
    level.

    Imported here so importing this module stays cheap; the hook and CLI entry
    points never reach this function.
    """
    import importlib
    import importlib.metadata

    try:
        importlib.invalidate_caches()
        return importlib.metadata.version("jdocmunch-mcp")
    except importlib.metadata.PackageNotFoundError:
        return None
    except Exception:
        global _version_failure_logged
        if not _version_failure_logged:
            _version_failure_logged = True
            logger.debug("installed version unreadable; skipping it", exc_info=True)
        return None


def _changed_paths() -> list:
    """Watched paths whose mtime post-dates this process loading its code."""
    changed = []
    pkg = _package_dir()
    for candidate in (pkg / name for name in _WATCH):
        try:
            if candidate.stat().st_mtime > _LOADED_AT + _MTIME_SLACK_SECONDS:
                changed.append(candidate.name)
        except OSError:
            continue
    return changed


def staleness_report() -> Optional[dict]:
    """Return a disclosure dict when this process is running superseded code.

    ``None`` when fresh, so the caller attaches nothing and the response shape is
    unchanged for everyone not affected.
    """
    if os.environ.get("JDOCMUNCH_DISABLE_STALENESS_CHECK"):
        return None

    now = time.time()
    if now - _cache["checked_at"] < _TTL_SECONDS:
        return _cache["report"]

    report = None
    try:
        running = _running_version()
        installed = _installed_version()
        changed = _changed_paths()

        reasons = []
        if installed and running != "unknown" and installed != running:
            reasons.append("version_changed")
        if changed:
            reasons.append("files_changed")

        if reasons:
            report = {
                "stale": True,
                "running_version": running,
                "reasons": reasons,
                "action": (
                    "This server process is running code that has since been "
                    "replaced on disk. Restart the client session to load it. "
                    "Short-lived entry points (the CLI and the Claude Code "
                    "hooks) already use the new build; only this long-lived "
                    "server is pinned to the old one."
                ),
            }
            if installed:
                report["installed_version"] = installed
            if changed:
                report["changed_paths"] = changed
    except Exception:
        # A disclosure is never worth failing a tool call for, but a probe that
        # fails must not look like a fresh server forever: log it once. The None
        # is cached for the TTL, which bounds the cost of a probe that keeps
        # failing.
        global _failure_logged
        if not _failure_logged:
            _failure_logged = True
            logger.warning("staleness probe failed; no disclosure", exc_info=True)
        report = None

    _cache["checked_at"] = now
    _cache["report"] = report
    return report
