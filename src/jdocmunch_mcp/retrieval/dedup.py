"""Section near-duplicate detector (v1.34.0).

Some doc sets carry the same content under different paths: copied
"Configuration" sub-pages across products, FAQ entries reproduced in
multiple guides, license headers expanded into entire fake sections. The
v1.24 boilerplate detector caught the line-level repetition; this module
catches whole-section near-duplicates.

Strategy: shingled token hash (Jaccard over k-shingles) + greedy
clustering. Pure-Python — no MinHash library, no sklearn. Comparable
results at the scales jdocmunch indexes typically hit (a few hundred to
a few thousand sections per repo).

Pipeline:

  1. For each section, build the set of k-shingles (k=5 default token
     windows) over its content.
  2. Compute pairwise Jaccard similarity within an optional length-
     bucket (skip pairs whose lengths differ by > 2x — they cannot be
     near-duplicates).
  3. Cluster sections with similarity ≥ ``min_jaccard`` (default 0.85).
  4. Pick a representative per cluster (the longest section, breaking
     ties by section_id for determinism).

Sidecar at ``~/.doc-index/<owner>/<name>.duplicates.json``:

    {
        "version": 1,
        "captured_at": "...",
        "clusters": [
            {"representative_id": "...", "member_ids": ["...", "..."],
             "min_jaccard": 0.87}
        ]
    }

`dedupe` mode in search_sections collapses cluster members to the
representative, with the suppressed members listed in
``_meta.deduped[<representative_id>]: [member_ids]``.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Iterable, Optional

from .tokenize import tokenize

_FILENAME = "{name}.duplicates.json"
_LOCK = threading.Lock()
_SCHEMA_VERSION = 1
_DEFAULT_K = 5
_DEFAULT_MIN_JACCARD = 0.85

# jdoc#103 (@faxik): `detect_clusters` is all-pairs Jaccard with a length
# pre-filter. The pre-filter prunes to ~30% of the space -- a constant, not a
# change of asymptote -- and measured wall clock grows as ~O(n^2.3):
#
#     5,931 sections   7.4 s      12,493 -> 42.2 s      25,329 -> 206.7 s
#
# On a 187,985-section corpus the fit predicts ~5.5 hours, and the run gives no
# diagnostic: one core at 100% with no progress output.
#
# The code comment always said "fine up to a few thousand sections" and was
# right. What was missing is that nothing ENFORCED or SURFACED that ceiling: the
# sidecar ran unconditionally on every index with no flag and no size guard, and
# the caller's bare `except Exception: pass` meant a skip would have been silent
# too. The ceiling is now a number the caller can see and move.
_DEFAULT_SECTION_CEILING = 20_000
SECTION_CEILING_ENV_VAR = "JDOCMUNCH_DEDUP_MAX_SECTIONS"


def section_ceiling() -> int:
    """Section count above which the sidecar is skipped. 0 disables the guard.

    Env-only and deliberately generous: 20k sits above every published benchmark
    corpus (largest 10.4k) and above the reporter's 25.3k-section single-repo
    case only slightly, so the guard fires where the curve has actually turned
    over rather than where it is merely non-linear. An invalid value falls back
    to the default -- a typo must not silently uncap an O(n^2.3) loop.
    """
    import os
    raw = os.environ.get(SECTION_CEILING_ENV_VAR)
    if raw is None:
        return _DEFAULT_SECTION_CEILING
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return _DEFAULT_SECTION_CEILING
    return value if value >= 0 else _DEFAULT_SECTION_CEILING
# Skip sections shorter than this many tokens — the shingle set is too
# small to produce stable Jaccard.
_MIN_TOKENS = 10


def _path(base_path: Optional[str], owner: str, name: str) -> Path:
    from ..storage.paths import resolve_root  # jdoc#146
    root = resolve_root(base_path)
    safe_owner = (owner or "").strip().replace("/", "_").replace("\\", "_") or "_"
    safe_name = (name or "").strip().replace("/", "_").replace("\\", "_") or "_"
    return root / safe_owner / _FILENAME.format(name=safe_name)


def _shingles(tokens: list[str], k: int = _DEFAULT_K) -> set[str]:
    if len(tokens) < k:
        return set()
    return {" ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)


def detect_clusters(
    sections: Iterable,
    *,
    k: int = _DEFAULT_K,
    min_jaccard: float = _DEFAULT_MIN_JACCARD,
) -> list[dict]:
    """Return a list of duplicate-cluster dicts.

    Each cluster: ``{representative_id, member_ids:[...], min_jaccard}``
    where ``member_ids`` includes every section in the cluster (including
    the representative). Singletons are not emitted. Length pre-filter
    skips pairs whose token counts differ by > 2× — they cannot be
    near-duplicates by definition.
    """
    sigs: list[tuple[str, int, set]] = []
    for sec in sections:
        sid = sec.get("id") if isinstance(sec, dict) else getattr(sec, "id", "")
        text = (sec.get("content") if isinstance(sec, dict)
                else getattr(sec, "content", "")) or ""
        if not sid:
            continue
        toks = tokenize(text)
        if len(toks) < _MIN_TOKENS:
            continue
        sh = _shingles(toks, k=k)
        if not sh:
            continue
        sigs.append((sid, len(toks), sh))

    # Sort by token count so length pre-filter is cheap.
    sigs.sort(key=lambda s: s[1])
    n = len(sigs)
    parent: dict[str, str] = {sid: sid for sid, _, _ in sigs}
    cluster_min: dict[tuple[str, str], float] = {}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str, sim: float) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        parent[ra] = rb
        cluster_min[(rb, "min")] = min(cluster_min.get((rb, "min"), 1.0), sim)
        cluster_min[(ra, "min")] = min(cluster_min.get((ra, "min"), 1.0), sim)

    # O(N²) loop, length-pruned. Fine up to a few thousand sections.
    for i in range(n):
        sid_i, len_i, sh_i = sigs[i]
        for j in range(i + 1, n):
            sid_j, len_j, sh_j = sigs[j]
            if len_j > 2 * len_i:
                # Sorted ascending — no further j can satisfy the bound.
                break
            sim = _jaccard(sh_i, sh_j)
            if sim >= min_jaccard:
                union(sid_i, sid_j, sim)

    # Group by root.
    groups: dict[str, list[tuple[str, int]]] = {}
    for sid, length, _ in sigs:
        root = find(sid)
        groups.setdefault(root, []).append((sid, length))

    clusters: list[dict] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        # Representative: longest, ties broken by section_id for determinism.
        members.sort(key=lambda x: (-x[1], x[0]))
        rep_id = members[0][0]
        member_ids = sorted(m[0] for m in members)
        # Compute final cluster min jaccard from any pair in the cluster.
        rep_sh = next(sh for sid, _, sh in sigs if sid == rep_id)
        sims = []
        for mid in member_ids:
            if mid == rep_id:
                continue
            sh = next(sh for sid, _, sh in sigs if sid == mid)
            sims.append(_jaccard(rep_sh, sh))
        cluster_min_val = min(sims) if sims else min_jaccard
        clusters.append(
            {
                "representative_id": rep_id,
                "member_ids": member_ids,
                "min_jaccard": round(cluster_min_val, 4),
            }
        )
    # Sort clusters by representative_id for stable on-disk output.
    clusters.sort(key=lambda c: c["representative_id"])
    return clusters


_LAST_SKIP: Optional[dict] = None


def last_skip_reason() -> Optional[dict]:
    """Why the most recent `write` produced no clusters, or None if it ran.

    A skipped sidecar that reports nothing is indistinguishable from one that
    found no duplicates.
    """
    return _LAST_SKIP


def write(
    base_path: Optional[str],
    owner: str,
    name: str,
    sections: Iterable,
    *,
    k: int = _DEFAULT_K,
    min_jaccard: float = _DEFAULT_MIN_JACCARD,
    enabled: bool = True,
    max_sections: Optional[int] = None,
) -> int:
    """Detect + persist clusters. Returns cluster count.

    ``skipped`` is reported through ``last_skip_reason`` rather than raised: the
    caller swallows exceptions, so raising here would restore the silence this
    change exists to remove.
    """
    global _LAST_SKIP
    _LAST_SKIP = None
    if not enabled:
        _LAST_SKIP = {"reason": "disabled"}
        return 0
    sections = list(sections)
    ceiling = section_ceiling() if max_sections is None else max_sections
    if ceiling and len(sections) > ceiling:
        _LAST_SKIP = {
            "reason": "section_count",
            "sections": len(sections),
            "ceiling": ceiling,
            "detail": (
                "Near-duplicate clustering is all-pairs and grows ~O(n^2.3); it was "
                "skipped rather than run for hours. Raise or disable with "
                f"{SECTION_CEILING_ENV_VAR} (0 disables the guard)."
            ),
        }
        return 0
    clusters = detect_clusters(sections, k=k, min_jaccard=min_jaccard)
    path = _path(base_path, owner, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "version": _SCHEMA_VERSION,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "clusters": clusters,
    }
    with _LOCK:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
    return len(clusters)


def load(base_path: Optional[str], owner: str, name: str) -> list[dict]:
    """Return persisted cluster list, or [] when absent or corrupt."""
    path = _path(base_path, owner, name)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != _SCHEMA_VERSION:
            return []
        clusters = data.get("clusters") or []
        return [c for c in clusters if isinstance(c, dict)]
    except Exception:
        return []


def build_member_to_rep(clusters: list[dict]) -> dict[str, str]:
    """Return ``{member_id: representative_id}`` lookup. Excludes reps."""
    out: dict[str, str] = {}
    for c in clusters:
        rep = c.get("representative_id")
        if not rep:
            continue
        for m in (c.get("member_ids") or []):
            if m and m != rep:
                out[m] = rep
    return out


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
