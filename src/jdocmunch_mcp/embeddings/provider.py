"""Embedding providers for semantic section search.

Supports Gemini (text-embedding-004), OpenAI (text-embedding-3-small),
OpenAI-compatible endpoints, FastEmbed (offline, ONNX) and
sentence-transformers (offline, torch) — neither offline provider needs a key.

Auto-detection priority (first available wins):
    1. JDOCMUNCH_EMBEDDING_PROVIDER env var
       (gemini/openai/openai-compatible/fastembed/sentence-transformers/none)
    2. GOOGLE_API_KEY → Gemini            (opt-in, see below)
    3. OPENAI_API_KEY → OpenAI            (opt-in, see below)
    4. fastembed installed → local offline ONNX model
    5. sentence-transformers installed → local offline torch model

⚠ Steps 2 and 3 are PAID CLOUD providers and are SKIPPED by auto-detect unless
JDOCMUNCH_ALLOW_PAID_EMBEDDINGS is set. A bare key in the environment must not
silently bill, and must not silently send the indexed corpus to a third party.
Naming the provider in step 1 is always honored.

Set JDOCMUNCH_EMBEDDING_PROVIDER=none to disable all embedding.
"""

import logging
import math
import os
import subprocess
import threading
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # annotations below are strings; this makes them resolvable
    from collections import OrderedDict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Text preparation
# ---------------------------------------------------------------------------

# Bump when _section_embed_text's derivation changes, so the content_hash-keyed
# embedding cache re-embeds instead of serving vectors built from the old text.
_EMBED_TEXT_VERSION = "pv1"

# jdoc#111: default kept at 1000 deliberately. Raising it would silently
# invalidate every existing index and shift recall for every user who never
# asked for it; opt-in via env leaves them untouched.
_DEFAULT_EMBED_CHARS = 1000


def _embed_chars() -> int:
    """Max characters of prose fed to the embedder (``JDOCMUNCH_EMBED_CHARS``).

    jdoc#111, reported by @pnm-jgb with measurements: on a 1,992-section corpus
    the 1000-char cap withheld **41.2%** of available prose (778,236 → 457,284
    tokens), and the median section already exceeded it. Because the cap sits
    just under all-MiniLM-L6-v2's 256-token window, it also made longer-context
    models nearly pointless — the text never reached their window, so the cap,
    not the model, was the binding constraint.

    ⚠ A bad value is ignored rather than raising: this runs inside the embed
    loop, and failing a whole index over a typo'd env var is worse than
    embedding at the documented default.
    """
    raw = os.environ.get("JDOCMUNCH_EMBED_CHARS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return _DEFAULT_EMBED_CHARS


def _section_embed_text(section) -> str:
    """Build the text to embed for a section.

    Prepends title so short-titled sections (e.g. "Emotional Consequences"
    followed by a bullet list) still get a semantically rich embedding. The
    content is reduced to its prose view (frontmatter + fences stripped, #58)
    BEFORE the cap, so the embed window holds prose rather than YAML/TOML keys
    or fenced code, matching the BM25 channel's text.
    """
    from ..retrieval.tokenize import prose_view
    parts = [section.title]
    if section.summary and section.summary != section.title:
        parts.append(section.summary)
    if section.content:
        parts.append(prose_view(section.content).strip()[:_embed_chars()])
    return "\n".join(parts)


def _embed_cache_key(section) -> str:
    """Cache key for a section's embedding: content_hash salted with the embed
    text-derivation version, so a derivation change (#58) invalidates cleanly.

    jdoc#111: the char cap is part of the derivation, so it salts the key too.
    Without it, raising ``JDOCMUNCH_EMBED_CHARS`` on an unchanged corpus would
    serve vectors built from the shorter text while reporting success — the
    same shape of failure as jdoc#109, one layer down.

    ⚠⚠ The DEFAULT cap adds no salt, so the key stays byte-identical to every
    key already on disk. Salting unconditionally — as the report's sketch does
    — would make ``h#pv1`` miss against ``h#pv1-1000`` for every existing user
    on the default, re-embedding every corpus in the world on upgrade to buy
    nothing. The same reasoning as the header's legacy default: absence means
    1000.

    ⚠ The salt goes after the LAST ``#``: ``stored_hashes()`` recovers the bare
    content hash with ``rsplit("#", 1)`` and must keep working.
    """
    h = getattr(section, "content_hash", "") or ""
    if not h:
        return ""
    chars = _embed_chars()
    if chars == _DEFAULT_EMBED_CHARS:
        return f"{h}#{_EMBED_TEXT_VERSION}"
    return f"{h}#{_EMBED_TEXT_VERSION}-{chars}"


# ---------------------------------------------------------------------------
# Cosine similarity (pure Python — no numpy dependency)
# ---------------------------------------------------------------------------

def cosine_similarity(a: list, b: list) -> float:
    """Cosine similarity between two float vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

def _openai_compat_url() -> str:
    return os.environ.get("JDOCMUNCH_OPENAI_COMPAT_URL", "").strip()


def _openai_compat_model() -> str:
    return os.environ.get("JDOCMUNCH_OPENAI_COMPAT_MODEL", "").strip()


def _openai_compat_api_key() -> str:
    return os.environ.get("JDOCMUNCH_OPENAI_COMPAT_API_KEY") or "local"


def _openai_compat_batch_size(default: int = 32) -> int:
    value = os.environ.get("JDOCMUNCH_OPENAI_COMPAT_BATCH_SIZE", "").strip()
    if not value:
        return default
    try:
        batch_size = int(value)
    except ValueError:
        return default
    return batch_size if batch_size > 0 else default


def _st_model_name() -> str:
    return os.environ.get(
        "JDOCMUNCH_ST_MODEL", _SentenceTransformersProvider.DEFAULT_MODEL
    )


def _fastembed_model_name() -> str:
    return os.environ.get(
        "JDOCMUNCH_FASTEMBED_MODEL", _FastEmbedProvider.DEFAULT_MODEL
    )


def _canonical_hub_model_id(model: str) -> str:
    """Resolve a bare model name to the hub id both runtimes actually load.

    sentence-transformers accepts ``all-MiniLM-L6-v2`` and resolves it on the
    hub to ``sentence-transformers/all-MiniLM-L6-v2``; FastEmbed requires the
    qualified spelling. The two spellings name ONE model, so the equivalence
    check below compares canonical ids rather than whatever the user typed.
    """
    model = (model or "").strip()
    if not model or "/" in model:
        return model
    return f"sentence-transformers/{model}"


# ---------------------------------------------------------------------------
# FastEmbed ⇄ sentence-transformers vector equivalence (jdoc#126)
# ---------------------------------------------------------------------------
#
# ⚠⚠ An ALLOW-LIST OF MODELS, never a blanket rename of the provider. The
# sidecar header is matched by exact equality on (provider, model, dim), and
# ``_provider_identity`` returns dim=None for both offline providers — the
# cache treats that as a WILDCARD, so there is no dim backstop underneath a
# normalized provider name. Writing "sentence-transformers" over vectors that
# some other runtime produced would therefore make ``cache.load`` MATCH: the
# two derivations merge into one sidecar and search ranks across both,
# silently. That is jdoc#111's shape, and it is worse than the full re-embed
# it avoids — a re-embed is expensive and observable, this is cheap and
# invisible.
#
# A model earns a place here by being MEASURED identical across the two
# runtimes, not by looking like it should be. ``check_embedding_drift`` is the
# measurement: capture a canary under one runtime, re-run it under the other,
# and read ``max_drift``. Anything not named here is written under the
# ``fastembed`` provider name and re-embedded, which is the fail-closed side.
_FASTEMBED_ST_EQUIVALENT_MODELS = frozenset({
    "sentence-transformers/all-MiniLM-L6-v2",
})


def _st_model_is_cached(model: str) -> bool:
    """Whether ``model`` already sits in the local HuggingFace cache (jdoc#110).

    ⚠ Deliberately a filesystem check and not a hub API call: this runs on the
    startup path, so a network probe would reintroduce the very stall it exists
    to avoid. A local path is treated as cached.

    ⚠ Fails OPEN — an unreadable or unusual cache layout returns True, keeping
    the previous always-warm behaviour. Guessing "not cached" would skip the
    warmup for someone whose model is fine, moving a model load into a tool
    call, which is the outcome with the worse failure mode.
    """
    if not model:
        return True
    from pathlib import Path
    # ⚠⚠ Do NOT treat a forward slash as "this is a path". On Windows
    # `os.altsep` is "/", so `sentence-transformers/all-MiniLM-L6-v2` — an
    # ordinary hub id — would be probed as a filesystem path, never found, and
    # every org-qualified model would report uncached on Windows only.
    looks_local = (
        os.path.isabs(model)
        or model.startswith(("." + os.sep, ".." + os.sep, "~"))
        or model.startswith(("./", "../"))
        or (os.sep != "/" and os.sep in model)
    )
    if looks_local:
        return Path(model).expanduser().exists()
    root = os.environ.get("HF_HUB_CACHE") or os.environ.get("HF_HOME") or ""
    try:
        base = Path(root) / "hub" if (root and not os.environ.get("HF_HUB_CACHE")) \
            else (Path(root) if root else Path.home() / ".cache" / "huggingface" / "hub")
        if not base.exists():
            return False
        # ⚠ A bare name is NOT the cache key. sentence-transformers resolves
        # `all-MiniLM-L6-v2` to `sentence-transformers/all-MiniLM-L6-v2` on the
        # hub, so it lands in `models--sentence-transformers--all-MiniLM-L6-v2`.
        # Checking only the literal name reports the DEFAULT model as uncached
        # on every machine that has it, skipping every warmup.
        candidates = [model]
        if "/" not in model:
            candidates.append(f"sentence-transformers/{model}")
        return _hub_cache_has_model(base, candidates)
    except OSError:
        return True


def _hub_cache_has_model(base, candidates: list) -> bool:
    """Whether any of ``candidates`` has a populated snapshot dir under ``base``.

    Split out of :func:`_st_model_is_cached` so the FastEmbed probe reads the
    same layout rather than a second hand-copied version of it.
    """
    for cand in candidates:
        snapshots = base / ("models--" + cand.replace("/", "--")) / "snapshots"
        if snapshots.is_dir() and any(snapshots.iterdir()):
            return True
    return False


def _fastembed_model_is_cached(model: str) -> bool:
    """Whether ``model`` is already downloaded for FastEmbed (jdoc#126).

    ⚠⚠ FastEmbed does NOT use the HuggingFace hub cache by default — it
    downloads into its own directory (``FASTEMBED_CACHE_PATH``, otherwise
    ``<tempdir>/fastembed_cache``). Answering this question with
    :func:`_st_model_is_cached` would probe the wrong directory: it reports
    "cached" for a machine whose HF cache holds the model for torch while
    FastEmbed still has to download it, which reintroduces jdoc#110's outage —
    a model download inside the MCP client's connect timeout, reported to the
    user as nothing but "connection timed out".

    ⚠ Only FastEmbed's own roots are probed. A populated HuggingFace hub cache
    is NOT taken as evidence — FastEmbed passes its own ``cache_dir`` to
    ``snapshot_download``, so an HF copy may well be there while FastEmbed
    still downloads. Reading it as "cached" is the harmful guess in exactly the
    direction jdoc#110 is about, and "not cached" costs only a deferred load.

    Same fail-open-on-error discipline as the sentence-transformers probe: an
    unreadable cache is not evidence of absence.
    """
    if not model:
        return True
    import tempfile
    from pathlib import Path
    candidates = [model, _canonical_hub_model_id(model)]
    fe_root = os.environ.get("FASTEMBED_CACHE_PATH", "").strip()
    root = Path(fe_root) if fe_root else Path(tempfile.gettempdir()) / "fastembed_cache"
    try:
        if not root.exists():
            return False
        if _hub_cache_has_model(root, candidates):
            return True
        # FastEmbed also stores some models as a flat directory named after
        # the model rather than in the `models--` layout.
        for cand in candidates:
            flat = root / cand.replace("/", "_")
            if flat.is_dir() and any(flat.iterdir()):
                return True
        return False
    except OSError:
        return True


def _active_model_is_cached(name: str) -> bool:
    """Cache probe for whichever offline provider ``name`` selects."""
    if name == "fastembed":
        return _fastembed_model_is_cached(_fastembed_model_name())
    return _st_model_is_cached(_st_model_name())


def _fastembed_available() -> bool:
    """Return True if fastembed is importable.

    Answered from package METADATA for the same reason
    :func:`_sentence_transformers_available` is: this runs from
    ``get_provider_name()`` on the startup path, and detection must not be the
    thing that pays for an import.
    """
    try:
        import importlib.metadata as _md
        _md.version("fastembed")
        return True
    except Exception:
        return False


def _sentence_transformers_available() -> bool:
    """Return True if sentence-transformers is importable.

    jdoc#118: this is the FIRST place the process would import it, reached from
    ``get_provider_name()`` on the auto-detect path — long before warmup or any
    embedding call. It used to answer the question by trying the import here,
    which is exactly what must not happen: on Windows that import can deadlock
    in the native loader and take the whole process with it (see
    :func:`_sentence_transformers_imports_cleanly`). ``except ImportError`` never
    had a chance — a loader deadlock raises nothing.

    The answer now comes from package METADATA, which needs no import at all,
    and the bounded subprocess probe runs later — immediately before the one
    place that actually imports (:func:`warmup`) and before the provider is
    constructed. Detection stays cheap; the expensive check is paid only when
    an expensive import is about to happen anyway.
    """
    try:
        import importlib.metadata as _md
        _md.version("sentence-transformers")
        return True
    except Exception:
        return False


# Embedding providers that bill a remote cloud account per call AND send the
# indexed text off the machine. A bare env key for any of these must NEVER
# auto-enable embedding: it silently spends money and, worse, ships the corpus
# to a third party. Discovered when `index_local` on a PRIVATE memory store
# auto-selected OpenAI from an ambient OPENAI_API_KEY and began embedding it.
#
# The summarizer path already had this guard (`batch_summarize._PAID_CLOUD_PROVIDERS`)
# with the same rationale. It was never ported here, so AI summaries were
# correctly suppressed while embeddings sailed through the identical hazard.
# Naming the provider (JDOCMUNCH_EMBEDDING_PROVIDER) is always honored.
_PAID_CLOUD_EMBEDDING_PROVIDERS = frozenset({"gemini", "openai"})
_WARNED_SUPPRESSED_PAID_EMBED: set = set()

# (env var, provider name) in auto-detect priority order.
_EMBED_AUTO_DETECT_ORDER = (
    ("GOOGLE_API_KEY", "gemini"),
    ("OPENAI_API_KEY", "openai"),
)


def _paid_embeddings_allowed() -> bool:
    """Whether the user explicitly opted in to paid-cloud auto-embedding.

    Off by default: an ambient cloud API key never bills, and never exports the
    corpus, on its own. Turn on with JDOCMUNCH_ALLOW_PAID_EMBEDDINGS=1. Naming a
    provider explicitly (JDOCMUNCH_EMBEDDING_PROVIDER) is always honored and does
    not need this.
    """
    return os.environ.get("JDOCMUNCH_ALLOW_PAID_EMBEDDINGS", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def get_provider_name() -> Optional[str]:
    """Return the active provider name, or None if embeddings are disabled.

    Auto-detect NEVER selects a paid cloud provider from a bare env key unless
    JDOCMUNCH_ALLOW_PAID_EMBEDDINGS is set. Naming the provider explicitly
    bypasses this, because that is a deliberate choice rather than an ambient one.
    """
    explicit = os.environ.get("JDOCMUNCH_EMBEDDING_PROVIDER", "").lower().strip()
    if explicit == "gemini":
        return "gemini"
    if explicit == "openai":
        return "openai"
    if explicit == "openai-compatible":
        # Not in the paid set: it requires an explicitly configured URL + model,
        # which is itself the opt-in, and the common target is a local runtime.
        if _openai_compat_url() and _openai_compat_model():
            return "openai-compatible"
        return None
    if explicit in ("fastembed", "fast-embed", "onnx"):
        return "fastembed"
    if explicit in ("sentence-transformers", "sentence_transformers", "local"):
        return "sentence-transformers"
    if explicit == "none":
        return None
    # Auto-detect: cloud providers first, then offline fallback.
    allow_paid = _paid_embeddings_allowed()
    for env_var, name in _EMBED_AUTO_DETECT_ORDER:
        if not os.environ.get(env_var):
            continue
        if not allow_paid and name in _PAID_CLOUD_EMBEDDING_PROVIDERS:
            if name not in _WARNED_SUPPRESSED_PAID_EMBED:
                _WARNED_SUPPRESSED_PAID_EMBED.add(name)
                logger.warning(
                    "%s is set but paid-cloud embeddings are opt-in — NOT billing "
                    "%s automatically, and NOT sending indexed text off this "
                    "machine. To enable, set JDOCMUNCH_EMBEDDING_PROVIDER=%s (or "
                    "JDOCMUNCH_ALLOW_PAID_EMBEDDINGS=1). Indexing continues with "
                    "lexical BM25 search.",
                    env_var, name, name,
                )
            continue
        return name
    # jdoc#126: FastEmbed outranks sentence-transformers when both are
    # installed. It reaches the same vectors for the aliased model through
    # onnxruntime instead of torch, so it is the cheaper of two equal answers.
    # ⚠ Naming either provider explicitly still wins — this only decides what
    # an unconfigured machine gets, and `JDOCMUNCH_EMBEDDING_PROVIDER=
    # sentence-transformers` is the way back.
    if _fastembed_available():
        return "fastembed"
    if _sentence_transformers_available():
        return "sentence-transformers"
    return None


# ---------------------------------------------------------------------------
# Gemini provider
# ---------------------------------------------------------------------------

class _GeminiProvider:
    """Embed via Google Gemini text-embedding-004 (768 dims)."""

    MODEL = "models/text-embedding-004"
    BATCH_SIZE = 50  # conservative to avoid rate limits

    def __init__(self):
        import google.generativeai as genai
        genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
        self._genai = genai

    def embed_texts(self, texts: list, task_type: str = "retrieval_document") -> list:
        embeddings = []
        for text in texts:
            try:
                result = self._genai.embed_content(
                    model=self.MODEL,
                    content=text,
                    task_type=task_type,
                )
                embeddings.append(result["embedding"])
            except Exception:
                embeddings.append([])
        return embeddings


# ---------------------------------------------------------------------------
# OpenAI provider
# ---------------------------------------------------------------------------

class _OpenAIProvider:
    """Embed via OpenAI text-embedding-3-small (1536 dims)."""

    MODEL = "text-embedding-3-small"
    BATCH_SIZE = 100

    def __init__(self):
        from openai import OpenAI
        self._client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

    def embed_texts(self, texts: list, task_type: str = "retrieval_document") -> list:
        # task_type is ignored for OpenAI — included for interface compatibility
        embeddings = []
        for i in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[i:i + self.BATCH_SIZE]
            try:
                response = self._client.embeddings.create(model=self.MODEL, input=batch)
                embeddings.extend([e.embedding for e in response.data])
            except Exception:
                embeddings.extend([[] for _ in batch])
        return embeddings


# ---------------------------------------------------------------------------
# OpenAI-compatible provider
# ---------------------------------------------------------------------------

class _OpenAICompatibleProvider:
    """Embed via a caller-supplied OpenAI-compatible embeddings endpoint."""

    BATCH_SIZE = 32

    def __init__(self):
        base_url = _openai_compat_url()
        model = _openai_compat_model()
        if not base_url:
            raise ValueError("No JDOCMUNCH_OPENAI_COMPAT_URL")
        if not model:
            raise ValueError("No JDOCMUNCH_OPENAI_COMPAT_MODEL")

        from openai import OpenAI

        self.model = model
        self.batch_size = _openai_compat_batch_size(self.BATCH_SIZE)
        self._client = OpenAI(api_key=_openai_compat_api_key(), base_url=base_url)
        self.dim = self._probe_dim()

    def _probe_dim(self) -> Optional[int]:
        """Discover the endpoint's actual embedding dim with a one-token canary.

        Closes the silent-corruption window where a backing-model swap behind
        the same URL/model env vars (e.g. retagging an Ollama model) would mix
        vectors of different dims in the on-disk cache (jdoc#20).

        Failure is non-fatal: returns None and the cache layer falls back to
        its wildcard-dim behavior (pre-v1.66.3 semantics). Network outage,
        misbehaved endpoint, or any other probe error degrades gracefully.
        """
        try:
            response = self._client.embeddings.create(model=self.model, input=["."])
            vec = response.data[0].embedding
            n = len(vec)
            return n if n > 0 else None
        except Exception:
            return None

    def embed_texts(self, texts: list, task_type: str = "retrieval_document") -> list:
        # task_type is ignored for OpenAI-compatible endpoints.
        embeddings = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i:i + self.batch_size]
            try:
                response = self._client.embeddings.create(model=self.model, input=batch)
                embeddings.extend([e.embedding for e in response.data])
            except Exception:
                embeddings.extend([[] for _ in batch])
        return embeddings


# ---------------------------------------------------------------------------
# FastEmbed provider (fully offline, ONNX runtime)
# ---------------------------------------------------------------------------

class _FastEmbedProvider:
    """Embed via FastEmbed (all-MiniLM-L6-v2 by default, 384 dims).

    Runs entirely offline and pulls in onnxruntime rather than torch. Install
    with::

        pip install jdocmunch-mcp[fastembed]

    Override the model with the JDOCMUNCH_FASTEMBED_MODEL env var. ⚠ Doing so
    leaves the equivalence allow-list unless the new id is named there, so the
    sidecar is written under the ``fastembed`` provider name and the corpus is
    re-embedded. That is deliberate — see
    :data:`_FASTEMBED_ST_EQUIVALENT_MODELS`.
    """

    DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
    BATCH_SIZE = 64

    def __init__(self):
        from fastembed import TextEmbedding
        model_name = _fastembed_model_name()
        self.model_name = model_name
        self._model = TextEmbedding(model_name=model_name)

    def embed_texts(self, texts: list, task_type: str = "retrieval_document") -> list:
        # task_type is ignored — MiniLM is symmetric, matching the
        # sentence-transformers provider's treatment of the same model.
        if not texts:
            return []
        try:
            return [list(map(float, vec))
                    for vec in self._model.embed(texts, batch_size=self.BATCH_SIZE)]
        except Exception:
            return [[] for _ in texts]


# ---------------------------------------------------------------------------
# sentence-transformers provider (fully offline)
# ---------------------------------------------------------------------------

class _SentenceTransformersProvider:
    """Embed via sentence-transformers (all-MiniLM-L6-v2 by default, 384 dims).

    Runs entirely offline — no API key required. Install with:
        pip install sentence-transformers
    Override the model with JDOCMUNCH_ST_MODEL env var.
    """

    DEFAULT_MODEL = "all-MiniLM-L6-v2"
    BATCH_SIZE = 64

    def __init__(self):
        from sentence_transformers import SentenceTransformer
        model_name = os.environ.get("JDOCMUNCH_ST_MODEL", self.DEFAULT_MODEL)
        self._model = SentenceTransformer(model_name)

    def embed_texts(self, texts: list, task_type: str = "retrieval_document") -> list:
        # task_type is ignored — sentence-transformers handles asymmetric search
        # via separate query/passage models when needed; for MiniLM it's symmetric.
        try:
            embeddings = self._model.encode(texts, batch_size=self.BATCH_SIZE, show_progress_bar=False)
            return [emb.tolist() for emb in embeddings]
        except Exception:
            return [[] for _ in texts]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Provider cache (B7) — avoid re-instantiation on every search query.
#
# A factory map is exposed so tests can stub providers; production code reads
# only via _get_provider().
# ---------------------------------------------------------------------------

def _embed_worker_enabled() -> bool:
    """Whether sentence-transformers should be run out of process (jdoc#118).

    ⚠ Imported lazily and failing closed: :mod:`worker` must never become a
    hard dependency of provider detection, which runs on the startup path.
    """
    try:
        from . import worker as _worker
    except Exception:  # pragma: no cover - defensive
        return False
    try:
        return _worker.worker_enabled()
    except Exception:  # pragma: no cover - defensive
        return False


def _sentence_transformers_factory():
    """Build the sentence-transformers provider, in this process or a child.

    jdoc#118 phase 1: ``JDOCMUNCH_EMBED_WORKER=1`` swaps the implementation and
    nothing else — same interface, same cache keys, same identity header. ⚠ The
    identity is deliberately NOT changed to carry the dim the child reports:
    ``_provider_identity`` returns ``None`` for this provider and the cache
    treats that as a wildcard, so filling it in would flip
    ``identity_matches`` and re-embed every existing corpus on first run
    (jdoc#109's escalation, triggered by a refactor rather than a rotation).
    """
    if _embed_worker_enabled():
        from . import worker as _worker
        instance = _worker.WorkerProvider(_st_model_name())
        if not getattr(instance, "spawn_failed", False):
            return instance
        # ⚠⚠ The guard that makes defaulting the worker ON safe. If the child
        # could not be spawned at all — no interpreter at `sys.executable`, a
        # frozen bundle, a sandbox that forbids it — then degrading to lexical
        # would silently remove semantic search from machines where it works
        # today. That is a NEW defect traded for jdoc#118's, which is not a
        # trade worth making by default. Nothing has been learned about the
        # import here, so the in-process provider is exactly as safe as it was
        # before this change.
        logger.warning(
            "the embedding worker could not be started; falling back to the "
            "in-process provider. On Windows this restores the jdoc#118 "
            "deadlock exposure — set JDOCMUNCH_PRELOAD_EMBEDDINGS=1 to import "
            "the stack on the main thread instead."
        )
    return _SentenceTransformersProvider()


_PROVIDER_FACTORIES: dict = {
    "gemini": _GeminiProvider,
    "openai": _OpenAIProvider,
    "openai-compatible": _OpenAICompatibleProvider,
    "fastembed": _FastEmbedProvider,
    "sentence-transformers": _sentence_transformers_factory,
}

# Cache: {(provider_name, model_signature): provider_instance}
_PROVIDER_CACHE: dict = {}

# ⚠⚠ jdoc#110: construction is now reachable from two threads at once — the
# background warmup and a tool call that arrives before it finishes. Without
# this lock both would build a provider, meaning two simultaneous model loads
# (~7.6 s each, and for sentence-transformers two copies in memory) with one
# silently discarded. Guarding the whole construct-and-store, not just the
# store, is the point: the expensive part is the factory call.
_PROVIDER_LOCK = threading.Lock()


def _provider_signature(name: str) -> tuple:
    """Compute a cache key that invalidates when env-driven model choice changes."""
    if name == "sentence-transformers":
        return (name, os.environ.get("JDOCMUNCH_ST_MODEL", _SentenceTransformersProvider.DEFAULT_MODEL))
    if name == "fastembed":
        return (name, _fastembed_model_name())
    if name == "gemini":
        return (name, _GeminiProvider.MODEL, os.environ.get("GOOGLE_API_KEY", "")[:8])
    if name == "openai":
        return (name, _OpenAIProvider.MODEL, os.environ.get("OPENAI_API_KEY", "")[:8])
    if name == "openai-compatible":
        return (
            name,
            _openai_compat_url(),
            _openai_compat_model(),
            _openai_compat_api_key()[:8],
            _openai_compat_batch_size(_OpenAICompatibleProvider.BATCH_SIZE),
        )
    return (name,)


def _reset_provider_cache() -> None:
    """Test hook — clears the provider cache."""
    _PROVIDER_CACHE.clear()


def _get_provider():
    name = get_provider_name()
    if not name:
        return None
    factory = _PROVIDER_FACTORIES.get(name)
    if not factory:
        return None
    # jdoc#118: the sentence-transformers factory imports the package. Prove it
    # can import in a subprocess we can abandon before importing it HERE, where
    # a native loader deadlock would be unkillable and would block every later
    # library load in this process. Cached, so this costs one subprocess at most
    # once per process — and only on the path that was about to pay for the
    # import anyway.
    #
    # ⚠ Skipped entirely when the worker is enabled: this process never imports
    # sentence-transformers, so there is nothing here to protect, and the
    # worker's own ready handshake is a strictly better probe — it tests the
    # long-lived child that will do the work rather than a short-lived one that
    # merely resembles it.
    if (
        name == "sentence-transformers"
        and not _embed_worker_enabled()
        and not _sentence_transformers_imports_cleanly()
    ):
        logger.warning(
            "sentence-transformers is installed but could not be imported in a "
            "probe subprocess (%s); embeddings are unavailable. Lexical search "
            "is unaffected.",
            _import_probe_detail or "unknown",
        )
        return None
    key = _provider_signature(name)
    cached = _PROVIDER_CACHE.get(key)
    if cached is not None:
        return cached
    with _PROVIDER_LOCK:
        # Re-check inside the lock: the thread we waited on may have built it.
        cached = _PROVIDER_CACHE.get(key)
        if cached is not None:
            return cached
        try:
            instance = factory()
        except Exception:
            return None
        _PROVIDER_CACHE[key] = instance
        return instance


def _provider_identity(name: str) -> tuple[str, Optional[int]]:
    """Return ``(model_name, dim)`` for the active provider.

    Used by the embedding cache to validate the sidecar's identity header.
    Dim is best-effort: providers expose it as a class constant when known,
    otherwise None and the cache treats the dim slot as wildcard.
    """
    if name == "gemini":
        return (_GeminiProvider.MODEL, 768)
    if name == "openai":
        return (_OpenAIProvider.MODEL, 1536)
    if name == "openai-compatible":
        # Read dim from the cached provider instance (probed once at __init__).
        # Falls back to None when no instance is constructed yet — the cache
        # layer treats dim=None as a wildcard, preserving v1.66.0 behavior.
        inst = _PROVIDER_CACHE.get(_provider_signature(name))
        dim = getattr(inst, "dim", None) if inst is not None else None
        return (f"{_openai_compat_url()}::{_openai_compat_model()}", dim)
    if name == "sentence-transformers":
        return (
            os.environ.get("JDOCMUNCH_ST_MODEL", _SentenceTransformersProvider.DEFAULT_MODEL),
            None,
        )
    if name == "fastembed":
        return (_fastembed_model_name(), None)
    return (name, None)


def _fastembed_aliases_to_st() -> bool:
    """Whether the active FastEmbed model may reuse a sentence-transformers sidecar.

    Every condition must hold, and each one fails CLOSED:

    * the configured FastEmbed model canonicalises into
      :data:`_FASTEMBED_ST_EQUIVALENT_MODELS` — an unmeasured model never
      inherits another runtime's vectors;
    * it canonicalises to the SAME model the sentence-transformers side would
      load. ``JDOCMUNCH_ST_MODEL`` is user-settable, so a machine configured
      for two different models has two different vector spaces and must keep
      two sidecars.
    """
    fe = _canonical_hub_model_id(_fastembed_model_name())
    st = _canonical_hub_model_id(_st_model_name())
    return bool(fe) and fe == st and fe in _FASTEMBED_ST_EQUIVALENT_MODELS


def sidecar_identity(name: str) -> tuple[str, str, Optional[int]]:
    """Return the ``(provider, model, dim)`` triple written to the sidecar header.

    Usually the runtime's own name and identity. The one exception is jdoc#126:
    FastEmbed and sentence-transformers loading the SAME allow-listed model
    produce interchangeable vectors, so re-embedding a whole corpus to swap
    runtimes buys nothing. Inside the allow-list FastEmbed writes the
    sentence-transformers identity and reuses the existing sidecar.

    ⚠⚠ The model string is the sentence-transformers side's SPELLING
    (``_st_model_name()``), not the canonical hub id. The header is matched by
    exact string equality, and an existing sidecar carries whatever the user
    configured — bare ``all-MiniLM-L6-v2`` by default. Writing the canonical id
    would fail to match the very file this exists to reuse.

    ⚠ Dim stays ``None``. The stored header says ``None`` for every
    sentence-transformers sidecar ever written, and an active dim of 384 would
    compare unequal to it and purge the file — the same trap
    :func:`_sentence_transformers_factory` documents for the embed worker.

    ⚠ Every caller that reads the sidecar identity must call THIS, not
    ``_provider_identity``, or the reader disagrees with the writer and reports
    a rotation that never happened (the jdoc#109 lesson).
    """
    if name == "fastembed" and _fastembed_aliases_to_st():
        return ("sentence-transformers", _st_model_name(), None)
    model, dim = _provider_identity(name)
    return (name, model, dim)


def embed_sections(
    sections: list,
    *,
    owner: Optional[str] = None,
    name: Optional[str] = None,
    storage_path: Optional[str] = None,
    prune: bool = False,
) -> list:
    """Generate and attach embeddings to sections in-place.

    When ``owner`` and ``name`` are supplied, looks up cached vectors keyed
    by ``content_hash`` from ``~/.doc-index/<owner>/<name>.embeddings.jsonl``.
    Only cache misses are sent to the provider — typical incremental
    re-indexes touch <10% of sections, so cache hit-rate dominates cost.

    Cache header records (provider, model, dim); a mismatch on load
    purges the file and forces a full re-embed.

    ⚠⚠ ``prune`` decides whether the sidecar is REWRITTEN from ``sections``
    or MERGED into. It defaults to False (merge) because the sidecar is not
    really a cache: since jdoc#75 the vectors are stripped from the monolith
    at save time and live ONLY here, so dropping an entry destroys it.
    jdoc#107: this rewrote unconditionally, and on an incremental refresh
    ``sections`` holds only the changed documents — a reporter's 5,316-vector
    sidecar came back with 21, exit 0, no warning. Pass ``prune=True`` ONLY
    from a full-corpus pass, where ``sections`` is authoritative and stale
    entries for deleted sections should go.

    Silently degrades to no-embeddings when no provider is configured.
    Backward-compatible with the v1.0–v1.14 signature
    ``embed_sections(sections)`` — caching is opt-in via owner+name.
    """
    provider = _get_provider()
    if not provider:
        return sections

    # jdoc#126: the header identity, which is the runtime's own name except
    # when an allow-listed model lets FastEmbed reuse a sentence-transformers
    # sidecar. Never `_provider_identity` directly — see `sidecar_identity`.
    provider_name, model, dim = sidecar_identity(get_provider_name() or "")
    # jdoc#111: the char cap is part of the identity, not just the key salt.
    chars = _embed_chars()

    cache_enabled = bool(owner and name)
    if cache_enabled:
        from . import cache as _cache  # local import to avoid circulars
        cached = _cache.load(
            storage_path, owner, name,
            provider=provider_name, model=model, dim=dim, embed_chars=chars,
        )
    else:
        cached = {}

    # First pass: split sections into cache-hits and misses.
    misses: list = []
    miss_indices: list[int] = []
    for i, sec in enumerate(sections):
        k = _embed_cache_key(sec)
        vec = cached.get(k) if k else None
        if vec:
            sec.embedding = vec
        else:
            misses.append(sec)
            miss_indices.append(i)

    # Second pass: embed misses in one provider batch.
    embed_failed = False
    if misses:
        texts = [_section_embed_text(s) for s in misses]
        try:
            embeddings = provider.embed_texts(texts, task_type="retrieval_document")
            for sec, emb in zip(misses, embeddings):
                if emb:
                    sec.embedding = emb
        except Exception:
            # Lexical search still works. ⚠⚠ But this pass is now KNOWN to have
            # produced nothing, which the purge below must not read as "the
            # corpus legitimately has no vectors" (jdoc#109).
            embed_failed = True

    # Persist. jdoc#107: start from what is already on disk unless this pass
    # is authoritative for the whole corpus.
    #
    # ⚠⚠ jdoc#109 corrects the claim that used to sit here — that `cached` is
    # {} on rotation and so "this collapses back to a clean rewrite, so
    # rotation still purges." It only purges when at least one section reaches
    # this function. Hand it zero sections during a rotation and `entries` is
    # empty, the guard below skips the write, and the stale sidecar survives
    # under its OLD header with vectors of the wrong width.
    #
    # An empty write is therefore meaningful when the on-disk identity does not
    # match: it is how the old vectors get purged. Only skip the write when
    # there is nothing to say AND nothing stale to retract.
    if cache_enabled:
        from . import cache as _cache
        entries: dict = {} if prune else dict(cached)
        for sec in sections:
            k = _embed_cache_key(sec)
            vec = getattr(sec, "embedding", None)
            if k and vec:
                entries[k] = list(vec)
        stale_identity = False
        if not entries and not embed_failed:
            # ⚠⚠ `not embed_failed` is load-bearing. Purging on an empty pass is
            # correct when the corpus genuinely produced no vectors, and is DATA
            # LOSS when the provider merely threw: a transient outage during a
            # rotation would delete the whole vector store, write the NEW header
            # over it, and thereby convince the next run that nothing is stale.
            # The loss would be permanent and silent — jdoc#107's exact shape.
            # Leaving the old sidecar in place is recoverable: the vectors are
            # the wrong width, which the query side now degrades and discloses.
            stored = _cache.identity(storage_path, owner, name)
            stale_identity = stored is not None and not _cache.identity_matches(
                stored, provider_name, model, dim, chars
            )
        if entries or stale_identity:
            try:
                _cache.write(
                    storage_path, owner, name,
                    provider=provider_name, model=model, dim=dim,
                    entries=list(entries.items()), embed_chars=chars,
                )
            except Exception:
                pass

    return sections


_IMPORT_PROBE_TIMEOUT = 30.0
_import_probe_result: Optional[bool] = None
_import_probe_detail = ""
_import_probe_lock = threading.Lock()


def record_import_probe(ok: bool, detail: str = "") -> None:
    """Record a directly-observed import outcome as the probe's answer.

    jdoc#118: called by :mod:`jdocmunch_mcp.preload` after it imports
    sentence-transformers **on the main thread, while the process is still
    single-threaded**. That is strictly better evidence than the subprocess
    probe -- it tested THIS interpreter, with this `sys.path`, rather than a
    child that merely resembles it -- so it supersedes rather than supplements.

    ⚠ It also stops the probe shelling out a second time. Without this, a
    broken install pays the failing import twice: once on the main thread and
    once in the probe, doubling the startup cost of the very case we most want
    to be cheap.
    """
    global _import_probe_result, _import_probe_detail
    with _import_probe_lock:
        _import_probe_result = ok
        _import_probe_detail = detail


def _sentence_transformers_imports_cleanly() -> bool:
    """Can `import sentence_transformers` succeed, without risking THIS process?

    jdoc#118: the answer cannot be obtained by trying it here. On Windows the
    import loads numpy's bundled OpenBLAS, and its ``DllMain`` -- running while
    the process-wide **loader lock** is held -- has been observed parked in
    `RtlEnterCriticalSection` under `LdrLoadDll`, indefinitely, by a native
    stack taken twice 25 s apart. The Python-visible half is a second thread
    stuck in `threading.Thread.start()`: a new thread needs that same loader
    lock to run its `DLL_THREAD_ATTACH` callbacks, so it never reaches
    `_bootstrap_inner` and `_started` is never set.

    ⚠ **What it is NOT: OpenBLAS's own thread pool.** That was the first
    explanation here, and `OPENBLAS_NUM_THREADS=1` was then measured against a
    reproduction that wedged 7 runs in 8 -- it **still wedged**. At one thread
    `blas_thread_init` spawns none, so whatever that `DllMain` waits on, it is
    not threads it created. The remedy is unaffected either way; the sentence
    was not, and a wrong mechanism in a docstring is what the next person
    reasons from.

    ⚠⚠ A failed in-process attempt is not recoverable and is not local. Once a
    thread wedges inside `LdrLoadDll` the loader lock is never released, so
    EVERY later `LoadLibrary` in the process blocks too — a later, unrelated
    caller inherits the hang. There is nothing to interrupt: it is a kernel-mode
    wait, so a timeout, a thread kill, and a try/except are all equally useless.
    The only bounded probe is one that runs somewhere we can abandon.

    ⚠ The check earns its cost independently of the deadlock: a provider whose
    import raises (observed in the wild as
    ``ImportError: cannot import name 'HybridCache' from 'transformers'``, a
    sentence-transformers/transformers version pairing the user did not choose)
    is unusable, and finding that out at the END of a heavyweight import chain
    is strictly worse than finding it out up front.

    Cached: the probe runs at most once per process.
    """
    global _import_probe_result, _import_probe_detail
    with _import_probe_lock:
        if _import_probe_result is not None:
            return _import_probe_result
        import sys

        # ⚠ The question is "is importing here SAFE", not "will it succeed".
        # An absent package raises ImportError immediately — fast, catchable,
        # and handled by every caller already. Only a native loader deadlock is
        # unrecoverable, and an absent package cannot produce one. Answering
        # False here would also be a behaviour change well beyond this fix: it
        # would make warmup decline before the uncached-model branch that
        # silences download progress bars, and that silencing is what keeps
        # chatter out of the JSON-RPC framing (jdoc#110).
        if not _sentence_transformers_available():
            _import_probe_result = True
            return True
        try:
            proc = subprocess.run(
                [sys.executable, "-c", "import sentence_transformers"],
                capture_output=True, timeout=_IMPORT_PROBE_TIMEOUT,
                # Never inherit this process's stdin/stdout: jdoc#110 gave
                # JSON-RPC a private stdout and a child must not reach it.
                stdin=subprocess.DEVNULL,
                encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            _import_probe_result = False
            _import_probe_detail = (
                f"the import did not finish within {_IMPORT_PROBE_TIMEOUT:.0f}s "
                "(a native loader deadlock looks exactly like this; see jdoc#118)"
            )
            return False
        except Exception as exc:  # probe itself failed — do not punish the provider
            logger.debug("import probe could not run (%s); assuming importable", exc)
            _import_probe_result = True
            return True
        if proc.returncode == 0:
            _import_probe_result = True
            return True
        tail = (proc.stderr or "").strip().splitlines()
        _import_probe_result = False
        _import_probe_detail = tail[-1][:300] if tail else f"exit {proc.returncode}"
        return False


def warmup() -> str:
    """Force-load the active embedding provider so its first call is hot.

    Returns the provider name that was warmed, or empty string if nothing
    was warmed (no provider configured, warmup not needed for this provider,
    or warmup failed).

    Only providers with significant first-call latency get warmed.
    sentence-transformers lazy-loads a local model on first embed_query,
    which (a) can hang past the MCP client's tool-call timeout, and
    (b) can write progress chatter to stdout, corrupting MCP JSON-RPC
    framing if it happens after stdio_server takes over.

    Network providers (gemini, openai, openai-compatible) are first-call-fast
    enough that warmup is unnecessary; warming them would add an avoidable
    network round-trip to startup.

    jdoc#110: warmup is SKIPPED when the model is not already in the local
    HuggingFace cache. A cached load costs ~7.6 s; an uncached one downloads
    inside the same window, and a 440 MB model pushed a reporter past the MCP
    client's 30 s connect timeout — the server never registered at all, and the
    error said only "connection timed out", naming neither models nor
    downloads. Deferring an uncached model turns a one-cycle outage into a slow
    first tool call that can report a real error.

    ⚠⚠ Warmup is not merely an optimization and must not be made unconditional
    background work: it exists so the model load happens BEFORE `stdio_server`
    owns stdout. `contextlib.redirect_stdout` is process-global, so a load
    running concurrently with JSON-RPC cannot be redirected safely — chatter
    would corrupt framing for every request. Skipping is safe; backgrounding
    is not.

    Set ``JDOCMUNCH_EMBED_WARMUP=0`` to skip entirely and accept a lazy first
    load.
    """
    if os.environ.get("JDOCMUNCH_EMBED_WARMUP", "").strip().lower() in (
        "0", "false", "no", "off", "n", "f",
    ):
        return ""
    name = get_provider_name()
    if name not in ("sentence-transformers", "fastembed"):
        return ""
    # ⚠ jdoc#126: the probe below is about sentence-transformers SPECIFICALLY.
    # FastEmbed never imports that package, so probing it would answer a
    # question about a stack this process is not going to load — and on a
    # machine where sentence-transformers is broken it would suppress warmup
    # for a provider that works fine. onnxruntime loads a different DLL set;
    # jdoc#118's deadlock is an argument about torch, not evidence about ONNX,
    # so FastEmbed gets neither the probe nor the worker.
    #
    # jdoc#118: prove the provider can import BEFORE importing it here. An
    # in-process attempt that deadlocks in the native loader is unkillable and
    # poisons every subsequent library load in this process, so a failure here
    # is not confined to embeddings — it takes the whole server with it.
    #
    # ⚠ With the worker enabled the import happens in a child, so warming is
    # both safe and worth doing on this daemon thread: the wait is bounded and
    # the child, unlike a wedged thread, can be killed.
    if (
        name == "sentence-transformers"
        and not _embed_worker_enabled()
        and not _sentence_transformers_imports_cleanly()
    ):
        logger.warning(
            "skipping embedding warmup: sentence-transformers could not be "
            "imported in a probe subprocess (%s). Lexical search is "
            "unaffected; semantic search will not work until this is fixed.",
            _import_probe_detail or "unknown",
        )
        return ""
    if not _active_model_is_cached(name):
        # ⚠⚠ Deferring the load hands the chatter problem to the first tool
        # call, which is precisely the framing hazard warmup was built to
        # avoid — and by then stdout belongs to JSON-RPC and cannot be
        # redirected. Silence the progress bars at the source instead. Only
        # set what is unset: a user who configured these owns them.
        for var in ("HF_HUB_DISABLE_PROGRESS_BARS", "TQDM_DISABLE"):
            os.environ.setdefault(var, "1")
        logger.info(
            "embedding model %s is not in the local cache; skipping startup "
            "warmup so the MCP handshake is not blocked by a download "
            "(jdoc#110). It will load on first use.",
            _fastembed_model_name() if name == "fastembed" else _st_model_name(),
        )
        return ""
    try:
        embed_query("jdocmunch warmup")
        return name
    except Exception:
        return ""


def should_embed(flag) -> bool:
    """Resolve a use_embeddings flag (bool, 'auto', or string boolean) to a concrete bool.

    'auto' → True when an embedding provider is configured, else False.

    Recognises common string booleans (case-insensitive, whitespace-trimmed):
    'true'/'false', '1'/'0', 'yes'/'no', 'on'/'off', 't'/'f', 'y'/'n'.
    Unknown strings fall through to bool(flag) so previously-truthy strings
    keep their behavior (1.x compat).
    """
    if isinstance(flag, str):
        s = flag.strip().lower()
        if s == "auto":
            return get_provider_name() is not None
        if s in ("true", "1", "yes", "on", "y", "t"):
            return True
        if s in ("false", "0", "no", "off", "n", "f", ""):
            return False
    return bool(flag)


# ---------------------------------------------------------------------------
# Query-embedding cache (v1.13.0)
#
# The same query gets re-embedded across hybrid + semantic_only retries within
# one search, and across consecutive paginated calls. A small TTL'd LRU keeps
# the second hit free. Keyed by (provider_signature, query) — provider rotates
# implicitly invalidate when get_provider_name() changes (the cache key looks
# up the live provider's signature).
# ---------------------------------------------------------------------------

_QUERY_CACHE: "OrderedDict[tuple, tuple[float, list]]" = None  # type: ignore[assignment]
_QUERY_CACHE_MAXSIZE = 256
_QUERY_CACHE_TTL_SECONDS = 300.0  # 5 minutes


def _query_cache() -> "OrderedDict":
    global _QUERY_CACHE
    if _QUERY_CACHE is None:
        from collections import OrderedDict
        _QUERY_CACHE = OrderedDict()
    return _QUERY_CACHE


def _reset_query_cache() -> None:
    """Test hook — clears the query embedding cache."""
    cache = _query_cache()
    cache.clear()


def embed_query(query: str) -> Optional[list]:
    """Embed a search query. Returns None if no provider is configured.

    Caches by (provider_signature, query) for ``_QUERY_CACHE_TTL_SECONDS``.
    Provider rotation invalidates implicitly via the signature key.
    """
    import time as _time

    name = get_provider_name()
    if not name:
        return None
    sig = _provider_signature(name)
    key = (sig, query)
    cache = _query_cache()
    now = _time.time()

    cached = cache.get(key)
    if cached is not None:
        ts, vec = cached
        if now - ts < _QUERY_CACHE_TTL_SECONDS:
            cache.move_to_end(key)
            return vec
        # Stale — drop and refetch.
        del cache[key]

    provider = _get_provider()
    if not provider:
        return None
    try:
        results = provider.embed_texts([query], task_type="retrieval_query")
        vec = results[0] if results and results[0] else None
    except Exception:
        return None
    if vec is None:
        return None

    cache[key] = (now, vec)
    cache.move_to_end(key)
    while len(cache) > _QUERY_CACHE_MAXSIZE:
        cache.popitem(last=False)
    return vec
