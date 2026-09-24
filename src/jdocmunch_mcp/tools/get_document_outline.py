"""Section hierarchy for one file (no content)."""

import time
from typing import Optional

from ..storage import DocStore
from ..storage.token_tracker import estimate_savings, record_savings, cost_avoided


def get_document_outline(
    repo: str,
    doc_path: str,
    storage_path: Optional[str] = None,
) -> dict:
    """Return the section structure for a single document, without content."""
    t0 = time.perf_counter()
    store = DocStore(base_path=storage_path)
    owner, name = store._resolve_repo(repo)
    index = store.load_index(owner, name)

    if not index:
        return {"error": f"Repo not found: {repo}"}

    doc_sections = [
        s for s in index.sections if s.get("doc_path") == doc_path
    ]

    if not doc_sections:
        # Try partial match
        doc_sections = [
            s for s in index.sections
            if s.get("doc_path", "").endswith(doc_path) or doc_path in s.get("doc_path", "")
        ]

    if not doc_sections:
        return {"error": f"Document not found: {doc_path}"}

    doc_sections = sorted(doc_sections, key=lambda s: s.get("byte_start", 0))

    outline = []
    for sec in doc_sections:
        outline.append({
            "id": sec.get("id"),
            "title": sec.get("title"),
            "level": sec.get("level"),
            "summary": sec.get("summary"),
            "parent_id": sec.get("parent_id"),
            "children": sec.get("children"),
            "byte_start": sec.get("byte_start"),
            "byte_end": sec.get("byte_end"),
        })

    # jdoc#138: the document's cached file, not the sum of section
    # ``content``, which is empty after a reload.
    raw_bytes = store.raw_doc_bytes(owner, name, (s.get("doc_path") for s in doc_sections))
    response_bytes = sum(len(str(o).encode("utf-8")) for o in outline)
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total = record_savings(tokens_saved, storage_path)
    ca = cost_avoided(tokens_saved, total)

    latency_ms = int((time.perf_counter() - t0) * 1000)
    return {
        "repo": f"{owner}/{name}",
        "doc_path": doc_path,
        "sections": outline,
        "section_count": len(outline),
        "_meta": {
            "latency_ms": latency_ms,
            "sections_returned": len(outline),
            "tokens_saved": tokens_saved,
            **ca,
        },
    }
