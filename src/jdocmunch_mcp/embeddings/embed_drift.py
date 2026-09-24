"""Embedding-drift canary (v1.15.0).

A small fixed corpus of representative strings is embedded once and
persisted. On demand, we re-embed and compare cosine drift against the
saved snapshot. When max_drift > threshold, alarm — the active embedding
provider/model is no longer compatible with what's in the index.

The canary catches three real failure modes silently introduced by upstream:

1. Provider model upgrade without dim change (silent recall regression).
2. Library / tokenizer change in the SBERT backend (subtle vector shifts).
3. Misconfigured environment that swapped provider unexpectedly.

CANARY_STRINGS is an append-only contract: never reorder, never remove.
New strings can be appended over time, but existing positions must stay
fixed so old snapshots remain comparable. Strings cover function names,
prose, code-like tokens, and a multilingual sample so the canary tracks
both natural-language and technical-token shifts.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Optional

# Append-only — never reorder or remove. Index is the implicit ID.
CANARY_STRINGS: tuple[str, ...] = (
    "search_sections returns hybrid retrieval scores",
    "byte_offset preserves authored section boundaries",
    "DocIndex.bm25_stats persists corpus statistics",
    "the quick brown fox jumps over the lazy dog",
    "import json from pathlib import Path",
    "raise ValueError(f\"Invalid {field_name}: {value!r}\")",
    "401 Unauthorized retry with refreshed bearer token",
    "https://api.example.com/v2/users?page=2",
    "OpenAPI components/schemas/User.properties.email",
    "TODO(jgravelle): replace heuristic with proper BM25",
    "Section.title Section.summary Section.content",
    "embedding cosine similarity 0.87 above threshold",
    "git rev-parse HEAD returned a non-zero exit",
    "CamelCase snake_case kebab-case mixed-case identifiers",
    "数据检索 — multilingual query test fixture",
    "P95 latency exceeded 250ms for the search_sections tool",
)

# Bumping the version forces a fresh capture even on identical strings —
# use when the canary semantics change.
CANARY_VERSION = 1


def _canary_path(base_path: Optional[str] = None) -> Path:
    from ..storage.paths import resolve_root  # jdoc#146
    root = resolve_root(base_path)
    root.mkdir(parents=True, exist_ok=True)
    return root / "embed_canary.json"


def _cosine(a: list, b: list) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _embed_with_active_provider(strings: list[str]) -> tuple[Optional[list[list[float]]], str, str, Optional[int]]:
    """Embed via whatever provider :func:`get_provider_name()` resolves to.

    Returns ``(vectors_or_None, provider_name, model, dim)``. Vectors is
    None when no provider is configured (canary cannot capture or check).
    """
    from . import provider as _p

    name = _p.get_provider_name()
    if not name:
        return None, "", "", None
    inst = _p._get_provider()
    if not inst:
        return None, name, "", None

    model, dim = _p._provider_identity(name)
    try:
        vecs = inst.embed_texts(list(strings), task_type="retrieval_document")
    except Exception:
        return None, name, model, dim
    return vecs, name, model, dim


def capture_canary(
    *,
    force: bool = False,
    base_path: Optional[str] = None,
) -> dict:
    """Embed CANARY_STRINGS and persist the snapshot.

    Idempotent — returns ``{"status": "canary_already_exists"}`` when a
    snapshot is already on disk and ``force=False``.
    """
    path = _canary_path(base_path)
    if path.exists() and not force:
        return {"status": "canary_already_exists", "path": str(path)}

    vecs, provider, model, dim = _embed_with_active_provider(list(CANARY_STRINGS))
    if vecs is None:
        return {
            "status": "no_provider",
            "hint": "Set GOOGLE_API_KEY, OPENAI_API_KEY, openai-compatible + JDOCMUNCH_OPENAI_COMPAT_URL + JDOCMUNCH_OPENAI_COMPAT_MODEL, or install sentence-transformers before capturing.",
        }

    snapshot = {
        "canary_version": CANARY_VERSION,
        "provider": provider,
        "model": model,
        "dim": dim,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "strings": list(CANARY_STRINGS),
        "vectors": [list(v) for v in vecs],
    }
    path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return {"status": "captured", "path": str(path), "n_canaries": len(CANARY_STRINGS)}


def check_drift(
    *,
    threshold: float = 0.05,
    base_path: Optional[str] = None,
) -> dict:
    """Re-embed CANARY_STRINGS and compare against the saved snapshot.

    Returns ``{has_canary, alarm, threshold, max_drift, mean_drift, ...,
    per_canary:[{index, string, cosine, drift}]}``.

    Alarm fires when ``max_drift > threshold``. Drift is ``1 - cosine``
    so 0.05 ≈ cosine similarity below 0.95.
    """
    path = _canary_path(base_path)
    if not path.exists():
        return {
            "has_canary": False,
            "alarm": False,
            "hint": "Run capture_canary first to seed the embed_canary.json snapshot.",
        }

    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "has_canary": False,
            "alarm": True,
            "hint": "embed_canary.json could not be parsed; re-run capture_canary(force=True).",
        }

    saved_strings = snapshot.get("strings", [])
    saved_vectors = snapshot.get("vectors", [])
    if len(saved_strings) > len(CANARY_STRINGS):
        # Append-only contract violated — caller has older code than the snapshot.
        return {
            "has_canary": True,
            "alarm": True,
            "hint": "Saved snapshot has more canary strings than the current code; upgrade jdocmunch-mcp.",
        }

    # Re-embed only the saved subset (preserves backward compat when new
    # strings are appended in a future release).
    vecs, provider, model, dim = _embed_with_active_provider(saved_strings)
    if vecs is None:
        return {
            "has_canary": True,
            "alarm": False,
            "hint": "No active embedding provider — cannot check drift right now.",
        }

    per_canary = []
    drifts = []
    for i, (s, saved_vec, current_vec) in enumerate(zip(saved_strings, saved_vectors, vecs)):
        cos = _cosine(saved_vec, current_vec)
        drift = max(0.0, 1.0 - cos)
        drifts.append(drift)
        per_canary.append(
            {
                "index": i,
                "string": s[:60] + ("..." if len(s) > 60 else ""),
                "cosine": round(cos, 6),
                "drift": round(drift, 6),
            }
        )

    max_drift = max(drifts) if drifts else 0.0
    mean_drift = sum(drifts) / len(drifts) if drifts else 0.0
    alarm = max_drift > threshold

    return {
        "has_canary": True,
        "alarm": alarm,
        "threshold": threshold,
        "max_drift": round(max_drift, 6),
        "mean_drift": round(mean_drift, 6),
        "n_canaries": len(saved_strings),
        "captured_provider": snapshot.get("provider"),
        "captured_model": snapshot.get("model"),
        "captured_at": snapshot.get("captured_at"),
        "captured_dim": snapshot.get("dim"),
        "current_provider": provider,
        "current_model": model,
        "current_dim": dim,
        "per_canary": per_canary,
        "hint": (
            "Drift exceeded threshold — re-run embed_repo and capture_canary(force=True) "
            "to refresh embeddings against the current provider."
            if alarm
            else None
        ),
    }
