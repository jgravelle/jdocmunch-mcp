"""Lightweight metadata-only retrieval for a section (v1.38.0).

`get_section` returns content (byte-range read). `get_toc` returns
brief handles (title, level, summary). Nothing returned the *full*
metadata for one section without paying for the content fetch.

This tool fills that gap. Returns the indexed Section minus the byte
range — title, summary, role, tags, metadata, parent_id, children,
content_hash, byte_start/end, byte_length. Agents can use this to
inspect a section's role/tags/structured metadata before deciding
whether the content is worth reading.

Pairs with v1.37.0's `section_neighbors` (also handle-only). Together
they let agents navigate and inspect without touching disk content.
"""

from __future__ import annotations

import time
from typing import Optional

from ..storage import DocStore


def get_section_summary(
    repo: str,
    section_id: str,
    storage_path: Optional[str] = None,
) -> dict:
    """Return the indexed metadata for one section, omitting content.

    The response includes everything `Section.to_dict` carries except
    `content`. `byte_length` is computed from `byte_start`/`byte_end`
    so callers can size content reads without a separate call.
    """
    t0 = time.perf_counter()
    store = DocStore(base_path=storage_path)
    owner, name = store._resolve_repo(repo)
    index = store.load_index(owner, name)

    if not index:
        return {"error": f"Repo not found: {repo}"}

    sec = index.get_section(section_id)
    # v1.147.0: an ambiguous `doc.md#anchor` reference is disclosed, never
    # resolved silently -- on the miss path as well as the hit path.
    disclosure = index.anchor_disclosure(section_id)
    if not sec:
        if disclosure:
            return {"error": disclosure["warning"], **disclosure}
        return {"error": f"Section not found: {section_id}"}

    # Strip content + raw embedding (issue #11). Everything else passes through.
    summary_view = {k: v for k, v in sec.items() if k not in ("content", "embedding")}

    byte_start = int(sec.get("byte_start", 0) or 0)
    byte_end = int(sec.get("byte_end", 0) or 0)
    summary_view["byte_length"] = max(0, byte_end - byte_start)

    latency_ms = int((time.perf_counter() - t0) * 1000)
    out = {
        "section": summary_view,
        "_meta": {
            "latency_ms": latency_ms,
            "repo": f"{owner}/{name}",
            "indexed_at": index.indexed_at,
        },
    }
    if disclosure:
        out.update(disclosure)
    return out
