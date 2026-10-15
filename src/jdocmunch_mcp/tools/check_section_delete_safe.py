"""check_section_delete_safe — composite preflight for section deletion (v1.60.0).

Inspired by jcodemunch-mcp's ``check_delete_safe``. Answers a question
every wiki maintainer asks every week: *can I safely remove this
section?*

Composes four channels into a single verdict + ranked blockers:

  1. **Tutorial-path membership.** Section appears in a Next/Prev chain,
     Sphinx toctree, VuePress sidebar, or ordered-filename sequence —
     deleting it breaks readers walking the tutorial.
  2. **Anchor-specific backlinks.** Other sections link directly to
     ``doc#slug``. Removing the section breaks those anchored links.
  3. **Doc-level backlinks (transitive).** Other sections link to the
     enclosing document; if the section being deleted contains the
     content those references are about, the link still resolves but
     the reader lands on a thinner page. Walked to ``transitive_depth``.
  4. **Recent-edit recency.** The section's source has been edited
     within ``recent_edit_days`` — signals active work that may resume.

Verdict tiers, highest severity first:

  - ``tutorial_path_blocking`` — section is part of an ordered chain
  - ``anchor_referenced`` — direct ``doc#slug`` refs exist
  - ``backlinks_blocking`` — doc-level transitive refs above threshold
  - ``recently_edited_blocking`` — recent activity, defer deletion
  - ``safe_to_delete`` — none of the above

Returns ``{verdict, blockers[≤5], evidence, recommended_action,
_meta}``. Read-only — never mutates the index.
"""

from __future__ import annotations

import posixpath
import time
from typing import Optional

from ..storage.doc_store import DocStore
from ..retrieval.freshness import FreshnessProbe
from .get_backlinks import get_backlinks
from .get_tutorial_path import get_tutorial_path


_MAX_BLOCKERS = 5
_TRANSITIVE_BACKLINK_THRESHOLD = 3  # ≥ this many doc-level refs → blocking


def _parse_section_id(section_id: str) -> tuple[str, str, str, int]:
    """Decompose ``{repo}::{doc_path}::{slug}#{level}``.

    Returns ``(repo, doc_path, slug, level)``. Missing pieces fall back
    to empty strings / 0 — callers should only rely on fields they
    verified upstream.
    """
    repo = ""
    doc_path = ""
    slug = ""
    level = 0
    try:
        # repo may contain "::"? In practice it's owner/name with no "::".
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


def _collect_anchor_backlinks(
    index, doc_path: str, slug: str
) -> list[dict]:
    """Return refs of the form ``...#slug`` that resolve to ``doc_path``.

    Anchor matching is intentionally fuzzy: docs use slugs derived from
    headings, but inline links may use any of:
      - ``page.md#exact-slug``
      - ``page.md#leaf-only`` (hierarchical slugs flattened)
      - ``page.md#Heading%20Text`` (URL-encoded title)

    We treat any href whose anchor *contains* the leaf slug as a hit.
    Rare false positives are acceptable for a preflight; the alternative
    (strict equality) misses too many real refs.
    """
    if not slug:
        return []
    target_norm = posixpath.normpath(doc_path.lstrip("/"))
    leaf_slug = slug.rsplit("/", 1)[-1].lower()
    hits: list[dict] = []
    for sec in index.sections:
        source_doc = sec.get("doc_path", "")
        for href in sec.get("references", []) or []:
            if _is_external(href):
                continue
            if "#" not in href:
                continue
            file_part, anchor = href.split("#", 1)
            if not file_part:
                # Same-doc anchor; resolve against source.
                resolved = posixpath.normpath(source_doc.lstrip("/"))
            else:
                resolved = posixpath.normpath(_resolve(source_doc, file_part))
            if resolved != target_norm:
                continue
            if leaf_slug in anchor.lower():
                hits.append({
                    "source_file": source_doc,
                    "source_section_id": sec.get("id", ""),
                    "source_section_title": sec.get("title", ""),
                    "link": href,
                })
    # Dedup
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for h in hits:
        key = (h["source_section_id"], h["link"])
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


def _transitive_backlink_count(
    repo: str,
    doc_path: str,
    depth: int,
    storage_path: Optional[str],
    seen: Optional[set] = None,
) -> tuple[int, list[str]]:
    """BFS over doc-level backlinks up to ``depth`` hops.

    Returns ``(count_excluding_target, sample_doc_paths)``. Sample is
    bounded so the evidence payload stays tight.
    """
    if depth <= 0:
        return 0, []
    seen = seen if seen is not None else {doc_path}
    frontier = [doc_path]
    sample: list[str] = []
    total = 0
    for hop in range(depth):
        next_frontier: list[str] = []
        for dp in frontier:
            res = get_backlinks(repo=repo, doc_path=dp, storage_path=storage_path)
            if "result" not in res:
                continue
            for bl in res["result"].get("backlinks", []):
                src = bl.get("source_file", "")
                if not src or src in seen:
                    continue
                seen.add(src)
                next_frontier.append(src)
                total += 1
                if len(sample) < 5:
                    sample.append(src)
        if not next_frontier:
            break
        frontier = next_frontier
    return total, sample


def _last_edit_days_ago(index, section_id: str) -> Optional[int]:
    """Best-effort 'how recently was this section's source edited?'.

    Reads ``file_mtimes`` from the index when present. Returns days as
    a float-floored int, or None when no signal is available.
    """
    sec = index.get_section(section_id)
    if not sec:
        return None
    doc_path = sec.get("doc_path", "")
    mtimes = getattr(index, "file_mtimes", {}) or {}
    mtime = mtimes.get(doc_path)
    if not mtime:
        return None
    try:
        delta = time.time() - float(mtime)
    except (TypeError, ValueError):
        return None
    if delta < 0:
        return 0
    return int(delta // 86400)


def _freshness_bucket(store, owner: str, name: str, index, sec: dict) -> Optional[str]:
    """Return FreshnessProbe bucket for ``sec``, or None on error."""
    try:
        probe = FreshnessProbe(store=store, owner=owner, name=name, index=index)
        return probe._classify(dict(sec))  # copy so we don't mutate the index entry
    except Exception:
        return None


def check_section_delete_safe(
    repo: str,
    section_id: str,
    transitive_depth: int = 3,
    recent_edit_days: int = 14,
    storage_path: Optional[str] = None,
) -> dict:
    """Composite preflight: is this section safe to delete?

    Args:
        repo: Repository identifier (owner/name).
        section_id: Stable section ID, format
            ``{repo}::{doc_path}::{slug}#{level}``. v1.147.0: a ``doc.md#anchor``
            reference is accepted; an ambiguous one is refused with the candidates.
        transitive_depth: Backlink BFS depth. Default 3.
        recent_edit_days: Days within which a recent edit becomes a
            soft blocker. Default 14.
        storage_path: Custom storage path.

    Returns:
        ``{verdict, blockers, evidence, recommended_action, _meta}``.
    """
    t0 = time.perf_counter()
    store = DocStore(base_path=storage_path)
    owner, name = store._resolve_repo(repo)
    index = store.load_index(owner, name)
    if not index:
        return {"error": f"Repo not found: {repo}"}

    # v1.147.0: a verdict covers ONE section, so an ambiguous `doc.md#anchor`
    # reference is refused outright rather than answered for the first duplicate.
    refusal = index.anchor_refusal(section_id)
    if refusal:
        return refusal

    sec = index.get_section(section_id)
    if not sec:
        return {"error": f"Section not found: {section_id}"}

    # Every channel below reads the STORED id: the slug is parsed out of it, the
    # tutorial chains are compared against it. A `doc.md#anchor` reference or an
    # alias would parse to an empty slug and skip the anchor-link channel.
    stored_id = sec.get("id") or section_id
    doc_path = sec.get("doc_path", "")
    _, _, slug, _ = _parse_section_id(stored_id)
    title = sec.get("title", "")

    blockers: list[dict] = []
    evidence: dict = {}

    # --- Channel 1: tutorial-path membership ----------------------------
    tutorial_memberships: list[dict] = []
    try:
        tp = get_tutorial_path(repo=repo, section_id=stored_id, storage_path=storage_path)
        if isinstance(tp, dict) and tp.get("chain"):
            chain = tp.get("chain") or []
            # We're the START of the chain → membership counts when chain
            # has > 1 entry (we have downstream readers).
            if len(chain) > 1:
                tutorial_memberships.append({
                    "role": "chain_start",
                    "strategy": tp.get("strategy", "unknown"),
                    "chain_length": len(chain),
                })
                blockers.append({
                    "kind": "tutorial_path_membership",
                    "ref": f"{tp.get('strategy', 'tutorial')}: starts a chain of {len(chain)} sections",
                    "severity": "high",
                    "evidence": f"Deleting this section breaks {len(chain) - 1} downstream link(s).",
                })
    except Exception:
        # Don't fail the whole preflight because tutorial detection
        # raised on a weird doc — just skip the channel.
        pass

    # Heuristic for "appears mid-chain": walk from each indexed doc in
    # the same directory and check whether this section appears as a
    # non-first chain entry. Bounded scan — only first-section-per-doc.
    same_dir = posixpath.dirname(doc_path.replace("\\", "/"))
    seen_starts: set[str] = set()
    for other in index.sections:
        other_doc = other.get("doc_path", "")
        if other_doc == doc_path:
            continue
        if posixpath.dirname(other_doc.replace("\\", "/")) != same_dir:
            continue
        other_id = other.get("id", "")
        if not other_id or other_id in seen_starts:
            continue
        seen_starts.add(other_id)
        if len(seen_starts) > 20:  # bounded
            break
        try:
            tp2 = get_tutorial_path(repo=repo, section_id=other_id, storage_path=storage_path)
        except Exception:
            continue
        if not isinstance(tp2, dict) or not tp2.get("chain"):
            continue
        chain_ids = [c.get("section_id") for c in tp2.get("chain", [])]
        if stored_id in chain_ids and chain_ids.index(stored_id) > 0:
            tutorial_memberships.append({
                "role": "chain_member",
                "strategy": tp2.get("strategy", "unknown"),
                "chain_starts_at": other.get("doc_path", ""),
            })
            blockers.append({
                "kind": "tutorial_path_membership",
                "ref": f"appears mid-chain starting at {other.get('doc_path', '')}",
                "severity": "high",
                "evidence": f"Tutorial chain detected via {tp2.get('strategy', 'unknown')}.",
            })
            break  # one mid-chain hit is enough

    evidence["tutorial_path_memberships"] = tutorial_memberships

    # --- Channel 2: anchor-specific backlinks ---------------------------
    anchor_backs = _collect_anchor_backlinks(index, doc_path, slug)
    evidence["anchor_backlink_count"] = len(anchor_backs)
    if anchor_backs:
        # Top blocker = first anchor ref.
        first = anchor_backs[0]
        blockers.append({
            "kind": "anchor_reference",
            "ref": f"{first['source_file']} → {first['link']}",
            "severity": "high",
            "evidence": f"{len(anchor_backs)} anchored link(s) point at this section's slug.",
        })

    # --- Channel 3: doc-level + transitive backlinks --------------------
    direct = get_backlinks(repo=repo, doc_path=doc_path, storage_path=storage_path)
    doc_backlink_count = 0
    if isinstance(direct, dict) and "result" in direct:
        doc_backlink_count = direct["result"].get("backlink_count", 0)
    evidence["doc_backlink_count"] = doc_backlink_count

    trans_count, trans_sample = _transitive_backlink_count(
        repo=repo, doc_path=doc_path,
        depth=max(1, transitive_depth),
        storage_path=storage_path,
    )
    evidence["transitive_backlink_count"] = trans_count
    evidence["transitive_backlink_sample"] = trans_sample
    if trans_count >= _TRANSITIVE_BACKLINK_THRESHOLD:
        blockers.append({
            "kind": "transitive_backlinks",
            "ref": f"{trans_count} doc(s) reference this page within {transitive_depth} hops",
            "severity": "medium",
            "evidence": f"Sample: {', '.join(trans_sample[:3])}",
        })

    # --- Channel 4: recent edit recency ---------------------------------
    days_ago = _last_edit_days_ago(index, stored_id)
    evidence["last_edit_days_ago"] = days_ago
    freshness = _freshness_bucket(store, owner, name, index, sec)
    evidence["freshness"] = freshness
    if days_ago is not None and days_ago <= recent_edit_days:
        blockers.append({
            "kind": "recently_edited",
            "ref": f"section edited {days_ago} day(s) ago",
            "severity": "low",
            "evidence": "Recent activity suggests work is in progress; defer deletion.",
        })
    elif freshness == "edited_uncommitted":
        blockers.append({
            "kind": "recently_edited",
            "ref": "section has uncommitted edits on disk",
            "severity": "low",
            "evidence": "Source diverges from index; pending changes are not yet captured.",
        })

    # --- Verdict -------------------------------------------------------
    verdict = "safe_to_delete"
    recommended: str
    if any(b["kind"] == "tutorial_path_membership" for b in blockers):
        verdict = "tutorial_path_blocking"
        recommended = (
            "Update or relocate the surrounding tutorial chain before deleting. "
            "Walk the chain via get_tutorial_path and rewrite next/prev links."
        )
    elif any(b["kind"] == "anchor_reference" for b in blockers):
        verdict = "anchor_referenced"
        recommended = (
            f"Rewrite {len(anchor_backs)} anchored link(s) before deletion. "
            "Each one will 404 once the section is gone."
        )
    elif trans_count >= _TRANSITIVE_BACKLINK_THRESHOLD:
        verdict = "backlinks_blocking"
        recommended = (
            f"{trans_count} doc(s) link into this page transitively; if this section is "
            "the substance of those references, deletion is destructive. "
            "Consider merging the content into the linked target instead."
        )
    elif any(b["kind"] == "recently_edited" for b in blockers):
        verdict = "recently_edited_blocking"
        recommended = (
            f"Section was edited within the last {recent_edit_days} day(s). "
            "Confirm the author isn't mid-work before deleting."
        )
    else:
        recommended = "No blockers detected. Safe to delete."

    # Cap blockers to top _MAX_BLOCKERS, ordered by the order we appended
    # (which is the severity order above).
    blockers = blockers[:_MAX_BLOCKERS]

    result = {
        "repo": f"{owner}/{name}",
        "section_id": section_id,
        "title": title,
        "doc_path": doc_path,
        "verdict": verdict,
        "blockers": blockers,
        "evidence": evidence,
        "recommended_action": recommended,
    }
    if stored_id != section_id:
        # v1.147.0: the caller sent an anchor reference or an alias; say which
        # stored section the verdict is about.
        result["resolved_section_id"] = stored_id

    return {
        "result": result,
        "_meta": {
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "transitive_depth": transitive_depth,
            "recent_edit_days_threshold": recent_edit_days,
        },
    }
