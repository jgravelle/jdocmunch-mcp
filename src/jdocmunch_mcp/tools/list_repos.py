"""List all indexed doc repos."""

import time
from typing import Optional

from ..storage import DocStore


def _has_embeddings(storage_path: Optional[str], owner: str, name: str) -> bool:
    """Whether the index has an embeddings sidecar, without opening the index.

    Same two locations ``DocStore.load_index`` probes: co-located with the
    store, then the default root ``embed_sections`` writes to when it was
    called without a storage path. Unknown reads as False, which is the honest
    answer for "can this index do semantic search right now".
    """
    try:
        from ..embeddings.cache import _cache_path
        if _cache_path(storage_path, owner, name).exists():
            return True
        return storage_path is not None and _cache_path(None, owner, name).exists()
    except Exception:
        return False


def list_repos(storage_path: Optional[str] = None) -> dict:
    """List all indexed documentation repositories."""
    t0 = time.perf_counter()
    store = DocStore(base_path=storage_path)
    repos = store.list_repos()
    # Per row: can this index serve hybrid search, or is it words-only? A user on
    # a default install has no embedding provider and nothing else says so.
    base = str(store.base_path)
    without = 0
    for row in repos:
        row["has_embeddings"] = _has_embeddings(base, row.get("owner", ""), row.get("name", ""))
        without += not row["has_embeddings"]
    latency_ms = int((time.perf_counter() - t0) * 1000)
    meta = {"latency_ms": latency_ms}
    if without:
        meta["embeddings_tip"] = (
            f"{without} of {len(repos)} indexes have no embeddings, so their searches match words only. "
            'For semantic search: pip install "jdocmunch-mcp[fastembed]" (offline, no key), then re-index.'
        )
    return {
        "repos": repos,
        "count": len(repos),
        "_meta": meta,
    }
