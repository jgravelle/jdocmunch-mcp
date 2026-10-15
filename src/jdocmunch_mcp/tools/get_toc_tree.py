"""Nested TOC tree per document."""

import time
from typing import Optional

from ..storage import DocStore
from ..storage.doc_store import path_glob_matches
from ..storage.token_tracker import estimate_savings, record_savings, cost_avoided
from ._response_bounds import ceiling_refusal, page, paging_args


def get_toc_tree(
    repo: str,
    path_glob: Optional[str] = None,
    storage_path: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
) -> dict:
    """Return a nested table of contents tree, grouped by document.

    Each document contains a tree of sections structured by parent/child
    relationships. Content is excluded.

    Args:
        repo: Repository identifier.
        path_glob: v1.36+ — when set, restrict to documents whose
            doc_path matches the fnmatch glob (e.g. ``"api/**/*.md"``).
            Default None means no filter.
        limit: jdoc#176 — at most this many DOCUMENTS, starting at
            ``offset``. A document's tree is never split across pages.
            Default None returns every document, subject to the response
            ceiling (``JDOCMUNCH_RESPONSE_MAX_BYTES``), over which the call is
            refused rather than truncated.
        offset: jdoc#176 — documents to skip, in doc_path order. Pass the
            previous response's ``next_offset``.
    """
    t0 = time.perf_counter()
    limit, offset, bad = paging_args(limit, offset)
    if bad:
        return {"error": bad}
    store = DocStore(base_path=storage_path)
    owner, name = store._resolve_repo(repo)
    index = store.load_index(owner, name)

    if not index:
        return {"error": f"Repo not found: {repo}"}

    # Group sections by doc_path, sorted by byte_start
    docs: dict = {}
    iter_sections = index.sections
    if path_glob:
        iter_sections = [s for s in index.sections
                         if path_glob_matches(s.get("doc_path", ""), path_glob)]
    for sec in sorted(iter_sections, key=lambda s: (s.get("doc_path", ""), s.get("byte_start", 0))):
        dp = sec.get("doc_path", "")
        docs.setdefault(dp, []).append(sec)
    doc_paths, paging = page(list(docs), limit, offset, "documents")
    docs = {dp: docs[dp] for dp in doc_paths}

    def _make_node(sec: dict) -> dict:
        return {
            "id": sec.get("id"),
            "title": sec.get("title"),
            "level": sec.get("level"),
            "summary": sec.get("summary"),
            "byte_start": sec.get("byte_start"),
            "byte_end": sec.get("byte_end"),
            "children": [],
        }

    tree_docs = []
    for doc_path, sections in docs.items():
        # Build id -> node map
        nodes: dict = {s["id"]: _make_node(s) for s in sections if "id" in s}
        roots = []
        for sec in sections:
            sec_id = sec.get("id")
            if not sec_id or sec_id not in nodes:
                continue
            parent_id = sec.get("parent_id", "")
            if parent_id and parent_id in nodes:
                nodes[parent_id]["children"].append(nodes[sec_id])
            else:
                roots.append(nodes[sec_id])

        tree_docs.append({
            "doc_path": doc_path,
            "sections": roots,
        })

    # jdoc#138: see get_toc. Scoped to the documents in the tree.
    raw_bytes = store.raw_doc_bytes(owner, name, docs.keys())
    response_str = str(tree_docs)
    response_bytes = len(response_str.encode("utf-8"))
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total = record_savings(tokens_saved, storage_path)
    ca = cost_avoided(tokens_saved, total)

    latency_ms = int((time.perf_counter() - t0) * 1000)
    meta = {
        "latency_ms": latency_ms,
        "sections_returned": sum(len(s) for s in docs.values()),
        "tokens_saved": tokens_saved,
        **ca,
    }
    if path_glob:
        meta["path_glob"] = path_glob
    result = {
        "repo": f"{owner}/{name}",
        "documents": tree_docs,
        "doc_count": len(tree_docs),
        **paging,
        "_meta": meta,
    }
    refusal = ceiling_refusal(
        result, tool="get_toc_tree", unit="documents", item_count=len(tree_docs),
        narrow_with=["path_glob", "limit"],
    )
    return refusal or result
