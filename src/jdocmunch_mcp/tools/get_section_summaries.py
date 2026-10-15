"""Batch metadata retrieval — companion to v1.38's get_section_summary (v1.48.0).

When an agent has 5+ section_ids from a search and wants full metadata
for each, calling get_section_summary five times is five round-trips.
This tool resolves them in one call against a single load_index().

Per-id errors are reported in-line on the corresponding result entry
rather than aborting the batch — partial failure is preferable to total
failure when an agent is surveying many ids.
"""

from __future__ import annotations

import time
from typing import Optional

from ..storage import DocStore


def get_section_summaries(
    repo: str,
    section_ids: list,
    storage_path: Optional[str] = None,
) -> dict:
    """Return indexed metadata (no content) for a batch of sections.

    Args:
        repo: Repository identifier.
        section_ids: List of section IDs to look up. Order is preserved
            in the response.
        storage_path: Custom storage path.

    Returns:
        ``{repo, sections: [{section?, error?, requested_id}, ...],
        section_count, found_count, missing_count, _meta}``. Each entry
        carries either a ``section`` key (with the full metadata view)
        or an ``error`` key (when the id wasn't found). ``requested_id``
        is always present so callers can correlate without scanning.
    """
    t0 = time.perf_counter()
    store = DocStore(base_path=storage_path)
    owner, name = store._resolve_repo(repo)
    index = store.load_index(owner, name)

    if not index:
        return {"error": f"Repo not found: {repo}"}

    if not isinstance(section_ids, (list, tuple)):
        return {"error": "section_ids must be a list"}

    out: list = []
    found = 0
    missing = 0
    ambiguous: list = []
    for sid in section_ids:
        if not isinstance(sid, str):
            out.append({"requested_id": sid, "error": "section_id must be a string"})
            missing += 1
            continue
        sec = index.get_section(sid)
        # v1.147.0: an ambiguous `doc.md#anchor` entry is disclosed on its own
        # row, never resolved silently.
        disclosure = index.anchor_disclosure(sid)
        if disclosure:
            ambiguous.append(disclosure["warning"])
        if not sec:
            if disclosure:
                out.append({"requested_id": sid, "error": disclosure["warning"], **disclosure})
            else:
                out.append({"requested_id": sid, "error": f"Section not found: {sid}"})
            missing += 1
            continue
        view = {k: v for k, v in sec.items() if k not in ("content", "embedding")}
        byte_start = int(sec.get("byte_start", 0) or 0)
        byte_end = int(sec.get("byte_end", 0) or 0)
        view["byte_length"] = max(0, byte_end - byte_start)
        entry = {"requested_id": sid, "section": view}
        if disclosure:
            entry.update(disclosure)
        out.append(entry)
        found += 1

    resp = {
        "repo": f"{owner}/{name}",
        "sections": out,
        "section_count": len(out),
        "found_count": found,
        "missing_count": missing,
        "_meta": {
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "indexed_at": index.indexed_at,
        },
    }
    if ambiguous:
        # Top level as well as per entry, as get_sections does.
        resp["anchor_ambiguous"] = True
        resp["warning"] = " ".join(ambiguous)
    return resp
