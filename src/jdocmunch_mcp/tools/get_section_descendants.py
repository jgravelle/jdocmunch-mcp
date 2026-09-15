"""Recursive subtree traversal from a section (v1.43.0).

`get_section_path` (v1.40) returns the ancestor chain (root → target).
`section_neighbors` (v1.37) returns only the *first* child. Agents that
wanted the full subtree from a section had to recursively call
`section_neighbors`.

This tool walks `parent_id` downward via BFS over the in-memory section
list and returns every descendant in document order. Each entry carries
its `depth` offset from the target (immediate child = 1, grandchild =
2, etc.). Optional `max_depth` caps the walk.

Cycle-safe: a parent_id pointing back into the visited set is skipped.
"""

from __future__ import annotations

import time
from typing import Optional

from ..storage import DocStore


def get_section_descendants(
    repo: str,
    section_id: str,
    max_depth: Optional[int] = None,
    storage_path: Optional[str] = None,
) -> dict:
    """Return every descendant of a section in document order.

    Args:
        repo: Repository identifier.
        section_id: Target section. Its descendants are reported; the
            target itself is NOT included in the response (use
            `get_section_summary` for that).
        max_depth: Optional cap on how far to descend. ``None`` (default)
            means walk the full subtree. ``1`` = immediate children only.
        storage_path: Custom storage path.

    Returns:
        ``{section_id, descendants: [{id, title, level, doc_path, depth},
        ...], descendant_count, max_depth, _meta}``. Sorted by
        ``(depth, byte_start)`` so parents precede their children.
    """
    t0 = time.perf_counter()
    if max_depth is not None and max_depth < 0:
        return {"error": "max_depth must be non-negative"}
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
    # The subtree is walked by STORED id; the caller's string may be an anchor
    # reference or an alias, which no section lists as its parent.
    tid = target.get("id") or section_id

    # Build parent_id → [children] index once so the BFS is O(N).
    children_by_parent: dict = {}
    for sec in index.sections:
        pid = sec.get("parent_id")
        if pid:
            children_by_parent.setdefault(pid, []).append(sec)

    # Stable ordering at each depth level.
    for kids in children_by_parent.values():
        kids.sort(key=lambda s: s.get("byte_start", 0) or 0)

    descendants: list[dict] = []
    visited: set = {tid}
    # BFS queue carries (section_id, depth_from_target).
    queue: list = [(tid, 0)]
    while queue:
        sid, depth = queue.pop(0)
        if max_depth is not None and depth >= max_depth:
            continue
        for child in children_by_parent.get(sid, []):
            cid = child.get("id")
            if not cid or cid in visited:
                continue
            visited.add(cid)
            descendants.append({
                "id": cid,
                "title": child.get("title"),
                "level": child.get("level"),
                "doc_path": child.get("doc_path"),
                "depth": depth + 1,
            })
            queue.append((cid, depth + 1))

    # Sort: depth asc, then byte_start asc — parents before children,
    # left-to-right within a level.
    by_id = {s.get("id"): s for s in index.sections}
    descendants.sort(key=lambda d: (
        d["depth"],
        by_id.get(d["id"], {}).get("byte_start", 0) or 0,
    ))

    latency_ms = int((time.perf_counter() - t0) * 1000)
    out = {
        "section_id": section_id,
        "doc_path": target.get("doc_path", ""),
        "descendants": descendants,
        "descendant_count": len(descendants),
        "max_depth": max_depth,
        "_meta": {
            "latency_ms": latency_ms,
            "repo": f"{owner}/{name}",
        },
    }
    if disclosure:
        out.update(disclosure)
    return out
