"""get_section_blast_radius — transitive impact of a section change (v1.60.0).

Inspired by jcodemunch-mcp's ``get_blast_radius``. Answers: *if I rewrite
or restructure this section, what is affected downstream?*

``get_backlinks`` answers the same question at depth 1 only. This walks
the inverse reference graph to ``max_depth`` (default 3), categorising
each hit by:

  - **anchor**:  the inbound link targets this section's specific anchor
  - **doc**:     the link targets the enclosing doc only
  - **tutorial**: the section appears in a Next/Prev / toctree chain

Returns ``direct_impact`` (depth 1), ``transitive_impact`` (depth ≥ 2),
a ``summary`` of counts, and a normalised ``blast_score`` in [0, 1] so
the caller can compare blast radius across sections of different size.

Read-only. Composes existing primitives — no new persisted state.
"""

from __future__ import annotations

import posixpath
import time
from typing import Optional

from ..storage.doc_store import DocStore
from .get_tutorial_path import get_tutorial_path


_MAX_IMPACT_ITEMS = 50  # bound per direct/transitive list to keep payloads tight


def _parse_section_id(section_id: str) -> tuple[str, str, str, int]:
    """Decompose ``{repo}::{doc_path}::{slug}#{level}``."""
    repo = ""
    doc_path = ""
    slug = ""
    level = 0
    try:
        parts = section_id.split("::", 2)
        if len(parts) == 3:
            repo = parts[0]
            doc_path = parts[1]
            rest = parts[2]
            if "#" in rest:
                slug, lvl_s = rest.rsplit("#", 1)
                try:
                    level = int(lvl_s)
                except ValueError:
                    level = 0
            else:
                slug = rest
    except Exception:
        pass
    return repo, doc_path, slug, level


def _is_external(href: str) -> bool:
    return href.startswith(("http://", "https://", "ftp://", "mailto:", "tel:"))


def _resolve(source_doc: str, target_file: str) -> str:
    if target_file.startswith("/"):
        return target_file.lstrip("/")
    src_dir = posixpath.dirname(source_doc)
    return posixpath.normpath(posixpath.join(src_dir, target_file))


def _all_inbound_refs(index, target_doc: str, target_slug: Optional[str]) -> list[dict]:
    """Single-pass scan of every section's outbound refs that resolve to
    ``target_doc``. Returns a list of inbound-link descriptors, each
    tagged ``link_kind`` ∈ {anchor, doc}.

    When ``target_slug`` is provided, refs whose anchor contains the
    slug's leaf component are classified as ``anchor``; others fall
    under ``doc``. When ``target_slug`` is None, every match is ``doc``.
    """
    target_norm = posixpath.normpath(target_doc.lstrip("/"))
    leaf_slug = target_slug.rsplit("/", 1)[-1].lower() if target_slug else ""
    hits: list[dict] = []
    for sec in index.sections:
        source_doc = sec.get("doc_path", "")
        if source_doc == target_doc:
            continue  # don't count self-references
        for href in sec.get("references", []) or []:
            if _is_external(href):
                continue
            anchor = ""
            file_part = href
            if "#" in href:
                file_part, anchor = href.split("#", 1)
            if not file_part:
                continue
            resolved = posixpath.normpath(_resolve(source_doc, file_part))
            if resolved != target_norm:
                continue
            link_kind = "doc"
            if leaf_slug and anchor and leaf_slug in anchor.lower():
                link_kind = "anchor"
            hits.append({
                "source_section_id": sec.get("id", ""),
                "source_section_title": sec.get("title", ""),
                "source_doc": source_doc,
                "link_kind": link_kind,
                "link": href,
            })
    return hits


def _walk_transitive(
    index,
    start_doc: str,
    start_slug: Optional[str],
    max_depth: int,
) -> tuple[list[dict], list[dict]]:
    """BFS over the inbound reference graph.

    Returns ``(direct, transitive)``. Each item is a dict with
    ``section_id, doc_path, title, depth, via, link_kind``.

    ``via`` is the doc_path of the immediate predecessor — useful for
    callers explaining *how* the impact propagates.
    """
    direct: list[dict] = []
    transitive: list[dict] = []
    seen_docs: set[str] = {start_doc}

    # Depth 1: refs that point directly at the target.
    depth1 = _all_inbound_refs(index, start_doc, start_slug)
    for h in depth1:
        if h["source_doc"] in seen_docs:
            # Already accounted for; can happen with duplicate refs.
            continue
        seen_docs.add(h["source_doc"])
        direct.append({
            "section_id": h["source_section_id"],
            "doc_path": h["source_doc"],
            "title": h["source_section_title"],
            "depth": 1,
            "via": start_doc,
            "link_kind": h["link_kind"],
        })
        if len(direct) >= _MAX_IMPACT_ITEMS:
            break

    # Depth 2..N: walk from each depth-(d-1) doc.
    frontier_docs = [d["doc_path"] for d in direct]
    for depth in range(2, max_depth + 1):
        next_frontier: list[str] = []
        for fdoc in frontier_docs:
            # We treat "doc" here as the destination; collect refs whose
            # *target* is fdoc — i.e., walk one more hop outward.
            hops = _all_inbound_refs(index, fdoc, target_slug=None)
            for h in hops:
                src = h["source_doc"]
                if src in seen_docs:
                    continue
                seen_docs.add(src)
                next_frontier.append(src)
                transitive.append({
                    "section_id": h["source_section_id"],
                    "doc_path": src,
                    "title": h["source_section_title"],
                    "depth": depth,
                    "via": fdoc,
                    "link_kind": h["link_kind"],
                })
                if len(transitive) >= _MAX_IMPACT_ITEMS:
                    return direct, transitive
        if not next_frontier:
            break
        frontier_docs = next_frontier

    return direct, transitive


def _tutorial_chains_broken(
    repo: str,
    section_id: str,
    doc_path: str,
    index,
    storage_path: Optional[str],
) -> tuple[int, list[dict]]:
    """Count tutorial chains that include this section as a non-final
    step. Each such chain breaks if the section is removed or renamed.
    """
    chains: list[dict] = []

    # Chain that starts at this section
    try:
        tp = get_tutorial_path(repo=repo, section_id=section_id, storage_path=storage_path)
        if isinstance(tp, dict) and tp.get("chain") and len(tp["chain"]) > 1:
            chains.append({
                "role": "chain_start",
                "strategy": tp.get("strategy", "unknown"),
                "chain_length": len(tp["chain"]),
            })
    except Exception:
        pass

    # Chains that pass through this section, walked from sibling starts.
    same_dir = posixpath.dirname(doc_path.replace("\\", "/"))
    seen_starts: set[str] = set()
    for other in index.sections:
        other_doc = other.get("doc_path", "")
        if other_doc == doc_path:
            continue
        if posixpath.dirname(other_doc.replace("\\", "/")) != same_dir:
            continue
        oid = other.get("id", "")
        if not oid or oid in seen_starts:
            continue
        seen_starts.add(oid)
        if len(seen_starts) > 20:
            break
        try:
            tp2 = get_tutorial_path(repo=repo, section_id=oid, storage_path=storage_path)
        except Exception:
            continue
        if not isinstance(tp2, dict) or not tp2.get("chain"):
            continue
        chain_ids = [c.get("section_id") for c in tp2.get("chain", [])]
        if section_id in chain_ids and chain_ids.index(section_id) > 0:
            chains.append({
                "role": "chain_member",
                "strategy": tp2.get("strategy", "unknown"),
                "chain_starts_at": other_doc,
            })
            break  # one is enough for the summary
    return len(chains), chains


def _blast_score(
    direct_count: int,
    transitive_count: int,
    tutorial_count: int,
    anchor_count: int,
    total_sections: int,
) -> float:
    """Normalised 0..1 blast score.

    Weights: anchor refs and tutorial chains are higher-impact than
    plain doc-level refs. Normalised against ``total_sections`` so a
    section with 5 referers in a 10-section wiki scores higher than the
    same 5 in a 500-section wiki.
    """
    if total_sections <= 0:
        return 0.0
    weighted = (
        2.0 * tutorial_count
        + 1.5 * anchor_count
        + 1.0 * direct_count
        + 0.5 * transitive_count
    )
    # Soft normalise: score saturates at ~20% of wiki size.
    denom = max(1.0, 0.2 * total_sections)
    score = weighted / denom
    return round(min(1.0, score), 3)


def get_section_blast_radius(
    repo: str,
    section_id: str,
    max_depth: int = 3,
    storage_path: Optional[str] = None,
) -> dict:
    """Transitive impact of rewriting / restructuring a section.

    Args:
        repo: Repository identifier (owner/name).
        section_id: Stable section ID, format
            ``{repo}::{doc_path}::{slug}#{level}``. v1.147.0: a ``doc.md#anchor``
            reference is accepted; an ambiguous one is refused with the candidates.
        max_depth: BFS depth over the inbound reference graph.
            Default 3.
        storage_path: Custom storage path.

    Returns:
        ``{result: {target, direct_impact, transitive_impact, summary,
        blast_score}, _meta}``.
    """
    t0 = time.perf_counter()
    if max_depth < 1:
        max_depth = 1

    store = DocStore(base_path=storage_path)
    owner, name = store._resolve_repo(repo)
    index = store.load_index(owner, name)
    if not index:
        return {"error": f"Repo not found: {repo}"}

    # v1.147.0: the walk answers for ONE section, so an ambiguous
    # `doc.md#anchor` reference is refused rather than walked for the first
    # duplicate.
    refusal = index.anchor_refusal(section_id)
    if refusal:
        return refusal

    sec = index.get_section(section_id)
    if not sec:
        return {"error": f"Section not found: {section_id}"}

    # The slug and the tutorial chains are read from the STORED id; an anchor
    # reference or an alias would parse to an empty slug and drop the anchor
    # channel from the walk.
    stored_id = sec.get("id") or section_id
    doc_path = sec.get("doc_path", "")
    _, _, slug, _ = _parse_section_id(stored_id)
    title = sec.get("title", "")

    direct, transitive = _walk_transitive(index, doc_path, slug, max_depth)

    anchor_count = sum(1 for d in direct if d["link_kind"] == "anchor")
    tutorial_count, tutorial_details = _tutorial_chains_broken(
        repo=repo, section_id=stored_id, doc_path=doc_path,
        index=index, storage_path=storage_path,
    )

    docs_affected = len({d["doc_path"] for d in direct} | {t["doc_path"] for t in transitive})
    sections_affected = len({d["section_id"] for d in direct} | {t["section_id"] for t in transitive})

    score = _blast_score(
        direct_count=len(direct),
        transitive_count=len(transitive),
        tutorial_count=tutorial_count,
        anchor_count=anchor_count,
        total_sections=len(index.sections),
    )

    target = {
        "section_id": section_id,
        "doc_path": doc_path,
        "title": title,
    }
    if stored_id != section_id:
        # v1.147.0: the caller sent an anchor reference or an alias; say which
        # stored section the walk started from.
        target["resolved_section_id"] = stored_id

    return {
        "result": {
            "repo": f"{owner}/{name}",
            "target": target,
            "direct_impact": direct,
            "transitive_impact": transitive,
            "summary": {
                "docs_affected": docs_affected,
                "sections_affected": sections_affected,
                "tutorial_chains_broken": tutorial_count,
                "anchor_refs": anchor_count,
                "direct_count": len(direct),
                "transitive_count": len(transitive),
            },
            "tutorial_details": tutorial_details,
            "blast_score": score,
        },
        "_meta": {
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "max_depth": max_depth,
            "total_sections": len(index.sections),
        },
    }
