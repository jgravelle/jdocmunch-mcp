"""get_related_sections — retrieve structural and/or semantic neighbors (v2.0.0)."""

from __future__ import annotations

import time
from typing import Optional

from ..retrieval.related import get_related
from ..retrieval.related_persist import lookup as _persisted_lookup
from ..storage import DocStore


def get_related_sections(
    repo: str,
    section_id: str,
    mode: str = "both",
    top_n: int = 5,
    min_score: float = 0.6,
    max_per_kind: int = 10,
    storage_path: Optional[str] = None,
) -> dict:
    """Return related sections for ``section_id``.

    ``mode`` ∈ {"structural", "semantic", "both"} (default both).
    Semantic neighbors require an index built with embeddings; absent
    that, the semantic list is empty and a hint is emitted.
    """
    t0 = time.perf_counter()
    if mode not in ("structural", "semantic", "both"):
        return {"error": f"Unknown mode: {mode!r}. Use 'structural', 'semantic', or 'both'."}

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
    # The sidecar and the relation graph are keyed by STORED id; the caller's
    # string may be an anchor reference or an alias, which neither contains.
    tid = target.get("id") or section_id

    # v1.24.0: prefer the persisted adjacency sidecar when present.
    persisted_used = False
    persisted = _persisted_lookup(storage_path, owner, name, tid)
    if persisted is not None:
        out = {
            "section_id": section_id,
            "structural": persisted.get("structural", []) if mode in ("structural", "both") else [],
            "semantic": persisted.get("semantic", []) if mode in ("semantic", "both") else [],
        }
        # Honor caller-supplied limits even on the cached payload.
        out["structural"] = out["structural"][:max_per_kind * 4] if max_per_kind else out["structural"]
        out["semantic"] = [n for n in out["semantic"] if (n.get("score") or 0) >= min_score][:top_n]
        persisted_used = True
    else:
        # jdoc#75: on-demand semantic neighbors read per-section vectors, which
        # now live in the sidecar; materialize them first (no-op when absent or
        # for structural-only mode).
        if mode in ("semantic", "both") and hasattr(index, "_rehydrate_embeddings"):
            index._rehydrate_embeddings()
        out = get_related(
            index.sections,
            tid,
            mode=mode,
            top_n=top_n,
            min_score=min_score,
            max_per_kind=max_per_kind,
        )

    meta: dict = {
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "structural_count": len(out.get("structural", [])),
        "semantic_count": len(out.get("semantic", [])),
        "mode": mode,
        "source": "sidecar" if persisted_used else "on_demand",
    }
    if mode in ("semantic", "both") and not index._has_embeddings():
        meta["hint"] = (
            "Semantic neighbors require embeddings. Re-index with use_embeddings=True, "
            "set GOOGLE_API_KEY / OPENAI_API_KEY, or use openai-compatible + "
            "JDOCMUNCH_OPENAI_COMPAT_URL + "
            "JDOCMUNCH_OPENAI_COMPAT_MODEL."
        )

    resp = {
        "repo": f"{owner}/{name}",
        "section_id": section_id,
        "title": target.get("title", ""),
        "structural": out.get("structural", []),
        "semantic": out.get("semantic", []),
        "_meta": meta,
    }
    if disclosure:
        resp.update(disclosure)
    return resp
