"""Cheap content preview for one section (v1.41.0).

`get_section` returns full content. `get_section_summary` (v1.38) returns
metadata only. The middle ground — a short content preview — required
calling get_section and slicing client-side. This tool does the slice
server-side and reports byte-savings in `_meta`.

Use when:
- The agent has a list of candidate ids and wants to quickly judge which
  is worth fetching in full.
- A search returned 5 hits and the agent wants a 200-byte peek at each
  before deciding which to read.

The excerpt is taken at byte boundary, then trimmed to the last full
line so we don't return a half-cut sentence. UTF-8 char-boundary safe.
"""

from __future__ import annotations

import time
from typing import Optional

from ..storage import DocStore
from ..storage.token_tracker import estimate_savings, record_savings, cost_avoided


_DEFAULT_MAX_BYTES = 500


def _safe_truncate(text: str, max_bytes: int) -> tuple[str, bool]:
    """Truncate to ≤ max_bytes UTF-8 bytes; trim to last full line.

    Returns ``(excerpt, truncated)``. ``truncated`` is True when the
    original encoded longer than max_bytes.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False

    # Walk back from max_bytes to a UTF-8 char boundary.
    cut = max_bytes
    while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
        cut -= 1
    sliced = encoded[:cut].decode("utf-8", errors="ignore")

    # Trim to last newline so we don't end mid-paragraph.
    nl = sliced.rfind("\n")
    if nl > 0 and nl > cut // 2:
        sliced = sliced[:nl]

    return sliced.rstrip() + "\n…", True


def get_section_excerpt(
    repo: str,
    section_id: str,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    storage_path: Optional[str] = None,
) -> dict:
    """Return a short content preview for a section.

    Args:
        repo: Repository identifier.
        section_id: Section to preview.
        max_bytes: Soft cap on excerpt size in UTF-8 bytes (default 500).
            Excerpt is trimmed to the last newline before the cap so it
            ends on a paragraph boundary when possible. ``…`` marker is
            appended when truncated.
        storage_path: Custom storage path.

    Returns:
        ``{section: {id, title, doc_path, level, role, summary},
        excerpt: str, truncated: bool, full_byte_length: int,
        excerpt_byte_length: int, _meta: {...}}``.
    """
    t0 = time.perf_counter()
    if max_bytes <= 0:
        return {"error": "max_bytes must be positive"}
    store = DocStore(base_path=storage_path)
    owner, name = store._resolve_repo(repo)
    index = store.load_index(owner, name)

    if not index:
        return {"error": f"Repo not found: {repo}"}

    sec = index.get_section(section_id)
    # v1.147.0: an ambiguous `doc.md#anchor` reference is disclosed, never
    # resolved silently -- on the miss path as well as the hit path.
    disclosure = index.anchor_disclosure(section_id)
    if not sec:
        if disclosure:
            return {"error": disclosure["warning"], **disclosure}
        return {"error": f"Section not found: {section_id}"}

    content = store.get_section_content(owner, name, section_id, _index=index)
    if content is None:
        return {"error": f"Content not available for section: {section_id}"}

    full_bytes = len(content.encode("utf-8"))
    # [ocr] extra: an excerpt of a scanned page's OCR text credits nothing.
    credit_full = 0 if sec.get("text_source") == "ocr" else full_bytes
    excerpt, truncated = _safe_truncate(content, max_bytes)
    excerpt_bytes = len(excerpt.encode("utf-8"))

    role = (sec.get("metadata") or {}).get("role")
    summary_view = {
        "id": sec.get("id"),
        "title": sec.get("title"),
        "doc_path": sec.get("doc_path"),
        "level": sec.get("level"),
        "role": role,
        "summary": sec.get("summary"),
    }

    tokens_saved = estimate_savings(credit_full, excerpt_bytes)
    total = record_savings(tokens_saved, storage_path)
    ca = cost_avoided(tokens_saved, total)

    latency_ms = int((time.perf_counter() - t0) * 1000)
    out = {
        "section": summary_view,
        "excerpt": excerpt,
        "truncated": truncated,
        "full_byte_length": full_bytes,
        "excerpt_byte_length": excerpt_bytes,
        "_meta": {
            "latency_ms": latency_ms,
            "repo": f"{owner}/{name}",
            "max_bytes": max_bytes,
            "tokens_saved": tokens_saved,
            **ca,
        },
    }
    if disclosure:
        out.update(disclosure)
    return out
