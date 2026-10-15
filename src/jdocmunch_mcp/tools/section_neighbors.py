"""Lightweight document-order navigation for a section (v1.37.0).

`get_section_context` returns ancestors + the target's content + immediate
children. That's the right tool when the agent wants to *read* a section
in context. This tool is for *navigating* — the agent already knows where
it is and just wants the document-order neighbors so it can step forward
or backward without doing another search.

Returns shallow handles ({id, title, level, doc_path}) for prev / next /
parent / first_child. No byte-range reads, no content materialization,
no embedding lookups — just the section tree walked once.
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


def section_neighbors(
    repo: str,
    section_id: str,
    storage_path: Optional[str] = None,
) -> dict:
    """Return prev/next/parent/first_child handles for a section.

    Document order = (doc_path, byte_start). prev/next are restricted to
    the same doc_path so we don't accidentally hop documents. parent is
    resolved via Section.parent_id; first_child via the first
    parent_id-matching section, also in byte_start order.
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
    tid = target.get("id") or section_id

    target_doc = target.get("doc_path", "")
    target_start = target.get("byte_start", 0) or 0

    # Sections in this doc, sorted by byte_start.
    same_doc = sorted(
        (s for s in index.sections if s.get("doc_path") == target_doc),
        key=lambda s: s.get("byte_start", 0) or 0,
    )

    prev_sec: Optional[dict] = None
    next_sec: Optional[dict] = None
    for i, sec in enumerate(same_doc):
        if sec.get("id") != tid:
            continue
        if i > 0:
            prev_sec = same_doc[i - 1]
        if i + 1 < len(same_doc):
            next_sec = same_doc[i + 1]
        break

    # Parent.
    parent_id = target.get("parent_id")
    parent_sec = index.get_section(parent_id) if parent_id else None

    # First child = first section whose parent_id matches the target,
    # ordered by byte_start.
    children = sorted(
        (s for s in index.sections if s.get("parent_id") == tid),
        key=lambda s: s.get("byte_start", 0) or 0,
    )
    first_child = children[0] if children else None

    latency_ms = int((time.perf_counter() - t0) * 1000)
    out = {
        "section_id": section_id,
        "doc_path": target_doc,
        "prev": _handle(prev_sec),
        "next": _handle(next_sec),
        "parent": _handle(parent_sec),
        "first_child": _handle(first_child),
        "child_count": len(children),
        "_meta": {
            "latency_ms": latency_ms,
            "repo": f"{owner}/{name}",
        },
    }
    if disclosure:
        out.update(disclosure)
    return out
