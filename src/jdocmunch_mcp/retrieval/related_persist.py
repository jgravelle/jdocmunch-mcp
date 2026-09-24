"""Persisted related-section adjacency list (v1.24.0).

The v1.20 ``retrieval/related.py`` module computes structural and
semantic neighbors on demand. For large indexes this is acceptable for
occasional calls but expensive when ``get_related_sections`` runs in a
hot path or when ``get_section_context(include_related=True)`` is used
broadly. v1.24 builds the adjacency list once at index time and
persists it as a JSON sidecar; the on-demand path stays as the fallback
when the sidecar is missing or stale.

Sidecar location: ``~/.doc-index/<owner>/<name>.related.json``.

Schema:

    {
        "version": 1,
        "captured_at": "...",
        "section_count": int,
        "by_section": {
            "<section_id>": {
                "structural": [{"id", "title", "level", "kind"}, ...],
                "semantic":   [{"id", "title", "level", "score"}, ...]
            },
            ...
        }
    }

Build cost is O(N) for structural edges (parent/child/sibling) since
v1.64.1: a single by-id map and parent→children map are precomputed once
and threaded into every per-section ``structural_neighbors`` call. The
v1.24–v1.63 path was O(N²) (jdoc#14, reported by @LuigiNicaPRO). Semantic
edges are computed when embeddings are present and run O(N²) over the
embedding set; we cap output at top-5 per section so the sidecar size
stays linear in N.

This module is purely additive on the 1.x line — `get_related_sections`
still returns identical shapes when the sidecar is absent.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

from .related import (
    _by_id as _related_by_id,
    _children_by_parent,
    semantic_neighbors,
    structural_neighbors,
)

_FILENAME = "{name}.related.json"
_LOCK = threading.Lock()
_SCHEMA_VERSION = 1

logger = logging.getLogger(__name__)

# Rows of the cosine matrix computed per matmul; bounds peak extra memory to
# roughly _SEMANTIC_ROW_BLOCK * N * 8 bytes regardless of corpus size.
_SEMANTIC_ROW_BLOCK = 512

# When numpy is unavailable the semantic build falls back to the pure-Python
# all-pairs path (O(N^2) interpreted cosine). Above this many embedded sections
# that fallback would stall indexing (jdoc#62), so we skip semantic edges and
# log instead. The numpy fast path -- the normal embedded-corpus case, since the
# embedding stack already pulls numpy in -- has no such cap.
_PUREPY_SEMANTIC_MAX = 2_000


def _path(base_path: Optional[str], owner: str, name: str) -> Path:
    from ..storage.paths import resolve_root  # jdoc#146
    root = resolve_root(base_path)
    safe_owner = (owner or "").strip().replace("/", "_").replace("\\", "_") or "_"
    safe_name = (name or "").strip().replace("/", "_").replace("\\", "_") or "_"
    return root / safe_owner / _FILENAME.format(name=safe_name)


def _semantic_edges_matrix(section_dicts, *, top_n, min_score):
    """All-pairs semantic neighbors in one normalized matmul (jdoc#62).

    Equivalent to calling ``semantic_neighbors`` for every section, but computes
    the whole cosine matrix in bulk rather than one pure-Python pair at a time.
    Returns ``{section_id: [edge, ...]}`` for sections that carry an embedding
    (others are absent; the caller maps them to ``[]``). Returns ``None`` when
    numpy cannot be imported, so ``build`` can fall back to the pure-Python path.

    Parity with ``semantic_neighbors`` is deliberate and exact:
      * cosine via L2-normalized dot product (a zero vector scores 0 against
        everything, matching ``cosine_similarity``'s ``norm == 0 -> 0.0`` guard);
      * keep edges with ``score >= min_score``, self excluded;
      * order by score descending, ties broken by ascending section order (the
        reference appends in order, then stable-sorts ``reverse=True``);
      * ``top_n`` cap; each edge ``{id, title, level, score}`` rounded to 4dp.
    """
    # Embedded sections only, kept in original order (drives the tie-break).
    ids: list = []
    titles: list = []
    levels: list = []
    vectors: list = []
    for sec in section_dicts:
        sid = sec.get("id")
        emb = sec.get("embedding")
        if sid and emb:
            ids.append(sid)
            titles.append(sec.get("title", ""))
            levels.append(sec.get("level", 0))
            vectors.append(emb)
    if not vectors:
        # jdoc#119: return BEFORE importing numpy. A lexical-only corpus has no
        # embeddings at all, so the import is pure cost for a function that is
        # about to return an empty map either way. It became a per-refresh cost
        # when jdoc#117 put the sidecar rebuild on the incremental path, and on
        # a machine where imports are slow it is the whole latency of the call.
        # Output is unchanged: `build` maps an absent id to [] exactly as it
        # does for the numpy-missing fallback on a corpus with no vectors.
        return {}

    try:
        import numpy as np
    except Exception:
        return None

    # float64 so cosine values round to the same 4 decimals as the pure-Python
    # reference. Normalize once; cosine is then a plain dot product.
    matrix = np.asarray(vectors, dtype=np.float64)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0  # zero vector stays zero -> scores 0, never NaN
    matrix /= norms

    n = matrix.shape[0]
    edges: dict = {}
    for start in range(0, n, _SEMANTIC_ROW_BLOCK):
        block = matrix[start:start + _SEMANTIC_ROW_BLOCK]   # (b, D)
        sims = block @ matrix.T                              # (b, N) cosine
        for offset in range(sims.shape[0]):
            i = start + offset
            row = sims[offset]
            row[i] = -1.0                                    # never self
            cand = np.nonzero(row >= min_score)[0]
            if cand.size == 0:
                edges[ids[i]] = []
                continue
            # lexsort's last key is primary: score desc, then original index asc.
            chosen = cand[np.lexsort((cand, -row[cand]))][:top_n]
            edges[ids[i]] = [
                {
                    "id": ids[j],
                    "title": titles[j],
                    "level": levels[j],
                    "score": round(float(row[j]), 4),
                }
                for j in chosen
            ]
    return edges


def build(sections: list, *, top_n_semantic: int = 5, min_score: float = 0.6) -> dict:
    """Compute the full adjacency list from a list of section dicts.

    Hot-path build for `index_local`: was O(N^2) per outer iteration (jdoc#14)
    because `section_dicts`, the by-id map, and per-parent child scans were
    rebuilt inside the loop. Precomputed once now; structural build is O(N).
    The semantic half is vectorized via a single numpy matmul (jdoc#62) when
    numpy is present, with a pure-Python all-pairs fallback (size-guarded)
    when it is not. Output is unchanged either way.
    """
    # Single normalization pass — Section objects → dicts.
    section_dicts = [
        s if isinstance(s, dict) else _section_to_dict(s) for s in sections
    ]
    by_id_cache = _related_by_id(section_dicts)
    children_cache = _children_by_parent(section_dicts)

    # jdoc#62: compute every semantic edge in one vectorized pass when numpy
    # is available, instead of an O(N^2) per-section pure-Python cosine. The
    # result is identical to calling semantic_neighbors() for every section.
    semantic_map = _semantic_edges_matrix(
        section_dicts, top_n=top_n_semantic, min_score=min_score
    )
    skip_semantic = False
    if semantic_map is None:
        embedded = sum(
            1 for s in section_dicts if s.get("id") and s.get("embedding")
        )
        if embedded > _PUREPY_SEMANTIC_MAX:
            logger.warning(
                "related sidecar: numpy unavailable and %d embedded sections "
                "exceed the pure-Python cap (%d); skipping semantic edges. "
                "Install numpy for full semantic neighbors at this scale.",
                embedded, _PUREPY_SEMANTIC_MAX,
            )
            skip_semantic = True

    by_section: dict = {}
    for sec in section_dicts:
        sid = sec.get("id")
        if not sid:
            continue
        struct = structural_neighbors(
            section_dicts,
            sid,
            _by_id_cache=by_id_cache,
            _children_cache=children_cache,
        )
        if semantic_map is not None:
            sem = semantic_map.get(sid, [])
        elif skip_semantic:
            sem = []
        else:
            # numpy absent and corpus small enough: identical pure-Python path.
            sem = semantic_neighbors(
                section_dicts,
                sid,
                top_n=top_n_semantic,
                min_score=min_score,
                _by_id_cache=by_id_cache,
            )
        by_section[sid] = {"structural": struct, "semantic": sem}
    return {
        "version": _SCHEMA_VERSION,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "section_count": len(by_section),
        "by_section": by_section,
    }


def _section_to_dict(sec) -> dict:
    """Best-effort dict view of a Section dataclass for the structural walk."""
    return {
        "id": getattr(sec, "id", ""),
        "title": getattr(sec, "title", ""),
        "level": getattr(sec, "level", 0),
        "parent_id": getattr(sec, "parent_id", "") or "",
        "embedding": getattr(sec, "embedding", []) or [],
    }


def write(
    base_path: Optional[str],
    owner: str,
    name: str,
    sections: list,
    *,
    top_n_semantic: int = 5,
    min_score: float = 0.6,
) -> int:
    """Build + atomically write the adjacency list. Returns section count."""
    data = build(sections, top_n_semantic=top_n_semantic, min_score=min_score)
    path = _path(base_path, owner, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with _LOCK:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)
    return data["section_count"]


def load(base_path: Optional[str], owner: str, name: str) -> Optional[dict]:
    """Return the persisted adjacency dict, or None when absent / corrupt."""
    path = _path(base_path, owner, name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        if data.get("version") != _SCHEMA_VERSION:
            return None
        return data
    except Exception:
        return None


def lookup(
    base_path: Optional[str],
    owner: str,
    name: str,
    section_id: str,
) -> Optional[dict]:
    """Return ``{structural, semantic}`` for one section from the sidecar.

    Returns None when the sidecar is absent or the section_id is missing
    from it (caller should fall back to on-demand build).
    """
    data = load(base_path, owner, name)
    if not data:
        return None
    return (data.get("by_section") or {}).get(section_id)


def purge(base_path: Optional[str], owner: str, name: str) -> bool:
    """Delete the sidecar. Returns True on success."""
    path = _path(base_path, owner, name)
    if path.exists():
        try:
            path.unlink()
            return True
        except OSError:
            return False
    return False
