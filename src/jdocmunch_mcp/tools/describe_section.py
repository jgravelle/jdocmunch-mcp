"""Consolidated section context — handle bundle in one call (v1.54.0).

Agents frequently want the full picture of a section without paying for
content: metadata + ancestors (breadcrumb) + immediate neighbors.
Before v1.54 that meant calling get_section_summary, get_section_path,
and section_neighbors separately — three round-trips against three
load_index() calls.

This tool resolves all three in one shot against a single load_index().
The output composes the three views; nothing returned here is unique.
Pure additive; the underlying tools remain available.
"""

from __future__ import annotations

import time
from typing import Optional

from ..storage import DocStore


def _handle(sec: Optional[dict]) -> Optional[dict]:
    if not sec:
        return None
    return {
        "id": sec.get("id"),
        "title": sec.get("title"),
        "level": sec.get("level"),
        "doc_path": sec.get("doc_path"),
    }


def describe_section(
    repo: str,
    section_id: str,
    storage_path: Optional[str] = None,
) -> dict:
    """Return metadata + breadcrumb + neighbors for one section.

    Composition:
      - ``section`` — full Section.to_dict view minus content + derived
        ``byte_length`` (same shape as v1.38 get_section_summary).
      - ``path`` — ancestor chain root → target, handles only (v1.40
        get_section_path shape).
      - ``neighbors`` — ``{prev, next, parent, first_child}`` handles
        (v1.37 section_neighbors shape) plus ``child_count``.

    Cycle-protected on the parent walk; same-doc-path restriction on
    prev/next so navigation doesn't accidentally hop documents.
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
    # Everything below walks the tree by STORED id; the caller's string may be an
    # anchor reference or an alias.
    tid = target.get("id") or section_id

    # ---- section view (minus content + raw embedding, plus derived byte_length) ----
    section_view = {k: v for k, v in target.items() if k not in ("content", "embedding")}
    bs = int(target.get("byte_start", 0) or 0)
    be = int(target.get("byte_end", 0) or 0)
    section_view["byte_length"] = max(0, be - bs)

    # ---- ancestor chain ----
    chain: list[dict] = [target]
    seen: set = {tid}
    cur = target
    while True:
        pid = cur.get("parent_id")
        if not pid or pid in seen:
            break
        parent = index.get_section(pid)
        if not parent:
            break
        chain.append(parent)
        seen.add(pid)
        cur = parent
    chain.reverse()
    path = [_handle(s) for s in chain]
    depth = len(chain) - 1

    # ---- neighbors ----
    target_doc = target.get("doc_path", "")
    same_doc = sorted(
        (s for s in index.sections if s.get("doc_path") == target_doc),
        key=lambda s: s.get("byte_start", 0) or 0,
    )
    prev_sec = next_sec = None
    for i, sec in enumerate(same_doc):
        if sec.get("id") == tid:
            if i > 0:
                prev_sec = same_doc[i - 1]
            if i + 1 < len(same_doc):
                next_sec = same_doc[i + 1]
            break
    parent_id = target.get("parent_id")
    parent_sec = index.get_section(parent_id) if parent_id else None
    children = sorted(
        (s for s in index.sections if s.get("parent_id") == tid),
        key=lambda s: s.get("byte_start", 0) or 0,
    )
    first_child = children[0] if children else None

    out = {
        "section_id": section_id,
        "doc_path": target_doc,
        "section": section_view,
        "path": path,
        "depth": depth,
        "neighbors": {
            "prev": _handle(prev_sec),
            "next": _handle(next_sec),
            "parent": _handle(parent_sec),
            "first_child": _handle(first_child),
            "child_count": len(children),
        },
        "_meta": {
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "repo": f"{owner}/{name}",
            "indexed_at": index.indexed_at,
        },
    }
    if disclosure:
        out.update(disclosure)
    return out
