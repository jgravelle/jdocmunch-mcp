"""DocIndex + DocStore: CRUD, search scoring, and byte-range content reads."""

import fnmatch
import functools
import inspect
import hashlib
import json
import os
import re
import shutil
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import fcntl  # POSIX advisory file locks (cross-process)
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None
try:
    import msvcrt  # Windows byte-range file locks (cross-process)
except ImportError:  # pragma: no cover - non-Windows
    msvcrt = None

from ..embeddings import embed_query, cosine_similarity

INDEX_VERSION = 3

# How long `delete_index(lock_wait=True)` may wait for a contended retirement
# record before declaring the lifecycle busy.
#
# ⚠ jdoc#114: named because a test duplicated this as a bare `1.0` literal and
# asserted the TOTAL round trip finished in under it — i.e. the whole call had
# to beat the budget of one step inside it, leaving zero headroom by
# construction. It went red at 1.588 s on a loaded Windows runner. Anything
# asserting against this budget must import it, not restate it.
RECORD_LOCK_WAIT_SECONDS = 1.0
COMMIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_UNSET = object()

# Every file suffix an index owns beside its `<name>.json` monolith. The
# canonical list: `delete_index` removes these, `_leftover_artifacts` reports
# them, and `list_repos` must never mistake one for a primary index.
#
# ⚠ jdoc#121: this existed as three hand-copied tuples and the copy that was
# MISSING is what the issue reports — `list_repos` globbed `*/*.json` and
# excluded only `_`-prefixed files and `.summary.json`, so it opened and
# json-parsed every `.terms/.related/.boilerplate/.duplicates` sidecar in the
# store on a documented first-call hot path, then discarded each one for
# lacking primary-index fields. Measured on a 75-index store: 2,044 ms ->
# 3,460 ms median, 300 extra parses. `.related.json` is the expensive member
# (one measured file was 1.2 GB), and the parsed set is bounded by sidecar
# bytes, not by live corpus size.
#
# ⚠ `tests/test_jdoc_121_list_repos_sidecars.py` derives each suffix from the
# module that WRITES it and fails if one is missing here, so a fifth sidecar
# cannot be added without joining this list.
INDEX_OWNED_SIDECAR_SUFFIXES = (
    ".summary.json",
    ".embeddings.jsonl",
    ".terms.json",
    ".related.json",
    ".boilerplate.json",
    ".duplicates.json",
)

# The subset that `list_repos`'s `*/*.json` glob can actually match, so the
# candidate filter never stats for a suffix the glob cannot produce.
_SIDECAR_SUFFIXES_MATCHING_JSON_GLOB = tuple(
    s for s in INDEX_OWNED_SIDECAR_SUFFIXES if s.endswith(".json")
)

# Authoritative storage outcomes for delete_index. The semantic keys are
# internal; the values are the stable public reason codes emitted by storage
# and interpreted by the public tool.
DELETE_REASON_CODES = {
    "deleted": "index_deleted",
    "not_found": "index_not_found",
    "lifecycle_busy": "index_lifecycle_busy",
}

# Additive disclosure emitted only when a guarded retirement committed its
# primary unlink but exact-publication record completion did not finish. This
# is the single authority for the field names, types, and meanings that
# SPEC.md publishes and the drift guard checks.
RETIREMENT_CLEANUP_OUTCOME_SCHEMA = {
    "retirement_cleanup_pending": {
        "json_type": "boolean",
        "allowed_values": ("false", "true"),
        "meaning": (
            "True when durable record state remains readable or unreadable; "
            "false only when it is absent."
        ),
    },
    "retirement_cleanup_record_state": {
        "json_type": "string",
        "allowed_values": ("absent", "readable", "unreadable"),
        "meaning": (
            "Observed durable retirement-record state after the completion "
            "attempt."
        ),
    },
    "retirement_cleanup_owned": {
        "json_type": "boolean",
        "allowed_values": ("false", "true"),
        "meaning": (
            "True only for a readable record whose publication_id equals the "
            "exact completing publication; false for absent, unreadable, or "
            "replacement state."
        ),
    },
}


class RetirementConflict(Exception):
    """jdoc#88 QA-01: a guarded ``delete_index`` found an index changed since
    its retirement was approved. Raised at the entry check (nothing touched)
    or at the final recheck after the retained-handle gate is acquired and
    immediately before the primary record would be unlinked (jdoc#89 QA-06).
    In both entry and final conflicts, the retiring primary remains loadable
    because its primary unlink did not commit; at worst rebuildable auxiliary
    artifacts of the retiring handle are gone. A retained peer that changed or
    disappeared is not restored. ``changed`` lists the ``owner/name`` handles
    that no longer match their proof-time fingerprints (an expected value of
    None always conflicts; missing state never authorizes removal). Only ever
    raised when the caller passed ``expected_fingerprints``; unguarded deletes
    are unaffected."""

    def __init__(self, changed: list):
        self.changed = list(changed)
        super().__init__(
            f"index state changed since retirement was approved: {self.changed}"
        )


def _with_index_lock(method):
    """Serialize same-repo index writes across processes.

    jdocmunch rewrites the whole ``<name>.json`` on every save. Without a
    cross-process lock, two concurrent writers for the same repo (e.g. a
    scheduled reindex and a per-edit hook) interleave their read-modify-write
    and ``os.replace`` then installs a corrupt/partial index, or one writer's
    update is silently dropped (last-replace-wins). This decorator holds an
    exclusive lock for the whole method -- including the ``load_index`` read in
    ``incremental_save`` -- so the read-modify-write is atomic between processes
    on both POSIX (flock) and Windows (msvcrt).

    Non-reentrant (the lock is per-fd), so a decorated method must not call
    another decorated writer for the *same* repo while holding the lock. Today
    neither writer calls the other.

    jdoc#93 QA-23: a decorated method may opt into a ZERO-WAIT acquisition by
    accepting a ``lock_wait`` parameter and being called with
    ``lock_wait=False``. Contention then returns False immediately with
    ``outcome["reason_code"] = "index_lifecycle_busy"`` instead of queueing.
    That is the PUBLIC-path contract: a tool call that silently blocks on a
    lock is indistinguishable from a hang to its caller, so contention is
    reported as a typed, retryable answer. Internal coordinated operations
    (the retirement's own guarded delete) keep the blocking acquisition —
    they are mid-protocol and a bounded wait is correct there.

    Only ``delete_index`` opts in today; the default is unchanged blocking, so
    every other writer behaves exactly as before.
    """

    # Resolved once, at decoration time: a method that declares `lock_wait`
    # owns its own default, so an omitted argument honors the signature rather
    # than this decorator's opinion. Methods without the parameter (save_index,
    # incremental_save) keep blocking acquisition unconditionally.
    try:
        _param = inspect.signature(method).parameters.get("lock_wait")
        _default_lock_wait = (
            True if _param is None or _param.default is inspect.Parameter.empty
            else bool(_param.default)
        )
    except (TypeError, ValueError):  # pragma: no cover - exotic callables
        _default_lock_wait = True

    @functools.wraps(method)
    def wrapper(self, owner=None, name=None, *args, **kwargs):
        if kwargs.get("lock_wait", _default_lock_wait) is False:
            with self._try_index_write_lock(owner, name) as acquired:
                if not acquired:
                    outcome = kwargs.get("outcome")
                    if outcome is not None:
                        outcome["reason_code"] = DELETE_REASON_CODES[
                            "lifecycle_busy"
                        ]
                    return False
                return method(self, owner, name, *args, **kwargs)
        with self._index_write_lock(owner, name):
            return method(self, owner, name, *args, **kwargs)

    return wrapper

# Module-level LRU cache: {(str(index_path), mtime_ns): DocIndex}
# Keyed by path + mtime so the entry auto-invalidates whenever the file changes.
# Bounded to prevent leaks in long-running MCP servers.
_INDEX_CACHE_MAXSIZE = 8
_INDEX_CACHE: "OrderedDict[tuple, DocIndex]" = OrderedDict()


def _stamp_load_provenance(index, index_path, mtime_ns: int):
    """Record which monolith an index came from and its mtime at load time.

    Lets a retrieval verdict detect that the index was rewritten UNDERNEATH a
    scan (``retrieval.verdict.index_changed_since_load``) — the fifth absence
    refusal rule. Deliberately a filesystem signal so it still fires when the
    rebuild is driven by a SEPARATE process (a watcher), which in-process state
    cannot see. Matters more here than elsewhere: sections score through a lazy
    ``_content_loader`` that reads body text from disk at scan time.
    """
    try:
        index._index_path = str(index_path)
        index._loaded_mtime_ns = int(mtime_ns)
    except Exception:  # pragma: no cover - defensive; never break a load
        import logging

        logging.getLogger(__name__).debug(
            "Could not stamp load provenance", exc_info=True
        )
    return index


def _index_cache_get(key: tuple):
    """LRU lookup — moves the entry to the most-recently-used end on hit."""
    val = _INDEX_CACHE.get(key)
    if val is not None:
        _INDEX_CACHE.move_to_end(key)
    return val


def _index_cache_put(key: tuple, value) -> None:
    """LRU insert — evicts oldest when over capacity."""
    _INDEX_CACHE[key] = value
    _INDEX_CACHE.move_to_end(key)
    while len(_INDEX_CACHE) > _INDEX_CACHE_MAXSIZE:
        _INDEX_CACHE.popitem(last=False)


def _file_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def normalize_commit_sha(value: Optional[str]) -> Optional[str]:
    """Return a normalized 40-hex commit SHA, or None for non-commit refs."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not COMMIT_SHA_RE.fullmatch(value):
        return None
    return value.lower()


def format_repo_at_sha(
    repo: str,
    head_sha: Optional[str],
    source_dirty: bool = False,
    sha_certified: bool = False,
) -> Optional[str]:
    """Return the immutable repo@sha handle when this index is commit-clean."""
    sha = normalize_commit_sha(head_sha)
    if not sha or source_dirty or not sha_certified:
        return None
    return f"{repo}@{sha}"


def _evict_index_cache(index_path: Path) -> None:
    """Remove all cache entries for a given index path (any mtime)."""
    path_str = str(index_path)
    stale = [k for k in _INDEX_CACHE if k[0] == path_str]
    for k in stale:
        del _INDEX_CACHE[k]


def _load_sidecar_vectors(sidecar_path: str) -> dict:
    """Stream ``{content_hash: array('f')}`` from an embeddings sidecar (jdoc#75).

    Read-only and identity-agnostic: unlike ``embeddings.cache.load`` this never
    purges on a provider/model header mismatch -- a lazy query-time rehydration
    must never be able to destroy gigabytes of cached vectors. Keys are bare
    content hashes; the ``#pv<N>`` embed-text-version salt (see
    ``provider._embed_cache_key``) is stripped so they match the ``content_hash``
    field of serialized section dicts. Vectors land as ``array('f')`` (~4 KB per
    1024-dim section) rather than Python float lists (~70 KB) so a 100k-section
    corpus rehydrates in well under a gigabyte instead of the ~8 GB the inline
    monolith cost.
    """
    from array import array
    out: dict = {}
    try:
        with open(sidecar_path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("_header") is True:
                    continue
                h = entry.get("hash")
                vec = entry.get("vector")
                if isinstance(h, str) and isinstance(vec, list) and vec:
                    # Strip the embed-text-version salt (``<hash>#pv1``) so the
                    # key matches a section's bare ``content_hash``.
                    out[h.rsplit("#", 1)[0]] = array("f", vec)
    except OSError:
        return {}
    return out


@dataclass
class DocIndex:
    """Index for a repository's documentation."""
    repo: str
    owner: str
    name: str
    indexed_at: str
    doc_paths: list
    doc_types: dict        # {".md": 5, ".txt": 2}
    sections: list         # Serialized Section dicts (without content by default)
    index_version: int = INDEX_VERSION
    file_hashes: dict = field(default_factory=dict)
    head_sha: Optional[str] = None
    source_dirty: bool = False
    sha_certified: bool = False
    # v1.12.0: BM25 corpus stats. Empty dict for legacy indices — score_section
    # gracefully degrades when stats are missing.
    bm25_stats: dict = field(default_factory=dict)
    # v1.30.0: absolute path to the original source folder so tools can re-read
    # raw files when the cached/converted form has lost information (notably the
    # VuePress grouped-dict sidebar). Empty string when unknown — tools must
    # tolerate the missing case.
    source_root: str = ""
    # Original upstream repository for GitHub indexes. Empty for legacy/local indexes.
    source_repo: str = ""
    # jdoc#81: durable documentation-selection descriptor ("full" or
    # "subset:<sha>:<count>") that, together with the normalized source_root,
    # forms the corpus identity index_local uses to prevent duplicate indexes.
    # Empty for legacy/GitHub indexes — legacy is presumed full-corpus.
    corpus_selection: str = ""
    # jdoc#116: the corpus-shaping patterns THEMSELVES, not just their digest.
    # `corpus_selection` records THAT a corpus was shaped ("full+shape:<hash>")
    # and never HOW, so before this field a re-entry point that inherited the
    # descriptor would have asserted an exclusion it could not reapply: the
    # index would claim `full+shape:...` while containing the excluded files.
    # Empty list = no shaping (legacy indexes included; absence is not evidence
    # of a shaped corpus, and a legacy `full+shape:` with no stored patterns is
    # therefore treated as unknown-shape, never as unshaped).
    corpus_shape_patterns: list = field(default_factory=list)
    # jdoc#83 (Item B): worktree-translated identity evidence. lineage key =
    # sha1[:16] of the normalized Git common directory (linked-worktree
    # family); relative root = corpus location relative to the worktree top
    # level (posix, "" = repo root). Both optional — legacy/non-Git/GitHub
    # indexes carry "" and are treated as evidence-unknown, never inferred.
    worktree_lineage_key: str = ""
    repo_relative_root: str = ""
    # Identity schema version for the jdoc#83 evidence fields (0 = pre-#83).
    corpus_identity_version: int = 0
    # jdoc#80 Part B (v1.106.0): reconciliation state. "" = normal (proven or
    # confirmed-non-Git); "provisional" = created when Git lineage could not be
    # verified (both common-dir probes were unavailable — timeout/missing/OS
    # error, NOT a clean not-a-repo answer). A provisional index is authority-
    # free: it never wins worktree reuse, is never an established_handle, and
    # never carries a lineage key. Part B ships NO graduation path — it stays
    # provisional until the Part C reconciler (behind the #80 proof gate).
    reconciliation_state: str = ""
    # v1.103.0: coverage contract for absence claims — {walk, files_indexed,
    # skip_counts{reason: count}, no_sections_count, recorded_at} from the
    # last full discovery walk. Empty = unknown (legacy index or no full walk
    # recorded); an absent verdict then carries no coverage block. Suite
    # parity with jCodeMunch v1.108.145.
    coverage: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Build O(1) lookup dict once at load time
        self._section_index: dict = {s["id"]: s for s in self.sections if "id" in s}
        # Lazy content loader injected by DocStore.load_index. Signature:
        #   loader(doc_path: str, byte_start: int, byte_end: int) -> str
        # Returns "" on failure. Set to None when no loader is available
        # (e.g. in-memory tests that build a DocIndex directly).
        self._content_loader = None  # type: ignore[var-annotated]
        # Per-search content cache: section_id -> str. Cleared between searches.
        self._content_cache: dict = {}
        # jdoc#75: embedding vectors live ONLY in the ``<name>.embeddings.jsonl``
        # sidecar, never inline in the monolith. DocStore.load_index injects the
        # sidecar path here; ``_rehydrate_embeddings`` streams vectors back onto
        # the section dicts the first time a semantic code path needs them.
        self._embeddings_sidecar = None
        self._embeddings_rehydrated = False

    @property
    def repo_at_sha(self) -> Optional[str]:
        return format_repo_at_sha(
            self.repo,
            self.head_sha,
            self.source_dirty,
            self.sha_certified,
        )

    def _ensure_content(self, sec: dict) -> str:
        """Return section content, loading from disk lazily if missing.

        Sections persisted to JSON do NOT carry their content (Section.to_dict
        intentionally drops it to keep the index small). Lexical scoring used
        to silently read sec.get("content","") and always score zero on the
        content channel. This restores correctness via byte-range reads through
        the loader injected by DocStore.
        """
        body = sec.get("content")
        if body:
            return body
        sec_id = sec.get("id", "")
        if sec_id and sec_id in self._content_cache:
            return self._content_cache[sec_id]
        loader = self._content_loader
        if loader is None:
            return ""
        try:
            text = loader(sec.get("doc_path", ""), int(sec.get("byte_start", 0)), int(sec.get("byte_end", 0)))
        except Exception:
            text = ""
        if sec_id:
            self._content_cache[sec_id] = text or ""
        return text or ""

    def get_section(self, section_id: str) -> Optional[dict]:
        """Find a section dict by ID (O(1))."""
        return self._section_index.get(section_id)

    def _has_embeddings(self) -> bool:
        """Return True if at least some sections have embeddings stored."""
        if any(s.get("embedding") for s in self.sections):
            return True
        # jdoc#75: vectors may be sidecar-only (stripped from the monolith, not
        # yet rehydrated). A present sidecar means embeddings exist.
        sidecar = getattr(self, "_embeddings_sidecar", None)
        return bool(sidecar) and os.path.exists(sidecar)

    def _rehydrate_embeddings(self) -> None:
        """Attach sidecar vectors to section dicts in-place, at most once (jdoc#75).

        Vectors are ``array('f')`` (~4 KB per 1024-dim section) rather than
        Python float lists (~70 KB), so a large corpus rehydrates in a fraction
        of the RAM the old inline monolith cost. Every wire-facing consumer
        strips the ``embedding`` key before serializing (``_strip``,
        ``_index_to_dict``, get_section/get_sections), so the non-JSON ``array``
        type never leaks into a response or a saved index.
        """
        if getattr(self, "_embeddings_rehydrated", False):
            return
        self._embeddings_rehydrated = True
        sidecar = getattr(self, "_embeddings_sidecar", None)
        if not sidecar or not os.path.exists(sidecar):
            return
        vectors = _load_sidecar_vectors(sidecar)
        if not vectors:
            return
        for sec in self.sections:
            if sec.get("embedding"):
                continue
            vec = vectors.get(sec.get("content_hash") or "")
            if vec is not None:
                sec["embedding"] = vec

    def _embedded_section_count(self) -> int:
        """Count sections that carry (or can rehydrate) an embedding (jdoc#75).

        Rehydrates from the sidecar first so a monolith with vectors stripped
        still reports true embedding coverage (e.g. get_doc_health's
        embedding_coverage axis), not a false zero.
        """
        self._rehydrate_embeddings()
        return sum(1 for s in self.sections if s.get("embedding"))

    @staticmethod
    def _path_excluded(sec: dict, doc_path: Optional[str], path_glob: Optional[str]) -> bool:
        """Candidate pre-filter shared by every search mode (jdoc#32).

        ``path_glob`` must run here, before any top-k cut — as a tool-layer
        post-filter it starved single-document queries whenever the target
        document didn't rank in the corpus-wide top k.
        """
        sec_path = sec.get("doc_path", "")
        if doc_path and sec_path != doc_path:
            return True
        if path_glob and not fnmatch.fnmatch(sec_path, path_glob):
            return True
        return False

    def search(
        self,
        query: str,
        doc_path: Optional[str] = None,
        max_results: int = 10,
        semantic: Optional[bool] = None,
        semantic_only: bool = False,
        semantic_weight: float = 0.5,
        lexical_engine: str = "bm25",
        path_glob: Optional[str] = None,
    ) -> list:
        # Per-call content cache — bounded scope keeps memory predictable.
        self._content_cache = {}
        if lexical_engine not in ("bm25",):
            raise ValueError(
                f"Unknown lexical_engine: {lexical_engine!r}. "
                f"v1.20.0 dropped the legacy scorer; only 'bm25' is supported."
            )
        self._lexical_engine = lexical_engine
        """Search sections with BM25-style lexical + optional semantic fusion.

        Params:
          semantic: None (auto — hybrid when embeddings exist), True (force hybrid),
                    False (force lexical-only).
          semantic_only: Skip lexical; rank purely by embedding cosine similarity.
                        Implies semantic=True.
          semantic_weight: 0.0–1.0 weight of semantic component in fusion. 0.0 =
                          lexical-only, 1.0 = semantic-only. Default 0.5.

        Returns sections sorted by relevance, with content and embedding stripped.
        """
        has_emb = self._has_embeddings()
        if semantic_only:
            return self._semantic_search(query, doc_path, max_results, path_glob) if has_emb else []

        want_semantic = semantic if semantic is not None else has_emb
        if want_semantic and has_emb and 0.0 < semantic_weight <= 1.0:
            results = self._hybrid_search(query, doc_path, max_results, semantic_weight, path_glob)
            if results:
                return results
        return self._lexical_search(query, doc_path, max_results, path_glob)

    @staticmethod
    def _strip(sec: dict) -> dict:
        return {k: v for k, v in sec.items() if k not in ("content", "embedding")}

    def _semantic_matrices(self):
        """L2-normalized embedding matrices, BUCKETED BY VECTOR LENGTH (jdoc#109).

        Returns ``(np, {dim: (matrix, rows)})`` or None when numpy is
        unavailable or nothing is embedded.

        ⚠⚠ Bucketing is not tidiness. Stored vectors and the live query encoder
        can disagree: rotate ``JDOCMUNCH_ST_MODEL`` and the sidecar keeps 384-dim
        vectors while queries arrive at 768. The old single matrix then hit
        ``mat @ q`` and raised a raw numpy error out of every search
        ("size 768 is different from 384") — the index read as destroyed when
        only its vectors were stale. A sidecar can even hold BOTH widths at
        once, after a rotation that touched some files, and ``np.asarray`` on
        ragged rows raises before any query is scored.

        Keying by width means the caller asks for the bucket its query actually
        fits and the rest are simply absent, which is a miss, not a crash.
        """
        # jdoc#75: vectors live in the embeddings sidecar; stream them onto the
        # section dicts before building the matrix (no-op once rehydrated).
        self._rehydrate_embeddings()
        cached = getattr(self, "_sem_matrices_cache", "unset")
        if cached != "unset":
            return cached
        try:
            import numpy as np
        except Exception:
            self._sem_matrices_cache = None
            return None

        by_dim: dict = {}
        for sec in self.sections:
            emb = sec.get("embedding")
            if not emb:
                continue
            by_dim.setdefault(len(emb), []).append(sec)

        result = None
        if by_dim:
            built = {}
            for dim, rows in by_dim.items():
                mat = np.asarray([s["embedding"] for s in rows], dtype=np.float64)
                norms = np.linalg.norm(mat, axis=1, keepdims=True)
                norms[norms == 0.0] = 1.0  # zero vector stays zero -> cosine 0, never NaN
                mat /= norms
                built[dim] = (mat, rows)
            result = (np, built)
        self._sem_matrices_cache = result
        return result

    def _ensure_semantic_matrix(self):
        """Back-compat shim: ``(np, matrix, rows)`` for the WIDEST-COVERAGE dim.

        Predates the per-dimension bucketing in ``_semantic_matrices``. Kept
        because it is the documented jdoc#63 entry point; scoring goes through
        the bucketed form so it can match the query's own width.
        """
        built = self._semantic_matrices()
        cached = getattr(self, "_sem_matrix_cache", "unset")
        if cached != "unset":
            return cached
        result = None
        if built is not None:
            np, by_dim = built
            # Most-covered width, ties broken by the smaller dim for determinism.
            dim = sorted(by_dim, key=lambda d: (-len(by_dim[d][1]), d))[0]
            mat, rows = by_dim[dim]
            result = (np, mat, rows)
        self._sem_matrix_cache = result
        return result

    def embedding_dims(self) -> dict:
        """``{vector_length: section_count}`` over stored embeddings (jdoc#109).

        The signal a caller needs to say "your index is embedded at 384 and
        your model emits 768" instead of surfacing a matmul traceback.
        """
        self._rehydrate_embeddings()
        out: dict = {}
        for sec in self.sections:
            emb = sec.get("embedding")
            if emb:
                out[len(emb)] = out.get(len(emb), 0) + 1
        return out

    def _semantic_scored(self, query_vec, doc_path, path_glob):
        """Unsorted [(cosine, section), ...] for embedded, path-included sections
        (jdoc#63). One matrix-vector product when numpy is present, else the
        original per-section pure-Python cosine.

        jdoc#109: sections whose vector width differs from the query's are
        skipped rather than scored. Both branches need that check and for
        different reasons — numpy RAISES on the mismatched product, while
        ``cosine_similarity``'s ``zip`` silently truncates to the shorter vector
        and returns a plausible number, which is the worse of the two failures
        because nothing anywhere reports it.
        """
        qdim = len(query_vec)
        stored = self.embedding_dims()
        # ⚠ Degrading to lexical WITHOUT saying so would swap a loud failure for
        # a quiet one. The caller lifts this into `_meta.embedding_stale`.
        if stored and qdim not in stored:
            self._embedding_width_mismatch = {
                "query_dim": qdim,
                "stored_dims": dict(sorted(stored.items())),
            }

        built = self._semantic_matrices()
        if built is None:
            out = []
            for sec in self.sections:
                if self._path_excluded(sec, doc_path, path_glob):
                    continue
                emb = sec.get("embedding")
                if not emb or len(emb) != qdim:
                    continue
                out.append((cosine_similarity(query_vec, emb), sec))
            return out
        np, by_dim = built
        if qdim not in by_dim:
            return []
        mat, rows = by_dim[qdim]
        q = np.asarray(query_vec, dtype=np.float64)
        qn = float(np.linalg.norm(q))
        if qn == 0.0:
            return []
        q = q / qn
        scores = mat @ q   # (R,) cosine in one BLAS call
        out = []
        for i, sec in enumerate(rows):
            if self._path_excluded(sec, doc_path, path_glob):
                continue
            out.append((float(scores[i]), sec))
        return out

    def _semantic_search(
        self,
        query: str,
        doc_path: Optional[str],
        max_results: int,
        path_glob: Optional[str] = None,
    ) -> list:
        """Cosine-similarity search using stored section embeddings."""
        query_vec = embed_query(query)
        if not query_vec:
            return []

        # jdoc#63: one matrix-vector product instead of a per-section cosine.
        scored = self._semantic_scored(query_vec, doc_path, path_glob)
        scored.sort(key=lambda x: (-x[0], x[1].get("id", "")))
        out: list[dict] = []
        for score, sec in scored[:max_results]:
            stripped = self._strip(sec)
            stripped["_score"] = float(score)
            out.append(stripped)
        return out

    def _hybrid_search(
        self,
        query: str,
        doc_path: Optional[str],
        max_results: int,
        semantic_weight: float,
        path_glob: Optional[str] = None,
    ) -> list:
        """Hybrid lexical + semantic ranking via Reciprocal Rank Fusion (v1.13.0).

        Min-max normalization (the v1.9 approach) was unstable under sparse
        candidate sets — a single result always normalized to 1.0. RRF is
        rank-based: each ranking contributes ``w / (k + rank_i)`` per item.
        ``semantic_weight`` is the relative weight of the semantic ranking;
        the lexical ranking gets ``1 - semantic_weight``. ``k=60`` follows
        Cormack 2009.
        """
        from ..retrieval.prune import reciprocal_rank_fusion

        query_lower = query.lower()
        query_words = set(query_lower.split())
        query_vec = embed_query(query) if semantic_weight > 0 else None
        if semantic_weight > 0 and query_vec is None:
            # Embedding provider unavailable at query time — degrade to lexical.
            return self._lexical_search(query, doc_path, max_results, path_glob)

        # ----- Lexical ranking (Stage A prune + BM25) -----
        engine = getattr(self, "_lexical_engine", "bm25")
        candidate_ids: Optional[set] = None
        if engine == "bm25":
            from ..retrieval.prune import get_or_build

            posting = get_or_build(self, content_loader=self._content_loader)
            candidate_ids = posting.candidates(query)

        lex_pairs: list[tuple[float, dict]] = []
        for sec in self.sections:
            if candidate_ids is not None and sec.get("id") not in candidate_ids:
                continue
            if self._path_excluded(sec, doc_path, path_glob):
                continue
            score = self._score_section(sec, query, query_words)
            if score > 0:
                lex_pairs.append((score, sec))
        lex_pairs.sort(key=lambda x: (-x[0], x[1].get("id", "")))
        lex_ranking = [s.get("id", "") for _, s in lex_pairs]

        # ----- Semantic ranking (cosine over stored embeddings) -----
        # jdoc#63: vectorized semantic scoring (same ranking as the loop).
        sem_pairs = self._semantic_scored(query_vec, doc_path, path_glob) if query_vec else []
        sem_pairs.sort(key=lambda x: (-x[0], x[1].get("id", "")))
        sem_ranking = [s.get("id", "") for _, s in sem_pairs]

        if not lex_ranking and not sem_ranking:
            return []

        # ----- RRF fusion -----
        fused = reciprocal_rank_fusion(
            [lex_ranking, sem_ranking],
            weights=[1.0 - semantic_weight, semantic_weight],
            k=60,
        )

        # Materialize top max_results sections.
        by_id = {s.get("id"): s for s in self.sections}
        out: list[dict] = []
        for sid, score in fused[:max_results]:
            sec = by_id.get(sid)
            if sec is not None:
                stripped = self._strip(sec)
                stripped["_score"] = float(score)
                out.append(stripped)
        return out

    def _lexical_search(
        self,
        query: str,
        doc_path: Optional[str],
        max_results: int,
        path_glob: Optional[str] = None,
    ) -> list:
        """Two-stage retrieval (v1.13.0): posting-list prune → BM25 rescore.

        Stage A reduces the candidate set to sections containing at least one
        query token (capped at MAX_CANDIDATES). Stage B applies the
        per-section scoring engine (BM25 by default, legacy on demand). The
        prune is skipped under the legacy engine because the legacy heuristic
        depends on substring matches that the tokenizer doesn't preserve.

        Falls back to full-corpus scan when the posting index can't help —
        no in-vocab terms, or legacy engine selected.
        """
        engine = getattr(self, "_lexical_engine", "bm25")
        query_lower = query.lower()
        query_words = set(query_lower.split())

        candidate_ids: Optional[set] = None
        if engine == "bm25":
            from ..retrieval.prune import get_or_build

            posting = get_or_build(self, content_loader=self._content_loader)
            candidate_ids = posting.candidates(query)

        scored = []
        for sec in self.sections:
            if candidate_ids is not None and sec.get("id") not in candidate_ids:
                continue
            if self._path_excluded(sec, doc_path, path_glob):
                continue
            score = self._score_section(sec, query, query_words)
            if score > 0:
                scored.append((score, sec))

        scored.sort(key=lambda x: (-x[0], x[1].get("id", "")))
        out: list[dict] = []
        for score, sec in scored[:max_results]:
            stripped = self._strip(sec)
            stripped["_score"] = float(score)
            out.append(stripped)
        return out

    @staticmethod
    def _word_matches(word: str, text: str) -> bool:
        """True if word is an exact match or prefix of any word in text."""
        if word in text:
            return True
        # prefix match: "authenticat" hits "authentication"
        return any(t.startswith(word) for t in text.split() if len(word) >= 3)

    def _score_section(self, sec: dict, query: str, query_words: set) -> float:
        """BM25-Okapi scoring with tag-match kicker.

        ``query`` is the ORIGINAL query text, NOT a lowercased copy (#91
        follow-up, @tetiz123). ``bm25.tokenize`` de-camels before it
        lowercases, so pre-lowercasing the query collapses ``OvertimeService``
        to one token on the query side while the document side split it in two
        — code-identifier searches then scored 0. ``tokenize`` lowercases
        internally, so passing the raw query is both correct and free.
        ``query_words`` stays the lowercased set: the tag kicker matches
        case-folded tags.

        v1.20.0: dropped the v1.0–v1.11 legacy heuristic fallback. Callers
        that pass ``lexical_engine="legacy"`` now get a ValueError at search
        time so the deprecation surfaces loudly rather than silently.
        """
        from ..retrieval.bm25 import score_section as _bm25_score

        # Provide the loader so BM25 can lazily fetch content for the
        # content channel.
        def _loader(doc_path: str, byte_start: int, byte_end: int) -> str:
            fake = {"content": "", "doc_path": doc_path, "byte_start": byte_start, "byte_end": byte_end, "id": sec.get("id", "")}
            return self._ensure_content(fake)

        score = _bm25_score(
            sec,
            query,
            stats=self.bm25_stats or None,
            content_loader=_loader,
        )
        tags = sec.get("tags", [])
        if tags and query_words:
            tag_hits = sum(1 for t in tags if t.lower() in query_words)
            score += 0.5 * tag_hits
        return score


class DocStore:
    """Storage for doc indexes with byte-offset content retrieval."""

    def __init__(self, base_path: Optional[str] = None):
        if base_path:
            self.base_path = Path(base_path)
        else:
            # #37: honor DOC_INDEX_PATH for EVERY entry point (CLI + hooks), not
            # just the MCP dispatch path, so storage can't split-brain. An
            # explicit base_path still takes precedence.
            env_path = os.environ.get("DOC_INDEX_PATH")
            self.base_path = Path(env_path) if env_path else Path.home() / ".doc-index"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _safe_repo_component(self, value: str, field_name: str) -> str:
        # Shares retirements.is_safe_path_component so the store side and the
        # record side cannot drift into two different notions of "safe".
        from .retirements import is_safe_path_component
        if not is_safe_path_component(value):
            raise ValueError(f"Invalid {field_name}: {value!r}")
        return value

    def _index_path(self, owner: str, name: str) -> Path:
        o = self._safe_repo_component(owner, "owner")
        n = self._safe_repo_component(name, "name")
        return self.base_path / o / f"{n}.json"

    def _content_dir(self, owner: str, name: str) -> Path:
        o = self._safe_repo_component(owner, "owner")
        n = self._safe_repo_component(name, "name")
        return self.base_path / o / n

    def _summary_path(self, owner: str, name: str) -> Path:
        """Path to the tiny per-index list_repos summary sidecar (jdoc#77)."""
        return self._index_path(owner, name).with_name(
            f"{self._safe_repo_component(name, 'name')}.summary.json"
        )

    def _ensure_sidecar_from_sections(self, owner: str, name: str, sections: list) -> None:
        """Guarantee stripped monolith vectors are recoverable (jdoc#75).

        Vectors are dropped from the monolith at save time and rehydrated from
        the ``<name>.embeddings.jsonl`` sidecar. In the normal flow ``embed_sections``
        has already written that sidecar before ``save_index`` runs, so this is a
        no-op. But an index whose sections carry embeddings set outside the embed
        pipeline (e.g. an in-process build) would have no sidecar -- stripping
        would then lose the vectors. When the sidecar is absent and sections do
        carry embeddings, persist one here so the strip is always lossless.

        The header identity is a placeholder (``_load_sidecar_vectors`` ignores
        the header); a subsequent real embed pass sees the mismatch and simply
        re-embeds, which is safe.

        ⚠⚠ jdoc#107: this used to RETURN EARLY whenever a sidecar existed, on
        the reasoning that ``embed_sections`` had already written the
        authoritative one. That made the net unable to extend a sidecar — so
        an incremental ``index_file`` pass, whose sections were embedded
        outside the cache pipeline, had its vectors stripped by the save and
        never persisted anywhere. It now APPENDS the missing keys instead,
        which never removes a row and never overwrites a real provider header
        with the ``__inline__`` placeholder (doing so would make the next
        embed pass read an identity mismatch and purge the whole file).
        """
        try:
            from ..embeddings.provider import _EMBED_TEXT_VERSION
            entries = [
                (f"{s['content_hash']}#{_EMBED_TEXT_VERSION}", list(s["embedding"]))
                for s in sections
                if isinstance(s, dict) and s.get("embedding") and s.get("content_hash")
            ]
            if not entries:
                return
            from ..embeddings import cache as _emb_cache
            _emb_cache.append_entries(
                str(self.base_path), owner, name,
                entries=entries,
                identity_if_new=("__inline__", "__inline__", None),
            )
        except Exception:
            pass  # best-effort; never fail a save over the sidecar safety net

    def _write_summary(self, owner: str, name: str, index: "DocIndex") -> None:
        """Persist a tiny summary next to the monolith so list_repos never has to
        json-parse the whole index just to take two ``len()``s (jdoc#77).

        Written atomically inside the same per-repo write lock that guards the
        monolith (both save paths are ``@_with_index_lock``), so the summary can
        never lag its index. Best-effort: a summary write failure must never
        fail an otherwise-successful index save (list_repos falls back to the
        full parse when the summary is absent or unreadable).
        """
        try:
            summary = {
                "repo": index.repo,
                "indexed_at": index.indexed_at,
                "section_count": len(index.sections),
                "doc_count": len(index.doc_paths),
                "doc_types": index.doc_types,
                "index_version": index.index_version,
                "head_sha": index.head_sha,
                "source_dirty": bool(index.source_dirty),
                "sha_certified": bool(index.sha_certified),
                "source_root": getattr(index, "source_root", "") or "",
                "source_repo": getattr(index, "source_repo", "") or "",
                "corpus_selection": getattr(index, "corpus_selection", "") or "",
                "corpus_shape_patterns": list(
                    getattr(index, "corpus_shape_patterns", None) or []
                ),
                "worktree_lineage_key": getattr(index, "worktree_lineage_key", "") or "",
                "repo_relative_root": getattr(index, "repo_relative_root", "") or "",
                "reconciliation_state": getattr(index, "reconciliation_state", "") or "",
                # jdoc#85 C1-09: written even when 0 so the summary's presence
                # or absence of THIS KEY distinguishes a pre-fix summary (fall
                # back to the monolith) from a genuinely legacy index.
                "corpus_identity_version": int(
                    getattr(index, "corpus_identity_version", 0) or 0
                ),
            }
            summary_path = self._summary_path(owner, name)
            tmp = summary_path.with_name(f"{summary_path.name}.{os.getpid()}.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(summary, f, separators=(",", ":"))
            self._atomic_replace(tmp, summary_path)
        except (OSError, ValueError, TypeError):
            pass

    def _safe_content_path(self, content_dir: Path, relative_path: str) -> Optional[Path]:
        try:
            base = content_dir.resolve()
            candidate = (content_dir / relative_path).resolve()
            if os.path.commonpath([str(base), str(candidate)]) != str(base):
                return None
            return candidate
        except (OSError, ValueError):
            return None

    @contextmanager
    def _index_write_lock(self, owner, name):
        """Exclusive cross-process lock guarding writes to one repo's index.

        Backed by an advisory lock on a per-repo ``<name>.json.lock`` file:
        ``flock`` on POSIX, ``msvcrt.locking`` on Windows. No-op only when
        neither primitive is available or owner/name are missing -- the per-PID
        temp name plus the replace-retry in the writers still prevent structural
        corruption between processes in that degenerate case.
        """
        try:
            lock_path = self._index_path(owner, name).with_name(
                f"{self._index_path(owner, name).name}.lock"
            )
        except (ValueError, TypeError):
            yield
            return
        if (fcntl is None and msvcrt is None) or not owner or not name:
            yield
            return
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
                # jdoc#89 QA-15: POSIX locks attach to the open inode, not the
                # pathname. delete_index can unlink the lockfile while another
                # process holds (or is queued on) its old inode; a lock taken
                # on a stale inode would no longer coordinate with later
                # acquirers of the recreated path. Verify the fd still IS the
                # path after acquiring; if not, retry on the fresh inode.
                try:
                    path_stat = os.stat(str(lock_path))
                    fd_stat = os.fstat(fd)
                    if (path_stat.st_ino, path_stat.st_dev) == (
                        fd_stat.st_ino, fd_stat.st_dev
                    ):
                        break
                except OSError:
                    pass  # unlinked under us — stale inode, retry
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
                continue
            # Windows: LK_LOCK blocks ~10s then raises; loop until granted.
            # An open file cannot be unlinked on Windows, so the inode-split
            # hazard above cannot occur here.
            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            break
        try:
            yield
        finally:
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                else:
                    try:
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
            finally:
                os.close(fd)

    @contextmanager
    def _try_index_write_lock(self, owner, name):
        """Non-blocking sibling of :meth:`_index_write_lock`; yields acquired?

        jdoc#93 QA-19 (Path A). The retirement gate must be able to tell that a
        save or delete is *in flight* on the retained handle. A fingerprint
        cannot: it proves the file has not changed YET, and says nothing about a
        writer holding the lock one instruction from publishing.

        Deliberately NON-BLOCKING. The QA-14 deadlock surface was closed by the
        rule that no caller ever blocks on two locks, and this gate already
        holds its own handle lock plus its record lock. An attempt that never
        waits cannot participate in a cycle, so the rule survives: on
        contention we fail closed rather than queue.

        Degenerate case: when neither locking primitive exists, or the path is
        unusable, this yields True — matching ``_index_write_lock``, which is
        itself a no-op there. The check is exactly as vacuous as the lock it
        probes, never more optimistic than the surrounding model.
        """
        try:
            lock_path = self._index_path(owner, name).with_name(
                f"{self._index_path(owner, name).name}.lock"
            )
        except (ValueError, TypeError):
            yield True
            return
        if (fcntl is None and msvcrt is None) or not owner or not name:
            yield True
            return
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        fd = None
        acquired = False
        # Bounded, because the QA-15 inode recheck can legitimately retry. With
        # QA-21 fixed nothing unlinks the lockfile any more, so this should
        # settle on the first pass; the cap keeps a pathological racer from
        # spinning here forever.
        for _ in range(5):
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
            try:
                if fcntl is not None:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError:
                        os.close(fd)
                        fd = None
                        break  # held by someone else — that is the answer
                    # QA-15: confirm we locked the inode this path names.
                    try:
                        path_stat = os.stat(str(lock_path))
                        fd_stat = os.fstat(fd)
                        if (path_stat.st_ino, path_stat.st_dev) == (
                            fd_stat.st_ino, fd_stat.st_dev
                        ):
                            acquired = True
                            break
                    except OSError:
                        pass  # unlinked under us — stale inode, retry
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)
                    fd = None
                    continue
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                except OSError:
                    os.close(fd)
                    fd = None
                    break  # held by someone else
                acquired = True
                break
            except Exception:
                if fd is not None:
                    os.close(fd)
                    fd = None
                raise

        try:
            yield acquired
        finally:
            if fd is not None:
                try:
                    if fcntl is not None:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    else:
                        try:
                            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                        except OSError:
                            pass
                finally:
                    os.close(fd)

    @contextmanager
    def _gate_retained_handle(self, retained, owner, name):
        """Hold the RETAINED peer's write lock across the destructive step.

        Yields True when the gate may proceed. Yields False only when a writer
        or deleter genuinely holds the retained handle right now — the caller
        turns that into a ``RetirementConflict`` so both indexes stay loadable.

        Two cases deliberately proceed without acquiring anything:

        * No retained handle (an unguarded delete). There is no peer to couple.
        * The retained handle IS this handle. ``_index_write_lock`` is
          non-reentrant and per-fd, so acquiring it again on this thread would
          conflict with the lock this very delete already holds and turn every
          such retirement into a permanent false conflict.
        """
        if not retained or not isinstance(retained, str) or "/" not in retained:
            yield True
            return
        r_owner, _, r_name = retained.partition("/")
        if (r_owner, r_name) == (owner, name):
            yield True
            return
        with self._try_index_write_lock(r_owner, r_name) as got:
            yield got

    @staticmethod
    def _atomic_replace(tmp_path: Path, index_path: Path,
                        attempts: int = 10, base_delay: float = 0.02) -> None:
        """``os.replace(tmp, dst)`` with bounded backoff for Windows share races.

        POSIX ``rename`` is atomic and never collides. On Windows a concurrent
        reader holding the destination open makes the replace raise
        ``PermissionError`` (WinError 5/32) transiently; a brief retry rides it
        out. After the attempts are exhausted the original error is re-raised,
        so the default failure mode is unchanged (1.x contract: never
        newly-raise).
        """
        for i in range(attempts):
            try:
                os.replace(tmp_path, index_path)
                return
            except PermissionError:
                if os.name != "nt" or i == attempts - 1:
                    raise
                time.sleep(base_delay * (i + 1))

    @_with_index_lock
    def save_index(
        self,
        owner: str,
        name: str,
        sections: list,         # list[Section]
        raw_files: dict,        # {doc_path: content}
        doc_types: dict,        # {".md": N}
        file_hashes: Optional[dict] = None,
        head_sha: Optional[str] = None,
        source_dirty: bool = False,
        sha_certified: bool = False,
        source_root: str = "",
        source_repo: str = "",
        corpus_selection: str = "",
        corpus_shape_patterns: Optional[list] = None,
        worktree_lineage_key: str = "",
        repo_relative_root: str = "",
        corpus_identity_version: int = 0,
        reconciliation_state: str = "",
        coverage: Optional[dict] = None,
    ) -> "DocIndex":
        """Save index and raw files to storage atomically."""
        if file_hashes is None:
            file_hashes = {fp: _file_hash(c) for fp, c in raw_files.items()}

        doc_paths = sorted(raw_files.keys())

        # Compute BM25 corpus stats from the in-memory Section objects (which
        # carry full content) before to_dict() drops it.
        from ..retrieval.bm25 import compute_corpus_stats
        bm25_stats = compute_corpus_stats(sections)

        index = DocIndex(
            repo=f"{owner}/{name}",
            owner=owner,
            name=name,
            indexed_at=datetime.now().isoformat(),
            doc_paths=doc_paths,
            doc_types=doc_types,
            sections=[s.to_dict() for s in sections],
            index_version=INDEX_VERSION,
            file_hashes=file_hashes,
            head_sha=head_sha,
            source_dirty=source_dirty,
            sha_certified=sha_certified,
            bm25_stats=bm25_stats,
            source_root=source_root or "",
            source_repo=source_repo or "",
            corpus_selection=corpus_selection or "",
            corpus_shape_patterns=list(corpus_shape_patterns or []),
            worktree_lineage_key=worktree_lineage_key or "",
            repo_relative_root=repo_relative_root or "",
            corpus_identity_version=int(corpus_identity_version or 0),
            reconciliation_state=reconciliation_state or "",
            coverage=dict(coverage) if coverage else {},
        )

        index_path = self._index_path(owner, name)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        # Per-PID temp name so concurrent writers never share (and clobber) one
        # temp file; the cross-process lock serializes the replace itself.
        # jdoc#75: make sure the vectors we're about to strip from the monolith
        # are recoverable from the sidecar before we overwrite it.
        self._ensure_sidecar_from_sections(owner, name, index.sections)
        tmp_path = index_path.with_name(f"{index_path.name}.{os.getpid()}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            # jdoc#75: compact separators -- indent=2 inflated the monolith to
            # multiple GB on broadly-indexed corpora.
            json.dump(self._index_to_dict(index), f, separators=(",", ":"))
        self._atomic_replace(tmp_path, index_path)
        # jdoc#89 QA-07: cancel the pending retirement only AFTER the rewrite
        # actually landed — a save that fails before the replace leaves the
        # still-pending work discoverable instead of erasing its record.
        self._cancel_pending_retirement(owner, name)
        _evict_index_cache(index_path)
        self._write_summary(owner, name, index)  # jdoc#77

        # Cache the indexed content mirror for byte-range reads. NB these are
        # the *preprocessed* strings (transformed formats like .json/.jsonc/.svg
        # are converted by preprocess_content before storage), not raw workspace
        # bytes; byte offsets and content_hash are in this preprocessed domain
        # (jdoc#74).
        content_dir = self._content_dir(owner, name)
        content_dir.mkdir(parents=True, exist_ok=True)

        for doc_path, content in raw_files.items():
            dest = self._safe_content_path(content_dir, doc_path)
            if not dest:
                raise ValueError(f"Unsafe doc path in raw_files: {doc_path}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                f.write(content.encode("utf-8"))

        return index

    def load_index(self, owner: str, name: str) -> Optional[DocIndex]:
        """Load index from storage, using an in-memory cache keyed by (path, mtime)."""
        try:
            index_path = self._index_path(owner, name)
        except ValueError:
            return None
        if not index_path.exists():
            return None

        mtime_ns = index_path.stat().st_mtime_ns
        cache_key = (str(index_path), mtime_ns)
        cached = _index_cache_get(cache_key)
        if cached is not None:
            return _stamp_load_provenance(cached, index_path, mtime_ns)

        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        stored_version = data.get("index_version", 1)
        if stored_version != INDEX_VERSION:
            # Version mismatch (older or newer): trigger full re-index.
            return None

        index = DocIndex(
            repo=data["repo"],
            owner=data["owner"],
            name=data["name"],
            indexed_at=data["indexed_at"],
            doc_paths=data["doc_paths"],
            doc_types=data["doc_types"],
            sections=data["sections"],
            index_version=stored_version,
            file_hashes=data.get("file_hashes", {}),
            head_sha=data.get("head_sha"),
            source_dirty=bool(data.get("source_dirty", False)),
            sha_certified=bool(data.get("sha_certified", False)),
            bm25_stats=data.get("bm25_stats", {}),
            source_root=data.get("source_root", ""),
            source_repo=data.get("source_repo", ""),
            corpus_selection=data.get("corpus_selection", ""),
            corpus_shape_patterns=list(data.get("corpus_shape_patterns") or []),
            worktree_lineage_key=data.get("worktree_lineage_key", ""),
            repo_relative_root=data.get("repo_relative_root", ""),
            corpus_identity_version=int(data.get("corpus_identity_version", 0) or 0),
            reconciliation_state=data.get("reconciliation_state", ""),
            coverage=data.get("coverage") or {},
        )

        # Inject lazy content loader so search can score on body text (B1).
        owner_str, name_str = owner, name
        content_dir = self._content_dir(owner_str, name_str)

        def _loader(doc_path: str, byte_start: int, byte_end: int) -> str:
            if not doc_path or byte_end <= byte_start:
                return ""
            file_path = self._safe_content_path(content_dir, doc_path)
            if not file_path or not file_path.exists():
                return ""
            try:
                with open(file_path, "rb") as fh:
                    fh.seek(byte_start)
                    raw = fh.read(byte_end - byte_start)
                return raw.decode("utf-8", errors="replace")
            except OSError:
                return ""

        index._content_loader = _loader
        # jdoc#75: point the index at its embeddings sidecar so vectors can
        # rehydrate lazily on first semantic use (see _rehydrate_embeddings).
        # Prefer the sidecar co-located with this monolith; fall back to the
        # default-root location that embed_sections writes to when it was called
        # with storage_path=None (its _cache_path ignores DOC_INDEX_PATH).
        try:
            from ..embeddings.cache import _cache_path as _emb_cache_path
            co_located = _emb_cache_path(str(self.base_path), owner_str, name_str)
            if co_located.exists():
                index._embeddings_sidecar = str(co_located)
            else:
                default_root = _emb_cache_path(None, owner_str, name_str)
                index._embeddings_sidecar = str(
                    default_root if default_root.exists() else co_located
                )
        except Exception:
            index._embeddings_sidecar = None
        _index_cache_put(cache_key, index)
        return _stamp_load_provenance(index, index_path, mtime_ns)

    def detect_changes(
        self,
        owner: str,
        name: str,
        current_files: dict,
    ) -> tuple:
        """Detect changed, new, and deleted files by comparing hashes.

        Returns (changed, new, deleted) — each a list of doc_path strings.
        """
        index = self.load_index(owner, name)
        if not index:
            return [], list(current_files.keys()), []

        old_hashes = index.file_hashes
        current_hashes = {fp: _file_hash(c) for fp, c in current_files.items()}

        old_set = set(old_hashes.keys())
        new_set = set(current_hashes.keys())

        new_files = list(new_set - old_set)
        deleted_files = list(old_set - new_set)
        changed_files = [
            fp for fp in (old_set & new_set)
            if old_hashes[fp] != current_hashes[fp]
        ]

        return changed_files, new_files, deleted_files

    @_with_index_lock
    def incremental_save(
        self,
        owner: str,
        name: str,
        changed_files: list,
        new_files: list,
        deleted_files: list,
        new_sections: list,     # list[Section]
        raw_files: dict,        # {doc_path: content} for changed + new files only
        doc_types: dict,
        head_sha=_UNSET,
        source_dirty=_UNSET,
        sha_certified=_UNSET,
        source_root=_UNSET,
        source_repo=_UNSET,
        corpus_selection=_UNSET,
        corpus_shape_patterns=_UNSET,
        worktree_lineage_key=_UNSET,
        repo_relative_root=_UNSET,
        corpus_identity_version=_UNSET,
        reconciliation_state=_UNSET,
        coverage=_UNSET,
    ) -> Optional["DocIndex"]:
        """Incrementally update an existing index.

        Removes sections for deleted/changed files, adds new sections,
        updates raw content files, and saves atomically.
        """
        index = self.load_index(owner, name)
        if not index:
            return None

        # Drop sections belonging to deleted or changed files
        files_to_remove = set(deleted_files) | set(changed_files)
        kept_sections = [s for s in index.sections if s.get("doc_path") not in files_to_remove]

        # Merge in new sections
        all_section_dicts = kept_sections + [s.to_dict() for s in new_sections]

        # Recompute doc_types from surviving + new sections
        seen: dict = {}
        for s in all_section_dicts:
            dp = s.get("doc_path", "")
            if dp and dp not in seen:
                import os as _os
                seen[dp] = _os.path.splitext(dp)[1].lower()
        recomputed_types: dict = {}
        for ext in seen.values():
            recomputed_types[ext] = recomputed_types.get(ext, 0) + 1
        if not recomputed_types and doc_types:
            recomputed_types = doc_types

        # Update doc_paths list
        old_paths = set(index.doc_paths)
        for f in deleted_files:
            old_paths.discard(f)
        for f in new_files + changed_files:
            old_paths.add(f)

        # Update file hashes
        file_hashes = dict(index.file_hashes)
        for f in deleted_files:
            file_hashes.pop(f, None)
        for fp, content in raw_files.items():
            file_hashes[fp] = _file_hash(content)

        # Recompute BM25 stats. Kept sections come from the loaded index
        # (no inline content); pass a content_loader so the stats reflect
        # body text, then merge in the new in-memory Section objects.
        from ..retrieval.bm25 import compute_corpus_stats

        # Reuse the index's content loader (set up at load_index time) so
        # kept sections can be byte-range-read for stats. New raw files
        # haven't been flushed to disk yet, so we shadow them via an
        # in-memory map first.
        kept_loader = getattr(index, "_content_loader", None)
        new_raw_map = dict(raw_files)

        def _stats_loader(doc_path: str, byte_start: int, byte_end: int) -> str:
            buf = new_raw_map.get(doc_path)
            if buf is not None and byte_end > byte_start:
                return buf[byte_start:byte_end]
            if kept_loader:
                return kept_loader(doc_path, byte_start, byte_end) or ""
            return ""

        # Inline content for the new tail so compute_corpus_stats doesn't
        # need to re-read disk for them; kept sections fall through to the
        # _stats_loader byte-range read.
        merged_for_stats = list(kept_sections) + [
            {**s.to_dict(), "content": (getattr(s, "content", "") or "")}
            for s in new_sections
        ]
        bm25_stats = compute_corpus_stats(merged_for_stats, content_loader=_stats_loader)

        updated = DocIndex(
            repo=f"{owner}/{name}",
            owner=owner,
            name=name,
            indexed_at=datetime.now().isoformat(),
            doc_paths=sorted(old_paths),
            doc_types=recomputed_types,
            sections=all_section_dicts,
            index_version=INDEX_VERSION,
            file_hashes=file_hashes,
            head_sha=index.head_sha if head_sha is _UNSET else head_sha,
            source_dirty=index.source_dirty if source_dirty is _UNSET else bool(source_dirty),
            sha_certified=index.sha_certified if sha_certified is _UNSET else bool(sha_certified),
            bm25_stats=bm25_stats,
            source_root=index.source_root if source_root is _UNSET else (source_root or ""),
            source_repo=index.source_repo if source_repo is _UNSET else (source_repo or ""),
            corpus_selection=(
                getattr(index, "corpus_selection", "")
                if corpus_selection is _UNSET
                else (corpus_selection or "")
            ),
            corpus_shape_patterns=(
                list(getattr(index, "corpus_shape_patterns", None) or [])
                if corpus_shape_patterns is _UNSET
                else list(corpus_shape_patterns or [])
            ),
            worktree_lineage_key=(
                getattr(index, "worktree_lineage_key", "")
                if worktree_lineage_key is _UNSET
                else (worktree_lineage_key or "")
            ),
            repo_relative_root=(
                getattr(index, "repo_relative_root", "")
                if repo_relative_root is _UNSET
                else (repo_relative_root or "")
            ),
            corpus_identity_version=(
                getattr(index, "corpus_identity_version", 0)
                if corpus_identity_version is _UNSET
                else int(corpus_identity_version or 0)
            ),
            # jdoc#80 Part B: reconciliation_state carries forward across
            # incremental refreshes (a provisional index stays provisional —
            # Part B has no graduation path).
            reconciliation_state=(
                getattr(index, "reconciliation_state", "")
                if reconciliation_state is _UNSET
                else (reconciliation_state or "")
            ),
            # v1.103.0: coverage carries forward across incremental saves; a
            # full re-walk (save_index) overwrites it (self-heals).
            coverage=(
                getattr(index, "coverage", {}) or {}
                if coverage is _UNSET
                else (dict(coverage) if coverage else {})
            ),
        )

        # Save atomically (per-PID temp + retried replace; see save_index)
        index_path = self._index_path(owner, name)
        # jdoc#75: sidecar safety net before stripping vectors from the monolith.
        self._ensure_sidecar_from_sections(owner, name, updated.sections)
        tmp_path = index_path.with_name(f"{index_path.name}.{os.getpid()}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            # jdoc#75: compact separators (see save_index).
            json.dump(self._index_to_dict(updated), f, separators=(",", ":"))
        self._atomic_replace(tmp_path, index_path)
        # jdoc#89 QA-07: cancel only after the rewrite landed (see save_index).
        self._cancel_pending_retirement(owner, name)
        _evict_index_cache(index_path)
        self._write_summary(owner, name, updated)  # jdoc#77

        # Update cached raw files
        content_dir = self._content_dir(owner, name)
        content_dir.mkdir(parents=True, exist_ok=True)

        for fp in deleted_files:
            dead = self._safe_content_path(content_dir, fp)
            if dead and dead.exists():
                dead.unlink()

        for fp, content in raw_files.items():
            dest = self._safe_content_path(content_dir, fp)
            if not dest:
                raise ValueError(f"Unsafe doc path in raw_files: {fp}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                f.write(content.encode("utf-8"))

        return updated

    def get_section_content(self, owner: str, name: str, section_id: str, _index: Optional["DocIndex"] = None) -> Optional[str]:
        """Read section content using stored byte offsets. O(1) — no re-parsing.

        Pass _index to avoid a redundant load_index() call when the caller
        already holds a loaded index.
        """
        index = _index or self.load_index(owner, name)
        if not index:
            return None

        section = index.get_section(section_id)
        if not section:
            return None

        doc_path = section.get("doc_path", "")
        byte_start = section.get("byte_start", 0)
        byte_end = section.get("byte_end", 0)

        file_path = self._safe_content_path(self._content_dir(owner, name), doc_path)
        if not file_path or not file_path.exists():
            return None

        with open(file_path, "rb") as f:
            f.seek(byte_start)
            raw = f.read(byte_end - byte_start)

        return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _summary_row(section_count: int, doc_count: int, data: dict) -> dict:
        """Build one list_repos row from summary-shaped fields (jdoc#77).

        Shared by the fast summary-sidecar path and the full-parse fallback so
        both emit a byte-identical row. ``data`` supplies repo/identity/freshness
        fields; the two counts are passed in because the summary carries them
        precomputed while the full parse derives them from the section/doc lists.
        """
        # jdoc#67 / #68: expose typed identity fields so a consumer can
        # distinguish the durable lookup handle (`repo`, e.g. `local/foo-docs`)
        # from the bare refresh/index `name` (`foo-docs`) without parsing, and
        # tell a doc handle from a jCodeMunch code handle (`repo_kind`).
        _owner, _, _bare = str(data["repo"]).partition("/")
        row = {
            "repo": data["repo"],
            "repo_kind": "doc_index",
            "owner": _owner or "",
            "name": _bare or str(data["repo"]),
            "indexed_at": data["indexed_at"],
            "section_count": section_count,
            "doc_count": doc_count,
            "doc_types": data["doc_types"],
            "index_version": data.get("index_version", 1),
        }
        sha = normalize_commit_sha(data.get("head_sha"))
        source_dirty = bool(data.get("source_dirty", False))
        sha_certified = bool(data.get("sha_certified", False))
        if sha:
            row["head_sha"] = sha
        row["source_dirty"] = source_dirty
        row["sha_certified"] = sha_certified
        repo_at_sha = format_repo_at_sha(data["repo"], sha, source_dirty, sha_certified)
        if repo_at_sha:
            row["repo_at_sha"] = repo_at_sha
        if data.get("source_root"):
            row["source_root"] = data["source_root"]
        if data.get("corpus_selection"):
            row["corpus_selection"] = data["corpus_selection"]
        if data.get("worktree_lineage_key"):
            row["worktree_lineage_key"] = data["worktree_lineage_key"]
        if data.get("repo_relative_root"):
            row["repo_relative_root"] = data["repo_relative_root"]
        if data.get("reconciliation_state"):
            row["reconciliation_state"] = data["reconciliation_state"]
        # jdoc#85 C1-09: identity version must survive the summary/list
        # projection, or a modern index reads as pre-1.102 legacy to consumers
        # like legacy_sibling_handles. Omit-when-zero matches the monolith.
        _civ = int(data.get("corpus_identity_version", 0) or 0)
        if _civ:
            row["corpus_identity_version"] = _civ
        if data.get("source_repo"):
            row["source_repo"] = data["source_repo"]
            source_repo_at_sha = format_repo_at_sha(
                data["source_repo"],
                sha,
                source_dirty,
                sha_certified,
            )
            if source_repo_at_sha:
                row["source_repo_at_sha"] = source_repo_at_sha
        return row

    def _is_owned_sidecar(self, path: Path) -> bool:
        """Whether ``path`` is an index-owned sidecar rather than a monolith.

        Decided by suffix and one ``exists()`` -- never by opening the file,
        which is the whole point of jdoc#121.

        ⚠ The suffix alone is not quite sufficient, because repo names may
        contain dots (``is_safe_path_component`` allows ``[A-Za-z0-9._-]``).
        A repo genuinely named ``api.related`` writes its PRIMARY monolith to
        ``api.related.json``, and a bare suffix test would hide it -- turning a
        performance fix into a repo that stopped being listed. So a candidate
        that looks like a sidecar is readmitted when it has its own summary
        sidecar (``api.related.summary.json``): every index saved since jdoc#77
        writes one, and nothing anywhere writes a ``.summary.json`` beside a
        real sidecar. That readmission costs one stat and no parse.

        ⚠ A pre-jdoc#77 legacy index whose name ends in one of these suffixes
        has no summary to vouch for it and is not listed. Recorded rather than
        solved: the only way to tell it from an orphaned sidecar is to parse
        the file, which is the cost being removed.
        """
        name = path.name
        for suffix in _SIDECAR_SUFFIXES_MATCHING_JSON_GLOB:
            if not name.endswith(suffix):
                continue
            if len(name) == len(suffix):
                # A file literally named `.related.json` owns no index and is
                # left to the old behaviour rather than silently dropped.
                continue
            if suffix != ".summary.json" and path.with_name(
                f"{path.stem}.summary.json"
            ).exists():
                return False
            return True
        return False

    def list_repos(self) -> list:
        """List all indexed doc sets.

        jdoc#77: reads the tiny ``<name>.summary.json`` sidecar when present
        instead of json-parsing the whole monolith just to take two ``len()``s
        (a documented first-call hot path, also hit by the PreCompact snapshot
        hook). Falls back to the full parse for legacy indexes written before
        the sidecar existed; a per-index parse failure only drops that one row.

        jdoc#121: candidates are filtered on the SIDECAR SUFFIX, not on whether
        a matching primary exists beside them. Keying on the primary would fix
        the live case and leave the other one alone -- a store that lost an
        index to a pre-1.108.0 ``delete_index`` still carries its four
        sidecars, and those were being parsed in full to return no row at all.
        The reporter measured 1,093 such files, 2.0 GB, opened on every call.
        """
        repos = []
        for index_file in self.base_path.glob("*/*.json"):
            if index_file.name.startswith("_"):
                continue
            if self._is_owned_sidecar(index_file):
                continue
            summary_path = index_file.with_name(f"{index_file.stem}.summary.json")
            if summary_path.exists():
                try:
                    with open(summary_path, "r", encoding="utf-8") as f:
                        s = json.load(f)
                    if isinstance(s, dict) and "repo" in s and "section_count" in s:
                        # jdoc#85 C1-09: a summary written before the
                        # corpus_identity_version key existed cannot say
                        # whether the index is modern or legacy — fall through
                        # to the monolith (the save path rewrites the summary
                        # with the key, so this heals on the next refresh).
                        if "corpus_identity_version" in s:
                            repos.append(self._summary_row(
                                int(s.get("section_count", 0)),
                                int(s.get("doc_count", 0)),
                                s,
                            ))
                            continue
                except Exception:
                    pass  # unreadable summary -> fall through to full parse
            try:
                with open(index_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                repos.append(self._summary_row(
                    len(data["sections"]),
                    len(data["doc_paths"]),
                    data,
                ))
            except Exception:
                continue
        return repos

    def _cancel_pending_retirement(self, owner: str, name: str) -> None:
        """jdoc#88 QA-01 item 3: a refresh of a retiring handle CANCELS the
        pending retirement instead of racing it. The proof-time fingerprints
        are stale by definition once the handle is rewritten, and a voided
        retirement must not linger as pending work. jdoc#89 QA-09: the same
        applies from the RETAINED side — rewriting the retained peer stales
        the record's stored proof, so any record naming this handle as
        retained is voided too. Fail-visible by design: the caller's write
        goes where they aimed it, never silently rerouted; the next reconcile
        re-proves against the new state. Best-effort."""
        try:
            from .retirements import (
                void_retirement_if_stale, void_retirements_referencing,
            )
            current_fingerprint = self.index_fingerprint(owner, name)
            void_retirement_if_stale(
                self.base_path, owner, name, current_fingerprint
            )
            void_retirements_referencing(
                self.base_path,
                f"{owner}/{name}",
                current_fingerprint=current_fingerprint,
            )
        except Exception:
            pass

    def index_fingerprint(self, owner: str, name: str) -> Optional[str]:
        """sha256 of the stored monolith bytes, or None when unreadable.

        The retirement precondition token (jdoc#88 QA-01): captured at proof
        time and re-verified inside :meth:`delete_index`, it detects ANY
        change to the stored index — content, certification, or metadata —
        between approval and physical removal.

        Shares :func:`retirements.fingerprint_index_file` with the record-side
        re-proof: two implementations that drifted by a byte would make every
        publication refuse itself."""
        from .retirements import fingerprint_index_file
        try:
            return fingerprint_index_file(self._index_path(owner, name))
        except ValueError:
            return None

    def _verify_expected_fingerprints(self, expected_fingerprints: dict) -> list:
        """The handles whose current fingerprints no longer match their
        proof-time values. jdoc#89 QA-06: an expected value of None fails
        closed — a missing or unreadable fingerprint at proof time never
        authorizes a removal, so ``None == None`` can never pass the guard."""
        changed = []
        for handle, expected in expected_fingerprints.items():
            h_owner, _, h_name = handle.partition("/")
            if (
                expected is None
                or self.index_fingerprint(h_owner, h_name) != expected
            ):
                changed.append(handle)
        return changed

    def _commit_guarded_retirement(
        self, owner: str, name: str, index_path: Path, *,
        expected_fingerprints: dict,
        expected_publication: str,
        entry_record: Optional[dict],
        outcome: Optional[dict],
    ) -> bool:
        """The final authorization gate: the ONE destructive step.

        jdoc#89 QA-06 / jdoc#90 QA-17 / jdoc#93 QA-19. Called holding this
        handle's write lock, with every auxiliary artifact already removed and
        the primary ``<name>.json`` still in place. Acquires the record lock,
        then the retained peer's gate NONBLOCKINGLY, and holds both through
        the unlink and publication-scoped completion.

        Everything before the retained gate is fast rejection only. A
        fingerprint read before that gate cannot see a writer already in
        flight on the retained handle: one that passed its own void scan
        before our record existed, or a save paused an instruction short of
        ``_atomic_replace``. Both hold the retained handle's write lock, so
        only the proof repeated AFTER the gate closes can authorize the
        commit.

        Returns True when exact-publication record completion also succeeded,
        False when the primary unlink committed but that completion did not.
        The caller must then leave the durable record alone as recoverable
        state. Raises :class:`RetirementConflict` on any refusal, always
        before the unlink, so the retiring monolith stays loadable; only
        rebuildable auxiliary artifacts may already be gone.
        """
        from .retirements import (
            _retirement_record_state, finish_retirement, hold_record_lock,
            retirement_record, RetirementRecordLockError,
            _valid_retirement_record,
        )

        def _publication_conflict() -> RetirementConflict:
            """Name the peer this retirement can no longer speak for."""
            return RetirementConflict(
                [(entry_record or {}).get("retained") or f"{owner}/{name}"]
            )

        def _published_proof(current: Optional[dict]) -> dict:
            """The durable proof this publication actually authorizes.

            The receipt and the proof travel together or the receipt means
            nothing: a caller holding a valid publication_id could otherwise
            hand in an empty or partial map and have the gate verify only the
            handles it chose to mention, so a changed retained peer would go
            unnoticed. The record's own map is the authority, and the caller's
            copy must equal it exactly.
            """
            if (
                current is None
                or current.get("publication_id") != expected_publication
                or not _valid_retirement_record(current, owner, name)
            ):
                raise _publication_conflict()
            published = current.get("fingerprints")
            if (
                not isinstance(published, dict)
                or not published
                or published != expected_fingerprints
            ):
                raise _publication_conflict()
            return published

        try:
            with hold_record_lock(self.base_path, owner, name):
                record = retirement_record(self.base_path, owner, name)
                _published_proof(record)
                retained = record.get("retained")
                with self._gate_retained_handle(
                    retained, owner, name
                ) as gate_ok:
                    if not gate_ok:
                        raise RetirementConflict(
                            [retained or f"{owner}/{name}"]
                        )
                    # Re-read under the gate and verify the DURABLE map, not
                    # the caller's argument, so the authority that authorizes
                    # the unlink is the same object that recorded the proof.
                    published = _published_proof(
                        retirement_record(self.base_path, owner, name)
                    )
                    changed = self._verify_expected_fingerprints(published)
                    if changed:
                        raise RetirementConflict(changed)

                    _evict_index_cache(index_path)
                    index_path.unlink()
                    if outcome is not None:
                        outcome["_primary_unlink_committed"] = True

                    # Past this point the retirement HAS committed. Add no
                    # durable write to a critical section that still holds
                    # both the record lock and the retained gate. A failed
                    # completion is already evidence the store is unhealthy.
                    # Read the durable state, disclose it, and get out.
                    if finish_retirement(
                        self.base_path, owner, name,
                        publication_id=expected_publication,
                        _lock_held=True,
                    ):
                        return True
                    if outcome is not None:
                        state, record = _retirement_record_state(
                            self.base_path, owner, name
                        )
                        outcome.update(
                            retirement_cleanup_pending=state != "absent",
                            retirement_cleanup_record_state=state,
                            retirement_cleanup_owned=(
                                record is not None
                                and record.get("publication_id")
                                == expected_publication
                            ),
                        )
                    return False
        except RetirementRecordLockError:
            # jdoc#95 AC-06: no authoritative lock, no critical section.
            raise RetirementConflict([f"{owner}/{name}"])

    @_with_index_lock
    def delete_index(
        self,
        owner: str,
        name: str,
        expected_fingerprints: Optional[dict] = None,
        outcome: Optional[dict] = None,
        lock_wait: bool = False,
        retirement_publication: Optional[str] = None,
    ) -> bool:
        """Delete an index and its raw content cache.

        Holds the same cross-process write lock as ``save_index`` /
        ``incremental_save`` (jdoc#89 QA-06: every writer and direct delete
        of a handle joins one lifecycle coordinator). Retirement additionally
        takes the retained handle's gate nonblockingly only inside the final
        commit, so it never waits while holding two handle locks.

        ``expected_fingerprints`` (jdoc#88 QA-01) maps ``owner/name`` handles
        to :meth:`index_fingerprint` values captured when the retirement was
        approved. The precondition is verified twice inside the deletion
        boundary: at entry before any removal (a mismatch raises
        :class:`RetirementConflict` with nothing touched), and again
        after the retained gate is acquired and immediately before the primary
        ``<name>.json`` record is removed. The second check aborts with the
        handle still loadable when a concurrent save or direct delete changed
        either participant; auxiliary artifacts may already be gone, and a
        refresh rebuilds them. An expected value of None always conflicts.
        Omitted (``None``) → the pre-existing unguarded behavior.

        Passing any dict, INCLUDING an empty one, selects the guarded path and
        therefore also requires ``retirement_publication``. An empty proof
        asserting nothing can never authorize a removal, so it conflicts
        rather than silently degrading to an unguarded delete. Callers that
        want the unguarded behavior omit the argument.

        jdoc#93 QA-20: ``outcome`` is an optional caller-supplied dict that
        receives ``{"reason_code": ...}`` and, for guarded post-commit record
        cleanup failure, additive durable cleanup state. The bool return cannot
        distinguish "no such index" from "lifecycle contention, try again",
        and the public
        tool rendered both as *Index not found.* — so an agent hitting a busy
        retirement concluded the index never existed and re-indexed, which is
        the duplicate-creation failure this arc exists to prevent. Out-param
        rather than a changed return type so every existing caller is
        untouched.

        jdoc#93 QA-23: ``lock_wait=False`` makes contention return False
        immediately rather than wait. The public tool passes False explicitly
        (fast ``index_lifecycle_busy`` refusal); retirement passes True.

        jdoc#95 QA-25 (RESOLVED, was UNRESOLVED): two tests once called
        ``delete_index(owner, name)`` with no lock_wait while requiring
        OPPOSITE behavior on the SAME lock — the target handle's write lock
        taken by ``_with_index_lock``:

        * ``test_v1_115_0_qa90.py::test_qa17_retained_delete_refused_inside_final_gate``
          needs an immediate False. A retirement is paused mid-unlink holding
          this handle through ``_gate_retained_handle``, and blocking would
          wait on work that cannot finish.
        * ``test_v1_115_0_lifecycle_v2.py::test_three_processes_keep_one_lock_inode``
          (Linux-only, SKIPS on Windows) needs a block. Contention there is an
          ordinary cross-process writer that will release.

        Neither is wrong, and no default satisfies both. We proposed inferring
        intent from surrounding state (refuse when a pending retirement record
        names this handle as retained, else honor lock_wait). The reviewer, who
        authored both tests, rejected that in favour of the simpler rule:
        **every contention-sensitive caller states whether it waits or
        refuses, and the lock never deduces it.** Both tests now pass the flag
        explicitly, so this default arbitrates nothing.

        ⚠ The default stays False, and ``test_v1_115_0_qa25.py`` asserts that
        by signature inspection. It is not a preference: a caller that forgot
        to say gets the refusing behavior, which preserves the QA-17 guarantee
        that both participating indexes are never simultaneously absent — a
        data-loss property. Defaulting to blocking would make forgetting cost
        an index. Do not flip it; add an explicit argument at the call site."""
        def _out(code: str) -> None:
            if outcome is not None:
                outcome["reason_code"] = code

        try:
            index_path = self._index_path(owner, name)
            content_dir = self._content_dir(owner, name)
        except ValueError:
            _out(DELETE_REASON_CODES["not_found"])
            return False

        guarded_retirement = expected_fingerprints is not None
        if guarded_retirement:
            changed = self._verify_expected_fingerprints(expected_fingerprints)
            if changed:
                raise RetirementConflict(changed)
            if (
                not isinstance(retirement_publication, str)
                or not retirement_publication
            ):
                raise RetirementConflict([f"{owner}/{name}"])
        expected_publication = retirement_publication

        # jdoc#90 QA-17: this handle may be the RETAINED peer of a pending
        # retirement. Coordinate through the retirement record's lock BEFORE
        # any destructive step: voiding the record here guarantees the owning
        # retirement's final gate (which re-checks record existence under the
        # same lock) conflicts instead of completing against a peer this
        # delete is about to remove. When a record's lock is held — the
        # owning retirement is inside its destructive step this instant —
        # refuse the delete (retry succeeds as soon as the gate closes) so no
        # interleaving can end with both participating indexes absent.
        from .retirements import (
            retirement_record,
            try_void_retirements_referencing,
        )
        if not try_void_retirements_referencing(
            self.base_path,
            f"{owner}/{name}",
            timeout_seconds=RECORD_LOCK_WAIT_SECONDS if lock_wait else 0.0,
        ):
            # A retirement owning this handle as its retained peer is inside
            # its destructive step right now. Retryable, and NOT missing.
            _out(DELETE_REASON_CODES["lifecycle_busy"])
            return False

        # jdoc#90 QA-17: note whether a durable record backs THIS guarded
        # delete at entry — the final gate then requires it to still exist,
        # so a retained-peer delete that voided it (through the record lock
        # above, in another process) turns into a conflict, never a
        # completed retirement against a vanished peer.
        entry_record = (
            retirement_record(self.base_path, owner, name)
            if guarded_retirement else None
        )
        if guarded_retirement and (
            entry_record is None
            or entry_record.get("publication_id") != expected_publication
            # Fast rejection of a proof this publication does not authorize.
            # The final gate repeats it under the record lock; refusing here
            # keeps auxiliary artifacts intact when the mismatch is already
            # visible.
            or not isinstance(entry_record.get("fingerprints"), dict)
            or not entry_record.get("fingerprints")
            or entry_record.get("fingerprints") != expected_fingerprints
        ):
            raise RetirementConflict([f"{owner}/{name}"])

        # jdoc#88 QA-02: remove the primary index record LAST. Auxiliary
        # artifacts (content cache, sidecars) go first; the `<name>.json`
        # monolith is what `load_index`/`list_repos` key on, so if any earlier
        # step fails (e.g. a content rmtree raises), the index stays fully
        # discoverable and the caller's retry can find the handle again. The
        # previous order unlinked the primary first, so a mid-cleanup failure
        # left an un-loadable, un-retryable half-deleted index.
        deleted = False
        retirement_completion_failed = False
        if content_dir.exists():
            shutil.rmtree(content_dir)
            deleted = True
        # jdoc#77: best-effort removal of the list_repos summary sidecar.
        try:
            summary_path = self._summary_path(owner, name)
            if summary_path.exists():
                summary_path.unlink()
        except (OSError, ValueError):
            pass
        # jdoc#85 C1-07/C1-08: every index-owned auxiliary sidecar goes with
        # the index — embeddings, glossary terms, related graph, boilerplate,
        # duplicates. Leaving any behind lets a retired index shed ghost files
        # into the owner directory (and a future same-name index inherit them).
        for suffix in INDEX_OWNED_SIDECAR_SUFFIXES:
            if suffix == ".summary.json":
                continue  # already removed above, with its own error handling
            sidecar = index_path.with_name(f"{name}{suffix}")
            if sidecar.exists():
                try:
                    sidecar.unlink()
                except OSError:
                    pass
        # jdoc#93 QA-21: the per-repo write-lock file is DELIBERATELY NOT
        # removed. This delete is holding that very lock. On Windows the
        # unlink of an open file fails and is swallowed, so the pathname
        # survives and the hazard is invisible there; on POSIX it succeeds
        # mid-critical-section, and a newcomer then O_CREATs a fresh inode,
        # acquires it uncontended, and runs concurrently with this deleter
        # on the orphaned one. The QA-15 stale-inode recheck in
        # _index_write_lock cannot catch that case — nothing about the new
        # inode is stale, it is simply a different lock.
        #
        # An empty lockfile is the stable coordination object; leaving it is
        # cheaper than the race. This also demotes the QA-15 retry from
        # load-bearing to defensive, since the unlink it compensates for is
        # exactly what no longer happens here.
        # jdoc#81: remove any corpus-creation claim naming this repo so a
        # deleted corpus can be re-created under a fresh name without a
        # phantom conflict.
        try:
            from .corpus_claims import cleanup_claims_for_repo
            cleanup_claims_for_repo(self.base_path, f"{owner}/{name}")
        except Exception:
            pass
        # jdoc#88 QA-02: primary record removed LAST — once this succeeds the
        # index is gone; until then a failed earlier step leaves it loadable.
        if index_path.exists():
            if guarded_retirement:
                retirement_completion_failed = (
                    not self._commit_guarded_retirement(
                        owner, name, index_path,
                        expected_fingerprints=expected_fingerprints,
                        expected_publication=expected_publication,
                        entry_record=entry_record,
                        outcome=outcome,
                    )
                )
                deleted = True
            else:
                _evict_index_cache(index_path)
                index_path.unlink()
                deleted = True
        # jdoc#88 QA-01/QA-02: after the primary commit the result is retired,
        # but failed exact-publication completion can leave a durable cleanup
        # record. Preserve that discoverable state for a fresh pending read;
        # otherwise remove stale retiring records after the primary removal.
        # jdoc#89 QA-10: a direct delete also voids records naming this handle
        # as the retained peer. Both cleanup paths revalidate ownership and
        # remain best-effort, like claims.
        try:
            from .retirements import (
                void_retirement_if_stale, void_retirements_referencing,
            )
            if not retirement_completion_failed:
                void_retirement_if_stale(
                    self.base_path, owner, name, current_fingerprint=None
                )
            void_retirements_referencing(
                self.base_path, f"{owner}/{name}"
            )
        except Exception:
            pass
        _out(
            DELETE_REASON_CODES["deleted"]
            if deleted
            else DELETE_REASON_CODES["not_found"]
        )
        return deleted

    def _index_to_dict(self, index: DocIndex) -> dict:
        d = {
            "repo": index.repo,
            "owner": index.owner,
            "name": index.name,
            "indexed_at": index.indexed_at,
            "doc_paths": index.doc_paths,
            "doc_types": index.doc_types,
            # jdoc#75: embedding vectors are persisted ONLY in the
            # ``<name>.embeddings.jsonl`` sidecar. Strip the ``embedding`` key
            # non-mutatingly -- the in-memory Section dicts still carry vectors
            # for post-save consumers (the related/boilerplate/dedup sidecars
            # built right after save_index during index_local).
            "sections": [
                {k: v for k, v in s.items() if k != "embedding"}
                if isinstance(s, dict) and "embedding" in s else s
                for s in index.sections
            ],
            "index_version": index.index_version,
            "file_hashes": index.file_hashes,
        }
        if index.head_sha:
            d["head_sha"] = index.head_sha
        if index.source_dirty:
            d["source_dirty"] = True
        if index.sha_certified:
            d["sha_certified"] = True
        if index.bm25_stats:
            d["bm25_stats"] = index.bm25_stats
        if getattr(index, "source_root", ""):
            d["source_root"] = index.source_root
        if getattr(index, "source_repo", ""):
            d["source_repo"] = index.source_repo
        if getattr(index, "corpus_selection", ""):
            d["corpus_selection"] = index.corpus_selection
        # jdoc#116. Written only when non-empty, like its neighbours, so an
        # unshaped index gains no key and legacy files are byte-identical.
        # ⚠ This serializer is an explicit ALLOW-LIST, not asdict(): a field
        # added to the dataclass and to every save/load signature still round-
        # trips as empty until it is named HERE. That cost a debugging cycle.
        if getattr(index, "corpus_shape_patterns", None):
            d["corpus_shape_patterns"] = list(index.corpus_shape_patterns)
        if getattr(index, "worktree_lineage_key", ""):
            d["worktree_lineage_key"] = index.worktree_lineage_key
        if getattr(index, "repo_relative_root", ""):
            d["repo_relative_root"] = index.repo_relative_root
        if getattr(index, "corpus_identity_version", 0):
            d["corpus_identity_version"] = index.corpus_identity_version
        if getattr(index, "reconciliation_state", ""):
            d["reconciliation_state"] = index.reconciliation_state
        if getattr(index, "coverage", {}):
            d["coverage"] = index.coverage
        return d

    def _split_repo_at_sha(self, repo: str) -> tuple[str, Optional[str]]:
        if not isinstance(repo, str):
            return str(repo), None
        base, sep, suffix = repo.rpartition("@")
        if sep and normalize_commit_sha(suffix):
            return base, normalize_commit_sha(suffix)
        return repo, None

    def _resolve_repo_base(self, repo: str) -> tuple:
        """Resolve a 'owner/name' or bare 'name' string.

        Returns (owner, name). For bare names without a slash, tries to find
        a matching index file using glob.
        """
        if "/" in repo:
            parts = repo.split("/", 1)
            return parts[0], parts[1]

        # Try to find by name glob — sanitize first to prevent glob injection
        try:
            repo = self._safe_repo_component(repo, "repo")
        except ValueError:
            return "local", repo
        matches = list(self.base_path.glob(f"*/{repo}.json"))
        if len(matches) == 1:
            owner = matches[0].parent.name
            return owner, repo

        # Default to local/name
        return "local", repo

    def _resolve_repo(self, repo: str) -> tuple:
        """Resolve repo identifiers, including strict repo@40hex aliases."""
        base_repo, wanted_sha = self._split_repo_at_sha(repo)
        owner, name = self._resolve_repo_base(base_repo)
        if not wanted_sha:
            return owner, name

        index = self.load_index(owner, name)
        indexed_sha = normalize_commit_sha(index.head_sha if index else None)
        if index and indexed_sha == wanted_sha and not index.source_dirty and index.sha_certified:
            return owner, name

        # Preserve the old tuple-only contract. The invalid name is intentionally
        # uncreatable as an index, so a miss cannot collide with a real repo.
        return "local", "__repo_at_sha_not_found__:sha_mismatch"
