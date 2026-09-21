"""Index GitHub repository tool — fetch, parse, summarize, save."""

import asyncio
import os
import time
from typing import Optional
from urllib.parse import quote, urlparse

import httpx

from ..parser import parse_file, preprocess_content, ALL_EXTENSIONS
from ..security import is_secret_file
from ..storage import DocStore
from ..storage.doc_store import format_repo_at_sha, normalize_commit_sha
from ..summarizer import summarize_sections
from ..embeddings import embed_sections, get_provider_name, should_embed
from ._embedding_coverage import attach_embedding_coverage as _attach_embedding_coverage
from ._constants import SKIP_PATTERNS, is_skipped_dot_dir


from ._changes import (  # jdoc#135
    build_changes_list,
    changes_fields,
    normalize_commit_date,
)


def parse_github_url(url: str) -> tuple:
    """Extract (owner, repo) from GitHub URL or owner/repo string."""
    url = url.removesuffix(".git")
    if "/" in url and "://" not in url:
        parts = url.split("/")
        return parts[0], parts[1]
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    parts = path.split("/")
    if len(parts) >= 2:
        return parts[0], parts[1]
    raise ValueError(f"Could not parse GitHub URL: {url}")


def _normalize_requested_ref(ref: Optional[str]) -> tuple[str, bool, Optional[str]]:
    """Return (ref_to_resolve, was_explicit, error)."""
    if ref is None:
        return "HEAD", False, None
    if not isinstance(ref, str):
        return "", True, f"Invalid ref: {ref!r}"
    original = ref
    ref = ref.strip()
    if not ref:
        return "", True, f"Invalid ref: {original!r}"
    return ref, True, None


def _should_skip(path: str, include_dot_dirs=None) -> bool:
    normalized = "/" + path.replace("\\", "/")
    for pat in SKIP_PATTERNS:
        if ("/" + pat) in normalized:
            return True
    # jdoc#113: same dot rule as the local walk. There is no os.walk to prune
    # here (the GitHub tree arrives flat), so every leading component of the
    # path is checked instead. ⚠ The final component is the FILENAME and is
    # deliberately excluded: `.gitignore` or a dotfile doc is not a directory,
    # and the extension filter already decides those.
    components = [c for c in normalized.split("/")[:-1] if c]
    return any(is_skipped_dot_dir(c, include_dot_dirs) for c in components)


async def fetch_head_commit(
    owner: str,
    repo: str,
    token: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
    ref: str = "HEAD",
) -> tuple:
    """Fetch a commit's SHA and date in one request.

    Returns ``(sha, date)``; either may be None.

    ⚠ jdoc#135: the date was ALREADY in this response and was being discarded.
    `commit.committer.date` costs no extra request and no rate-limit budget.
    ⚠ `committer.date` rather than `author.date`: the question a reader is
    asking is when the content was published, not when it was written.
    """
    ref_path = quote(ref, safe="")
    url = f"https://api.github.com/repos/{owner}/{repo}/commits/{ref_path}"
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    def _read(payload: dict) -> tuple:
        sha = normalize_commit_sha(payload.get("sha"))
        commit = payload.get("commit") or {}
        committer = commit.get("committer") or {}
        return sha, normalize_commit_date(committer.get("date"))

    try:
        if client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return _read(response.json())
        async with httpx.AsyncClient(timeout=15.0) as c:
            response = await c.get(url, headers=headers)
            response.raise_for_status()
            return _read(response.json())
    except Exception:
        return None, None


async def fetch_head_commit_sha(
    owner: str,
    repo: str,
    token: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
    ref: str = "HEAD",
) -> Optional[str]:
    """Fetch a commit SHA cheaply (single lightweight request).

    ⚠ Kept as the SHA-only spelling so existing callers and tests are unchanged;
    `fetch_head_commit` is the one that also returns the date.
    """
    sha, _ = await fetch_head_commit(owner, repo, token, client, ref)
    return sha


async def fetch_repo_tree(
    owner: str,
    repo: str,
    token: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
    ref: str = "HEAD",
) -> list:
    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{ref}"
    params = {"recursive": "1"}
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    if client:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        return response.json().get("tree", [])
    async with httpx.AsyncClient() as c:
        response = await c.get(url, params=params, headers=headers)
        response.raise_for_status()
        return response.json().get("tree", [])


async def fetch_file_content(
    owner: str, repo: str, path: str, token: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
    ref: str = "HEAD",
) -> str:
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    params = {"ref": ref}
    headers = {"Accept": "application/vnd.github.v3.raw"}
    if token:
        headers["Authorization"] = f"token {token}"
    if client:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        return response.text
    async with httpx.AsyncClient() as c:
        response = await c.get(url, params=params, headers=headers)
        response.raise_for_status()
        return response.text


async def fetch_gitignore(
    owner: str, repo: str, token: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
    ref: str = "HEAD",
) -> Optional[str]:
    try:
        return await fetch_file_content(owner, repo, ".gitignore", token, client=client, ref=ref)
    except Exception:
        return None


def discover_doc_files(tree_entries: list, max_files: int = 500, gitignore_spec=None) -> list:
    """Filter tree entries to doc files."""
    import os as _os

    files = []
    for entry in tree_entries:
        if entry.get("type") != "blob":
            continue
        path = entry.get("path", "")
        size = entry.get("size", 0)

        _, ext = _os.path.splitext(path)
        if ext.lower() not in ALL_EXTENSIONS:
            continue

        if _should_skip(path):
            continue

        if is_secret_file(path):
            continue

        if size > 500 * 1024:
            continue

        if gitignore_spec and gitignore_spec.match_file(path):
            continue

        files.append(path)

    return files[:max_files]


def _add_source_identity(
    result: dict,
    source_repo_id: str,
    head_sha: Optional[str],
    source_dirty: bool,
    sha_certified: bool,
) -> dict:
    result["source_repo"] = source_repo_id
    source_repo_at_sha = format_repo_at_sha(
        source_repo_id,
        head_sha,
        source_dirty,
        sha_certified,
    )
    if source_repo_at_sha:
        result["source_repo_at_sha"] = source_repo_at_sha
    return result


async def index_repo(
    url: str,
    use_ai_summaries: bool = True,
    use_embeddings="auto",
    github_token: Optional[str] = None,
    storage_path: Optional[str] = None,
    incremental: bool = True,
    name: Optional[str] = None,
    ref: Optional[str] = None,
) -> dict:
    """Index a GitHub repository's documentation.

    Args:
        url: GitHub repository URL or owner/repo string.
        use_ai_summaries: Whether to use AI for section summaries.
        use_embeddings: True/False/"auto". "auto" (default) enables embeddings when
                        an embedding provider is configured (GOOGLE_API_KEY,
                        OPENAI_API_KEY, openai-compatible
                        + JDOCMUNCH_OPENAI_COMPAT_URL + JDOCMUNCH_OPENAI_COMPAT_MODEL,
                        or sentence-transformers installed).
        github_token: GitHub API token (optional).
        storage_path: Custom storage path.
        incremental: When True and an existing index exists, only re-index changed files.
        name: Optional stored index name override. Defaults to the source repo name.
        ref: Optional GitHub branch, tag, or commit-ish to index. Defaults to HEAD.

    Returns:
        Dict with indexing results.
    """
    t0 = time.perf_counter()
    use_embeddings = should_embed(use_embeddings)

    try:
        owner, source_repo = parse_github_url(url)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    if not github_token:
        github_token = os.environ.get("GITHUB_TOKEN")

    requested_ref, explicit_ref, ref_error = _normalize_requested_ref(ref)
    if ref_error:
        return {"success": False, "error": ref_error}

    warnings = []

    import os as _os
    store = DocStore(base_path=storage_path)
    if name is not None and not isinstance(name, str):
        return {"success": False, "error": f"Invalid name: {name!r}"}
    try:
        index_name = source_repo if name is None else store._safe_repo_component(name, "name")
    except ValueError as e:
        return {"success": False, "error": str(e)}
    repo_id = f"{owner}/{index_name}"
    source_repo_id = f"{owner}/{source_repo}"

    try:
        # --- SHA fast-path: skip all HTTP fetches if HEAD commit hasn't changed ---
        if incremental:
            existing = store.load_index(owner, index_name)
            if existing and existing.head_sha:
                existing_source_repo = existing.source_repo or existing.repo
                current_sha = await fetch_head_commit_sha(owner, source_repo, github_token, ref=requested_ref)
                if (
                    current_sha
                    and current_sha == normalize_commit_sha(existing.head_sha)
                    and existing_source_repo == source_repo_id
                    and existing.sha_certified
                    and not existing.source_dirty
                ):
                    updated = existing
                    if existing.source_repo != source_repo_id:
                        updated = store.incremental_save(
                            owner=owner, name=index_name,
                            changed_files=[], new_files=[], deleted_files=[],
                            new_sections=[], raw_files={}, doc_types={},
                            head_sha=current_sha, source_dirty=False, sha_certified=True,
                            source_repo=source_repo_id,
                        ) or existing
                    latency_ms = int((time.perf_counter() - t0) * 1000)
                    message = (
                        "No changes detected (resolved ref SHA unchanged)"
                        if explicit_ref
                        else "No changes detected (HEAD SHA unchanged)"
                    )
                    result = {
                        "success": True,
                        "message": message,
                        "repo": repo_id,
                        "incremental": True,
                        "head_sha": current_sha,
                        "source_dirty": False,
                        "sha_certified": True,
                        "changed": 0, "new": 0, "deleted": 0,
                        # #132's rule: every success shape carries the keys, so
                        # nobody branches on whether they are present.
                        **changes_fields([]),
                        "_meta": {"latency_ms": latency_ms},
                    }
                    if updated.repo_at_sha:
                        result["repo_at_sha"] = updated.repo_at_sha
                    return _add_source_identity(
                        result,
                        source_repo_id,
                        current_sha,
                        False,
                        True,
                    )

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Resolve the requested ref once, then fetch all content at that SHA.
            head_sha, head_commit_date = await fetch_head_commit(
                owner,
                source_repo,
                github_token,
                client=client,
                ref=requested_ref,
            )
            if explicit_ref and not head_sha:
                return {
                    "success": False,
                    "error": f"GitHub ref could not be resolved: {owner}/{source_repo}@{requested_ref}",
                }
            tree_ref = head_sha or "HEAD"
            sha_certified = bool(head_sha)

            try:
                tree_entries = await fetch_repo_tree(owner, source_repo, github_token, client=client, ref=tree_ref)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    return {"success": False, "error": f"Repository not found: {owner}/{source_repo}"}
                elif e.response.status_code == 403:
                    return {"success": False, "error": "GitHub API rate limit exceeded. Set GITHUB_TOKEN."}
                raise

            gitignore_spec = None
            gitignore_content = await fetch_gitignore(owner, source_repo, github_token, client=client, ref=tree_ref)
            if gitignore_content:
                import pathspec
                try:
                    gitignore_spec = pathspec.PathSpec.from_lines("gitignore", gitignore_content.splitlines())
                except Exception:
                    pass

            source_files = discover_doc_files(tree_entries, gitignore_spec=gitignore_spec)
            if not source_files:
                return {"success": False, "error": "No documentation files found"}

            semaphore = asyncio.Semaphore(10)

            async def fetch_with_limit(path: str) -> tuple:
                async with semaphore:
                    try:
                        content = await fetch_file_content(owner, source_repo, path, github_token, client=client, ref=tree_ref)
                        return path, content
                    except Exception:
                        return path, ""

            tasks = [fetch_with_limit(p) for p in source_files]
            file_contents = await asyncio.gather(*tasks)

        # Build current_files map (preprocessed content keyed by path)
        current_files: dict = {}
        for path, content in file_contents:
            if not content:
                continue
            _, ext = _os.path.splitext(path)
            if ext.lower() not in ALL_EXTENSIONS:
                continue
            try:
                current_files[path] = preprocess_content(content, path)
            except Exception:
                warnings.append(f"Failed to preprocess {path}")

        # --- Incremental path ---
        if incremental and store.load_index(owner, index_name) is not None:
            changed, new, deleted = store.detect_changes(owner, index_name, current_files)

            if not changed and not new and not deleted:
                existing = store.load_index(owner, index_name)
                updated = existing
                existing_source_repo = (existing.source_repo or existing.repo) if existing else ""
                if existing and (
                    normalize_commit_sha(existing.head_sha) != head_sha
                    or existing.source_dirty
                    or bool(existing.sha_certified) != sha_certified
                    or existing_source_repo != source_repo_id
                ):
                    updated = store.incremental_save(
                        owner=owner, name=index_name,
                        changed_files=[], new_files=[], deleted_files=[],
                        new_sections=[], raw_files={}, doc_types={},
                        head_sha=head_sha, source_dirty=False, sha_certified=sha_certified,
                        source_repo=source_repo_id,
                    ) or existing
                latency_ms = int((time.perf_counter() - t0) * 1000)
                result = {
                    "success": True,
                    "message": "No changes detected",
                    "repo": repo_id,
                    "incremental": True,
                    "source_dirty": False,
                    "sha_certified": sha_certified,
                    "changed": 0, "new": 0, "deleted": 0,
                    **changes_fields([]),
                    "_meta": {"latency_ms": latency_ms},
                }
                if head_sha:
                    result["head_sha"] = head_sha
                if updated and updated.repo_at_sha:
                    result["repo_at_sha"] = updated.repo_at_sha
                return _add_source_identity(
                    result,
                    source_repo_id,
                    head_sha,
                    False,
                    sha_certified,
                )

            files_to_parse = set(changed) | set(new)
            new_sections = []
            raw_subset: dict = {}
            doc_types: dict = {}

            for path in files_to_parse:
                content = current_files[path]
                raw_subset[path] = content
                _, ext = _os.path.splitext(path)
                try:
                    sections = parse_file(content, path, repo_id)
                    if sections:
                        new_sections.extend(sections)
                        doc_types[ext.lower()] = doc_types.get(ext.lower(), 0) + 1
                except Exception:
                    warnings.append(f"Failed to parse {path}")

            new_sections = summarize_sections(new_sections, use_ai=use_ai_summaries)
            if use_embeddings:
                # jdoc#107: owner/name were missing here, so the cache was
                # disabled outright — every refresh re-embedded from scratch
                # and the new vectors reached the sidecar only via the save
                # safety net. Merge (no prune): `new_sections` is the changed
                # documents, not the corpus.
                new_sections = embed_sections(
                    new_sections,
                    owner=owner, name=index_name, storage_path=storage_path,
                )

            # jdoc#135: a repository has no per-file mtime — a checkout stamps
            # every file with the fetch moment. The commit is its only edit
            # event, so every file this pass touched takes the head commit date.
            repo_mtimes = (
                {dp: head_commit_date for dp in raw_subset} if head_commit_date else {}
            )
            updated = store.incremental_save(
                owner=owner, name=index_name,
                changed_files=changed, new_files=new, deleted_files=deleted,
                new_sections=new_sections, raw_files=raw_subset, doc_types=doc_types,
                file_mtimes=repo_mtimes,
                head_sha=head_sha, source_dirty=False, sha_certified=sha_certified,
                source_repo=source_repo_id,
            )

            latency_ms = int((time.perf_counter() - t0) * 1000)
            result = {
                "success": True,
                "repo": repo_id,
                "incremental": True,
                "changed": len(changed), "new": len(new), "deleted": len(deleted),
                **changes_fields(build_changes_list(new, changed, deleted, repo_mtimes)),
                "section_count": len(updated.sections) if updated else 0,
                "indexed_at": updated.indexed_at if updated else "",
                "semantic_search": use_embeddings and get_provider_name() is not None,
                "source_dirty": False,
                "sha_certified": sha_certified,
                "_meta": {"latency_ms": latency_ms},
            }
            _attach_embedding_coverage(  # jdoc#107
                result, storage_path=storage_path, owner=owner,
                name=index_name, index=updated, warnings=warnings,
            )
            if warnings:
                result["warnings"] = warnings
            if updated.head_sha:
                result["head_sha"] = updated.head_sha
            if updated.repo_at_sha:
                result["repo_at_sha"] = updated.repo_at_sha
            return _add_source_identity(
                result,
                source_repo_id,
                updated.head_sha,
                False,
                updated.sha_certified,
            )

        # --- Full index path ---
        all_sections = []
        doc_types = {}
        raw_files: dict = {}
        parsed_files = []

        for path, content in current_files.items():
            _, ext = _os.path.splitext(path)
            try:
                sections = parse_file(content, path, repo_id)
                if sections:
                    all_sections.extend(sections)
                    doc_types[ext.lower()] = doc_types.get(ext.lower(), 0) + 1
                    raw_files[path] = content
                    parsed_files.append(path)
            except Exception:
                warnings.append(f"Failed to parse {path}")

        if not all_sections:
            return {"success": False, "error": "No sections extracted"}

        all_sections = summarize_sections(all_sections, use_ai=use_ai_summaries)
        if use_embeddings:
            # Full corpus in hand — authoritative, so prune stale vectors.
            all_sections = embed_sections(
                all_sections,
                owner=owner, name=index_name, storage_path=storage_path,
                prune=True,
            )

        full_repo_mtimes = (
            {dp: head_commit_date for dp in raw_files} if head_commit_date else {}
        )
        saved = store.save_index(
            owner=owner,
            name=index_name,
            sections=all_sections,
            raw_files=raw_files,
            doc_types=doc_types,
            file_mtimes=full_repo_mtimes,
            head_sha=head_sha,
            sha_certified=sha_certified,
            source_repo=source_repo_id,
        )

        latency_ms = int((time.perf_counter() - t0) * 1000)
        result = {
            "success": True,
            "repo": repo_id,
            "indexed_at": saved.indexed_at,
            "file_count": len(parsed_files),
            "section_count": len(all_sections),
            "doc_types": doc_types,
            "files": parsed_files[:20],
            **changes_fields(
                build_changes_list(sorted(raw_files), [], [], full_repo_mtimes)
            ),
            "semantic_search": use_embeddings and get_provider_name() is not None,
            "source_dirty": False,
            "sha_certified": sha_certified,
            "_meta": {"latency_ms": latency_ms},
        }
        _attach_embedding_coverage(  # jdoc#107
            result, storage_path=storage_path, owner=owner,
            name=index_name, index=saved, warnings=warnings,
        )
        if saved.head_sha:
            result["head_sha"] = saved.head_sha
        if saved.repo_at_sha:
            result["repo_at_sha"] = saved.repo_at_sha
        _add_source_identity(
            result,
            source_repo_id,
            saved.head_sha,
            False,
            saved.sha_certified,
        )

        if warnings:
            result["warnings"] = warnings

        return result

    except Exception as e:
        return {"success": False, "error": f"Indexing failed: {str(e)}"}
