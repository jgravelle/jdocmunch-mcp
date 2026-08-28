"""Index local folder tool — walk, parse, summarize, save."""

import hashlib
import os
import re
import time
from pathlib import Path
from typing import Optional

import pathspec

from ..parser import parse_file, preprocess_content, ALL_EXTENSIONS
from ..parser.office import (
    OFFICE_EXTENSIONS,
    OFFICE_MAX_FILE_SIZE,
    office_available,
    convert_office,
    office_cache_dir,
)
from ..retrieval.roles import annotate_sections as _annotate_roles
from ..retrieval.glossary import extract_glossary, write_terms
from ..security import (
    validate_path,
    is_symlink_escape,
    is_secret_file,
    DEFAULT_MAX_FILE_SIZE,
)
from ..storage import DocStore
from ..storage.doc_store import INDEX_OWNED_SIDECAR_SUFFIXES, normalize_commit_sha
from ..summarizer import summarize_sections
from ..embeddings import embed_sections, get_provider_name, should_embed
from ._embedding_coverage import attach_embedding_coverage as _attach_embedding_coverage
from ._git import local_git_head, local_git_paths_dirty, local_git_paths_tracked, stable_local_git_state
from ._constants import SKIP_PATTERNS, is_skipped_dot_dir


def _default_local_name(folder_name: str, folder_path: Optional[str] = None) -> str:
    """Derive a storage-safe local index name from a folder basename (jdoc#72).

    Zero-config rule: if the basename is already a valid storage component,
    preserve it exactly (backward compatible). Otherwise slugify it to the
    allowed ``[A-Za-z0-9._-]`` charset and append a short hash of the folder's
    absolute path, so a label like ``"My Docs"`` becomes ``"my-docs-<hash>"``
    instead of failing storage validation downstream, and two
    differently-located folders that slugify to the same base don't silently
    collide.
    """
    if folder_name not in {".", ".."} and re.fullmatch(r"[A-Za-z0-9._-]+", folder_name):
        return folder_name
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", folder_name)
    slug = re.sub(r"-{2,}", "-", slug).strip("-.").lower()
    if not slug:
        slug = "local-docs"
    seed = folder_path if folder_path is not None else folder_name
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def normalize_local_index_name(
    name: Optional[str], folder_name: str, folder_path: Optional[str] = None
) -> str:
    """Resolve the caller-supplied ``name`` to a bare local storage component.

    Local doc indexes are stored under owner ``local`` and surfaced by
    ``doc_list_repos`` as the durable handle ``local/<name>``. An agent that
    discovers a repo and reuses that handle as the refresh ``name`` previously
    hit ``Invalid name: 'local/<name>'`` because ``name`` is validated as a
    single storage component (jdoc#67). Accept and normalize the ``local/``
    round-trip while still rejecting other owner prefixes, nested slashes, and
    empty local names (the downstream ``_safe_repo_component`` check catches
    ``.``/``..``/illegal chars on the returned value).

    When ``name`` is omitted the default is derived from the folder basename
    via :func:`_default_local_name`, so a basename that isn't a valid storage
    component (e.g. one containing spaces) yields a deterministic safe handle
    instead of failing storage validation downstream (jdoc#72).
    """
    if not name:
        return _default_local_name(folder_name, folder_path)
    if name.startswith("local/"):
        _owner, _, local_name = name.partition("/")
        # Empty or further-nested local names are not a valid round trip
        # (e.g. "local/" or "local/a/b"); reject here for a clean error.
        if not local_name or "/" in local_name or "\\" in local_name:
            raise ValueError(f"Invalid name: {name!r}")
        return local_name
    if "/" in name or "\\" in name:
        raise ValueError(f"Invalid name: {name!r}")
    return name


def _load_gitignore(folder_path: Path) -> Optional[pathspec.PathSpec]:
    gitignore_path = folder_path / ".gitignore"
    if gitignore_path.is_file():
        try:
            content = gitignore_path.read_text(encoding="utf-8", errors="replace")
            return pathspec.PathSpec.from_lines("gitignore", content.splitlines())
        except Exception:
            pass
    return None


def _add_commit_fields(result: dict, index) -> None:
    if not index:
        return
    if index.head_sha:
        result["head_sha"] = index.head_sha
    result["source_dirty"] = bool(index.source_dirty)
    result["sha_certified"] = bool(index.sha_certified)
    if index.repo_at_sha:
        result["repo_at_sha"] = index.repo_at_sha


def _attach_reconciliation_outcome(result, graduating, disclosure):
    """Attach the Part C graduation outcome to a refresh response: a `graduated`
    block when the provisional index was promoted this refresh, otherwise any
    ambiguous/diverged disclosure that kept it provisional (fail closed)."""
    from ._worktree_corpus import REASON_GRADUATED
    if graduating:
        result["reconciliation"] = {
            "state": "graduated",
            "reason_code": REASON_GRADUATED,
            "detail": (
                "Git lineage was verified on this refresh; the previously "
                "provisional index was graduated to an established index."
            ),
        }
    elif disclosure is not None:
        result["reconciliation"] = disclosure
    return result


def _leftover_artifacts(store, owner: str, name: str) -> list:
    """Names of index-owned files still present after a retirement.

    Visibility for jdoc#86 — a partially failed cleanup must never be silent.
    Best-effort: an unreadable path is skipped, never raised."""
    leftovers = []
    try:
        index_path = store._index_path(owner, name)
    except Exception:
        return leftovers
    candidates = [
        index_path,
        index_path.with_name(f"{index_path.stem}.summary.json"),
    ]
    for suffix in INDEX_OWNED_SIDECAR_SUFFIXES:
        if suffix == ".summary.json":
            continue  # already listed above, off index_path.stem
        candidates.append(index_path.with_name(f"{name}{suffix}"))
    try:
        candidates.append(store._content_dir(owner, name))
    except Exception:
        pass
    for p in candidates:
        try:
            if p.exists():
                leftovers.append(p.name)
        except OSError:
            pass
    return leftovers


def _execute_retirement(store, owner, repo_name, repo_id, target, family,
                        reverify):
    """jdoc#89 QA-06/QA-07 — the coordinated destructive step shared by all
    three retirement paths (legacy apply, supersession, exact-dedup).

    The ordering is the contract:

      1. Capture proof-time fingerprints for BOTH handles. A missing or
         unreadable fingerprint fails closed — ``None`` never authorizes.
      2. RELOAD both indexes and re-prove the decisive predicates
         (``reverify(sel, peer)``) on the reloaded state. Because the token
         was captured BEFORE this reload, any change the re-proof ran on is
         the state the token describes, and any change AFTER the reload is
         caught by the guard in step 4 — the proved state and the accepted
         token can no longer diverge (the QA-06 proof/capture gap).
      3. Publish the durable retiring record and REQUIRE the receipt:
         cleanup never starts without authoritative recovery state on disk
         (QA-07).
      4. Guarded delete: ``delete_index`` re-verifies the exact publication
         and fingerprints after nonblocking retained-handle coordination,
         then holds those authorities through primary removal and conditional
         completion.

    Returns ``(outcome, info)``. Outcomes: ``"retired"``; ``"conflict"``
    (an index changed or vanished — ``info`` may carry ``changed_handles``);
    ``"unproven"`` (the reloaded state no longer satisfies the proof);
    ``"record_unavailable"`` (record publication failed; nothing destructive
    was attempted); ``"cleanup_incomplete"`` (the primary commit did not
    occur, so the retiring handle remains loadable). After the primary unlink,
    the outcome is always ``"retired"``; ``info`` additively discloses durable
    record cleanup state when publication completion needs recovery."""
    from ..storage.doc_store import RetirementConflict
    from ..storage.retirements import (
        begin_retirement,
        finish_retirement,
        pending_retirement,
    )

    t_owner, _, t_name = target.partition("/")
    fingerprints = {
        repo_id: store.index_fingerprint(owner, repo_name),
        target: store.index_fingerprint(t_owner, t_name),
    }
    missing = [h for h, fp in fingerprints.items() if not fp]
    if missing:
        return "conflict", {"changed_handles": missing}
    sel = store.load_index(owner, repo_name)
    peer = store.load_index(t_owner, t_name)
    if sel is None or peer is None:
        return "conflict", {"changed_handles": [
            h for h, idx in ((repo_id, sel), (target, peer)) if idx is None
        ]}
    if not reverify(sel, peer):
        return "unproven", {}
    publication_id = begin_retirement(
        store.base_path, owner, repo_name,
        retained=target, fingerprints=fingerprints, family=family,
    )
    if not publication_id:
        return "record_unavailable", {}
    delete_outcome = {}
    try:
        # jdoc#93 QA-23: this is the INTERNAL coordinated operation, already
        # mid-protocol with a published record on disk. A bounded wait for the
        # retiring handle's own lock is correct here — bailing out would leave
        # a pending record behind for a lock that clears in milliseconds. The
        # public delete path is the one that must never silently wait.
        removed = store.delete_index(
            owner, repo_name, expected_fingerprints=fingerprints,
            outcome=delete_outcome,
            lock_wait=True,
            retirement_publication=publication_id,
        )
    except RetirementConflict as conflict:
        cleanup_finished = finish_retirement(
            store.base_path,
            owner,
            repo_name,
            publication_id=publication_id,
        )
        info = {"changed_handles": conflict.changed}
        if not cleanup_finished:
            pending = pending_retirement(
                store.base_path, owner, repo_name
            )
            if pending is not None:
                info["pending_retirement"] = True
        return "conflict", info
    except Exception:
        removed = bool(delete_outcome.get("_primary_unlink_committed"))
    cleanup_info = {
        key: value
        for key, value in delete_outcome.items()
        if not key.startswith("_") and key != "reason_code"
    }
    if not removed:
        info = {}
        if pending_retirement(store.base_path, owner, repo_name) is not None:
            info["pending_retirement"] = True
        return "cleanup_incomplete", info
    return "retired", cleanup_info


def _resolve_legacy_reconcile(
    store, owner, repo_name, repo_id, folder_path,
    wt_evidence, call_selection, mode, t0,
):
    """jdoc#87 Part C.2 — prove (and under ``apply``, retire) a genuine
    pre-1.102 fieldless legacy index against its modern peer. Runs AFTER the
    ordinary full refresh has backfilled identity/certification on the
    selected handle. Returns ``(block, replacement)``:

      ``block``       -> attach as ``result["legacy_reconciliation"]``.
      ``replacement`` -> a complete response to return instead (the apply
                         retirement path, which returns the peer's handle).

    Safety contract (maintainer decisions on #87): the explicitly selected
    legacy handle is the ONLY possible loser; retirement requires exactly one
    non-provisional modern peer with matching lineage + relative root +
    durable selection, the same clean certified SHA on both sides, and every
    selected-handle path present in the peer with the same stored hash
    (missing = unproven, fail closed). ``report`` changes neither handle;
    ``apply`` repeats proof immediately before the only destructive step and
    never touches the peer. Basename disclosure is not authority (LC2-02)."""
    from ._worktree_corpus import (
        classify_graduation,
        filter_lineage_candidates,
        REASON_LEGACY_AMBIGUOUS,
        REASON_LEGACY_CLEANUP_INCOMPLETE,
        REASON_LEGACY_CONFLICT,
        REASON_LEGACY_CONTENT_DIFFERS,
        REASON_LEGACY_NO_MODERN_PEER,
        REASON_LEGACY_READY,
        REASON_LEGACY_RECONCILED,
        REASON_LEGACY_UNCERTIFIED,
    )

    def _kept(reason_code, detail, **extra):
        block = {"state": "kept", "reason_code": reason_code, "detail": detail}
        block.update(extra)
        return block, None

    sel_index = store.load_index(owner, repo_name)
    if sel_index is None:
        return _kept(
            REASON_LEGACY_CONFLICT,
            "The selected handle vanished between refresh and proof. "
            "Nothing was removed; re-run to retry.",
        )

    peers = filter_lineage_candidates(
        store.list_repos(), wt_evidence, allow_containment=False
    )
    peers = [c for c in peers if c.get("repo") != repo_id]
    action, target = classify_graduation(peers, call_selection)
    if action == "graduate":
        return _kept(
            REASON_LEGACY_NO_MODERN_PEER,
            "No non-provisional modern peer matches this corpus identity "
            "(lineage + relative root + durable selection). The legacy index "
            "was refreshed and kept; nothing was removed. Re-run WITHOUT "
            "legacy_reconcile to backfill it into the identity system — it "
            "is the only index for this corpus.",
        )
    if action == "ambiguous":
        return _kept(
            REASON_LEGACY_AMBIGUOUS,
            "More than one non-provisional modern peer matches this corpus "
            "identity. Retirement requires exactly one; nothing was removed. "
            "Resolve the peer ambiguity first (delete_index the duplicates "
            "or reconcile them), then re-run.",
            peer_count=len(peers),
        )

    t_owner, _, t_name = target.partition("/")
    peer_index = store.load_index(t_owner, t_name)
    if peer_index is None:
        return _kept(
            REASON_LEGACY_CONFLICT,
            "The modern peer vanished between classification and proof. "
            "Nothing was removed; re-run to retry.",
            established_handle=target,
        )

    s_sha = getattr(sel_index, "head_sha", None) or ""
    p_sha = getattr(peer_index, "head_sha", None) or ""
    certified = (
        bool(getattr(sel_index, "sha_certified", False))
        and not getattr(sel_index, "source_dirty", False)
        and bool(getattr(peer_index, "sha_certified", False))
        and not getattr(peer_index, "source_dirty", False)
        and s_sha and s_sha == p_sha
    )
    if not certified:
        return _kept(
            REASON_LEGACY_UNCERTIFIED,
            "Retirement requires BOTH indexes to represent the same clean "
            "certified commit. One side is dirty, uncertified, or at a "
            "different commit; nothing was removed. Commit/refresh both "
            "sides to the same clean state and re-run.",
            established_handle=target,
            selected_sha=s_sha or None,
            established_sha=p_sha or None,
        )

    s_docs = set(getattr(sel_index, "doc_paths", []) or [])
    s_hashes = getattr(sel_index, "file_hashes", {}) or {}
    p_hashes = getattr(peer_index, "file_hashes", {}) or {}
    differing = sorted(
        fp for fp in s_docs
        if not s_hashes.get(fp) or s_hashes.get(fp) != p_hashes.get(fp)
    )
    if differing:
        return _kept(
            REASON_LEGACY_CONTENT_DIFFERS,
            "Every selected-handle path must exist in the modern peer with "
            "the same stored hash; the listed files differ (or equality "
            "could not be proven from stored hashes). Not a duplicate — "
            "both indexes were kept, nothing removed.",
            established_handle=target,
            differing_files=differing[:20],
            differing_file_count=len(differing),
        )

    if mode == "report":
        return {
            "state": "report",
            "reason_code": REASON_LEGACY_READY,
            "established_handle": target,
            "would_remove_handle": repo_id,
            "covered_file_count": len(s_docs),
            "certified_sha": s_sha,
            "detail": (
                "Proof passed: exactly one modern peer, same clean certified "
                "commit, full path-and-hash coverage. Nothing was changed. "
                "Re-run with legacy_reconcile='apply' to retire this legacy "
                "handle and keep the peer."
            ),
        }, None

    # apply: repeat proof immediately before the only destructive step
    # (jdoc#86 final-recheck primitive — target identity, candidate set, and
    # peer generation must all be unchanged).
    peers2 = filter_lineage_candidates(
        store.list_repos(), wt_evidence, allow_containment=False
    )
    peers2 = [c for c in peers2 if c.get("repo") != repo_id]
    action2, target2 = classify_graduation(peers2, call_selection)
    fresh_peer = (
        store.load_index(t_owner, t_name)
        if action2 == "reconcile" and target2 == target
        else None
    )
    if (
        fresh_peer is None
        or (getattr(fresh_peer, "head_sha", None) or "") != p_sha
    ):
        return _kept(
            REASON_LEGACY_CONFLICT,
            "The modern peer or the candidate set changed between proof and "
            "retirement. Nothing was removed; re-run to retry against the "
            "current state.",
            established_handle=target,
        )
    # jdoc#88 QA-01 / jdoc#89 QA-06/QA-07: the recheck above and the physical
    # removal are not one moment. _execute_retirement captures fingerprints
    # for BOTH handles FIRST, re-proves certification + hash coverage on a
    # reload the token covers, requires the durable-record receipt, then runs
    # the guarded delete (verified again inside the deletion boundary and at
    # the pre-removal recheck).
    def _reverify(sel2, peer2):
        s2 = getattr(sel2, "head_sha", None) or ""
        p2 = getattr(peer2, "head_sha", None) or ""
        if not (
            bool(getattr(sel2, "sha_certified", False))
            and not getattr(sel2, "source_dirty", False)
            and bool(getattr(peer2, "sha_certified", False))
            and not getattr(peer2, "source_dirty", False)
            and s2 and s2 == p2
        ):
            return False
        s2_hashes = getattr(sel2, "file_hashes", {}) or {}
        p2_hashes = getattr(peer2, "file_hashes", {}) or {}
        return all(
            s2_hashes.get(fp) and s2_hashes.get(fp) == p2_hashes.get(fp)
            for fp in (getattr(sel2, "doc_paths", []) or [])
        )

    outcome, info = _execute_retirement(
        store, owner, repo_name, repo_id, target,
        family="legacy_reconcile", reverify=_reverify,
    )
    if outcome in ("conflict", "unproven"):
        return _kept(
            REASON_LEGACY_CONFLICT,
            "An index changed between proof and removal. The retirement was "
            "voided before the legacy index record was removed; both indexes "
            "remain loadable. Re-run to retry against the current state.",
            established_handle=target,
            **info,
        )
    if outcome == "record_unavailable":
        return _kept(
            REASON_LEGACY_CLEANUP_INCOMPLETE,
            "Retirement was proven, but the durable retirement record could "
            "not be published, so the destructive step was never started. "
            "Nothing was removed; fix the store's .retirements path and "
            "re-run with legacy_reconcile='apply' to retry.",
            established_handle=target,
        )
    if outcome == "cleanup_incomplete":
        return _kept(
            REASON_LEGACY_CLEANUP_INCOMPLETE,
            "Retirement was proven but removing the legacy index did not "
            "complete. It remains discoverable; re-run with "
            "legacy_reconcile='apply' to retry (idempotent).",
            established_handle=target,
            **info,
        )
    leftovers = _leftover_artifacts(store, owner, repo_name)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    replacement = {
        "success": True,
        "repo": target,
        "reused_established_handle": True,
        "requested_path": str(folder_path),
        "legacy_reconciliation": {
            "state": "reconciled",
            "reason_code": REASON_LEGACY_RECONCILED,
            "established_handle": target,
            "removed_handle": repo_id,
            "removed_file_count": len(s_docs),
            "certified_sha": s_sha,
            **info,
            "detail": (
                "The selected legacy index was proven an exact duplicate of "
                "its modern peer (verified identity, same clean certified "
                "commit, full path-and-hash coverage) and retired. The peer "
                "is unchanged and its handle is returned."
            ),
        },
        "_meta": {"latency_ms": latency_ms},
    }
    if leftovers:
        replacement["legacy_reconciliation"]["cleanup_incomplete"] = True
        replacement["legacy_reconciliation"]["leftover_files"] = leftovers[:10]
    return None, replacement


def _report_legacy_reconcile(
    store, owner, repo_name, repo_id, folder_path,
    wt_evidence, call_selection, sel_index, t0,
):
    """jdoc#88 QA-03 — ``legacy_reconcile="report"``, actually read-only.

    The shipped report ran the full refresh first, rewriting the legacy index
    whenever source files changed — precisely when the read-only guarantee
    mattered. This resolver proves from STORED snapshots plus live Git
    evidence and never enters a save path.

    Certification transitivity replaces the refresh: the stored loser and the
    stored peer must both be certified clean at one SHA, and the live checkout
    must be clean at that same SHA. Three clean legs at one commit mean the
    stored snapshots describe the live tree, so the stored path-and-hash
    comparison is the same proof apply would reach after its refresh. A
    genuinely uncertified legacy index reports honestly that report cannot
    certify it without writing; apply (which refreshes under C.2 intent)
    remains the certify-and-retire path."""
    from ._worktree_corpus import (
        classify_graduation,
        filter_lineage_candidates,
        REASON_LEGACY_AMBIGUOUS,
        REASON_LEGACY_CONFLICT,
        REASON_LEGACY_CONTENT_DIFFERS,
        REASON_LEGACY_NO_MODERN_PEER,
        REASON_LEGACY_READY,
        REASON_LEGACY_UNCERTIFIED,
    )

    def _respond(block):
        return {
            "success": True,
            "repo": repo_id,
            "requested_path": str(folder_path),
            "legacy_reconciliation": block,
            "_meta": {
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "read_only": True,
            },
        }

    def _kept(reason_code, detail, **extra):
        block = {"state": "kept", "reason_code": reason_code, "detail": detail}
        block.update(extra)
        return _respond(block)

    peers = filter_lineage_candidates(
        store.list_repos(), wt_evidence, allow_containment=False
    )
    peers = [c for c in peers if c.get("repo") != repo_id]
    action, target = classify_graduation(peers, call_selection)
    if action == "graduate":
        return _kept(
            REASON_LEGACY_NO_MODERN_PEER,
            "No non-provisional modern peer matches this corpus identity "
            "(lineage + relative root + durable selection). Nothing was "
            "changed. Re-run WITHOUT legacy_reconcile to backfill this "
            "index into the identity system — it is the only index for "
            "this corpus.",
        )
    if action == "ambiguous":
        return _kept(
            REASON_LEGACY_AMBIGUOUS,
            "More than one non-provisional modern peer matches this corpus "
            "identity. Retirement requires exactly one; nothing was changed. "
            "Resolve the peer ambiguity first, then re-run.",
            peer_count=len(peers),
        )

    t_owner, _, t_name = target.partition("/")
    peer_index = store.load_index(t_owner, t_name)
    if peer_index is None:
        return _kept(
            REASON_LEGACY_CONFLICT,
            "The modern peer vanished between classification and proof. "
            "Nothing was changed; re-run to retry.",
            established_handle=target,
        )

    s_sha = getattr(sel_index, "head_sha", None) or ""
    p_sha = getattr(peer_index, "head_sha", None) or ""
    live_sha = getattr(wt_evidence, "head_sha", None) or ""
    certified = (
        bool(getattr(sel_index, "sha_certified", False))
        and not getattr(sel_index, "source_dirty", False)
        and bool(getattr(peer_index, "sha_certified", False))
        and not getattr(peer_index, "source_dirty", False)
        and s_sha and s_sha == p_sha
        and not getattr(wt_evidence, "corpus_dirty", False)
        and live_sha == s_sha
    )
    if not certified:
        return _kept(
            REASON_LEGACY_UNCERTIFIED,
            "A read-only report requires BOTH stored indexes certified clean "
            "at one commit AND a clean checkout at that same commit — that "
            "is what lets stored snapshots stand in for a refresh. One leg "
            "failed (dirty, uncertified, or a different commit); nothing was "
            "changed. Commit/refresh to a clean shared state and re-run, or "
            "use legacy_reconcile='apply', which refreshes before proving.",
            established_handle=target,
            selected_sha=s_sha or None,
            established_sha=p_sha or None,
            checkout_sha=live_sha or None,
        )

    s_docs = set(getattr(sel_index, "doc_paths", []) or [])
    s_hashes = getattr(sel_index, "file_hashes", {}) or {}
    p_hashes = getattr(peer_index, "file_hashes", {}) or {}
    differing = sorted(
        fp for fp in s_docs
        if not s_hashes.get(fp) or s_hashes.get(fp) != p_hashes.get(fp)
    )
    if differing:
        return _kept(
            REASON_LEGACY_CONTENT_DIFFERS,
            "Every selected-handle path must exist in the modern peer with "
            "the same stored hash; the listed files differ (or equality "
            "could not be proven from stored hashes). Not a duplicate — "
            "nothing was changed.",
            established_handle=target,
            differing_files=differing[:20],
            differing_file_count=len(differing),
        )

    return _respond({
        "state": "report",
        "reason_code": REASON_LEGACY_READY,
        "established_handle": target,
        "would_remove_handle": repo_id,
        "covered_file_count": len(s_docs),
        "certified_sha": s_sha,
        "detail": (
            "Proof passed from stored snapshots and live Git evidence: "
            "exactly one modern peer, both indexes certified clean at this "
            "checkout's commit, full path-and-hash coverage. Nothing was "
            "changed — report performs no writes. Re-run with "
            "legacy_reconcile='apply' to retire this legacy handle and keep "
            "the peer."
        ),
    })


def _finish_legacy_reconcile(result, legacy_ctx):
    """Run the C.2 resolver at a successful-refresh return site (jdoc#87).

    ``legacy_ctx`` is None on ordinary calls (byte-identical behavior) or the
    kwargs dict for ``_resolve_legacy_reconcile``. Attaches the outcome block,
    or returns the apply-retirement replacement response."""
    if not legacy_ctx:
        return result
    block, replacement = _resolve_legacy_reconcile(**legacy_ctx)
    if replacement is not None:
        return replacement
    result["legacy_reconciliation"] = block
    return result


def _resolve_graduation(
    store, owner, repo_name, repo_id, folder_path, provisional_index,
    wt_evidence, call_selection, t0,
):
    """jdoc#80 Part C — decide the fate of a provisional index now that Git
    lineage is CONFIRMED (§4.2). Returns a dict:

      {"graduate": bool, "return": <response dict|None>, "disclosure": <dict|None>}

    ``graduate`` True means promote in place (the caller clears the provisional
    flag on the refresh save). ``return`` is a ready response that must be
    returned immediately (the reconcile auto-cleanup path). ``disclosure`` is a
    block to attach to the eventual refresh response (ambiguous / diverged —
    stays provisional, fails closed).

    Side-effect-free EXCEPT the reconcile auto-cleanup, which deletes ONLY the
    provisional index (the loser). An established index is never touched (I5).
    The whole decision is gated on confirmed lineage — the same proof #83
    requires, never weaker, never an accumulation of signals (I1)."""
    from ._worktree_corpus import (
        classify_graduation,
        filter_lineage_candidates,
        RECONCILIATION_PROVISIONAL,
        REASON_RECONCILED,
        REASON_GRADUATION_AMBIGUOUS,
        REASON_GRADUATION_DIVERGED,
        REASON_GRADUATION_CONTENT_DIFFERS,
    )
    from ._worktree_corpus import (
        REASON_GRADUATION_CLEANUP_INCOMPLETE,
        REASON_PROVISIONAL_NEWER,
        REASON_SUPERSEDED,
        REASON_SUPERSESSION_CLEANUP_INCOMPLETE,
        REASON_SUPERSESSION_CONFLICT,
    )
    from ._git import (
        ANCESTRY_ANCESTOR,
        ANCESTRY_DESCENDANT,
        commit_ancestry,
    )

    est = filter_lineage_candidates(
        store.list_repos(), wt_evidence, allow_containment=False
    )
    est = [c for c in est if c.get("repo") != repo_id]
    action, target = classify_graduation(est, call_selection)

    if action == "graduate":
        return {"graduate": True, "return": None, "disclosure": None}
    if action == "ambiguous":
        return {
            "graduate": False, "return": None,
            "disclosure": {
                "state": RECONCILIATION_PROVISIONAL,
                "reason_code": REASON_GRADUATION_AMBIGUOUS,
                "detail": (
                    "Git lineage was verified, but more than one established "
                    "index matches this corpus identity. The index was kept "
                    "provisional (fail closed); resolve the ambiguity before it "
                    "can graduate."
                ),
            },
        }

    # reconcile: an established index for this identity exists (I5). The
    # provisional is the loser.
    t_owner, _, t_name = target.partition("/")
    target_index = store.load_index(t_owner, t_name) if target else None
    if target_index is None:
        # The target vanished between listing and now — graduate in place.
        return {"graduate": True, "return": None, "disclosure": None}

    p_docs = set(getattr(provisional_index, "doc_paths", []) or [])
    e_docs = set(getattr(target_index, "doc_paths", []) or [])
    if not (p_docs <= e_docs):
        # The provisional has documents the established index lacks. Deleting it
        # would lose content, so fail closed and stay provisional (never delete
        # on divergence — jjg's auto-cleanup is safe ONLY because the loser is
        # a recomputable subset of the winner).
        return {
            "graduate": False, "return": None,
            "disclosure": {
                "state": RECONCILIATION_PROVISIONAL,
                "reason_code": REASON_GRADUATION_DIVERGED,
                "established_handle": target,
                "detail": (
                    "Git lineage was verified and an established index for this "
                    "corpus exists, but the provisional index has documents not "
                    "present in it. It was kept provisional (no automatic "
                    "removal) to avoid document loss. Refresh the established "
                    "index from a checkout containing those documents, or "
                    "delete whichever index you no longer need."
                ),
            },
        }

    # jdoc#85 C1-01/C1-02: path coverage alone does not prove redundancy. The
    # second destructive-safety gate requires every provisional file to exist
    # in the established index with the SAME stored hash — a missing hash on
    # either side leaves equality unproven and fails closed. Dirty state needs
    # no special case here: equal hashes prove the snapshots identical
    # regardless of Git cleanliness, and unequal hashes are kept apart
    # (Git ancestry cannot order uncommitted content — C1-05).
    p_hashes = getattr(provisional_index, "file_hashes", {}) or {}
    e_hashes = getattr(target_index, "file_hashes", {}) or {}
    differing = sorted(
        fp for fp in p_docs
        if not p_hashes.get(fp) or p_hashes.get(fp) != e_hashes.get(fp)
    )
    if differing:
        # jdoc#86: before settling for content-differs, try modern
        # verified-snapshot supersession. Prerequisites (ALL required; any
        # miss falls through to content-differs, never destructive):
        # both snapshots certified-clean at valid distinct commits, and the
        # stored provisional snapshot is still exactly what this checkout has
        # (same HEAD, not dirty) — retiring a stale snapshot would discard
        # the caller's newer content intent.
        p_sha = getattr(provisional_index, "head_sha", None) or ""
        e_sha = getattr(target_index, "head_sha", None) or ""
        ancestry = None
        prereqs_ok = (
            bool(getattr(provisional_index, "sha_certified", False))
            and not getattr(provisional_index, "source_dirty", False)
            and bool(getattr(target_index, "sha_certified", False))
            and not getattr(target_index, "source_dirty", False)
            and p_sha and e_sha and p_sha != e_sha
            and not getattr(wt_evidence, "corpus_dirty", False)
            and (getattr(wt_evidence, "head_sha", None) or "") == p_sha
        )
        if prereqs_ok:
            ancestry = commit_ancestry(Path(folder_path), p_sha, e_sha)

        if ancestry == ANCESTRY_DESCENDANT:
            # MS-01: the provisional holds the NEWER snapshot. Never retire
            # or overwrite the established index; report the explicit
            # completion path (MS-03) instead.
            t_bare = target.partition("/")[2] or target
            return {
                "graduate": False, "return": None,
                "disclosure": {
                    "state": RECONCILIATION_PROVISIONAL,
                    "reason_code": REASON_PROVISIONAL_NEWER,
                    "established_handle": target,
                    "provisional_sha": p_sha,
                    "established_sha": e_sha,
                    "relationship": "established_is_strict_ancestor_of_provisional",
                    "differing_files": differing[:20],
                    "differing_file_count": len(differing),
                    "next_action": (
                        f"Refresh the established handle from this checkout "
                        f"(index_local with name='{t_bare}' and this path), "
                        f"then re-run this refresh — exact deduplication will "
                        f"then retire the provisional automatically."
                    ),
                    "detail": (
                        "Git proves this provisional snapshot is strictly newer "
                        "than the established index. The established index is "
                        "never replaced automatically; both were kept."
                    ),
                },
            }

        if ancestry == ANCESTRY_ANCESTOR:
            # MS-02: the provisional is a certified strict ancestor of the
            # established snapshot. Final recheck (identity, candidate set,
            # target generation) immediately before the only destructive step.
            est2 = filter_lineage_candidates(
                store.list_repos(), wt_evidence, allow_containment=False
            )
            est2 = [c for c in est2 if c.get("repo") != repo_id]
            action2, target2 = classify_graduation(est2, call_selection)
            fresh_target = (
                store.load_index(t_owner, t_name)
                if action2 == "reconcile" and target2 == target
                else None
            )
            if (
                fresh_target is None
                or (getattr(fresh_target, "head_sha", None) or "") != e_sha
            ):
                return {
                    "graduate": False, "return": None,
                    "disclosure": {
                        "state": RECONCILIATION_PROVISIONAL,
                        "reason_code": REASON_SUPERSESSION_CONFLICT,
                        "established_handle": target,
                        "detail": (
                            "The established target or the candidate set "
                            "changed between classification and retirement. "
                            "Nothing was removed; re-run to retry against the "
                            "current state."
                        ),
                    },
                }
            # jdoc#88 QA-01 / jdoc#89 QA-06/QA-07: coordinated destructive
            # step — fingerprints captured first, ancestry closure re-proved
            # on a reload the token covers (both snapshots still certified
            # clean at the exact SHAs the ancestry proof ordered), durable
            # record receipt required, then the guarded delete.
            def _reverify(sel2, peer2):
                return (
                    bool(getattr(sel2, "sha_certified", False))
                    and not getattr(sel2, "source_dirty", False)
                    and (getattr(sel2, "head_sha", None) or "") == p_sha
                    and bool(getattr(peer2, "sha_certified", False))
                    and not getattr(peer2, "source_dirty", False)
                    and (getattr(peer2, "head_sha", None) or "") == e_sha
                )

            outcome, info = _execute_retirement(
                store, owner, repo_name, repo_id, target,
                family="supersession", reverify=_reverify,
            )
            if outcome in ("conflict", "unproven"):
                return {
                    "graduate": False, "return": None,
                    "disclosure": {
                        "state": RECONCILIATION_PROVISIONAL,
                        "reason_code": REASON_SUPERSESSION_CONFLICT,
                        "established_handle": target,
                        **info,
                        "detail": (
                            "An index changed between proof and removal. The "
                            "retirement was voided before the provisional "
                            "index record was removed; both indexes remain "
                            "loadable. Re-run to retry against the current "
                            "state."
                        ),
                    },
                }
            if outcome == "record_unavailable":
                return {
                    "graduate": False, "return": None,
                    "disclosure": {
                        "state": RECONCILIATION_PROVISIONAL,
                        "reason_code": REASON_SUPERSESSION_CLEANUP_INCOMPLETE,
                        "established_handle": target,
                        "detail": (
                            "Supersession was proven, but the durable "
                            "retirement record could not be published, so the "
                            "destructive step was never started. Nothing was "
                            "removed; fix the store's .retirements path and "
                            "re-run to retry."
                        ),
                    },
                }
            if outcome == "cleanup_incomplete":
                return {
                    "graduate": False, "return": None,
                    "disclosure": {
                        "state": RECONCILIATION_PROVISIONAL,
                        "reason_code": REASON_SUPERSESSION_CLEANUP_INCOMPLETE,
                        "established_handle": target,
                        **info,
                        "detail": (
                            "Supersession was proven but retiring the "
                            "provisional index did not complete. It remains "
                            "discoverable; re-run to retry (idempotent)."
                        ),
                    },
                }
            leftovers = _leftover_artifacts(store, owner, repo_name)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            response = {
                "success": True,
                "repo": target,
                "reused_established_handle": True,
                "requested_path": str(folder_path),
                "reconciliation": {
                    "state": "superseded",
                    "reason_code": REASON_SUPERSEDED,
                    "established_handle": target,
                    "removed_handle": f"{owner}/{repo_name}",
                    "removed_file_count": len(p_docs),
                    "provisional_sha": p_sha,
                    "established_sha": e_sha,
                    "relationship": "provisional_is_strict_ancestor_of_established",
                    **info,
                    "detail": (
                        "Git proved the provisional snapshot is a strict "
                        "ancestor of the established index's snapshot (same "
                        "verified corpus identity, both certified clean). The "
                        "older provisional was retired; the established index "
                        "is unchanged and its handle is returned."
                    ),
                },
                "_meta": {"latency_ms": latency_ms},
            }
            if leftovers:
                response["reconciliation"]["cleanup_incomplete"] = True
                response["reconciliation"]["leftover_files"] = leftovers[:10]
            return {"graduate": False, "return": response, "disclosure": None}

        disclosure = {
            "state": RECONCILIATION_PROVISIONAL,
            "reason_code": REASON_GRADUATION_CONTENT_DIFFERS,
            "established_handle": target,
            "differing_files": differing[:20],
            "differing_file_count": len(differing),
            "detail": (
                "Git lineage was verified and an established index for this "
                "corpus exists, but the provisional index holds different "
                "content for the files listed (or content equality could not "
                "be proven from stored hashes). This is not an exact "
                "duplicate, so both indexes were kept and nothing was "
                "removed. Review the differing files, or refresh whichever "
                "index is stale and re-run."
            ),
        }
        if ancestry is not None:
            # Probed but unordered/unproven — say so (jdoc#86 MS-04 reporting).
            disclosure["ancestry"] = ancestry
        return {"graduate": False, "return": None, "disclosure": disclosure}

    # Exact duplicate proven (identity gate + per-file hash equality) —
    # auto-cleanup the provisional (loser only), return the established handle.
    # jdoc#88 QA-02: the delete result is authoritative. If removal did not
    # complete, the reconcile did NOT happen — never report `reconciled` or
    # emit `removed_handle` for a loser that still exists. Keep both indexes
    # discoverable and report the recoverable, retryable state (mirrors the
    # supersession/legacy cleanup-incomplete paths, which already check this).
    # jdoc#88 QA-01 / jdoc#89 QA-06/QA-07: coordinated destructive step —
    # fingerprints captured first, the exact-duplicate proof (path coverage +
    # per-file hash equality) re-run on a reload the token covers, durable
    # record receipt required, then the guarded delete (verified inside the
    # deletion boundary and at the pre-removal recheck).
    from ._worktree_corpus import REASON_GRADUATION_CONFLICT

    def _reverify(sel2, peer2):
        p2_docs = set(getattr(sel2, "doc_paths", []) or [])
        e2_docs = set(getattr(peer2, "doc_paths", []) or [])
        if not (p2_docs <= e2_docs):
            return False
        p2_hashes = getattr(sel2, "file_hashes", {}) or {}
        e2_hashes = getattr(peer2, "file_hashes", {}) or {}
        return all(
            p2_hashes.get(fp) and p2_hashes.get(fp) == e2_hashes.get(fp)
            for fp in p2_docs
        )

    outcome, info = _execute_retirement(
        store, owner, repo_name, repo_id, target,
        family="exact_dedup", reverify=_reverify,
    )
    if outcome in ("conflict", "unproven"):
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "graduate": False,
            "return": {
                "success": True,
                "repo": repo_id,
                "requested_path": str(folder_path),
                "reconciliation": {
                    "state": RECONCILIATION_PROVISIONAL,
                    "reason_code": REASON_GRADUATION_CONFLICT,
                    "established_handle": target,
                    **info,
                    "detail": (
                        "The provisional index was proven an exact duplicate, "
                        "but an index changed between proof and removal. The "
                        "retirement was voided before the provisional index "
                        "record was removed; both indexes remain loadable. "
                        "Re-run to retry against the current state."
                    ),
                },
                "_meta": {"latency_ms": latency_ms},
            },
            "disclosure": None,
        }
    if outcome == "record_unavailable":
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "graduate": False,
            "return": {
                "success": True,
                "repo": repo_id,
                "requested_path": str(folder_path),
                "reconciliation": {
                    "state": RECONCILIATION_PROVISIONAL,
                    "reason_code": REASON_GRADUATION_CLEANUP_INCOMPLETE,
                    "established_handle": target,
                    "detail": (
                        "The provisional index was proven an exact duplicate, "
                        "but the durable retirement record could not be "
                        "published, so the destructive step was never "
                        "started. Nothing was removed; fix the store's "
                        ".retirements path and re-run to retry."
                    ),
                },
                "_meta": {"latency_ms": latency_ms},
            },
            "disclosure": None,
        }
    if outcome == "cleanup_incomplete":
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "graduate": False,
            "return": {
                "success": True,
                "repo": repo_id,
                "requested_path": str(folder_path),
                "reconciliation": {
                    "state": RECONCILIATION_PROVISIONAL,
                    "reason_code": REASON_GRADUATION_CLEANUP_INCOMPLETE,
                    "established_handle": target,
                    **info,
                    "detail": (
                        "The provisional index was proven an exact duplicate "
                        "of its established peer, but removing it did not "
                        "complete. Nothing was reconciled: the provisional "
                        "remains discoverable and the peer is untouched. "
                        "Re-run to retry the retirement (idempotent)."
                    ),
                },
                "_meta": {"latency_ms": latency_ms},
            },
            "disclosure": None,
        }
    latency_ms = int((time.perf_counter() - t0) * 1000)
    return {
        "graduate": False,
        "return": {
            "success": True,
            "repo": target,
            "reused_established_handle": True,
            "requested_path": str(folder_path),
            "reconciliation": {
                "state": "reconciled",
                "reason_code": REASON_RECONCILED,
                "established_handle": target,
                "removed_handle": f"{owner}/{repo_name}",
                "removed_file_count": len(p_docs),
                **info,
                "detail": (
                    "Git lineage was verified; an established index for this "
                    "corpus already exists and every provisional file matches "
                    "it by stored hash. The provisional index was reconciled "
                    "(removed) and the established handle is returned. No "
                    "documents were lost."
                ),
            },
            "_meta": {"latency_ms": latency_ms},
        },
        "disclosure": None,
    }


def _should_skip(rel_path: str) -> bool:
    normalized = "/" + rel_path.replace("\\", "/")
    for pat in SKIP_PATTERNS:
        if ("/" + pat) in normalized:
            return True
    return False


def _sidecar_view(sections: list, content_for=None) -> list:
    """Normalize sections to dicts carrying body text, for the sidecar builders.

    jdoc#117: all four sidecars are derived from section CONTENT, and the two
    call paths hand it over differently. The full path holds in-memory
    ``Section`` objects whose ``.content`` is populated; the incremental path
    holds persisted dicts, and ``Section.to_dict`` deliberately drops content to
    keep the monolith small. Passing those dicts straight through would build
    every sidecar from empty strings -- a silent wipe, worse than the staleness
    it is meant to fix -- so the incremental caller supplies ``content_for`` to
    re-read the body by byte range.

    ``embedding`` is carried through when present because the related-graph's
    semantic half reads it; the other three ignore it.
    """
    out: list = []
    for sec in sections:
        if isinstance(sec, dict):
            row = dict(sec)
            if not row.get("content") and content_for is not None:
                row["content"] = content_for(sec) or ""
        else:
            row = sec.to_dict()
            row["content"] = getattr(sec, "content", "") or ""
            emb = getattr(sec, "embedding", None)
            if emb:
                row["embedding"] = emb
        out.append(row)
    return out


def _write_sidecars(storage_path, owner: str, name: str, sections: list) -> dict:
    """Rebuild the four derived sidecars. Returns a per-sidecar skip report.

    jdoc#117: these used to live inline on the full-index path only, three of
    them behind a bare ``except Exception: pass``. Two consequences, both
    invisible: an incremental refresh never rebuilt them at all (so
    ``get_related_sections`` answered from whenever the last FULL index ran),
    and a genuine failure on the full path was indistinguishable from a clean
    result. #103 already made that argument for the near-duplicate sidecar; it
    applies to all four. Failures are reported, never raised -- a sidecar is
    enrichment and must not fail an otherwise-good index.
    """
    skipped: dict = {}

    # v1.19.0: glossary.
    try:
        write_terms(storage_path, owner, name, extract_glossary(sections))
    except Exception as e:
        skipped["glossary"] = {"reason": "error", "detail": str(e)[:200]}

    # v1.24.0: related-graph adjacency list.
    try:
        from ..retrieval.related_persist import write as _write_related
        _write_related(storage_path, owner, name, sections)
    except Exception as e:
        skipped["related"] = {"reason": "error", "detail": str(e)[:200]}

    # v1.24.0: boilerplate detector.
    try:
        from ..retrieval.boilerplate import write as _write_boilerplate
        _write_boilerplate(storage_path, owner, name, sections)
    except Exception as e:
        skipped["boilerplate"] = {"reason": "error", "detail": str(e)[:200]}

    # v1.34.0: section near-duplicate detector. jdoc#103: ceiling-guarded, and
    # a skip is REPORTED rather than looking like "no duplicates found".
    try:
        from ..retrieval.dedup import write as _write_dedup, last_skip_reason
        _write_dedup(storage_path, owner, name, sections)
        reason = last_skip_reason()
        if reason:
            skipped["dedup"] = reason
    except Exception as e:
        skipped["dedup"] = {"reason": "error", "detail": str(e)[:200]}

    return skipped


_DISCOVERY_HARD_CEILING_MULT = 20  # safety: stop counting at max_files * this


def _count_skip(skip_counts: Optional[dict], reason: str) -> None:
    """Tally one discovery-time skip (v1.103.0 coverage contract).

    No-op when the caller didn't ask for counts, so every existing
    ``discover_doc_files`` call keeps its exact prior behavior.
    """
    if skip_counts is not None:
        skip_counts[reason] = skip_counts.get(reason, 0) + 1


def _walk_rel(dir_rel: str, name: str = "") -> str:
    """Root-relative POSIX path for an os.walk entry, as gitignore matching sees it.

    ⚠⚠ This exists because `f"{dir_rel}/{name}".lstrip("./")` was WRONG, and
    wrong in a way that read as correct (jdoc#102, @faxik). ``str.lstrip`` takes
    a CHARACTER SET, not a prefix, so it eats the leading dot of the first path
    component along with the "./" it was meant to remove::

        "./.worktrees/".lstrip("./")            -> "worktrees/"     # pattern misses
        ".worktrees/a/copy.md".lstrip("./")     -> "worktrees/a/copy.md"

    Undotted directories were pruned correctly, which is exactly why this
    survived: only a gitignored directory whose name STARTS WITH A DOT leaked,
    and those are the common ones — `.venv/`, `.tox/`, `.next/`, `.cache/`,
    `.worktrees/`. The reporter's repo indexed 9,813 documents where ~3,100
    exist, roughly 48 copies of one corpus, because git worktrees lived under a
    gitignored `.worktrees/`.

    Duplicate copies are not merely a cost: near-identical sections from stale
    branches compete with the live one, so retrieval quality degrades too.

    Both the directory-pruning and the per-file check must use this, or files
    nested under a leaked dot-directory escape the fallback as well.
    """
    base = "" if dir_rel in ("", ".") else f"{dir_rel}/"
    return f"{base}{name}"


def discover_doc_files(
    folder_path: Path,
    max_files: int = 10_000,
    max_size: int = DEFAULT_MAX_FILE_SIZE,
    extra_ignore_patterns: Optional[list] = None,
    follow_symlinks: bool = False,
    sort_by: str = "newest",
    skip_counts: Optional[dict] = None,
    include_dot_dirs: Optional[list] = None,
) -> tuple:
    """Discover doc files (.md, .txt, .rst) with security filtering.

    ``include_dot_dirs`` (jdoc#113): directory NAMES to index despite starting
    with a dot, unioned with ``DOT_DIR_ALLOWLIST`` (``.github``). Dotted
    directories are otherwise pruned by rule, so a tool that writes a dotfile
    cache into the corpus cannot have it ingested as documentation.

    ``skip_counts`` (v1.103.0): optional dict the walk tallies per-reason skip
    counts into (``unsupported_extension``, ``oversize``, ``gitignored``, ...)
    at the existing skip sites — the index-time half of the coverage contract
    an absent verdict discloses. Omitted = no counting, behavior unchanged.

    Returns ``(files, warnings, discovered_count)``. ``files`` is capped at
    ``max_files``; ``discovered_count`` is the total that matched all filters
    (capped at ``max_files * _DISCOVERY_HARD_CEILING_MULT`` so a pathological
    directory tree cannot run forever). When ``discovered_count > max_files``
    the caller is responsible for surfacing truncation (jdoc#15).

    ``sort_by`` (jdoc#16) controls truncation order:
      * ``"newest"`` (default): when the cap is hit, the indexed subset is
        the ``max_files`` files with the most recent mtime. So a freshly-
        edited file is always in the index regardless of where it sits in
        the filesystem walk.
      * ``"walk_order"``: take the first ``max_files`` in filesystem-walk
        order (the pre-jdoc#16 behavior). Useful for deterministic
        reproducible builds where mtimes can shift.
    """
    discovered_items: list = []  # [(file_path, mtime_or_zero), ...]
    warnings = []
    hard_ceiling = max_files * _DISCOVERY_HARD_CEILING_MULT
    root = folder_path.resolve()

    gitignore_spec = _load_gitignore(root)
    extra_spec = None
    if extra_ignore_patterns:
        try:
            extra_spec = pathspec.PathSpec.from_lines("gitignore", extra_ignore_patterns)
        except Exception:
            pass

    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
        dir_path = Path(dirpath)
        try:
            dir_rel = dir_path.relative_to(root).as_posix()
        except ValueError:
            dirnames.clear()
            continue

        # Prune skipped directories in-place so os.walk won't descend into them.
        # ⚠ Build the relative path with _walk_rel, never `lstrip("./")` — see
        # its docstring (jdoc#102). Every ignore check below must see the SAME
        # string git would match, or an ignored directory is silently walked.
        kept_dirs = []
        for d in dirnames:
            # jdoc#113: dotted directories are pruned by RULE. Counted so the
            # exclusion is reportable — a walk that silently drops a subtree is
            # indistinguishable from a corpus that never had one.
            if is_skipped_dot_dir(d, include_dot_dirs):
                _count_skip(skip_counts, "dot_directory")
                continue
            walk_rel = _walk_rel(dir_rel, f"{d}/")
            if _should_skip(walk_rel):
                continue
            if gitignore_spec and gitignore_spec.match_file(walk_rel):
                continue
            if extra_spec and extra_spec.match_file(walk_rel):
                continue
            kept_dirs.append(d)
        dirnames[:] = kept_dirs

        for filename in filenames:
            file_path = dir_path / filename

            if not follow_symlinks and file_path.is_symlink():
                _count_skip(skip_counts, "symlink_not_followed")
                continue
            if file_path.is_symlink() and is_symlink_escape(root, file_path):
                warnings.append(f"Skipped symlink escape: {file_path}")
                _count_skip(skip_counts, "symlink_escape")
                continue

            if not validate_path(root, file_path):
                warnings.append(f"Skipped path traversal: {file_path}")
                _count_skip(skip_counts, "path_traversal")
                continue

            rel_path = _walk_rel(dir_rel, filename)

            if _should_skip(rel_path):
                _count_skip(skip_counts, "skip_pattern")
                continue

            if gitignore_spec and gitignore_spec.match_file(rel_path):
                _count_skip(skip_counts, "gitignored")
                continue

            if extra_spec and extra_spec.match_file(rel_path):
                _count_skip(skip_counts, "extra_ignore_pattern")
                continue

            if is_secret_file(rel_path):
                warnings.append(f"Skipped secret file: {rel_path}")
                _count_skip(skip_counts, "secret_file")
                continue

            ext = file_path.suffix.lower()
            if ext in OFFICE_EXTENSIONS:
                if not office_available():
                    # pdf/docx/pptx/epub need the optional markitdown extra
                    # (pip install jdocmunch-mcp[office]); distinct skip
                    # reason so coverage reporting names the actual cause.
                    _count_skip(skip_counts, "office_extra_not_installed")
                    continue
            elif ext not in ALL_EXTENSIONS:
                _count_skip(skip_counts, "unsupported_extension")
                continue

            try:
                st = file_path.stat()
                size_cap = OFFICE_MAX_FILE_SIZE if ext in OFFICE_EXTENSIONS else max_size
                if st.st_size > size_cap:
                    _count_skip(skip_counts, "oversize")
                    continue
                mtime = st.st_mtime
            except OSError:
                _count_skip(skip_counts, "stat_error")
                continue

            discovered_items.append((file_path, mtime))

        # Stop walking entirely when the safety ceiling is reached so an
        # adversarial / runaway directory tree can't churn forever.
        if len(discovered_items) >= hard_ceiling:
            break

    discovered = len(discovered_items)
    if sort_by == "newest" and discovered > max_files:
        # Only sort on the truncation path; the un-truncated case
        # preserves walk order so callers see no behavior change.
        discovered_items.sort(key=lambda item: item[1], reverse=True)
    files = [fp for fp, _ in discovered_items[:max_files]]
    return files, warnings, discovered


def _resolve_explicit_paths(
    folder_path: Path,
    paths: list,
    max_files: int,
    follow_symlinks: bool,
    include_dot_dirs: Optional[list] = None,
) -> tuple:
    """Resolve a caller-supplied list of paths into the doc-file shape that the
    downstream pipeline expects. Each entry may be:

      * an absolute path under ``folder_path``, or
      * a path relative to ``folder_path``,
      * a directory (recursed via ``discover_doc_files`` against that subtree),
      * a file (validated and added when its extension is known).

    Returns ``(files, warnings, requested)``. ``requested`` is the list of
    root-relative POSIX paths for every entry that resolved inside
    ``folder_path`` — including entries that no longer exist on disk — so the
    caller can scope an incremental diff to exactly what was asked for
    (jdoc#31). Mirrors ``discover_doc_files`` semantics for security: rejects
    symlink escapes, path-traversal attempts, and entries outside
    ``folder_path``. Skips entries with unknown extensions silently (caller
    gets a `warnings` entry per skip).
    """
    files: list = []
    warnings: list = []
    requested: list = []
    seen: set = set()

    for raw in paths:
        if not isinstance(raw, str) or not raw.strip():
            warnings.append(f"Skipped empty/non-string path: {raw!r}")
            continue
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (folder_path / p)
        try:
            p = p.resolve()
        except OSError as e:
            warnings.append(f"Skipped unresolvable path {raw!r}: {e}")
            continue

        try:
            requested.append(p.relative_to(folder_path).as_posix())
        except ValueError:
            warnings.append(f"Skipped path outside folder: {raw!r}")
            continue

        if not p.exists():
            warnings.append(f"Skipped non-existent path: {raw!r}")
            continue

        if p.is_dir():
            # ⚠ `p` is this sub-walk's ROOT, so naming a dotted directory in
            # `paths` still indexes it (an explicit request is not a stray
            # cache). Only dotted directories BELOW it are pruned.
            sub_files, sub_warnings, _sub_discovered = discover_doc_files(
                p,
                max_files=max_files - len(files),
                follow_symlinks=follow_symlinks,
                include_dot_dirs=include_dot_dirs,
            )
            warnings.extend(sub_warnings)
            for f in sub_files:
                fr = f.resolve()
                if fr not in seen:
                    seen.add(fr)
                    files.append(f)
                    if len(files) >= max_files:
                        break
        elif p.is_file():
            if not follow_symlinks and p.is_symlink():
                warnings.append(f"Skipped symlink (follow_symlinks=False): {raw!r}")
                continue
            if p.is_symlink() and is_symlink_escape(folder_path, p):
                warnings.append(f"Skipped symlink escape: {raw!r}")
                continue
            if not validate_path(folder_path, p):
                warnings.append(f"Skipped path traversal: {raw!r}")
                continue
            ext = p.suffix.lower()
            if ext in OFFICE_EXTENSIONS:
                if not office_available():
                    warnings.append(
                        f"Skipped office document (install "
                        f"jdocmunch-mcp[office]): {raw!r}"
                    )
                    continue
            elif ext not in ALL_EXTENSIONS:
                warnings.append(f"Skipped unsupported extension: {raw!r}")
                continue
            pr = p.resolve()
            if pr not in seen:
                seen.add(pr)
                files.append(p)
        else:
            warnings.append(f"Skipped non-file/non-dir entry: {raw!r}")

        if len(files) >= max_files:
            break

    return files[:max_files], warnings, requested


def index_local(
    path: str,
    name: Optional[str] = None,
    use_ai_summaries: bool = True,
    use_embeddings="auto",
    storage_path: Optional[str] = None,
    extra_ignore_patterns: Optional[list] = None,
    include_dot_dirs: Optional[list] = None,
    follow_symlinks: bool = False,
    incremental: bool = True,
    max_files: int = 10_000,
    sort_by: str = "newest",
    autotune: bool = False,
    paths: Optional[list] = None,
    worktree_mode: str = "reuse_equivalent",
    legacy_reconcile: Optional[str] = None,
) -> dict:
    """Index a local folder containing documentation files.

    Args:
        path: Path to local folder.
        name: Optional repo identifier override. Use when two folders share the same
              name (e.g. two libraries both with a 'docs' folder). Defaults to the
              folder name.
        use_ai_summaries: Whether to use AI for section summaries.
        use_embeddings: True/False/"auto". "auto" (default) enables embeddings when
                        an embedding provider is configured (GOOGLE_API_KEY,
                        OPENAI_API_KEY, openai-compatible
                        + JDOCMUNCH_OPENAI_COMPAT_URL + JDOCMUNCH_OPENAI_COMPAT_MODEL,
                        or sentence-transformers installed).
        storage_path: Custom storage path (default: ~/.doc-index/).
        extra_ignore_patterns: Additional gitignore-style patterns to exclude.
        follow_symlinks: Whether to follow symlinks.
        incremental: When True and an existing index exists, only re-index changed files.
        max_files: Maximum number of doc files to index. Default 10000.
                   When hit, response includes truncated/discovered/indexed
                   top-level fields (jdoc#15).
        sort_by: "newest" (default) or "walk_order". Controls which subset
                 is indexed when discovered > max_files. "newest" sorts by
                 mtime descending so recently-edited files always make it
                 into the index regardless of filesystem-walk position
                 (jdoc#16). "walk_order" preserves the pre-1.65 behavior
                 for callers needing deterministic reproducible builds.
                 No effect when the corpus fits under the cap.
        paths: Optional list of explicit paths to index. When provided, the tree
            walk is skipped; only these files (and the contents of any directories
            in the list) are indexed. Each entry may be absolute or relative to
            ``path``. Useful for batch-indexing exactly the files an agent already
            knows about — e.g. the doc files git just touched.
            On an existing index with ``incremental=True`` the diff is scoped to
            the listed subset (jdoc#31): listed files are added/updated, a listed
            file that no longer exists on disk is removed, and indexed files NOT
            in the list are left untouched (never treated as deleted).
        worktree_mode: ``"reuse_equivalent"`` (default, jdoc#83) recognizes when
            an equivalent corpus in a linked Git worktree already has an
            established index — a proven-fresh equivalent is reused (returned,
            not refreshed) instead of creating a duplicate; stale, dirty,
            ambiguous, or evidence-incomplete outcomes return a bounded
            decision with no write. ``"branch_local"`` opts out: intentionally
            create or refresh an exact-path index for THIS worktree.
        legacy_reconcile: jdoc#87 (Part C.2). ``"report"`` proves whether the
            explicitly named pre-1.102 fieldless legacy index is an exact
            duplicate of its single modern peer (same verified identity, same
            clean certified commit, full path-and-hash coverage) WITHOUT
            changing anything; ``"apply"`` repeats the proof immediately
            before retiring the selected legacy handle (the only possible
            loser — the peer is never touched). Requires an explicit
            ``name=`` selecting a handle that is fieldless at call start, a
            full refresh (no ``paths``), default ``worktree_mode``, and
            confirmed Git lineage; anything else fails closed with
            ``legacy_reconcile_not_applicable`` and no write. Omitted
            (default): an ordinary refresh stays backfill-only and never
            retires anything.

    Returns:
        Dict with indexing results.
    """
    t0 = time.perf_counter()
    folder_path = Path(path).expanduser().resolve()

    if not folder_path.exists():
        return {"success": False, "error": f"Folder not found: {path}"}
    if not folder_path.is_dir():
        return {"success": False, "error": f"Path is not a directory: {path}"}

    use_embeddings = should_embed(use_embeddings)
    warnings = []

    # jdoc#67: normalize a discovered `local/<name>` handle back to its bare
    # storage component so the doc_list_repos -> index_local refresh round trip
    # works. Done before the broad try below so an invalid name returns a clean
    # error rather than the "Indexing failed: ..." wrapper.
    try:
        repo_name = normalize_local_index_name(name, folder_path.name, str(folder_path))
    except ValueError as e:
        return {"success": False, "error": str(e)}
    owner = "local"
    repo_id = f"{owner}/{repo_name}"

    # jdoc#72: when name was omitted and the folder basename wasn't a valid
    # storage component, a safe local name was derived. Surface both labels so
    # the caller can record the durable handle, and warn that an explicit
    # name= overrides it.
    derivation_fields: dict = {}
    if not name and repo_name != folder_path.name:
        derivation_fields = {
            "original_folder_label": folder_path.name,
            "derived_local_name": repo_name,
        }
        warnings.append(
            f"Folder label {folder_path.name!r} is not a valid storage name; "
            f"indexed under the derived handle local/{repo_name}. Pass "
            f'name="<your-name>" to choose your own.'
        )

    # jdoc#81: creation-claim state, initialized outside the broad try so the
    # exception path can release an acquired claim (a failed create must not
    # leave a claim that blocks the retry).
    claim_token: Optional[str] = None
    claim_store = None

    try:
        # jdoc#116: resolve the effective shaping patterns BEFORE discovery,
        # because inheritance has to change which files the walk visits. A
        # descriptor inherited without the patterns would assert an exclusion
        # the walk did not apply, i.e. an index claiming `full+shape:<hash>`
        # while containing the excluded file. That is worse than the widening
        # it replaces, because today's widening is at least disclosed.
        #
        # None and [] are DIFFERENT and that distinction is the whole fix:
        #   None -> caller said nothing  -> inherit whatever is stored
        #   []   -> caller said "none"   -> widen, and disclose the change
        # Every entry point that omits the argument (the CLI, a watch refresh)
        # gets None, which is precisely the population this issue is about.
        store = DocStore(base_path=storage_path)
        _prior = store.load_index(owner, repo_name)
        inherited_patterns: list = []
        if extra_ignore_patterns is None and _prior is not None:
            inherited_patterns = list(
                getattr(_prior, "corpus_shape_patterns", None) or []
            )
            if inherited_patterns:
                extra_ignore_patterns = inherited_patterns

        requested_rels: list = []
        # v1.103.0: per-reason skip tally from the full discovery walk — the
        # index-time half of the coverage contract. Only a full walk (no
        # explicit `paths`) records it; a subset call is not a coverage claim.
        walk_skip_counts: dict = {}
        if paths:
            doc_files, discover_warnings, requested_rels = _resolve_explicit_paths(
                folder_path,
                list(paths),
                max_files=max_files,
                follow_symlinks=follow_symlinks,
                include_dot_dirs=include_dot_dirs,
            )
            discovered_count = len(doc_files)
        else:
            doc_files, discover_warnings, discovered_count = discover_doc_files(
                folder_path,
                max_files=max_files,
                extra_ignore_patterns=extra_ignore_patterns,
                include_dot_dirs=include_dot_dirs,
                follow_symlinks=follow_symlinks,
                sort_by=sort_by,
                skip_counts=walk_skip_counts,
            )
        warnings.extend(discover_warnings)

        initial_git_state = (local_git_head(folder_path), False)
        existing_index = store.load_index(owner, repo_name)

        # --- jdoc#81: corpus-identity resolution BEFORE any persistent write.
        # An equivalent local source (same normalized root + a covering durable
        # selection) must not gain a second physical index just because a
        # different name was supplied. Explicitly selecting an existing handle
        # stays refreshable; an explicit *conflicting* name returns a conflict;
        # several equivalent legacy indexes return bounded ambiguity — never a
        # guess, never another copy.
        from ._corpus_identity import (
            candidate_rows,
            corpus_norm_root,
            find_equivalent_indexes,
            selection_descriptor,
        )
        from ..storage.corpus_claims import claim_key, release_claim, try_claim

        explicit_name = bool(name and str(name).strip())
        call_selection = selection_descriptor(
            requested_rels if paths else None,
            extra_ignore_patterns=extra_ignore_patterns,
            follow_symlinks=follow_symlinks,
        )
        root_key = corpus_norm_root(folder_path)
        # jdoc#82: two relations, deliberately distinct. Identity (symmetric,
        # order-independent) drives conflict and ambiguity; refresh coverage
        # (directional — a full index also absorbs a temporary subset call)
        # drives only omitted-name routing to an established handle.
        identity_matches = find_equivalent_indexes(
            store, root_key, call_selection, exclude_repo=repo_id, mode="identity"
        )
        equivalents = identity_matches if explicit_name else find_equivalent_indexes(
            store, root_key, call_selection, exclude_repo=repo_id, mode="refresh"
        )
        reuse_fields: dict = {}

        if existing_index is None and equivalents:
            if explicit_name:
                # jdoc#82 invariant 2: several identity matches are NEVER
                # ordered into a winner — registry order must not promote an
                # established handle. Only a single match names one.
                if len(equivalents) > 1:
                    return {
                        "success": False,
                        "error": "ambiguous_corpus_identity",
                        "requested_handle": repo_id,
                        "candidates": candidate_rows(equivalents),
                        "total_matches": len(equivalents),
                        "hint": (
                            "Multiple existing indexes cover this source "
                            "equally well. Pass name= selecting one of the "
                            "candidates to refresh it, or delete_index the "
                            "duplicates. No new index was created."
                        ),
                    }
                est = equivalents[0]
                return {
                    "success": False,
                    "error": "corpus_already_indexed",
                    "requested_handle": repo_id,
                    "established_handle": est.get("repo", ""),
                    "candidates": candidate_rows(equivalents),
                    "total_matches": len(equivalents),
                    "hint": (
                        f"This documentation source is already indexed as "
                        f"'{est.get('repo', '')}'. Refresh it with "
                        f"index_local(path=..., name='{est.get('name') or est.get('repo', '')}'), "
                        f"or delete_index it first if you intend to re-home the "
                        f"corpus. No new index was created."
                    ),
                }
            if len(equivalents) == 1:
                est = equivalents[0]
                reuse_fields = {
                    "reused_established_handle": True,
                    "requested_handle": repo_id,
                    "established_handle": est.get("repo", ""),
                }
                repo_name = est.get("name") or est.get("repo", "").partition("/")[2]
                repo_id = f"{owner}/{repo_name}"
                existing_index = store.load_index(owner, repo_name)
            else:
                return {
                    "success": False,
                    "error": "ambiguous_corpus_identity",
                    "candidates": candidate_rows(equivalents),
                    "total_matches": len(equivalents),
                    "hint": (
                        "Multiple existing indexes cover this source equally "
                        "well. Pass name= selecting one of the candidates to "
                        "refresh it. No new index was created."
                    ),
                }
        elif existing_index is not None and not explicit_name and equivalents:
            # The derived handle exists AND other equivalent indexes exist —
            # the caller hasn't selected one, so guessing is not allowed.
            all_candidates = [{
                "repo": repo_id,
                "source_root": str(folder_path),
                "indexed_at": existing_index.indexed_at,
            }] + candidate_rows(equivalents)
            return {
                "success": False,
                "error": "ambiguous_corpus_identity",
                "candidates": all_candidates[:5],
                "total_matches": 1 + len(equivalents),
                "hint": (
                    "Multiple existing indexes cover this source equally well. "
                    "Pass name= selecting one of the candidates to refresh it. "
                    "No index was created or modified."
                ),
            }


        # jdoc#31: when explicit `paths` target an existing incremental index,
        # an empty resolution is not a dead end — every listed file may have
        # been deleted from disk, and the subset-scoped diff below must still
        # run to remove them. Every other shape keeps the early return.
        can_diff_subset = bool(
            paths and requested_rels and incremental and existing_index is not None
        )
        if not doc_files and not can_diff_subset:
            err: dict = {"success": False, "error": "No documentation files found"}
            if warnings:
                err["warnings"] = warnings
            return err

        # jdoc#83 (Item B): collect Git evidence ONCE for this request —
        # used by the worktree gate below and persisted as identity metadata
        # on every save path. Non-Git folders come back in_git=False and keep
        # exactly the pre-#83 behavior.
        from ._worktree_corpus import (
            ResolutionRequest,
            collect_git_evidence,
            count_provisional_for_root,
            filter_lineage_candidates,
            legacy_sibling_handles,
            resolve_worktree_corpus,
            worktree_claim_key,
            CORPUS_IDENTITY_VERSION,
            PROVISIONAL_PER_ROOT_CAP,
            REASON_PROVISIONAL_CAP,
            REASON_PROVISIONAL_CREATED,
            RECONCILIATION_PROVISIONAL,
        )

        wt_evidence = collect_git_evidence(folder_path)
        branch_local = str(worktree_mode or "reuse_equivalent") == "branch_local"
        lineage_kwargs: dict = {}
        if wt_evidence.lineage_state == "confirmed":
            lineage_kwargs = {
                "worktree_lineage_key": wt_evidence.lineage_key,
                "repo_relative_root": wt_evidence.relative_root,
                "corpus_identity_version": CORPUS_IDENTITY_VERSION,
            }

        # jdoc#87 Part C.2 — explicit-intent legacy reconciliation precheck.
        # Every precondition failure is a fail-closed error BEFORE any write:
        # the caller asked for a destructive-capable operation, so a call that
        # cannot be proven eligible must not silently degrade into an ordinary
        # refresh. Omitted intent keeps refresh backfill-only (LC2-01).
        legacy_ctx: Optional[dict] = None
        if legacy_reconcile is not None:
            from ._worktree_corpus import REASON_LEGACY_NOT_APPLICABLE

            mode_str = str(legacy_reconcile).strip().lower()
            if mode_str not in ("report", "apply"):
                return {
                    "success": False,
                    "error": (
                        f"Invalid legacy_reconcile: {legacy_reconcile!r}. "
                        "Use 'report' (prove without changing anything) or "
                        "'apply' (prove, then retire the selected legacy "
                        "handle)."
                    ),
                }

            def _c2_refused(detail: str) -> dict:
                return {
                    "success": False,
                    "error": REASON_LEGACY_NOT_APPLICABLE,
                    "requested_handle": repo_id,
                    "detail": detail,
                    "hint": (
                        "Nothing was written. Re-run without "
                        "legacy_reconcile for an ordinary refresh."
                    ),
                }

            if not explicit_name:
                return _c2_refused(
                    "legacy_reconcile requires an explicit name= selecting "
                    "the legacy handle — the selected handle is the only "
                    "possible loser, so it is never derived or guessed."
                )
            if existing_index is None:
                return _c2_refused(
                    f"No index named '{repo_id}' exists. C.2 applies only "
                    "to an existing pre-1.102 fieldless legacy index."
                )
            if paths:
                return _c2_refused(
                    "legacy_reconcile requires a FULL refresh; a paths= "
                    "subset cannot certify the whole corpus."
                )
            if branch_local:
                return _c2_refused(
                    "legacy_reconcile requires the default worktree_mode; "
                    "branch_local opts out of lineage-based identity, which "
                    "the proof depends on."
                )
            if (
                getattr(existing_index, "reconciliation_state", "") or ""
            ) == RECONCILIATION_PROVISIONAL:
                return _c2_refused(
                    "The selected handle is provisional, not a pre-1.102 "
                    "legacy index. Provisionals reconcile through the "
                    "graduation path (Part C.1), not legacy_reconcile."
                )
            if (
                int(getattr(existing_index, "corpus_identity_version", 0) or 0) != 0
                or (getattr(existing_index, "worktree_lineage_key", "") or "")
            ):
                return _c2_refused(
                    "The selected handle already carries corpus-identity "
                    "fields; C.2 applies only to handles proven fieldless "
                    "at call start. Modern duplicates reconcile through the "
                    "supersession path automatically."
                )
            if wt_evidence.lineage_state != "confirmed":
                return _c2_refused(
                    "Git lineage could not be confirmed for this path, and "
                    "retirement requires hard Git-verified proof. Re-run "
                    "once Git evidence is available."
                )
            if mode_str == "report":
                # jdoc#88 QA-03: report is proof-only and diverts BEFORE the
                # refresh — the shipped path refreshed first, rewriting the
                # legacy index whenever source files changed, exactly when
                # the documented read-only guarantee mattered. The read-only
                # resolver proves from stored snapshots + live Git evidence.
                return _report_legacy_reconcile(
                    store, owner, repo_name, repo_id, folder_path,
                    wt_evidence, call_selection, existing_index, t0,
                )
            legacy_ctx = {
                "store": store, "owner": owner, "repo_name": repo_name,
                "repo_id": repo_id, "folder_path": folder_path,
                "wt_evidence": wt_evidence, "call_selection": call_selection,
                "mode": mode_str, "t0": t0,
            }
            # Under C.2 intent the selected handle stays FIELDLESS: backfill
            # is the ordinary refresh's job (LC2-01), and writing identity
            # fields here would make a retry after a mid-flight failure
            # (cleanup_incomplete, conflict) flunk the fieldless-at-call-start
            # gate. Certification (head_sha/sha_certified) still refreshes —
            # the proof needs it; identity fields are not certification.
            lineage_kwargs = {}

        def _wt_decision() -> "object":
            candidates = filter_lineage_candidates(
                store.list_repos(), wt_evidence, allow_containment=False
            )
            # The requesting root's own same-root indexes were handled by the
            # Item A block; exclude the target handle itself defensively.
            candidates = [c for c in candidates if c.get("repo") != repo_id]
            return resolve_worktree_corpus(
                ResolutionRequest(
                    tool="index_local",
                    evidence=wt_evidence,
                    selection=call_selection,
                    branch_local=branch_local,
                ),
                candidates,
            )

        if existing_index is None and not branch_local and wt_evidence.in_git:
            decision = _wt_decision()
            if decision.status == "reusable":
                # PRD 9.3: reuse returns the established handle. It never
                # refreshes, retargets, or writes anything.
                latency_ms = int((time.perf_counter() - t0) * 1000)
                return {
                    "success": True,
                    "repo": decision.established_handle,
                    "reused_established_handle": True,
                    "requested_path": str(folder_path),
                    "message": (
                        "An established index for this documentation corpus "
                        "already exists in a linked worktree and is proven "
                        "fresh; it was reused. No index was created."
                    ),
                    "worktree_resolution": decision.to_public(did_write=False),
                    "_meta": {"latency_ms": latency_ms},
                }
            if decision.status in ("reference_only", "related", "ambiguous", "unknown"):
                out = {
                    "success": False,
                    "error": decision.reason_code,
                    "worktree_resolution": decision.to_public(did_write=False),
                }
                if decision.next_action:
                    out["hint"] = decision.next_action
                return out
            # decision.status == "created": creation may proceed under claim.

        # jdoc#80 Part B (B1/B3): quarantine on failed Git verification. When
        # both common-dir probes were UNAVAILABLE (timeout/missing/OS error —
        # not a clean not-a-repo answer) and we are creating a new index, the
        # index is stamped provisional (authority-free; no lineage key; NO
        # graduation path in Part B). Before creating, enforce the per-root cap
        # (B3): a large pile of provisional indexes for one source_root is an
        # anomaly, so creation beyond the cap fails closed and loud rather than
        # accreting silently.
        # jdoc#80 Part C: graduation. A provisional index being FULLY refreshed
        # (no `paths` subset, not branch-local) while Git lineage is now
        # CONFIRMED may graduate in place, reconcile to an established index, or
        # (ambiguous/diverged) stay provisional and fail closed (§4.2). Subset
        # refreshes and still-unverifiable Git never graduate (I1/I3).
        graduating = False
        reconciliation_disclosure = None
        if (
            existing_index is not None
            and getattr(existing_index, "reconciliation_state", "") == RECONCILIATION_PROVISIONAL
            and not paths
            and not branch_local
            and wt_evidence.lineage_state == "confirmed"
        ):
            grad = _resolve_graduation(
                store, owner, repo_name, repo_id, folder_path,
                existing_index, wt_evidence, call_selection, t0,
            )
            if grad["return"] is not None:
                return grad["return"]
            graduating = bool(grad["graduate"])
            reconciliation_disclosure = grad["disclosure"]

        provisional_state = ""
        if existing_index is not None:
            # A refresh carries forward the existing reconciliation state,
            # UNLESS this refresh graduates the index (Part C), which clears it.
            provisional_state = (
                "" if graduating
                else getattr(existing_index, "reconciliation_state", "") or ""
            )
        elif wt_evidence.verification_failed:
            existing_provisional = count_provisional_for_root(
                store.list_repos(), str(folder_path)
            )
            if existing_provisional >= PROVISIONAL_PER_ROOT_CAP:
                return {
                    "success": False,
                    "error": REASON_PROVISIONAL_CAP,
                    "requested_path": str(folder_path),
                    "provisional_count": existing_provisional,
                    "provisional_cap": PROVISIONAL_PER_ROOT_CAP,
                    "hint": (
                        "Git lineage could not be verified for this path and the "
                        "number of provisional (reconciliation-pending) indexes "
                        "for this source root has reached the cap. Resolve the Git "
                        "verification failure (the git binary, a timeout, or "
                        "permissions), or delete_index the stale provisional "
                        "indexes. No new index was created."
                    ),
                }
            provisional_state = RECONCILIATION_PROVISIONAL

        # jdoc#81: about to create a brand-new index — close the concurrent-
        # create race with an atomic claim. The loser of the race routes to the
        # winner's handle instead of creating a duplicate physical index.
        # jdoc#83: worktree-translated identities contend on ONE claim keyed by
        # lineage + relative location + selection, so two worktrees racing to
        # create the same logical corpus still produce a single winner;
        # branch-local and non-Git creation keep the exact-path key.
        if existing_index is None:
            wt_key = None if branch_local else worktree_claim_key(
                wt_evidence, call_selection
            )
            key = wt_key or claim_key(root_key, call_selection)
            acquired, existing_claim = try_claim(
                store.base_path, key, repo_id, root_key, call_selection,
                index_exists=lambda r: store.load_index(
                    r.partition("/")[0], r.partition("/")[2]
                ) is not None,
            )
            if acquired:
                claim_token = key
                claim_store = store
                # jdoc#83 R11: resolve AGAIN while holding the claim — a
                # competitor may have established the corpus between the
                # first resolution and the claim. A changed decision returns
                # the new result; creation proceeds only if still permitted.
                if not branch_local and wt_evidence.in_git:
                    recheck = _wt_decision()
                    if recheck.status != "created":
                        release_claim(store.base_path, claim_token)
                        claim_token = None
                        if recheck.status == "reusable":
                            latency_ms = int((time.perf_counter() - t0) * 1000)
                            return {
                                "success": True,
                                "repo": recheck.established_handle,
                                "reused_established_handle": True,
                                "requested_path": str(folder_path),
                                "message": (
                                    "An established index for this corpus was "
                                    "created concurrently and is proven fresh; "
                                    "it was reused. No index was created."
                                ),
                                "worktree_resolution": recheck.to_public(did_write=False),
                                "_meta": {"latency_ms": latency_ms},
                            }
                        out = {
                            "success": False,
                            "error": recheck.reason_code,
                            "worktree_resolution": recheck.to_public(did_write=False),
                        }
                        if recheck.next_action:
                            out["hint"] = recheck.next_action
                        return out
            elif existing_claim is None:
                # jdoc#82 invariant 1: a claim exists but its ownership payload
                # is not readable — a winner is mid-creation and its identity is
                # unknown. Creating anyway is exactly the two-physical-indexes
                # race; refuse with no persistent write.
                return {
                    "success": False,
                    "error": "corpus_creation_in_progress",
                    "requested_handle": repo_id,
                    "hint": (
                        "Another process is currently creating an index for "
                        "this documentation source. Retry shortly, or call "
                        "doc_resolve_repo to find the established handle once "
                        "creation completes. No new index was created."
                    ),
                }
            elif existing_claim.get("repo") not in ("", repo_id):
                claimed_repo = existing_claim["repo"]
                if explicit_name:
                    return {
                        "success": False,
                        "error": "corpus_already_indexed",
                        "requested_handle": repo_id,
                        "established_handle": claimed_repo,
                        "hint": (
                            f"This documentation source is already established "
                            f"(or being created) as '{claimed_repo}'. Refresh it "
                            f"through that handle, or delete_index it first. "
                            f"No new index was created."
                        ),
                    }
                reuse_fields = {
                    "reused_established_handle": True,
                    "requested_handle": repo_id,
                    "established_handle": claimed_repo,
                }
                repo_name = claimed_repo.partition("/")[2] or repo_name
                repo_id = f"{owner}/{repo_name}"
                existing_index = store.load_index(owner, repo_name)

        # Read all discovered files
        current_files: dict = {}
        for file_path in doc_files:
            if not validate_path(folder_path, file_path):
                continue
            try:
                rel_path = file_path.relative_to(folder_path).as_posix()
            except ValueError:
                continue
            try:
                if file_path.suffix.lower() in OFFICE_EXTENSIONS:
                    # Binary office document: convert to Markdown locally
                    # (parser/office.py); the converted text becomes the
                    # stored/indexed representation, same as other
                    # transformed formats (.ipynb/.html/...).
                    content = convert_office(
                        file_path, cache_dir=office_cache_dir(store.base_path)
                    )
                else:
                    # newline="" preserves CRLF/CR so byte offsets and hashes
                    # address the real on-disk bytes, matching the GitHub leg
                    # and the disk file (#52). Path.read_text lacks newline
                    # before 3.13, so use open(). errors="replace" stays for
                    # invalid UTF-8.
                    with open(file_path, encoding="utf-8", errors="replace", newline="") as fh:
                        content = fh.read()
                parsed_content = preprocess_content(content, rel_path)
                current_files[rel_path] = parsed_content
            except Exception as e:
                warnings.append(f"Failed to read {file_path}: {e}")
                if not paths:
                    _count_skip(walk_skip_counts, "read_error")

        final_git_state = (local_git_head(folder_path), False)
        head_sha, source_dirty = stable_local_git_state(initial_git_state, final_git_state)
        cert_paths = set(current_files.keys())
        if existing_index is not None:
            cert_paths.update(existing_index.doc_paths)
        if local_git_paths_dirty(folder_path, cert_paths):
            source_dirty = True
        paths_tracked = local_git_paths_tracked(folder_path, current_files.keys())
        if head_sha and not paths_tracked:
            source_dirty = True
        sha_certified = bool(head_sha and not source_dirty and paths_tracked)

        # --- jdoc#109: embedding-model rotation forces a full re-embed ---
        # ⚠⚠ This has to sit BEFORE the incremental branch, not inside it. The
        # reported repro rotates the model and touches no file, so the branch
        # below returns "No changes detected" without ever calling
        # embed_sections — the stale 384-dim sidecar survives under a 768-dim
        # encoder and every later search dies on the product. Escalating to the
        # full path is what actually re-embeds, and it already passes
        # prune=True, which is the only caller that purges the old identity.
        embedding_rotation_fields: dict = {}
        if use_embeddings and existing_index is not None:
            from ..embeddings import cache as _emb_cache
            from ..embeddings import provider as _emb_provider

            # ⚠ Read the identity from the provider module, NOT the name bound
            # into this module at import. `embed_sections` writes the sidecar
            # header from the provider module's own view, so reading it from
            # anywhere else lets the detector disagree with the writer and
            # report a rotation that never happened.
            # ⚠ jdoc#126: `sidecar_identity`, not `_provider_identity` — the
            # writer applies the FastEmbed alias and a reader that skips it
            # sees a rotation on every index for a corpus that never moved.
            _sidecar_identity = _emb_provider.sidecar_identity
            _active_provider = _emb_provider.get_provider_name() or ""
            if _active_provider:
                _active_provider, _active_model, _active_dim = _sidecar_identity(
                    _active_provider
                )
                # jdoc#111: the embed char cap is part of the identity, so
                # raising JDOCMUNCH_EMBED_CHARS escalates exactly like a model
                # change. Without this, a cap change on an unchanged corpus
                # would report success and keep the short-text vectors.
                _active_chars = _emb_provider._embed_chars()
                _stored = _emb_cache.identity(storage_path, owner, repo_name)
                if _stored is not None and not _emb_cache.identity_matches(
                    _stored, _active_provider, _active_model, _active_dim,
                    _active_chars,
                ):
                    # ⚠⚠ A paid cloud provider is NOT auto-escalated. This path
                    # is reachable from `watch.py`, a background daemon that
                    # calls index_local on every file-change batch and prints
                    # only "re-indexed N file(s)" — so an unattended service
                    # would re-send the whole corpus to a billed third party
                    # and the disclosure below would never reach a human.
                    # Same reasoning as jcm's `refresh` forcing summaries off:
                    # a scheduled job must not bill unasked. Identity can also
                    # flip with no user action at all (an openai-compatible
                    # endpoint restarted on another model reprobes a new dim),
                    # so "they changed the model, they must want this" does not
                    # hold. `JDOCMUNCH_ALLOW_PAID_EMBEDDINGS` is the existing,
                    # documented consent signal — reuse it rather than minting
                    # a second one. `--rebuild` stays the explicit override.
                    _is_paid = (
                        _active_provider
                        in _emb_provider._PAID_CLOUD_EMBEDDING_PROVIDERS
                        and not _emb_provider._paid_embeddings_allowed()
                    )
                    embedding_rotation_fields = {
                        "embedding_rotation": {
                            "from": _stored,
                            "to": {
                                "provider": _active_provider,
                                "model": _active_model,
                                "dim": _active_dim,
                                "embed_chars": _active_chars,
                            },
                            "action": "rebuild_required" if _is_paid
                            else "full_re_embed",
                        }
                    }
                    if _is_paid:
                        # Left stale on purpose: the vectors are the wrong
                        # width, which search degrades and discloses, and that
                        # is recoverable. Silently spending is not.
                        embedding_rotation_fields["embedding_rotation"]["reason"] = (
                            f"{_active_provider} is a paid cloud provider; "
                            "re-embedding the corpus was not performed "
                            "automatically"
                        )
                        embedding_rotation_fields["embedding_rotation"]["fix"] = (
                            "re-index with --rebuild (or incremental=false), or "
                            "set JDOCMUNCH_ALLOW_PAID_EMBEDDINGS=1 to allow "
                            "automatic re-embedding"
                        )
                        use_embeddings = False
                    else:
                        # A re-embed of the whole corpus is a real cost, so it
                        # is disclosed above rather than done quietly.
                        incremental = False

        # --- Incremental path ---
        if incremental and existing_index is not None:
            changed, new, deleted = store.detect_changes(owner, repo_name, current_files)

            # jdoc#31: `paths` narrows current_files to a subset, so the
            # corpus-wide diff above marks every unlisted indexed file as
            # deleted. Rescope deletions to what the caller actually listed:
            # an indexed file is deleted only when a requested entry covers it
            # (exact file, or under a listed directory) and it was not read
            # back from disk. Unlisted files are never pruned.
            if paths:
                old_files = set(existing_index.file_hashes)
                if any(req in ("", ".") for req in requested_rels):
                    covered = old_files  # the root itself was listed
                else:
                    covered = {
                        fp for fp in old_files
                        if any(fp == req or fp.startswith(req + "/")
                               for req in requested_rels)
                    }
                deleted = sorted(covered - set(current_files))

            # jdoc#81: a full-corpus refresh (re)asserts the durable selection;
            # a subset-scoped `paths` refresh never redefines it.
            selection_kwargs = {} if paths else {"corpus_selection": call_selection}
            # jdoc#116: persist the patterns alongside the descriptor. Storing
            # only the digest is what made this defect unfixable-in-place: the
            # descriptor records THAT the corpus was shaped and never HOW, so
            # nothing downstream could reapply the exclusion. Written on the
            # same `not paths` branch as the descriptor, so the two can never
            # disagree about whether this call redefined the selection.
            if not paths:
                selection_kwargs["corpus_shape_patterns"] = sorted(
                    {p for p in (extra_ignore_patterns or []) if isinstance(p, str) and p}
                )
            # jdoc#80 Part C: a graduating full refresh clears the provisional
            # flag (the incremental save otherwise carries it forward). The
            # `not paths` graduation gate means this only fires on a full
            # refresh, where lineage_kwargs are also written below.
            if graduating:
                selection_kwargs["reconciliation_state"] = ""
            # jdoc#83: a full-corpus refresh of the established handle also
            # backfills/validates the worktree identity metadata (PRD 11 —
            # backfill happens on explicit refresh, never during discovery).
            if not paths and lineage_kwargs:
                selection_kwargs.update(lineage_kwargs)
            # jdoc#82 invariant 4: when a corpus-shaping input changed the
            # durable selection, the identity change is reconciled and
            # DISCLOSED — stored coverage never shifts under an unchanged
            # identity. (Legacy "" normalizes to "full": backfill is silent.)
            selection_changed_fields: dict = {}
            if not paths:
                stored_sel = getattr(existing_index, "corpus_selection", "") or "full"
                if stored_sel != call_selection:
                    selection_changed_fields = {
                        "corpus_selection_changed": {
                            "from": stored_sel,
                            "to": call_selection,
                        }
                    }
                    # jdoc#116: an index written before patterns were persisted
                    # carries `full+shape:<hash>` with nothing to reapply. We
                    # cannot inherit it, so this call widens the corpus exactly
                    # as before — but silence here is what the issue is about.
                    # Say that the shape is unrecoverable and name the remedy.
                    if (
                        extra_ignore_patterns is None
                        and "+shape:" in stored_sel
                        and not getattr(existing_index, "corpus_shape_patterns", None)
                    ):
                        warnings.append(
                            f"This index was shaped ({stored_sel}) by an older "
                            "version that recorded the descriptor but not the "
                            "patterns, so the exclusion cannot be reapplied "
                            "automatically and this refresh has WIDENED the "
                            "corpus. Re-run once with the patterns to make them "
                            "durable: index_local(extra_ignore_patterns=[...]) "
                            "or --extra-ignore-pattern on the CLI."
                        )
            if not changed and not new and not deleted:
                updated = existing_index
                if (
                    normalize_commit_sha(existing_index.head_sha) != head_sha
                    or bool(existing_index.source_dirty) != bool(source_dirty)
                    or bool(existing_index.sha_certified) != bool(sha_certified)
                    or getattr(existing_index, "source_root", "") != str(folder_path)
                    or (not paths and getattr(existing_index, "corpus_selection", "") != call_selection)
                    or (
                        not paths
                        and bool(lineage_kwargs)
                        and getattr(existing_index, "worktree_lineage_key", "")
                        != lineage_kwargs.get("worktree_lineage_key", "")
                    )
                ):
                    updated = store.incremental_save(
                        owner=owner, name=repo_name,
                        changed_files=[], new_files=[], deleted_files=[],
                        new_sections=[], raw_files={}, doc_types={},
                        head_sha=head_sha,
                        source_dirty=source_dirty,
                        sha_certified=sha_certified,
                        source_root=str(folder_path),
                        **selection_kwargs,
                    ) or existing_index
                latency_ms = int((time.perf_counter() - t0) * 1000)
                nochange_result: dict = {
                    "success": True,
                    "message": "No changes detected",
                    "repo": f"{owner}/{repo_name}",
                    "folder_path": str(folder_path),
                    "incremental": True,
                    # jdoc#109: the full-rebuild payload has always reported
                    # this and the incremental ones did not, so a caller could
                    # not tell "embeddings are fine" from "embeddings were
                    # never touched". Absence is not a status.
                    "semantic_search": bool(use_embeddings) and get_provider_name() is not None,
                    "changed": 0, "new": 0, "deleted": 0,
                    "_meta": {"latency_ms": latency_ms},
                }
                nochange_result.update(derivation_fields)
                nochange_result.update(reuse_fields)
                # jdoc#109: a gated paid rotation returns HERE, not through the
                # full path. Without this the one payload that most needs the
                # disclosure — "nothing changed, and your vectors are stale" —
                # would be the one that omits it.
                nochange_result.update(embedding_rotation_fields)
                _attach_reconciliation_outcome(
                    nochange_result, graduating, reconciliation_disclosure
                )
                nochange_result.update(selection_changed_fields)
                _add_commit_fields(nochange_result, updated)
                # jdoc#15: report truncation even when nothing changed,
                # since the visible-corpus boundary is unchanged.
                if discovered_count > max_files:
                    nochange_result["truncated"] = True
                    nochange_result["discovered"] = discovered_count
                    nochange_result["indexed"] = len(doc_files)
                else:
                    nochange_result["truncated"] = False
                return _finish_legacy_reconcile(nochange_result, legacy_ctx)

            files_to_parse = set(changed) | set(new)
            new_sections = []
            raw_subset: dict = {}
            doc_types: dict = {}

            for rel_path in files_to_parse:
                content = current_files[rel_path]
                raw_subset[rel_path] = content
                ext = rel_path.rsplit(".", 1)[-1].lower() if "." in rel_path else ""
                try:
                    sections = parse_file(content, rel_path, repo_id)
                    if sections:
                        new_sections.extend(sections)
                        doc_types[f".{ext}"] = doc_types.get(f".{ext}", 0) + 1
                except Exception as e:
                    warnings.append(f"Failed to parse {rel_path}: {e}")

            new_sections = summarize_sections(new_sections, use_ai=use_ai_summaries)
            _annotate_roles(new_sections)
            if use_embeddings:
                new_sections = embed_sections(
                    new_sections,
                    owner=owner, name=repo_name, storage_path=storage_path,
                )

            updated = store.incremental_save(
                owner=owner, name=repo_name,
                changed_files=changed, new_files=new, deleted_files=deleted,
                new_sections=new_sections, raw_files=raw_subset, doc_types=doc_types,
                head_sha=head_sha,
                source_dirty=source_dirty,
                sha_certified=sha_certified,
                source_root=str(folder_path),
                **selection_kwargs,
            )

            # jdoc#117: rebuild the derived sidecars here too. They used to run
            # on the full-index path ONLY, so on any incrementally-refreshed
            # index get_related_sections answered from whenever the last FULL
            # index ran -- unbounded staleness, and silent because this path
            # never attempted the write.
            #
            # The persisted section dicts carry no body text (Section.to_dict
            # drops it), so hydrate through the store's byte-range loader before
            # building; the in-memory raw text for files touched THIS run is not
            # on disk under the old byte offsets yet, so it shadows the loader.
            sidecar_skips: dict = {}
            if updated is not None:
                try:
                    reloaded = store.load_index(owner, repo_name)
                    _loader = getattr(reloaded, "_content_loader", None) if reloaded else None

                    def _content_for(sec: dict) -> str:
                        buf = raw_subset.get(sec.get("doc_path", ""))
                        start = int(sec.get("byte_start", 0))
                        end = int(sec.get("byte_end", 0))
                        if buf is not None and end > start:
                            return buf[start:end]
                        if _loader is None:
                            return ""
                        try:
                            return _loader(sec.get("doc_path", ""), start, end) or ""
                        except Exception:
                            return ""

                    sidecar_skips = _write_sidecars(
                        storage_path, owner, repo_name,
                        _sidecar_view(updated.sections, content_for=_content_for),
                    )
                except Exception as _e:
                    sidecar_skips = {
                        "all": {"reason": "error", "detail": str(_e)[:200]}
                    }

            latency_ms = int((time.perf_counter() - t0) * 1000)
            result = {
                "success": True,
                "repo": f"{owner}/{repo_name}",
                "folder_path": str(folder_path),
                "incremental": True,
                "changed": len(changed), "new": len(new), "deleted": len(deleted),
                "section_count": len(updated.sections) if updated else 0,
                "indexed_at": updated.indexed_at if updated else "",
                "semantic_search": use_embeddings and get_provider_name() is not None,
                "_meta": {"latency_ms": latency_ms},
            }
            # jdoc#107: a coverage collapse must be visible in the response.
            _attach_embedding_coverage(
                result, storage_path=storage_path, owner=owner,
                name=repo_name, index=updated, warnings=warnings,
            )
            result.update(derivation_fields)
            result.update(reuse_fields)
            # jdoc#109: a gated paid rotation with changed files lands here.
            result.update(embedding_rotation_fields)
            result.update(selection_changed_fields)
            # jdoc#117: same disclosure as the full path.
            if sidecar_skips:
                result["sidecars_skipped"] = sidecar_skips
                if sidecar_skips.get("dedup"):
                    result["dedup_skipped"] = sidecar_skips["dedup"]
            _attach_reconciliation_outcome(
                result, graduating, reconciliation_disclosure
            )
            _add_commit_fields(result, updated)
            # jdoc#15: surface truncation on the incremental path too.
            if discovered_count > max_files:
                result["truncated"] = True
                result["discovered"] = discovered_count
                result["indexed"] = len(doc_files)
                warnings.append(
                    f"max_files cap hit: indexed {len(doc_files)} of "
                    f"{discovered_count} discovered files. Raise max_files "
                    f"to capture the rest."
                )
            else:
                result["truncated"] = False
            if warnings:
                result["warnings"] = warnings
            return _finish_legacy_reconcile(result, legacy_ctx)

        # --- Full index path ---
        all_sections = []
        doc_types = {}
        raw_files: dict = {}
        parsed_files = []
        # v1.103.0: files that were read and parsed but yielded zero sections
        # are invisible to search — an absence claim must disclose them.
        no_sections_count = 0

        for rel_path, content in current_files.items():
            ext = f".{rel_path.rsplit('.', 1)[-1].lower()}" if "." in rel_path else ""
            try:
                sections = parse_file(content, rel_path, repo_id)
                if sections:
                    all_sections.extend(sections)
                    doc_types[ext] = doc_types.get(ext, 0) + 1
                    raw_files[rel_path] = content
                    parsed_files.append(rel_path)
                else:
                    no_sections_count += 1
            except Exception as e:
                warnings.append(f"Failed to parse {rel_path}: {e}")
                no_sections_count += 1

        if not all_sections:
            if claim_token and claim_store is not None:
                release_claim(claim_store.base_path, claim_token)
            return {"success": False, "error": "No sections extracted from files"}

        all_sections = summarize_sections(all_sections, use_ai=use_ai_summaries)
        _annotate_roles(all_sections)
        if use_embeddings:
            # jdoc#107: prune ONLY here. This pass holds the whole corpus, so
            # the sidecar it writes is authoritative and stale vectors for
            # removed sections should go. The incremental path above must NOT
            # prune — its `sections` is just the changed documents.
            all_sections = embed_sections(
                all_sections,
                owner=owner, name=repo_name, storage_path=storage_path,
                prune=True,
            )

        # v1.103.0: coverage contract from the full discovery walk. Recorded
        # only when the walk covered the whole corpus (no explicit `paths`);
        # a full re-walk overwrites the prior block (self-heals), incremental
        # saves carry it forward unchanged. Empty = unknown, never fabricated.
        coverage_block: dict = {}
        if not paths:
            from datetime import datetime, timezone
            coverage_block = {
                "walk": "full",
                "files_indexed": len(parsed_files),
                "no_sections_count": no_sections_count,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
            nonzero_skips = {k: v for k, v in walk_skip_counts.items() if v}
            if nonzero_skips:
                coverage_block["skip_counts"] = nonzero_skips

        # jdoc#62: persist the core index BEFORE the optional sidecars. The
        # sidecars (the related-graph build especially) are best-effort
        # enrichment; a slow or failing one must never delay or block the
        # index that retrieval actually needs.
        saved = store.save_index(
            owner=owner,
            name=repo_name,
            sections=all_sections,
            raw_files=raw_files,
            doc_types=doc_types,
            head_sha=head_sha,
            source_dirty=source_dirty,
            sha_certified=sha_certified,
            source_root=str(folder_path),
            corpus_selection=call_selection,
            # jdoc#116: the CREATE path needs this as much as the refresh path.
            # Persisting only on refresh would mean a corpus shaped at creation
            # (the common case, and the reporter's) still had nothing to
            # inherit, so the very first CLI re-index would widen it anyway.
            corpus_shape_patterns=sorted(
                {p for p in (extra_ignore_patterns or []) if isinstance(p, str) and p}
            ),
            reconciliation_state=provisional_state,
            coverage=coverage_block or None,
            **lineage_kwargs,
        )

        # jdoc#117: all four derived sidecars, in one place, each reporting its
        # own skip. See _write_sidecars for why the bare passes are gone.
        sidecar_skips = _write_sidecars(
            storage_path, owner, repo_name, _sidecar_view(all_sections)
        )
        dedup_skipped = sidecar_skips.get("dedup")

        # v1.29.0: opt-in autotune. Runs the v1.23 weight tuner on this
        # repo's accumulated ranking events; no-op when telemetry isn't
        # enabled. Failures swallowed.
        autotune_result = None
        if autotune:
            try:
                from .tune_weights import tune_weights as _tune_weights
                autotune_result = _tune_weights(
                    repo=f"{owner}/{repo_name}",
                    storage_path=storage_path,
                )
            except Exception:
                autotune_result = None

        latency_ms = int((time.perf_counter() - t0) * 1000)
        result = {
            "success": True,
            "repo": f"{owner}/{repo_name}",
            "folder_path": str(folder_path),
            "indexed_at": saved.indexed_at,
            "file_count": len(parsed_files),
            "section_count": len(all_sections),
            "doc_types": doc_types,
            "files": parsed_files[:20],
            "semantic_search": use_embeddings and get_provider_name() is not None,
            "_meta": {"latency_ms": latency_ms},
        }
        # jdoc#107: same disclosure on the full path. A prune that wrote fewer
        # vectors than sections is exactly as invisible here.
        _attach_embedding_coverage(
            result, storage_path=storage_path, owner=owner,
            name=repo_name, index=saved, warnings=warnings,
        )
        result.update(derivation_fields)
        result.update(reuse_fields)
        # jdoc#109: say that the run escalated and why. A caller who asked for
        # an incremental refresh and silently got a full corpus re-embed has
        # been billed for something it did not request.
        result.update(embedding_rotation_fields)
        # jdoc#82 invariant 4 disclosure on the full-replace path too.
        if existing_index is not None:
            _stored_sel = getattr(existing_index, "corpus_selection", "") or "full"
            if _stored_sel != call_selection:
                result["corpus_selection_changed"] = {
                    "from": _stored_sel,
                    "to": call_selection,
                }
        _add_commit_fields(result, saved)
        if autotune_result is not None:
            result["autotune"] = autotune_result
        # jdoc#103: disclose a skipped near-duplicate sidecar. Additive key,
        # omitted when the sidecar ran, per the omit-when-empty convention.
        if dedup_skipped:
            result["dedup_skipped"] = dedup_skipped
        # jdoc#117: the other three sidecars disclose failure the same way.
        # `dedup_skipped` is RETAINED above rather than folded into this block:
        # 1.x forbids removing a response key that already ships.
        if sidecar_skips:
            result["sidecars_skipped"] = sidecar_skips

        # jdoc#80 Part B (B1): disclose reconciliation quarantine. A provisional
        # index was created because Git lineage could not be verified; it is
        # authority-free and reconciliation-pending (no graduation ships in
        # Part B). Surfaced as a structured block, not just a note string.
        if getattr(saved, "reconciliation_state", "") == RECONCILIATION_PROVISIONAL:
            result["reconciliation"] = {
                "state": RECONCILIATION_PROVISIONAL,
                "reason_code": REASON_PROVISIONAL_CREATED,
                "detail": (
                    "Git lineage could not be verified for this path (the Git "
                    "common-directory probe was unavailable, not a clean "
                    "not-a-repository answer), so this index is marked "
                    "provisional and reconciliation-pending. It will not be "
                    "reused as an established corpus by linked-worktree calls "
                    "until it is reconciled. Re-run once Git is reachable to "
                    "index it normally."
                ),
            }
        else:
            # jdoc#80 Part C: a full (non-incremental) refresh that graduated a
            # previously provisional index reaches this create/replace path;
            # surface the graduated / ambiguous / diverged outcome.
            _attach_reconciliation_outcome(
                result, graduating, reconciliation_disclosure
            )

        # jdoc#80 Part B (B2): disclose a pre-1.102 sibling. When a fresh index
        # was created and an older (identity-fieldless) index for a plausibly
        # equivalent corpus exists, the duplicate is no longer silent — the
        # caller can reindex the legacy corpus to bring it into the lineage
        # system. Non-blocking hint.
        if existing_index is None:
            _legacy = legacy_sibling_handles(store.list_repos(), str(folder_path))
            if _legacy:
                result["legacy_index_present"] = {
                    "handles": _legacy,
                    "detail": (
                        "One or more existing indexes (created before the "
                        "corpus-identity fields) may cover this same "
                        "documentation corpus from a different worktree or "
                        "path. A separate index was created here. To bring the "
                        "older index into the lineage system, re-run "
                        "index_local against its path."
                    ),
                }

        # jdoc#15: surface truncation as structured top-level fields so
        # callers can detect it programmatically, not just from a free-text
        # note string. `truncated` is False when the corpus fit entirely
        # under the cap; True when the cap was hit. `discovered` is the
        # full match count (capped at max_files * safety ceiling).
        if discovered_count > max_files:
            result["truncated"] = True
            result["discovered"] = discovered_count
            result["indexed"] = len(doc_files)
            warnings.append(
                f"max_files cap hit: indexed {len(doc_files)} of "
                f"{discovered_count} discovered files. Raise max_files to "
                f"capture the rest."
            )
            result["note"] = (
                f"Folder has many files; indexed first {max_files} of "
                f"{discovered_count}. Raise max_files to include the rest."
            )
        else:
            result["truncated"] = False

        if warnings:
            result["warnings"] = warnings

        return _finish_legacy_reconcile(result, legacy_ctx)

    except Exception as e:
        if claim_token and claim_store is not None:
            try:
                from ..storage.corpus_claims import release_claim as _release
                _release(claim_store.base_path, claim_token)
            except Exception:
                pass
        return {"success": False, "error": f"Indexing failed: {str(e)}"}
