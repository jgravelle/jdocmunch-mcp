"""Flat TOC: all sections sorted by doc_path + byte_start."""

import time
from typing import Optional

from ..storage import DocStore
from ..storage.token_tracker import estimate_savings, record_savings, cost_avoided


def get_toc(
    repo: str,
    path_glob: Optional[str] = None,
    storage_path: Optional[str] = None,
) -> dict:
    """Return a flat table of contents for all sections in a repo.

    Sections are sorted by (doc_path, byte_start). Content is excluded.

    Args:
        repo: Repository identifier.
        path_glob: v1.36+ — when set, restrict to sections whose doc_path
            matches the fnmatch glob (e.g. ``"api/**/*.md"``,
            ``"reference/*"``). Default None means no filter.
    """
    import fnmatch
    t0 = time.perf_counter()
    store = DocStore(base_path=storage_path)
    owner, name = store._resolve_repo(repo)
    index = store.load_index(owner, name)

    if not index:
        return {"error": f"Repo not found: {repo}"}

    sections = sorted(
        index.sections,
        key=lambda s: (s.get("doc_path", ""), s.get("byte_start", 0))
    )
    if path_glob:
        sections = [s for s in sections if fnmatch.fnmatch(s.get("doc_path", ""), path_glob)]

    toc = []
    for sec in sections:
        toc.append({
            "id": sec.get("id"),
            "doc_path": sec.get("doc_path"),
            "title": sec.get("title"),
            "level": sec.get("level"),
            "summary": sec.get("summary"),
            "parent_id": sec.get("parent_id"),
            "children": sec.get("children"),
            "byte_start": sec.get("byte_start"),
            "byte_end": sec.get("byte_end"),
        })

    # Estimate token savings vs returning full content
    # jdoc#138: the files this TOC covers, read from the cache. Section
    # ``content`` is empty after a reload, which made this 0. Scoped to the
    # path_glob set, not the whole index: those are the files the caller
    # would otherwise have read.
    raw_bytes = store.raw_doc_bytes(owner, name, (s.get("doc_path") for s in sections))
    response_bytes = sum(len(str(t).encode("utf-8")) for t in toc)
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total = record_savings(tokens_saved, storage_path)
    ca = cost_avoided(tokens_saved, total)

    latency_ms = int((time.perf_counter() - t0) * 1000)
    meta = {
        "latency_ms": latency_ms,
        "sections_returned": len(toc),
        "tokens_saved": tokens_saved,
        **ca,
    }
    if path_glob:
        meta["path_glob"] = path_glob
    return {
        "repo": f"{owner}/{name}",
        "sections": toc,
        "section_count": len(toc),
        "_meta": meta,
    }
