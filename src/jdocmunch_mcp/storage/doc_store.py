"""DocIndex + DocStore: CRUD, search scoring, and byte-range content reads."""

import hashlib
import json
import os
import re
import shutil
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..parser.sections import Section
from ..embeddings import embed_query, cosine_similarity

INDEX_VERSION = 3
COMMIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_UNSET = object()

# Module-level LRU cache: {(str(index_path), mtime_ns): DocIndex}
# Keyed by path + mtime so the entry auto-invalidates whenever the file changes.
# Bounded to prevent leaks in long-running MCP servers.
_INDEX_CACHE_MAXSIZE = 8
_INDEX_CACHE: "OrderedDict[tuple, DocIndex]" = OrderedDict()


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
    # Original upstream repository for GitHub indexes when storage name differs.
    # Empty for legacy/local indexes.
    source_repo: str = ""

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
        return any(s.get("embedding") for s in self.sections)

    def search(
        self,
        query: str,
        doc_path: Optional[str] = None,
        max_results: int = 10,
        semantic: Optional[bool] = None,
        semantic_only: bool = False,
        semantic_weight: float = 0.5,
        lexical_engine: str = "bm25",
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
            return self._semantic_search(query, doc_path, max_results) if has_emb else []

        want_semantic = semantic if semantic is not None else has_emb
        if want_semantic and has_emb and 0.0 < semantic_weight <= 1.0:
            results = self._hybrid_search(query, doc_path, max_results, semantic_weight)
            if results:
                return results
        return self._lexical_search(query, doc_path, max_results)

    @staticmethod
    def _strip(sec: dict) -> dict:
        return {k: v for k, v in sec.items() if k not in ("content", "embedding")}

    def _semantic_search(self, query: str, doc_path: Optional[str], max_results: int) -> list:
        """Cosine-similarity search using stored section embeddings."""
        query_vec = embed_query(query)
        if not query_vec:
            return []

        scored = []
        for sec in self.sections:
            if doc_path and sec.get("doc_path") != doc_path:
                continue
            sec_emb = sec.get("embedding")
            if not sec_emb:
                continue
            score = cosine_similarity(query_vec, sec_emb)
            scored.append((score, sec))

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
            return self._lexical_search(query, doc_path, max_results)

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
            if doc_path and sec.get("doc_path") != doc_path:
                continue
            score = self._score_section(sec, query_lower, query_words)
            if score > 0:
                lex_pairs.append((score, sec))
        lex_pairs.sort(key=lambda x: (-x[0], x[1].get("id", "")))
        lex_ranking = [s.get("id", "") for _, s in lex_pairs]

        # ----- Semantic ranking (cosine over stored embeddings) -----
        sem_pairs: list[tuple[float, dict]] = []
        if query_vec:
            for sec in self.sections:
                if doc_path and sec.get("doc_path") != doc_path:
                    continue
                sec_emb = sec.get("embedding")
                if not sec_emb:
                    continue
                sem_pairs.append((cosine_similarity(query_vec, sec_emb), sec))
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

    def _lexical_search(self, query: str, doc_path: Optional[str], max_results: int) -> list:
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
            if doc_path and sec.get("doc_path") != doc_path:
                continue
            score = self._score_section(sec, query_lower, query_words)
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

    def _score_section(self, sec: dict, query_lower: str, query_words: set) -> float:
        """BM25-Okapi scoring with tag-match kicker.

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
            query_lower,
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
            self.base_path = Path.home() / ".doc-index"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _safe_repo_component(self, value: str, field_name: str) -> str:
        import re
        if not value or value in {".", ".."}:
            raise ValueError(f"Invalid {field_name}: {value!r}")
        if "/" in value or "\\" in value:
            raise ValueError(f"Invalid {field_name}: {value!r}")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
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

    def _safe_content_path(self, content_dir: Path, relative_path: str) -> Optional[Path]:
        try:
            base = content_dir.resolve()
            candidate = (content_dir / relative_path).resolve()
            if os.path.commonpath([str(base), str(candidate)]) != str(base):
                return None
            return candidate
        except (OSError, ValueError):
            return None

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
        )

        index_path = self._index_path(owner, name)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = index_path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self._index_to_dict(index), f, indent=2)
        tmp_path.replace(index_path)
        _evict_index_cache(index_path)

        # Cache raw files for byte-range reads
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
            return cached

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
        _index_cache_put(cache_key, index)
        return index

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
        )

        # Save atomically
        index_path = self._index_path(owner, name)
        tmp_path = index_path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self._index_to_dict(updated), f, indent=2)
        tmp_path.replace(index_path)
        _evict_index_cache(index_path)

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

    def list_repos(self) -> list:
        """List all indexed doc sets."""
        repos = []
        for index_file in self.base_path.glob("*/*.json"):
            if index_file.name.startswith("_"):
                continue
            try:
                with open(index_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                row = {
                    "repo": data["repo"],
                    "indexed_at": data["indexed_at"],
                    "section_count": len(data["sections"]),
                    "doc_count": len(data["doc_paths"]),
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
                repos.append(row)
            except Exception:
                continue
        return repos

    def delete_index(self, owner: str, name: str) -> bool:
        """Delete an index and its raw content cache."""
        try:
            index_path = self._index_path(owner, name)
            content_dir = self._content_dir(owner, name)
        except ValueError:
            return False

        deleted = False
        if index_path.exists():
            _evict_index_cache(index_path)
            index_path.unlink()
            deleted = True
        if content_dir.exists():
            shutil.rmtree(content_dir)
            deleted = True
        return deleted

    def _index_to_dict(self, index: DocIndex) -> dict:
        d = {
            "repo": index.repo,
            "owner": index.owner,
            "name": index.name,
            "indexed_at": index.indexed_at,
            "doc_paths": index.doc_paths,
            "doc_types": index.doc_types,
            "sections": index.sections,
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
