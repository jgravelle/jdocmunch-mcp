"""get_section_context — target section + ancestor headings + optional child summaries."""

import time
from typing import Optional

from ..storage import DocStore
from ..storage.token_tracker import estimate_savings, record_savings, cost_avoided


def get_section_context(
    repo: str,
    section_id: str,
    max_tokens: int = 2000,
    include_children: bool = True,
    include_related: bool = False,
    strip_boilerplate: bool = False,
    storage_path: Optional[str] = None,
) -> dict:
    """Return a section with its surrounding hierarchy context.

    Returns:
        - ``ancestors``: list of {id, title, level} dicts from root down to the
          immediate parent — gives the LLM orientation without bulk content.
        - ``section``: the target section with full content (byte-range read).
        - ``children``: immediate child section summaries (no content reads),
          included when ``include_children=True`` and budget allows.

    Args:
        repo: Repository identifier (owner/repo or bare name).
        section_id: Target section ID.
        max_tokens: Approximate token budget for the target section's content
            (bytes / 4 estimate). Ancestors and child summaries are always
            included — they are metadata-only and cheap.
        include_children: Whether to append immediate child summaries.
        storage_path: Custom storage path.
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

    # --- Ancestor chain (root → parent) ---
    ancestors = []
    visited = set()
    current_parent_id = sec.get("parent_id")
    while current_parent_id:
        if current_parent_id in visited:
            break  # guard against corrupt cycles
        visited.add(current_parent_id)
        ancestor = index.get_section(current_parent_id)
        if not ancestor:
            break
        ancestors.append({
            "id": ancestor["id"],
            "title": ancestor.get("title", ""),
            "level": ancestor.get("level", 0),
        })
        current_parent_id = ancestor.get("parent_id")
    ancestors.reverse()  # root first

    # --- Target section content (byte-range read, budget-capped) ---
    content = store.get_section_content(owner, name, section_id, _index=index)
    if content is None:
        return {"error": f"Content not available for section: {section_id}"}

    boilerplate_stripped_bytes = 0
    if strip_boilerplate:
        from ..retrieval.boilerplate import load as _load_bp, strip as _strip_bp
        fragments = _load_bp(storage_path, owner, name)
        if fragments:
            content, boilerplate_stripped_bytes = _strip_bp(content, fragments)

    max_bytes = max_tokens * 4
    truncated = False
    if len(content.encode("utf-8")) > max_bytes:
        content = content.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
        truncated = True

    result_sec = {k: v for k, v in sec.items() if k not in ("content", "embedding")}
    result_sec["content"] = content
    if truncated:
        result_sec["content_truncated"] = True

    # --- Immediate children (summaries only, no reads) ---
    children = []
    if include_children:
        child_ids = sec.get("children", [])
        for child_id in child_ids:
            child = index.get_section(child_id)
            if child:
                children.append({
                    "id": child["id"],
                    "title": child.get("title", ""),
                    "level": child.get("level", 0),
                    "summary": child.get("summary", ""),
                })

    # --- v2.0.0: optional related-section summaries (adaptive context) ---
    related = []
    if include_related:
        from ..retrieval.related import get_related

        rel = get_related(
            index.sections,
            sec["id"],
            mode="both" if index._has_embeddings() else "structural",
            top_n=5,
            min_score=0.55,
            max_per_kind=4,
        )
        # Annotate each related entry with its summary so the response
        # carries explicit context — never re-loads content for these.
        for entry in rel.get("structural", []) + rel.get("semantic", []):
            related_sec = index.get_section(entry["id"])
            if not related_sec:
                continue
            related.append(
                {
                    "id": entry["id"],
                    "title": entry["title"],
                    "level": entry["level"],
                    "summary": related_sec.get("summary", ""),
                    "kind": entry.get("kind") or "semantic",
                    "score": entry.get("score"),
                }
            )

    # --- Token savings vs full-file read ---
    doc_path = sec.get("doc_path", "")
    raw_bytes = 0
    try:
        import os
        raw_file = store._safe_content_path(store._content_dir(owner, name), doc_path)
        if raw_file:
            raw_bytes = os.path.getsize(raw_file)
    except OSError:
        pass
    response_bytes = len(content.encode("utf-8"))
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total = record_savings(tokens_saved, storage_path)
    ca = cost_avoided(tokens_saved, total)

    latency_ms = int((time.perf_counter() - t0) * 1000)
    out = {
        "ancestors": ancestors,
        "section": result_sec,
        "children": children,
        "_meta": {
            "latency_ms": latency_ms,
            "ancestor_count": len(ancestors),
            "child_count": len(children),
            "tokens_saved": tokens_saved,
            **ca,
        },
    }
    if include_related:
        out["related"] = related
        out["_meta"]["related_count"] = len(related)
    if strip_boilerplate:
        out["_meta"]["boilerplate_stripped_bytes"] = boilerplate_stripped_bytes
    # v1.32.0: citation block.
    out["_meta"]["citation"] = {
        "repo": f"{owner}/{name}",
        "doc_path": sec.get("doc_path", ""),
        "section_id": sec.get("id") or section_id,
        "byte_start": int(sec.get("byte_start", 0) or 0),
        "byte_end": int(sec.get("byte_end", 0) or 0),
        "content_hash": sec.get("content_hash", ""),
        "indexed_at": index.indexed_at,
    }
    if disclosure:
        out.update(disclosure)
    return out
