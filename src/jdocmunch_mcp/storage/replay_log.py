"""Retrieval-replay log capture (v1.28.0).

Opt-in append-only JSONL stream of every ``search_sections`` call. Sister
to the v1.23 ``ranking_events`` SQLite ledger but human-readable and
grep-friendly — easier to ship to external analysis pipelines.

Enable via ``JDOCMUNCH_REPLAY_LOG=1``. Output lands at
``~/.doc-index/replay.log`` (one JSON object per line). Failures swallowed
— logging never breaks a working search.

Each line:

    {
        "ts": <epoch float>,
        "repo": "<owner>/<name>",
        "query": "<text>",
        "mode": "lexical" | "hybrid" | "semantic_only",
        "semantic_used": bool,
        "semantic_weight": float,
        "top1_id": "<section_id or null>",
        "top1_score": <float or null>,
        "confidence": <float or null>,
        "result_count": int
    }
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

_FILENAME = "replay.log"
_LOCK = threading.Lock()


def _enabled() -> bool:
    flag = os.environ.get("JDOCMUNCH_REPLAY_LOG", "")
    return flag.strip() not in ("", "0", "false", "False", "no")


def _path(base_path: Optional[str] = None) -> Path:
    from .paths import resolve_root  # jdoc#146
    root = resolve_root(base_path)
    root.mkdir(parents=True, exist_ok=True)
    return root / _FILENAME


def append(
    *,
    repo: Optional[str],
    query: str,
    mode: str,
    semantic_used: bool,
    semantic_weight: float,
    top1_id: Optional[str] = None,
    top1_score: Optional[float] = None,
    confidence: Optional[float] = None,
    result_count: int = 0,
    base_path: Optional[str] = None,
) -> None:
    """Append one replay row. No-op when JDOCMUNCH_REPLAY_LOG is unset."""
    if not _enabled():
        return
    try:
        path = _path(base_path)
        row = {
            "ts": time.time(),
            "repo": repo,
            "query": query,
            "mode": mode,
            "semantic_used": bool(semantic_used),
            "semantic_weight": float(semantic_weight),
            "top1_id": top1_id,
            "top1_score": float(top1_score) if top1_score is not None else None,
            "confidence": float(confidence) if confidence is not None else None,
            "result_count": int(result_count),
        }
        with _LOCK:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
    except Exception:
        pass


def read_all(base_path: Optional[str] = None, limit: Optional[int] = None) -> list[dict]:
    """Read the replay log into a list of dicts. Returns [] when absent.

    Skips corrupt lines. ``limit`` returns the most recent N rows when set.
    """
    path = _path(base_path)
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except OSError:
        return []
    if limit is not None and limit > 0 and len(rows) > limit:
        rows = rows[-limit:]
    return rows
