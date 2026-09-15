"""Section breadcrumb resolution (v1.40.0).

`section_neighbors` (v1.37) returns immediate prev/next/parent/child.
`get_section_summary` (v1.38) returns one section's metadata. Neither
walks the parent chain — agents that want the *full* path from doc
root to a section had to call section_neighbors repeatedly.

This tool walks `parent_id` upward and returns the breadcrumb in root-
first order. Handles only — `{id, title, level, doc_path}` per step.
Cycle-protected (parent_id pointing back into the chain breaks the
walk).
"""

from __future__ import annotations

import time
from typing import Optional

from ..storage import DocStore


def _handle(sec: dict) -> dict:
    return {
        "id": sec.get("id"),
        "title": sec.get("title"),
        "level": sec.get("level"),
        "doc_path": sec.get("doc_path"),
    }


def get_section_path(
    repo: str,
    section_id: str,
    storage_path: Optional[str] = None,
) -> dict:
    """Return the breadcrumb chain (root → … → target) for a section.

    The path is ordered root-first. The target itself is included as the
    last entry. ``depth`` is the path length minus one.
    """
    t0 = time.perf_counter()
    store = DocStore(base_path=storage_path)
    owner, name = store._resolve_repo(repo)
    index = store.load_index(owner, name)

    if not index:
        return {"error": f"Repo not found: {repo}"}

    target = index.get_section(section_id)
    # v1.147.0: an ambiguous `doc.md#anchor` reference is disclosed, never
    # resolved silently -- on the miss path as well as the hit path.
    disclosure = index.anchor_disclosure(section_id)
    if not target:
        if disclosure:
            return {"error": disclosure["warning"], **disclosure}
        return {"error": f"Section not found: {section_id}"}

    chain: list[dict] = [target]
    seen: set = {target.get("id") or section_id}
    cur = target
    while True:
        parent_id = cur.get("parent_id")
        if not parent_id or parent_id in seen:
            break
        parent = index.get_section(parent_id)
        if not parent:
            break
        chain.append(parent)
        seen.add(parent_id)
        cur = parent

    chain.reverse()  # root-first order.

    latency_ms = int((time.perf_counter() - t0) * 1000)
    out = {
        "section_id": section_id,
        "doc_path": target.get("doc_path", ""),
        "path": [_handle(s) for s in chain],
        "depth": len(chain) - 1,
        "_meta": {
            "latency_ms": latency_ms,
            "repo": f"{owner}/{name}",
        },
    }
    if disclosure:
        out.update(disclosure)
    return out
