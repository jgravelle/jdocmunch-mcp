"""verify_index — byte-offset integrity check (v1.27.0).

Walks every section in an indexed repo, byte-range-reads the bytes, recomputes
SHA-256, and compares to the stored ``content_hash``.

⚠⚠ **WHICH bytes is the whole question, and this docstring used to answer it
wrongly** (jdoc#105, @T0R0-xp). It said "its current on-disk content", which
reads as the workspace file. The default reads the CACHED RAW MIRROR under
``store._content_dir()``, so both sides of the comparison come from the index
and a source file that was edited, truncated or DELETED after indexing still
verifies CLEAN.

That default is deliberate and unchanged — it is a real check (it catches
corruption of ``~/.doc-index`` itself, which is what B1/B2 of the v1.10 audit
were about) and flipping it would silently change what existing CI gates on.
What was wrong was the description, and the absence of any way to ask the other
question. Both are fixed:

* ``source="cache"`` (default) — index integrity. A clean result proves the
  cached mirror still matches the recorded hashes. **It does NOT prove the
  source document is current.**
* ``source="live"`` — workspace integrity. Reads the real file under the
  index's ``source_root``, so an edited source is ``drift`` and a deleted one is
  ``missing``. Requires a recorded ``source_root``; without one every section
  reports ``no_source_root`` rather than quietly falling back to the cache and
  answering a question nobody asked.

``_meta.verify_layer`` names which one answered, on every call, so a caller
never has to infer it. Same disclosure discipline as the v1.122.0 content
tools, which hit this exact cached-mirror-vs-live-source split and resolved it
the same way.

Output:

    {
        repo, section_count,
        clean_count, drift_count, missing_count, error_count, skipped_count,
        drift_sections:[{section_id, doc_path, expected_hash, actual_hash}],
        missing_sections:[{section_id, doc_path, reason}],
        skipped_sections:[{section_id, doc_path, reason}],
        _meta: {latency_ms}
    }

Reasons for ``missing``:
  - "no_doc_path" (section persisted without a doc_path)
  - "file_missing" (cached raw file not on disk)

Reasons for ``skipped`` (jdoc#33 — unverifiable by design, distinct from
corruption; e.g. the structured OpenAPI parser persists every section with
``byte_start=0, byte_end=0``):
  - "empty_byte_range" (byte_end <= byte_start)
  - "no_stored_hash" (section has a byte range but no recorded content_hash,
    so there is nothing to compare against — unverifiable, NOT clean)

Invariant: clean_count + drift_count + missing_count + error_count +
skipped_count == section_count, so every section is accounted for.

Designed to be cheap enough for CI: O(N) where N is section count, with
one file read per distinct doc_path (cached within the call).
"""

from __future__ import annotations

import hashlib
import time
from typing import Optional

from ..storage import DocStore


def verify_index(
    repo: str,
    storage_path: Optional[str] = None,
    sample: Optional[int] = None,
    source: str = "cache",
) -> dict:
    """Verify every section's stored hash against its byte range.

    Args:
        repo: jdocmunch repo identifier.
        storage_path: Override DOC_INDEX_PATH for tests.
        sample: When set, verify only the first N sections (cheap CI mode).
        source: ``"cache"`` (default) verifies the indexed raw mirror — index
            integrity, and NOT evidence the source is current. ``"live"``
            verifies the workspace files under the index's ``source_root``, so
            an edited source drifts and a deleted one goes missing. See the
            module docstring for why the default is what it is (jdoc#105).
    """
    t0 = time.perf_counter()
    store = DocStore(base_path=storage_path)
    owner, name = store._resolve_repo(repo)
    index = store.load_index(owner, name)
    if not index:
        return {"error": f"Repo not found: {repo}"}

    sections = index.sections
    if sample is not None and sample > 0:
        sections = sections[:sample]

    clean = 0
    drift: list[dict] = []
    missing: list[dict] = []
    skipped: list[dict] = []
    error_count = 0

    source = (source or "cache").strip().lower()
    if source not in ("cache", "live"):
        return {"error": f"source must be 'cache' or 'live', got {source!r}"}

    # Cache file bytes per doc_path so we hash each file at most once.
    file_cache: dict[str, Optional[bytes]] = {}
    content_dir = store._content_dir(owner, name)
    source_root = (getattr(index, "source_root", "") or "").strip()
    # ⚠ A live check with no source_root must REFUSE, not fall back to the cache.
    # Falling back would answer the cache question under the live label, which is
    # the exact confusion jdoc#105 reported.
    live_unavailable = source == "live" and not source_root

    def _bytes_for(doc_path: str) -> Optional[bytes]:
        if doc_path in file_cache:
            return file_cache[doc_path]
        if source == "live":
            from pathlib import Path as _Path
            root = _Path(source_root)
            path = store._safe_content_path(root, doc_path)
        else:
            path = store._safe_content_path(content_dir, doc_path)
        if not path or not path.exists():
            file_cache[doc_path] = None
            return None
        try:
            file_cache[doc_path] = path.read_bytes()
        except OSError:
            file_cache[doc_path] = None
        return file_cache[doc_path]

    for sec in sections:
        sid = sec.get("id", "")
        doc_path = sec.get("doc_path", "")
        if live_unavailable:
            missing.append({
                "section_id": sid, "doc_path": doc_path,
                "reason": "no_source_root",
            })
            continue

        if not doc_path:
            missing.append({"section_id": sid, "doc_path": "", "reason": "no_doc_path"})
            continue

        byte_start = int(sec.get("byte_start", 0) or 0)
        byte_end = int(sec.get("byte_end", 0) or 0)
        expected_hash = sec.get("content_hash") or ""

        if byte_end <= byte_start:
            # Section persisted without a byte range — unverifiable by
            # design, not a drift. Tracked so the counters sum (jdoc#33).
            skipped.append(
                {"section_id": sid, "doc_path": doc_path, "reason": "empty_byte_range"}
            )
            continue

        if not expected_hash:
            # ⚠⚠ UNVERIFIABLE IS NOT VERIFIED. There is nothing to compare
            # against, so this section cannot be certified either way — and
            # the comparison below treats a falsy `expected_hash` as a pass,
            # which would count it CLEAN. That is the whole failure mode this
            # tool exists to prevent, one level up: a caller gating CI on
            # `drift_count == 0` would read "we checked it and it was fine"
            # where the truth is "we could not check it".
            #
            # ⚠ LATENT, not observed in the wild, and the fix is here anyway.
            # Every current producer routes through `compute_content_hash()`,
            # which returns the sha256 of the empty string rather than "" —
            # so no shipped parser emits this today. But `Section.content_hash`
            # DEFAULTS to "" (parser/sections.py), and the text parsers assign
            # it at the end of a loop, so one producer that returns early
            # reintroduces it silently. A certifier must not depend on every
            # producer remembering.
            skipped.append(
                {"section_id": sid, "doc_path": doc_path, "reason": "no_stored_hash"}
            )
            continue

        data = _bytes_for(doc_path)
        if data is None:
            missing.append({"section_id": sid, "doc_path": doc_path, "reason": "file_missing"})
            continue

        try:
            chunk = data[byte_start:byte_end]
        except Exception:
            error_count += 1
            continue

        actual_hash = hashlib.sha256(chunk).hexdigest()
        # `expected_hash` is guaranteed non-empty here — the absent case was
        # routed to `skipped` above rather than falling through to `clean`.
        if actual_hash != expected_hash:
            drift.append(
                {
                    "section_id": sid,
                    "doc_path": doc_path,
                    "expected_hash": expected_hash,
                    "actual_hash": actual_hash,
                }
            )
        else:
            clean += 1

    latency_ms = int((time.perf_counter() - t0) * 1000)
    return {
        "repo": f"{owner}/{name}",
        "section_count": len(sections),
        "clean_count": clean,
        "drift_count": len(drift),
        "missing_count": len(missing),
        "error_count": error_count,
        "skipped_count": len(skipped),
        "drift_sections": drift,
        "missing_sections": missing,
        "skipped_sections": skipped,
        "_meta": {
            "latency_ms": latency_ms,
            "files_read": sum(1 for v in file_cache.values() if v is not None),
            "sample": sample,
            # jdoc#105: never make the caller infer which bytes were compared.
            "verify_layer": source,
            "verifies": (
                "workspace source files under source_root"
                if source == "live"
                else "cached index mirror only; NOT proof the source is current"
            ),
            "source_root": source_root or None,
        },
    }
