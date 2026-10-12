"""Filter-only count without ranking or content (v1.59.0).

`search_sections` runs the full retrieval pipeline (BM25, semantic
fusion, post-filters, scoring, attach_scores). When the agent only
needs a count for a filter combination — "how many troubleshooting
sections in api/* tagged #api?" — paying for ranking is wasteful.

This tool re-uses the same filter semantics (path_glob, role(s),
tag(s), level range, byte_length range) but skips the BM25 stage and
all per-result enrichment. Returns just the count.
"""

from __future__ import annotations

import time
from typing import Optional

from ..storage import DocStore
from ..storage.doc_store import path_glob_matches


def count_sections(
    repo: str,
    doc_path: Optional[str] = None,
    path_glob: Optional[str] = None,
    role: Optional[str] = None,
    roles: Optional[list] = None,
    exclude_roles: Optional[list] = None,
    tags: Optional[list] = None,
    exclude_tags: Optional[list] = None,
    min_level: Optional[int] = None,
    max_level: Optional[int] = None,
    min_byte_length: Optional[int] = None,
    max_byte_length: Optional[int] = None,
    storage_path: Optional[str] = None,
) -> dict:
    """Return the count of sections matching all listed filters.

    Filters apply with AND semantics across axes; within a single axis
    (e.g. `tags`), AND-include is preserved for parity with v1.45 + v1.51
    + v1.52. Pass nothing to get the full section count.
    """
    t0 = time.perf_counter()
    store = DocStore(base_path=storage_path)
    owner, name = store._resolve_repo(repo)
    index = store.load_index(owner, name)

    if not index:
        return {"error": f"Repo not found: {repo}"}

    role_norm = (role or "").strip().lower() or None
    role_in = {r.strip().lower() for r in (roles or [])
               if isinstance(r, str) and r.strip()}
    role_out = {r.strip().lower() for r in (exclude_roles or [])
                if isinstance(r, str) and r.strip()}
    tag_in = {t.strip().lower() for t in (tags or [])
              if isinstance(t, str) and t.strip()}
    tag_out = {t.strip().lower() for t in (exclude_tags or [])
               if isinstance(t, str) and t.strip()}

    count = 0
    for sec in index.sections:
        dp = sec.get("doc_path") or ""
        if doc_path is not None and dp != doc_path:
            continue
        if path_glob and not path_glob_matches(dp, path_glob):
            continue
        # Level filter.
        lvl = sec.get("level")
        if min_level is not None and (not isinstance(lvl, int) or lvl < min_level):
            continue
        if max_level is not None and (not isinstance(lvl, int) or lvl > max_level):
            continue
        # Byte length.
        bs = int(sec.get("byte_start", 0) or 0)
        be = int(sec.get("byte_end", 0) or 0)
        length = max(0, be - bs)
        if min_byte_length is not None and length < min_byte_length:
            continue
        if max_byte_length is not None and length > max_byte_length:
            continue
        # Role.
        sec_role = ((sec.get("metadata") or {}).get("role") or "").strip().lower()
        if role_norm and sec_role != role_norm:
            continue
        if role_in and sec_role not in role_in:
            continue
        if role_out and sec_role in role_out:
            continue
        # Tags.
        if tag_in or tag_out:
            sec_tags = {str(t).strip().lower() for t in (sec.get("tags") or [])
                        if isinstance(t, str)}
            if tag_in and not tag_in.issubset(sec_tags):
                continue
            if tag_out and (tag_out & sec_tags):
                continue
        count += 1

    return {
        "repo": f"{owner}/{name}",
        "count": count,
        "total_sections": len(index.sections),
        "_meta": {
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        },
    }
