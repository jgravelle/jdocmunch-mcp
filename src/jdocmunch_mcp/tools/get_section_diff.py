"""get_section_diff — unified diff between indexed bytes and current bytes (v2.0.0)."""

from __future__ import annotations

import difflib
import hashlib
import time
from typing import Optional

from ..storage import DocStore


def _read_byte_range(store: DocStore, owner: str, name: str, doc_path: str, byte_start: int, byte_end: int) -> str:
    if byte_end <= byte_start or not doc_path:
        return ""
    file_path = store._safe_content_path(store._content_dir(owner, name), doc_path)
    if not file_path or not file_path.exists():
        return ""
    try:
        with open(file_path, "rb") as fh:
            fh.seek(byte_start)
            buf = fh.read(byte_end - byte_start)
        return buf.decode("utf-8", errors="replace")
    except OSError:
        return ""


def get_section_diff(
    repo: str,
    section_id: str,
    storage_path: Optional[str] = None,
) -> dict:
    """Return a unified diff between the indexed snapshot and the current
    on-disk byte range for ``section_id``.

    Indexed snapshot reconstructs from the section's stored content (when
    inline), else from a recorded ``content_hash``+placeholder. If the
    section has no inline content (typical post-load) we return only the
    hash comparison and skip the textual diff.

    Output: ``{section_id, indexed_hash, current_hash, identical, diff,
    indexed_text_present, current_text}``.
    """
    t0 = time.perf_counter()
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

    indexed_hash = sec.get("content_hash") or ""
    indexed_text = sec.get("content") or ""
    indexed_text_present = bool(indexed_text)

    doc_path = sec.get("doc_path", "")
    byte_start = int(sec.get("byte_start", 0) or 0)
    byte_end = int(sec.get("byte_end", 0) or 0)
    current_text = _read_byte_range(store, owner, name, doc_path, byte_start, byte_end)
    current_hash = hashlib.sha256(current_text.encode("utf-8")).hexdigest() if current_text else ""

    identical = bool(indexed_hash and current_hash and indexed_hash == current_hash)
    diff = ""
    if indexed_text_present and current_text and not identical:
        diff = "\n".join(
            difflib.unified_diff(
                indexed_text.splitlines(),
                current_text.splitlines(),
                fromfile=f"{section_id}@indexed",
                tofile=f"{section_id}@disk",
                lineterm="",
            )
        )

    out = {
        "repo": f"{owner}/{name}",
        "section_id": section_id,
        "doc_path": doc_path,
        "indexed_hash": indexed_hash,
        "current_hash": current_hash,
        "identical": identical,
        "indexed_text_present": indexed_text_present,
        "diff": diff,
        "current_text": current_text if not identical and current_text else "",
        "_meta": {
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "byte_range": [byte_start, byte_end],
        },
    }
    if disclosure:
        out.update(disclosure)
    return out
