"""Batch content preview — companion to v1.41's get_section_excerpt (v1.49.0).

When an agent has 5+ section_ids from a search and wants a quick peek
at each, calling get_section_excerpt five times is five round-trips.
This tool resolves them in one call against a single load_index().

Per-id errors are reported in-line on the corresponding result entry
rather than aborting the batch — same partial-failure contract as
v1.48's get_section_summaries.
"""

from __future__ import annotations

import time
from typing import Optional

from ..storage import DocStore
from ..storage.token_tracker import estimate_savings, record_savings, cost_avoided
from .get_section_excerpt import _safe_truncate

_DEFAULT_MAX_BYTES = 500


def get_section_excerpts(
    repo: str,
    section_ids: list,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    storage_path: Optional[str] = None,
) -> dict:
    """Return short content previews for many sections in one call.

    Args:
        repo: Repository identifier.
        section_ids: List of section IDs to preview. Order preserved.
        max_bytes: Per-section soft cap on excerpt size in UTF-8 bytes
            (default 500). Same trim-to-newline + UTF-8 boundary semantics
            as v1.41's get_section_excerpt.
        storage_path: Custom storage path.

    Returns:
        ``{repo, sections: [{requested_id, section?, excerpt?, truncated?,
        full_byte_length?, excerpt_byte_length?, error?}, ...],
        section_count, found_count, missing_count, _meta}``.
        ``_meta.tokens_saved`` aggregates byte savings across the batch.
    """
    t0 = time.perf_counter()
    if max_bytes <= 0:
        return {"error": "max_bytes must be positive"}
    store = DocStore(base_path=storage_path)
    owner, name = store._resolve_repo(repo)
    index = store.load_index(owner, name)

    if not index:
        return {"error": f"Repo not found: {repo}"}

    if not isinstance(section_ids, (list, tuple)):
        return {"error": "section_ids must be a list"}

    out: list = []
    ambiguous: list = []
    found = 0
    missing = 0
    total_full = 0
    total_excerpt = 0

    for sid in section_ids:
        if not isinstance(sid, str):
            out.append({"requested_id": sid, "error": "section_id must be a string"})
            missing += 1
            continue
        sec = index.get_section(sid)
        # v1.147.0: ambiguous `doc.md#anchor` references are disclosed per entry.
        disclosure = index.anchor_disclosure(sid)
        if disclosure:
            ambiguous.append(disclosure["warning"])
        if not sec:
            if disclosure:
                out.append({"requested_id": sid, "error": disclosure["warning"],
                            **disclosure})
            else:
                out.append({"requested_id": sid, "error": f"Section not found: {sid}"})
            missing += 1
            continue
        content = store.get_section_content(owner, name, sid, _index=index)
        if content is None:
            out.append({
                "requested_id": sid,
                "error": f"Content not available for section: {sid}",
            })
            missing += 1
            continue
        full_bytes = len(content.encode("utf-8"))
        excerpt, truncated = _safe_truncate(content, max_bytes)
        excerpt_bytes = len(excerpt.encode("utf-8"))
        total_full += full_bytes
        total_excerpt += excerpt_bytes
        role = (sec.get("metadata") or {}).get("role")
        out.append({
            "requested_id": sid,
            "section": {
                "id": sec.get("id"),
                "title": sec.get("title"),
                "doc_path": sec.get("doc_path"),
                "level": sec.get("level"),
                "role": role,
                "summary": sec.get("summary"),
            },
            "excerpt": excerpt,
            "truncated": truncated,
            "full_byte_length": full_bytes,
            "excerpt_byte_length": excerpt_bytes,
            **(disclosure or {}),
        })
        found += 1

    tokens_saved = estimate_savings(total_full, total_excerpt)
    total = record_savings(tokens_saved, storage_path)
    ca = cost_avoided(tokens_saved, total)

    result = {
        "repo": f"{owner}/{name}",
        "sections": out,
        "section_count": len(out),
        "found_count": found,
        "missing_count": missing,
        "_meta": {
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "max_bytes": max_bytes,
            "tokens_saved": tokens_saved,
            **ca,
        },
    }
    if ambiguous:
        # Top level too: `_meta` can be stripped by a response filter.
        result["anchor_ambiguous"] = True
        result["warning"] = " ".join(ambiguous)
    return result
