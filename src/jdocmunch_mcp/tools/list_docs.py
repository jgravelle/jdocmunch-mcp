"""Doc-level inventory of an indexed repo (v1.55.0).

`list_repos` enumerates indexed repos. `get_toc_tree` returns section
trees per doc. Nothing returned a flat doc-level inventory: which
documents exist in this repo, how many sections each has, what format,
how many bytes on disk.

This tool fills that gap. Pure additive aggregation, handle-only —
nothing returned here requires a content read.
"""

from __future__ import annotations

import os
import time
from typing import Optional

from ..storage import DocStore


def list_docs(
    repo: str,
    storage_path: Optional[str] = None,
) -> dict:
    """Return a flat list of indexed documents with per-doc stats.

    For each doc:
      - ``doc_path`` (POSIX-normalized relative path)
      - ``section_count`` (sections whose doc_path == this)
      - ``format`` (file extension, lowercase)
      - ``byte_size`` (current on-disk size of the cached source file,
        or 0 when the cache is missing — signal of a stale index)

    Sorted by `doc_path` ascending for stable output.
    """
    t0 = time.perf_counter()
    store = DocStore(base_path=storage_path)
    owner, name = store._resolve_repo(repo)
    index = store.load_index(owner, name)

    if not index:
        return {"error": f"Repo not found: {repo}"}

    counts: dict[str, int] = {}
    for sec in index.sections:
        dp = sec.get("doc_path") or ""
        if dp:
            counts[dp] = counts.get(dp, 0) + 1

    docs: list = []
    total_bytes = 0
    with_mtime = 0
    # jdoc#136: the STORED time, not a stat. `byte_size` below stats every
    # document anyway, so a live mtime would be free and fresher — and it would
    # make this key mean filesystem-time on a local index and commit-time on a
    # repository one. One index, one meaning; the value is as of the last
    # indexing pass, which is the same pass that updated the hash beside it.
    mtimes = index.file_mtimes or {}
    content_dir = store._content_dir(owner, name)
    for dp, n in counts.items():
        ext = os.path.splitext(dp)[1].lower()
        size = 0
        try:
            cached = store._safe_content_path(content_dir, dp)
            if cached and cached.exists():
                size = cached.stat().st_size
        except OSError:
            size = 0
        total_bytes += size
        entry = {
            "doc_path": dp,
            "section_count": n,
            "format": ext or None,
            "byte_size": size,
        }
        # ⚠ Omitted, never null: an absent key means the time could not be
        # established, which is not the same claim as "this document is old".
        mt = mtimes.get(dp)
        if mt:
            entry["mtime"] = mt
            with_mtime += 1
        docs.append(entry)

    docs.sort(key=lambda d: d["doc_path"])

    return {
        "repo": f"{owner}/{name}",
        "docs": docs,
        "doc_count": len(docs),
        "total_section_count": len(index.sections),
        "total_byte_size": total_bytes,
        "_meta": {
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "indexed_at": index.indexed_at,
            # jdoc#136. ⚠⚠ Always present, including as 0. Without a count, an
            # index written before this change, one where nothing has a recorded
            # time, and a partially filled one are indistinguishable to a caller
            # — a number computed and then withheld is the same defect as not
            # computing it. Compare against `doc_count` to tell the three apart.
            "docs_with_mtime": with_mtime,
        },
    }
