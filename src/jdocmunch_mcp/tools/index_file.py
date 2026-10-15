"""Re-index a single file within an existing index."""

import json
import time
from pathlib import Path
from typing import Optional

from ..parser import parse_file, preprocess_content, ALL_EXTENSIONS
from ..parser.sections import duplicate_anchor_fields
from ..parser.office import (
    OFFICE_EXTENSIONS,
    office_available,
    convert_office,
    office_cache_dir,
)
from ..storage import DocStore
from ..storage.doc_store import normalize_commit_sha
from ..summarizer import summarize_sections
from ..embeddings import embed_sections
from ._embedding_coverage import attach_embedding_coverage as _attach_embedding_coverage
from ._git import local_git_head, local_git_paths_dirty, local_git_paths_tracked


def _owner_by_source_root(
    file_path: Path,
    store: DocStore,
) -> Optional[tuple[str, str, str, Path]]:
    """The local index whose stored ``source_root`` contains the file (#172).

    Reads the ``<name>.summary.json`` sidecars (jdoc#77), which carry
    ``source_root``, and takes the LONGEST root that contains the file, so a
    nested index wins over one rooted above it. The same rule the PreToolUse
    probe uses (``cli/_hook_probe._index_state``), so the Read hint and the
    refresh it recommends agree on which index owns a file.
    """
    best = None
    for sidecar in sorted((store.base_path / "local").glob("*.summary.json")):
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
            root = (meta.get("source_root") or "").strip() if isinstance(meta, dict) else ""
            if not root:
                continue
            source_root = Path(root).expanduser().resolve()
            rel_path = file_path.relative_to(source_root).as_posix()
        except (OSError, ValueError):
            continue
        if best is None or len(str(source_root)) > len(str(best[3])):
            best = ("local", sidecar.name[: -len(".summary.json")], rel_path, source_root)
    return best


def _find_owning_index(
    file_path: Path,
    store: DocStore,
) -> Optional[tuple[str, str, str, Path]]:
    """Find which index owns a given file path.

    #172: the owner is the index whose stored ``source_root`` contains the
    file. Until 1.146.0 this matched ancestor FOLDER NAMES against index
    names, so an index built with ``--name`` was never found, and an ancestor
    folder that happened to share an index's name re-rooted the file there
    and added its sections a second time under the wrong path.

    The folder-name walk remains only for an index with no summary sidecar or
    no stored ``source_root`` (saved before either existed), and it now skips
    any index whose stored root is a different directory.

    Returns (owner, name, rel_path, source_root) or None.
    """
    file_path = file_path.resolve()
    owned = _owner_by_source_root(file_path, store)
    if owned is not None:
        return owned
    parts = file_path.parts

    # Try each ancestor directory as a potential source root
    import re
    for i in range(len(parts) - 1, 0, -1):
        candidate_root = Path(*parts[:i])
        folder_name = parts[i - 1]

        # Skip invalid folder names (drive roots, dots, etc.)
        if not folder_name or not re.fullmatch(r"[A-Za-z0-9._-]+", folder_name):
            continue

        # Check if a local index with this name exists
        try:
            index = store.load_index("local", folder_name)
        except ValueError:
            continue
        if index is None:
            continue

        # #172: a stored root that is somewhere else means this index only
        # shares the folder's name. Its true root was already searched above.
        stored_root = getattr(index, "source_root", "") or ""
        if stored_root:
            try:
                if Path(stored_root).expanduser().resolve() != candidate_root:
                    continue
            except (OSError, ValueError):
                continue

        # Check if the file's relative path is in this index
        try:
            rel_path = file_path.relative_to(candidate_root).as_posix()
        except ValueError:
            continue

        if rel_path in index.doc_paths or rel_path in index.file_hashes:
            return ("local", folder_name, rel_path, candidate_root)

        # The file might be new (not yet in doc_paths) but under this root
        # Accept if the root contains other indexed files
        if index.doc_paths:
            return ("local", folder_name, rel_path, candidate_root)

    return None


def _resolve_named_index(
    file_path: Path,
    store: DocStore,
    name: str,
) -> Optional[tuple[str, str, str, Path]]:
    """Resolve an explicitly named index and the file's path within it (#38).

    Skips folder-name inference entirely: loads the index by its stored name
    and computes the file's relative path from the index's ``source_root``.
    This is the only path that works for a custom ``--name`` or a corpus whose
    root folder name is not path-safe (e.g. contains spaces).

    ``name`` may be ``owner/name`` or a bare name (owner defaults to ``local``).
    Returns (owner, name, rel_path, source_root) or None.
    """
    owner, _, bare = name.rpartition("/")
    owner = owner or "local"
    try:
        index = store.load_index(owner, bare)
    except ValueError:
        return None
    if index is None:
        return None
    root = getattr(index, "source_root", "")
    if not root:
        return None
    source_root = Path(root).expanduser().resolve()
    try:
        rel_path = file_path.relative_to(source_root).as_posix()
    except ValueError:
        return None
    return (owner, bare, rel_path, source_root)


def _stat_mtime(file_path, rel_path: str) -> dict:
    """jdoc#136: {rel_path: naive-local ISO string}, or {} when it cannot be read.

    ⚠ An empty dict leaves whatever was stored alone — `incremental_save` only
    writes the keys it is given. Returning a fabricated "now" would record the
    indexing moment as the edit moment.
    """
    from datetime import datetime
    try:
        return {rel_path: datetime.fromtimestamp(Path(file_path).stat().st_mtime).isoformat()}
    except OSError:
        return {}


def index_file(
    file_path: str,
    storage_path: Optional[str] = None,
    use_ai_summaries: bool = True,
    name: Optional[str] = None,
    apply_corpus_rules: bool = False,
) -> dict:
    """Re-index a single file within an existing index.

    Finds which index owns the file (or uses ``name`` when given), re-parses
    it, and updates the index in place using incremental_save.

    ``apply_corpus_rules`` (jdoc#165): skip a file the owning index's corpus
    excludes, by its root's ``.gitignore`` and its stored
    ``corpus_shape_patterns``. The PostToolUse hook passes True: it names a
    file because an agent edited it, not because anyone asked for it to be
    indexed, which is the watcher's position in jdoc#115. A caller naming the
    file itself (``index-file <path>``) keeps the explicit-path bypass that
    ``index_local(paths=...)`` documents.

    Returns dict with results.
    """
    t0 = time.perf_counter()
    path = Path(file_path).expanduser().resolve()

    if not path.exists():
        return {"success": False, "error": f"File not found: {file_path}", "exit_code": 2}
    if not path.is_file():
        return {"success": False, "error": f"Not a file: {file_path}", "exit_code": 2}

    ext = path.suffix.lower()
    if ext in OFFICE_EXTENSIONS:
        if not office_available():
            return {"success": False, "error": (
                f"Office document support requires the optional markitdown "
                f"extra (pip install jdocmunch-mcp[office]): {file_path}"
            ), "exit_code": 2}
    elif ext not in ALL_EXTENSIONS:
        return {"success": False, "error": f"Not a doc file ({ext}): {file_path}", "exit_code": 2}

    store = DocStore(base_path=storage_path)
    if name:
        match = _resolve_named_index(path, store, name)
        if match is None:
            return {"success": False, "error": (
                f"No index named {name!r} with a source_root containing "
                f"{file_path}"
            ), "exit_code": 1}
    else:
        match = _find_owning_index(path, store)
        if match is None:
            return {"success": False, "error": f"File not in any index: {file_path}", "exit_code": 1}

    owner, name, rel_path, detected_root = match
    repo_id = f"{owner}/{name}"

    if apply_corpus_rules and owner == "local":
        from ..watch import _corpus_filters, _excluded_from_corpus

        gitignore_spec, extra_spec = _corpus_filters(
            str(detected_root), name, str(store.base_path)
        )
        if _excluded_from_corpus(str(path), str(detected_root), gitignore_spec, extra_spec):
            return {
                "success": True,
                "skipped": True,
                "reason": "excluded_by_corpus_rules",
                "repo": repo_id,
                "file": rel_path,
                "exit_code": 0,
            }

    # Read and parse the file
    try:
        if ext in OFFICE_EXTENSIONS:
            # Binary office document: convert to Markdown locally
            # (parser/office.py), matching index_local's read leg.
            content = convert_office(path, cache_dir=office_cache_dir(store.base_path))
        else:
            # newline="" preserves CRLF/CR so offsets/hashes address the real
            # on-disk bytes (#52); Path.read_text lacks newline before 3.13.
            with open(path, encoding="utf-8", errors="replace", newline="") as fh:
                content = fh.read()
        content = preprocess_content(content, rel_path)
    except Exception as e:
        return {"success": False, "error": f"Failed to read: {e}", "exit_code": 2}

    try:
        new_sections = parse_file(content, rel_path, repo_id)
    except Exception as e:
        return {"success": False, "error": f"Failed to parse: {e}", "exit_code": 2}

    if not new_sections:
        new_sections = []

    new_sections = summarize_sections(new_sections, use_ai=use_ai_summaries)

    # Determine if this is a new or changed file
    index = store.load_index(owner, name)
    is_new = rel_path not in (index.file_hashes if index else {})
    source_root = detected_root
    if index is not None and getattr(index, "source_root", ""):
        candidate = Path(index.source_root).expanduser()
        if candidate.exists():
            source_root = candidate.resolve()
    previous_sha = normalize_commit_sha(index.head_sha if index else None)
    previous_certified = bool(index is not None and index.sha_certified)
    indexed_paths = set(index.doc_paths if index else [])
    indexed_paths.add(rel_path)
    paths_tracked = local_git_paths_tracked(source_root, indexed_paths)
    paths_dirty = local_git_paths_dirty(source_root, indexed_paths)
    current_sha = local_git_head(source_root)
    head_moved = bool(previous_sha and current_sha and previous_sha != current_sha)
    legacy_uncertified = bool(current_sha and not previous_sha)
    lost_git_context = bool(previous_sha and current_sha is None)
    head_sha = current_sha or previous_sha
    source_dirty = bool(
        paths_dirty
        or head_moved
        or legacy_uncertified
        or lost_git_context
        or (bool(head_sha) and not paths_tracked)
        or (index is not None and index.source_dirty)
    )
    sha_certified = bool(
        previous_certified
        and current_sha
        and previous_sha == current_sha
        and not source_dirty
        and paths_tracked
    )

    # Preserve embedding parity: if the existing index has embeddings, embed new sections too.
    if index is not None and index._has_embeddings():
        # jdoc#107: owner/name were missing, so the cache was disabled and the
        # vectors were persisted only by the save safety net — which used to
        # bail whenever a sidecar already existed, i.e. always. Every vector
        # this path produced was silently discarded, and this is the
        # PostToolUse auto-reindex path, so it ran on every doc edit. Merge,
        # never prune: one file is never authoritative for the corpus.
        new_sections = embed_sections(
            new_sections,
            owner=owner, name=name, storage_path=storage_path,
        )

    # Use incremental_save to update just this file
    updated = store.incremental_save(
        owner=owner,
        name=name,
        changed_files=[] if is_new else [rel_path],
        new_files=[rel_path] if is_new else [],
        deleted_files=[],
        new_sections=new_sections,
        raw_files={rel_path: content},
        doc_types={ext: 1},
        # jdoc#136: this tool is what the PostToolUse edit hook fires, so it is
        # the path that keeps the most recently edited document current. A stat
        # that fails leaves the stored time alone rather than clearing it.
        file_mtimes=_stat_mtime(file_path, rel_path),
        head_sha=head_sha,
        source_dirty=source_dirty,
        sha_certified=sha_certified,
        source_root=str(source_root),
    )

    latency_ms = int((time.perf_counter() - t0) * 1000)
    result = {
        "success": True,
        "repo": repo_id,
        "file": rel_path,
        "is_new": is_new,
        "sections": len(new_sections),
        "total_sections": len(updated.sections) if updated else 0,
        # Scoped to the file just written, so an unrelated duplicate elsewhere
        # in the corpus is not repeated on every re-index. The `index-file` CLI
        # prints this dict; the PostToolUse hook's worker drops it.
        **duplicate_anchor_fields(new_sections),
        "exit_code": 0,
        "_meta": {"latency_ms": latency_ms},
    }
    # jdoc#107: this path fires from the PostToolUse hook on every doc edit,
    # so it is where a coverage collapse would be noticed first.
    _attach_embedding_coverage(
        result, storage_path=storage_path, owner=owner, name=name, index=updated,
    )
    if updated and updated.head_sha:
        result["head_sha"] = updated.head_sha
    if updated:
        result["source_dirty"] = bool(updated.source_dirty)
        result["sha_certified"] = bool(updated.sha_certified)
    if updated and updated.repo_at_sha:
        result["repo_at_sha"] = updated.repo_at_sha
    return result


def index_file_cli(
    file_path: str, name: Optional[str] = None, apply_corpus_rules: bool = False
) -> dict:
    """CLI entry point for index-file subcommand."""
    return index_file(file_path, name=name, apply_corpus_rules=apply_corpus_rules)
