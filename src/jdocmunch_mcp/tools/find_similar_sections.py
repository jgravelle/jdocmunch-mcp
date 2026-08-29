"""find_similar_sections — multi-signal section dedup detection (v1.60.0).

Inspired by jcodemunch-mcp's ``find_similar_symbols``. Surfaces clusters
of overlapping or duplicate sections so a maintainer can consolidate.

Every wiki of size accumulates "three pages that all say the same
thing." Manual grep finds title duplicates; embedding cosine alone
floods the result with related-but-distinct topics. This tool fuses two
signals — embedding cosine (when available) and title+body lexical
Jaccard — gated by a cheap title-token pre-filter to keep cost bounded
on large wikis.

⚠⚠ The body channel reads the section's ACTUAL BYTES (jdoc#129). It
used to read ``summary``, and under ``index_local(use_ai_summaries=
False)`` a summary IS the heading text — so ``body_tokens ==
title_tokens``, the 70/30 lexical fusion was one channel counted twice,
and every pair sharing a heading name scored exactly 1.0 and reported
``near_duplicate``. Measured on a 955-section corpus: 8 of 8 clusters
were that artifact. Reading bodies for the whole examined set costs
0.24 s at the default 1000-section cap; the title pre-filter admits 94%
of sections into some surviving pair, so deferring the read until after
it saves ~6% and is not worth the two-phase complexity.

``signal`` states which channels actually carried information. A pair
whose body adds nothing beyond its own title is ``title_only`` and can
never be ``near_duplicate`` — no unique body tokens on EITHER side means
the comparison had no content, not that the sections are identical.

Output is cluster-shaped: one entry per group of overlapping sections,
each with a ``canonical`` (recommended keeper) and a list of
``variants`` to fold in. Verdict tiers per cluster:

  - ``near_duplicate``    — combined score ≥ near_duplicate_threshold
  - ``overlapping_topic`` — combined score ∈ [min_score, threshold)
  - ``parallel_tutorial`` — overlap detected and *all* cluster members
    live in different doc directories (suggests parallel guides that
    should reference each other rather than be merged)

Read-only.
"""

from __future__ import annotations

import posixpath
import time
from typing import Optional

from ..embeddings import cosine_similarity
from ..retrieval.tokenize import tokenize_unique
from ..storage.doc_store import DocStore
from .get_backlinks import get_backlinks


_DEFAULT_MAX_SECTIONS = 1000
_TITLE_PREFILTER_MIN = 0.1  # title Jaccard floor before paying for cosine


def _byte_overlap_ratio(a: dict, b: dict) -> float:
    """Fraction of the smaller section's byte range that overlaps the
    other. 1.0 means full containment; 0.0 means disjoint.

    Used to filter out parser-artifact pairs: most parsers emit a
    doc-level wrapper section AND the heading-level section for the
    same content, with one containing the other. Those duplicates are
    not interesting to a dedup-detection caller.
    """
    a_s, a_e = int(a.get("byte_start", 0) or 0), int(a.get("byte_end", 0) or 0)
    b_s, b_e = int(b.get("byte_start", 0) or 0), int(b.get("byte_end", 0) or 0)
    if a_e <= a_s or b_e <= b_s:
        return 0.0
    inter = max(0, min(a_e, b_e) - max(a_s, b_s))
    smaller = min(a_e - a_s, b_e - b_s)
    if smaller <= 0:
        return 0.0
    return inter / smaller


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)


class _UnionFind:
    """Tiny disjoint-set, just enough for cluster collapse."""
    def __init__(self):
        self._parent: dict[str, str] = {}

    def add(self, x: str) -> None:
        if x not in self._parent:
            self._parent[x] = x

    def find(self, x: str) -> str:
        self.add(x)
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        # Path compression
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb

    def groups(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for node in self._parent:
            root = self.find(node)
            out.setdefault(root, []).append(node)
        return out


def _section_tokens(
    sec: dict, body_text: Optional[str]
) -> tuple[set[str], set[str], str]:
    """Return (title_tokens, body_tokens, body_source).

    ``body_text`` is the section's real bytes. It may be None/empty when
    the content mirror cannot serve them (missing file, unreadable
    range); we then fall back to ``summary``, and ``body_source`` says
    so. ⚠ That fallback is exactly the jdoc#129 degeneracy when summaries
    are heading text, which is why the caller must not treat a
    ``summary``/``title_only`` body as an independent channel.
    """
    title_tokens = tokenize_unique(sec.get("title", "") or "")
    if body_text:
        body_tokens = tokenize_unique(body_text)
        source = "content"
    else:
        body_tokens = tokenize_unique(sec.get("summary", "") or "")
        source = "summary"
    if body_tokens and body_tokens <= title_tokens:
        # The body repeats the title and nothing else: no independent
        # signal, whatever it was read from.
        source = "title_only"
    return title_tokens, body_tokens, source


def _pair_is_title_only(a: dict, b: dict) -> bool:
    """True when NEITHER side's body adds a token beyond its own title.

    The fused lexical score is then ``0.70 * x + 0.30 * x`` over one
    input, and ``differs_by`` reports no unique body tokens on either
    side — which is indistinguishable from "I did not read either side."
    Fail closed rather than report a two-channel verdict.
    """
    return a["body_source"] == "title_only" and b["body_source"] == "title_only"


def _combined_score(
    title_jac: float, body_jac: float, cosine: Optional[float]
) -> tuple[float, str]:
    """Fuse signals into one score in [0, 1]; return (score, dominant_signal).

    When embeddings exist, cosine dominates (60%) but title + body keep
    a 40% weight so a high-cosine pair with unrelated titles doesn't
    cluster as "duplicate." Without embeddings, score is 70% body + 30%
    title — title alone is too easy to fool.
    """
    if cosine is not None:
        score = 0.60 * cosine + 0.25 * body_jac + 0.15 * title_jac
        dominant = "embedding" if cosine >= max(body_jac, title_jac) else "lexical"
    else:
        score = 0.70 * body_jac + 0.30 * title_jac
        dominant = "lexical"
    return round(score, 4), dominant


def _differs_by(
    a_tokens: set[str],
    b_tokens: set[str],
    a_title: set[str],
    b_title: set[str],
    body_signal: str = "body",
) -> dict:
    """Per-pair breakdown of which dimensions differ. Cheap and informative.

    body_only: tokens unique to one side's body (5 worst — i.e. most
               distinctive on each side).
    title_diff: tokens unique to one title.
    body_signal: ``body`` when at least one side's bytes were read and
               carried something its title did not; ``title_only`` when
               neither did. ⚠ Without this key an empty ``body_unique_a``
               AND ``body_unique_b`` reads as "identical bodies" when it
               actually means "no body was compared" (jdoc#129).
    """
    body_only_a = sorted(a_tokens - b_tokens)[:5]
    body_only_b = sorted(b_tokens - a_tokens)[:5]
    return {
        "title_diff_a": sorted(a_title - b_title)[:5],
        "title_diff_b": sorted(b_title - a_title)[:5],
        "body_unique_a": body_only_a,
        "body_unique_b": body_only_b,
        "body_signal": body_signal,
    }


def _canonical_score(sec: dict, backlink_count: int) -> float:
    """Higher wins. Backlinks dominate (a section others reference is
    the de-facto canonical), with byte-length as tiebreaker (longer
    sections usually have more substance)."""
    bytes_len = max(0, int(sec.get("byte_end", 0) or 0) - int(sec.get("byte_start", 0) or 0))
    return backlink_count * 100.0 + bytes_len * 0.001


def _verdict(
    max_score: float,
    near_duplicate_threshold: float,
    doc_dirs: set[str],
    title_only: bool = False,
) -> str:
    """⚠ ``max_score`` here is the cluster's EVIDENCE score — the best
    pair that actually compared bodies — not its best pair overall.

    ``title_only`` clusters (no body-bearing pair at all) can never be
    ``near_duplicate``: every pair holding them together compared a body
    against itself, so the score describes heading overlap alone.
    Reporting a duplicate on that is the jdoc#129 wrong verdict. The
    topic overlap is real and is still reported.
    """
    if max_score >= near_duplicate_threshold and not title_only:
        return "near_duplicate"
    # All members in different directories → parallel tutorials.
    if len(doc_dirs) > 1 and len(doc_dirs) == len({d for d in doc_dirs}):
        # Only mark as parallel_tutorial when the cluster's members
        # actually live in N distinct directories (rare in same-doc
        # clusters, common in cross-tutorial overlap).
        return "parallel_tutorial"
    return "overlapping_topic"


def find_similar_sections(
    repo: str,
    min_score: float = 0.7,
    near_duplicate_threshold: float = 0.92,
    max_clusters: int = 50,
    exclude_same_doc: bool = False,
    max_sections: int = _DEFAULT_MAX_SECTIONS,
    storage_path: Optional[str] = None,
) -> dict:
    """Surface clusters of overlapping or duplicate sections.

    Args:
        repo: Repository identifier (owner/name).
        min_score: Pairwise score floor for clustering. Default 0.7.
        near_duplicate_threshold: Score at/above which a cluster is
            flagged ``near_duplicate``. Default 0.92.
        max_clusters: Cap on number of clusters returned. Default 50.
        exclude_same_doc: When True, pairs in the same doc don't count
            toward clustering. Useful when the wiki has long pages with
            repeated section structures. Default False.
        max_sections: Hard cap on sections examined. Default 1000.
        storage_path: Custom storage path.

    Returns:
        ``{result: {clusters: [...], cluster_count, ...}, _meta}``.
        Each cluster and variant carries ``signal``: ``body`` when the
        comparison read real section bytes that said something their
        titles did not, ``title_only`` when it did not. ⚠ A
        ``title_only`` cluster is never ``near_duplicate`` — see the
        module docstring for why that is a refusal and not a downgrade.
        ``_meta.body_sources`` counts how each examined section's body
        was obtained (``content`` / ``summary`` / ``title_only``).
    """
    t0 = time.perf_counter()
    store = DocStore(base_path=storage_path)
    owner, name = store._resolve_repo(repo)
    index = store.load_index(owner, name)
    if not index:
        return {"error": f"Repo not found: {repo}"}

    has_embeddings = bool(index._has_embeddings()) if hasattr(index, "_has_embeddings") else False
    # jdoc#75: embedding vectors live in the sidecar; stream them onto the
    # section dicts before the per-section reads below.
    if has_embeddings and hasattr(index, "_rehydrate_embeddings"):
        index._rehydrate_embeddings()

    all_sections = list(index.sections)
    examined = all_sections[:max_sections]

    # Precompute token sets + embeddings + identity tuples.
    # `_ensure_content` serves the section's bytes through the index's
    # byte-range loader and memoises per id, so a section read here is
    # not re-read by anything else in this run.
    loader = getattr(index, "_ensure_content", None)
    cache: list[dict] = []
    skipped_no_content = 0
    body_sources: dict[str, int] = {}
    for sec in examined:
        sid = sec.get("id")
        if not sid:
            continue
        # Skip parser-artifact ghost sections: doc-level wrappers with
        # zero byte range. They share content with the real heading
        # section under the same doc and would otherwise cluster as
        # near-duplicates of themselves.
        b_s = int(sec.get("byte_start", 0) or 0)
        b_e = int(sec.get("byte_end", 0) or 0)
        if b_e <= b_s:
            continue
        # jdoc#129: read the section's ACTUAL bytes. This used to be
        # `sec.get("summary")`, which under use_ai_summaries=False IS the
        # heading text — so the body channel was the title channel and
        # the fusion counted one input twice. Measured cost of reading
        # the whole examined set on a 955-section corpus: 0.24 s.
        # Deferring the read until after the title pre-filter saves only
        # ~6% there (94% of sections survive into some pair), so the
        # two-phase version is not worth its complexity.
        body_text = ""
        if loader is not None:
            try:
                body_text = loader(sec) or ""
            except Exception:
                body_text = ""
        title_tokens, body_tokens, body_source = _section_tokens(sec, body_text)
        emb = sec.get("embedding") if has_embeddings else None
        cache.append({
            "id": sid,
            "doc_path": sec.get("doc_path", ""),
            "title": sec.get("title", ""),
            "byte_start": sec.get("byte_start", 0),
            "byte_end": sec.get("byte_end", 0),
            "title_tokens": title_tokens,
            "body_tokens": body_tokens,
            "body_source": body_source,
            "embedding": emb,
            "sec": sec,
        })
        body_sources[body_source] = body_sources.get(body_source, 0) + 1
        if not body_tokens and not title_tokens:
            skipped_no_content += 1

    # Pairwise scan with title-Jaccard pre-filter. Quadratic-but-bounded.
    uf = _UnionFind()
    pair_scores: dict[tuple[str, str], dict] = {}
    title_only_pairs = 0
    n = len(cache)
    for i in range(n):
        a = cache[i]
        if not a["title_tokens"]:
            continue
        for j in range(i + 1, n):
            b = cache[j]
            if exclude_same_doc and a["doc_path"] == b["doc_path"]:
                continue
            if not b["title_tokens"]:
                continue
            # Parser-artifact filter: most doc parsers emit a doc-level
            # wrapper PLUS the heading-level section for the same bytes.
            # Skip when one section's range substantially contains the
            # other's (>0.5 of the smaller range).
            if a["doc_path"] == b["doc_path"] and _byte_overlap_ratio(a["sec"], b["sec"]) > 0.5:
                continue
            title_jac = _jaccard(a["title_tokens"], b["title_tokens"])
            body_jac = _jaccard(a["body_tokens"], b["body_tokens"])
            # Pre-filter: at least one signal must clear the floor before
            # we spend a cosine call.
            if title_jac < _TITLE_PREFILTER_MIN and body_jac < _TITLE_PREFILTER_MIN:
                continue
            cosine: Optional[float] = None
            if a["embedding"] and b["embedding"]:
                try:
                    cosine = float(cosine_similarity(a["embedding"], b["embedding"]))
                except Exception:
                    cosine = None
            score, dominant = _combined_score(title_jac, body_jac, cosine)
            if score < min_score:
                continue
            title_only = _pair_is_title_only(a, b) and cosine is None
            if title_only:
                title_only_pairs += 1
            pair_scores[(a["id"], b["id"])] = {
                "score": score,
                "title_jac": round(title_jac, 4),
                "body_jac": round(body_jac, 4),
                "cosine": None if cosine is None else round(cosine, 4),
                "dominant": dominant,
                "signal": "title_only" if title_only else "body",
            }
            uf.union(a["id"], b["id"])

    # Group into clusters. Singletons are filtered out.
    groups = uf.groups()
    clusters: list[dict] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        member_caches = [c for c in cache if c["id"] in set(members)]

        # Backlink counts (one call per doc, memoised within this run).
        doc_backlinks: dict[str, int] = {}
        for mc in member_caches:
            dp = mc["doc_path"]
            if dp in doc_backlinks:
                continue
            try:
                bl = get_backlinks(repo=repo, doc_path=dp, storage_path=storage_path)
                doc_backlinks[dp] = (bl.get("result") or {}).get("backlink_count", 0)
            except Exception:
                doc_backlinks[dp] = 0

        # Pick canonical.
        ranked = sorted(
            member_caches,
            key=lambda c: _canonical_score(c["sec"], doc_backlinks.get(c["doc_path"], 0)),
            reverse=True,
        )
        canonical = ranked[0]

        # Score stats for verdict.
        member_set = {c["id"] for c in member_caches}
        cluster_pairs = [
            v
            for (a_id, b_id), v in pair_scores.items()
            if a_id in member_set and b_id in member_set
        ]
        scores_in_cluster = [v["score"] for v in cluster_pairs]
        max_s = max(scores_in_cluster) if scores_in_cluster else 0.0
        avg_s = sum(scores_in_cluster) / len(scores_in_cluster) if scores_in_cluster else 0.0

        # ⚠⚠ The verdict rests on `evidence_max_score` — the best pair
        # that actually COMPARED BODIES — never on `max_s`.
        #
        # An "all pairs were title_only" rule is not enough, and the
        # corpus proves it: union-find merges transitively, so two empty
        # `## Architecture` stubs (title_only, score 1.0) join a cluster
        # holding two real, unrelated Architecture sections. The cluster
        # then contains a body pair, passes an all-pairs test, and takes
        # its 1.0 from the pair that read nothing. Reported `max_score`
        # stays the true max over all pairs so the two numbers together
        # show exactly what happened.
        body_scores = [
            v["score"] for v in cluster_pairs if v.get("signal") != "title_only"
        ]
        evidence_max = max(body_scores) if body_scores else 0.0
        cluster_title_only = not body_scores

        doc_dirs = {posixpath.dirname(c["doc_path"].replace("\\", "/")) for c in member_caches}
        verdict = _verdict(
            evidence_max, near_duplicate_threshold, doc_dirs, cluster_title_only
        )

        variants = []
        for c in ranked[1:]:
            # Pair score against canonical.
            key = (canonical["id"], c["id"]) if (canonical["id"], c["id"]) in pair_scores else (c["id"], canonical["id"])
            pair = pair_scores.get(key)
            if not pair:
                continue
            differs = _differs_by(
                canonical["body_tokens"], c["body_tokens"],
                canonical["title_tokens"], c["title_tokens"],
                pair.get("signal", "body"),
            )
            variants.append({
                "section_id": c["id"],
                "doc_path": c["doc_path"],
                "title": c["title"],
                "score": pair["score"],
                "dominant_signal": pair["dominant"],
                "signal": pair.get("signal", "body"),
                "differs_by": differs,
            })

        clusters.append({
            "verdict": verdict,
            "signal": "title_only" if cluster_title_only else "body",
            "canonical": {
                "section_id": canonical["id"],
                "doc_path": canonical["doc_path"],
                "title": canonical["title"],
                "backlink_count": doc_backlinks.get(canonical["doc_path"], 0),
                "rationale": f"highest backlink_count ({doc_backlinks.get(canonical['doc_path'], 0)}) + size",
            },
            "variants": variants,
            "size": len(member_caches),
            "max_score": round(max_s, 4),
            "evidence_max_score": round(evidence_max, 4),
            "avg_score": round(avg_s, 4),
        })

    # Sort clusters: near_duplicate first, then by max_score desc.
    verdict_rank = {"near_duplicate": 0, "overlapping_topic": 1, "parallel_tutorial": 2}
    clusters.sort(key=lambda c: (verdict_rank.get(c["verdict"], 9), -c["max_score"]))
    clusters = clusters[:max_clusters]

    return {
        "result": {
            "repo": f"{owner}/{name}",
            "cluster_count": len(clusters),
            "section_count_examined": n,
            "section_count_total": len(all_sections),
            "had_embeddings": has_embeddings,
            "clusters": clusters,
        },
        "_meta": {
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "min_score": min_score,
            "near_duplicate_threshold": near_duplicate_threshold,
            "skipped_no_content": skipped_no_content,
            "body_sources": body_sources,
            "title_only_pairs": title_only_pairs,
            "truncated": len(all_sections) > max_sections,
        },
    }
