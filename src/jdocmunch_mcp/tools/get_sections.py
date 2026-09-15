"""Batch content retrieval for multiple sections."""

import hashlib
import os
import time
from typing import Optional

from ..storage import DocStore
from ..storage.token_tracker import estimate_savings, record_savings, cost_avoided
from .get_section import _offload


def get_sections(
    repo: str,
    section_ids: list,
    verify: bool = False,
    strip_boilerplate: bool = False,
    compress_code: bool = False,
    storage_path: Optional[str] = None,
) -> dict:
    """Retrieve full content for multiple sections in one call.

    Args:
        repo: Repository identifier.
        section_ids: List of section IDs to retrieve.
        verify: If True, verify content hashes.
        storage_path: Custom storage path.

    Returns:
        Dict with list of section results.
    """
    t0 = time.perf_counter()
    store = DocStore(base_path=storage_path)
    owner, name = store._resolve_repo(repo)
    index = store.load_index(owner, name)

    if not index:
        return {"error": f"Repo not found: {repo}"}

    results = []
    ambiguous: list = []
    total_tokens_saved = 0
    total_boilerplate_stripped = 0
    total_code_compressed = 0
    fragments: list = []
    if strip_boilerplate:
        from ..retrieval.boilerplate import load as _load_bp
        fragments = _load_bp(storage_path, owner, name)
    # Cache raw file sizes per doc_path to avoid repeated os.path.getsize calls
    doc_raw_sizes: dict = {}

    for section_id in section_ids:
        sec = index.get_section(section_id)
        # v1.147.0: a caller-supplied id may be a `doc.md#anchor` reference; an
        # ambiguous one is disclosed on its own entry, never resolved silently.
        disclosure = index.anchor_disclosure(section_id) if isinstance(section_id, str) else None
        if disclosure:
            ambiguous.append(disclosure["warning"])
        if not sec:
            if disclosure:
                results.append({"error": disclosure["warning"], **disclosure})
            else:
                results.append({"error": f"Section not found: {section_id}"})
            continue

        content = store.get_section_content(owner, name, section_id, _index=index)
        if content is None:
            results.append({"error": f"Content not available for section: {section_id}"})
            continue

        # jdoc#70: capture raw indexed bytes before any response-only transform
        # so verification certifies source integrity, not the transformed copy.
        raw_content = content
        transformations = []

        if strip_boilerplate and fragments:
            from ..retrieval.boilerplate import strip as _strip_bp
            content, removed = _strip_bp(content, fragments)
            total_boilerplate_stripped += removed
            if removed > 0:
                transformations.append("strip_boilerplate")

        if compress_code:
            from ..retrieval.code_compress import compress_fenced_code as _compress
            content, saved = _compress(content)
            total_code_compressed += saved
            if saved > 0:
                transformations.append("compress_code")

        # Strip the raw embedding vector — internal index artifact, not API
        # payload (issue #11). Matches get_section_context behavior.
        result_sec = {k: v for k, v in sec.items() if k not in ("content", "embedding")}
        result_sec["content"] = content
        if transformations:
            result_sec["response_transformed"] = True
            result_sec["transformations"] = transformations

        if verify:
            stored_hash = sec.get("content_hash", "")
            # jdoc#70: hash_verified certifies the RAW indexed section, not the
            # transformed response copy, so it is not flipped false by
            # compress_code/strip_boilerplate. source_hash_verified is the
            # explicit alias for the same source-integrity check.
            source_hash = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
            source_verified = (source_hash == stored_hash) if stored_hash else None
            result_sec["hash_verified"] = source_verified
            result_sec["source_hash_verified"] = source_verified
            if transformations:
                response_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                result_sec["response_hash_matches_content_hash"] = (
                    (response_hash == stored_hash) if stored_hash else None
                )

        doc_path = sec.get("doc_path", "")
        if doc_path not in doc_raw_sizes:
            try:
                raw_file = store._safe_content_path(store._content_dir(owner, name), doc_path)
                doc_raw_sizes[doc_path] = os.path.getsize(raw_file) if raw_file else 0
            except OSError:
                doc_raw_sizes[doc_path] = 0
        raw_bytes = doc_raw_sizes[doc_path]

        response_bytes = len(content.encode("utf-8"))
        tokens_saved = estimate_savings(raw_bytes, response_bytes)
        total_tokens_saved += tokens_saved

        entry = {"section": result_sec, "tokens_saved": tokens_saved}
        if disclosure:
            entry.update(disclosure)
        results.append(entry)

    total = record_savings(total_tokens_saved, storage_path)
    ca = cost_avoided(total_tokens_saved, total)

    latency_ms = int((time.perf_counter() - t0) * 1000)
    meta = {
        "latency_ms": latency_ms,
        "sections_returned": len(results),
        "tokens_saved": total_tokens_saved,
        "total_tokens_saved": total,
        **ca,
    }
    if strip_boilerplate:
        meta["boilerplate_stripped_bytes"] = total_boilerplate_stripped
    if compress_code:
        meta["code_compressed_bytes"] = total_code_compressed
    # v1.32.0: per-section citation block.
    meta["citations"] = []
    for entry in results:
        sec = entry.get("section") if isinstance(entry, dict) else None
        if not isinstance(sec, dict):
            continue
        meta["citations"].append({
            "repo": f"{owner}/{name}",
            "doc_path": sec.get("doc_path", ""),
            "section_id": sec.get("id", ""),
            "byte_start": int(sec.get("byte_start", 0) or 0),
            "byte_end": int(sec.get("byte_end", 0) or 0),
            "content_hash": sec.get("content_hash", ""),
            "indexed_at": index.indexed_at,
        })
    # Per-section freshness + a verdict, matching get_section and the search
    # tools. Entries that are errors carry no section, so they are annotated
    # over the ones that do and counted nowhere else.
    from ..retrieval.freshness import FreshnessProbe
    from ..retrieval.verdict import section_verdict_for_index

    # jdoc#71 live-source layer, for the reason spelled out in get_section:
    # the cached mirror cannot see a workspace edit, so it would answer `fresh`
    # for a file that has changed. Disclosed, never assumed.
    _source_root = getattr(index, "source_root", "") or ""
    _use_live = bool(_source_root) and os.path.isdir(_source_root)
    _probe = FreshnessProbe(
        store, owner, name, index,
        source_root=_source_root if _use_live else None,
    )
    _secs = [
        e["section"] for e in results
        if isinstance(e, dict) and isinstance(e.get("section"), dict)
    ]
    for _s in _secs:
        _probe.annotate(_s)
    meta["freshness"] = _probe.summary(_secs)
    meta["drift_layer"] = "live_source" if _use_live else "cached_mirror"
    # ⚠ The WORST reading governs the batch verdict. Averaging, or taking the
    # first, would let one stale section ride out under an `ok` covering the
    # others — the caller has no way to act on a per-section flag it never sees
    # at the top level.
    _order = ["stale_index", "unknown", "edited_uncommitted", "fresh"]
    _worst = next(
        (b for b in _order if any(s.get("_freshness") == b for s in _secs)),
        None,
    )
    meta["verdict"] = section_verdict_for_index(
        index, found_count=len(_secs), freshness=_worst
    )
    out = {
        "sections": results,
        "section_count": len(results),
        "_meta": meta,
    }
    if ambiguous:
        # Top level as well as per entry: a default response filter can strip
        # `_meta`, and a caller that skims entries should still be told.
        out["anchor_ambiguous"] = True
        out["warning"] = " ".join(ambiguous)
    _mod = _offload()
    if _mod is not None:
        # Entries wrap the section under a "section" key and some are errors
        # with no section at all, so unwrap here rather than teaching the
        # shared criterion a jDocMunch-specific layout.
        _ids = [s_.get("id") for s_ in _secs if s_.get("id")]
        _mod.annotate(
            out,
            units=_secs,
            retrieval_mode=_mod.MODE_IDENTITY,
            body_field="content",
            container_field="doc_path",
            verify_with=(
                {
                    "tool": "get_section",
                    "args_each": [{"section_id": i, "verify": True} for i in _ids],
                }
                if _ids
                else None
            ),
        )
    return out
