"""Weighted section search returning summaries only."""

import time
from typing import Optional

from ..storage import DocStore
from ..storage.token_tracker import estimate_savings, record_savings, cost_avoided
from ..retrieval.projection import attach_snippets, project
from ..retrieval.verdict import (
    build_verdict,
    index_changed_since_load,
    index_coverage_meta,
    suggest_docs,
)


def search_sections(
    repo: Optional[str] = None,
    query: str = "",
    doc_path: Optional[str] = None,
    path_glob: Optional[str] = None,
    max_results: int = 10,
    semantic: Optional[bool] = None,
    semantic_only: bool = False,
    semantic_weight: Optional[float] = None,
    lexical_engine: str = "bm25",
    role: Optional[str] = None,
    profile: Optional[str] = None,
    dedupe: bool = False,
    repo_group: Optional[str] = None,
    min_answerability: Optional[float] = None,
    min_quotability: Optional[float] = None,
    min_level: Optional[int] = None,
    max_level: Optional[int] = None,
    tags: Optional[list] = None,
    exclude_tags: Optional[list] = None,
    roles: Optional[list] = None,
    exclude_roles: Optional[list] = None,
    min_byte_length: Optional[int] = None,
    max_byte_length: Optional[int] = None,
    compact: bool = False,
    fields: Optional[list] = None,
    snippet_bytes: int = 0,
    storage_path: Optional[str] = None,
) -> dict:
    """Search sections with BM25-style lexical + optional semantic fusion.

    Lexical scoring:
      title exact match:    +20
      title substring:      +10
      title word overlap:   +5 per word
      summary match:        +8 (substring), +2 per word
      tag match:            +3 per tag
      content word match:   +1 per word (capped at 5)

    Params:
      semantic:        None (auto — hybrid when embeddings exist), True (force
                       hybrid), False (force lexical-only).
      semantic_only:   Skip lexical; rank purely by embedding cosine similarity.
      semantic_weight: Weight (0.0–1.0) of semantic component in hybrid fusion.
                       None (default) = unset: use the repo's learned weight
                       from tuning.jsonc, else 0.5. jdoc#106: None is the ONLY
                       sentinel for unset, so passing 0.5 explicitly now means
                       0.5 and is no longer overridden by a learned weight.
                       `_meta.semantic_weight_source` reports which won.
      compact:         Drop per-row fields the caller cannot act on (jdoc#101).
      fields:          Explicit field whitelist; wins over compact. `id` always
                       survives.
      snippet_bytes:   Inline the first N bytes of each section's content so a
                       confident top hit needs no get_section round-trip.
    """
    t0 = time.perf_counter()

    # v1.32.0: task-aware retrieval profiles. Each profile maps to a small
    # role-boost bundle (sections matching listed roles get up-ranked among
    # the BM25 result candidates). Explicit role= always wins over the
    # profile's role-boost set; profile is a hint, not a hard filter.
    PROFILES = {
        "install":   {"boost_roles": {"how_to", "tutorial", "example"}},
        "debug":     {"boost_roles": {"troubleshooting", "faq", "example"}},
        "explain":   {"boost_roles": {"concept", "reference", "tutorial"}},
        "api":       {"boost_roles": {"api", "reference", "example"}},
    }
    profile_norm = (profile or "").strip().lower() or None
    profile_def = PROFILES.get(profile_norm) if profile_norm else None
    if profile_norm and profile_def is None:
        return {
            "error": f"Unknown profile: {profile!r}. "
                     f"Use one of: {sorted(PROFILES.keys())}.",
        }

    # v1.26.0: repo_group fan-out. When set, runs the query against each
    # constituent repo via this same function (single-repo mode), fuses
    # the result lists with Reciprocal Rank Fusion, and returns a
    # combined response. Per-repo errors are reported but never abort
    # the fan-out.
    if repo_group:
        from ..storage import repo_groups as _rg
        from ..retrieval.prune import reciprocal_rank_fusion

        member_repos = _rg.resolve(repo_group, base_path=storage_path)
        if not member_repos:
            return {
                "error": f"Repo group not found or empty: {repo_group!r}",
                "_meta": {"latency_ms": int((time.perf_counter() - t0) * 1000)},
            }

        per_repo: list[dict] = []
        rankings: list[list[str]] = []
        result_pool: dict[str, dict] = {}
        member_rebuilding = False
        for member in member_repos:
            sub = search_sections(
                repo=member, query=query,
                doc_path=doc_path,
                path_glob=path_glob,
                max_results=max(max_results, 10),
                semantic=semantic, semantic_only=semantic_only,
                semantic_weight=semantic_weight,
                lexical_engine=lexical_engine, role=role,
                # jdoc#101: snippets need the member's index to read content,
                # so they are produced member-side. Projection is NOT — it runs
                # once on the fused list below, where `repo` is still needed to
                # tell two members' rows apart.
                snippet_bytes=snippet_bytes,
                storage_path=storage_path,
            )
            _sub_meta = sub.get("_meta") or {}
            _entry = {
                "repo": member,
                "result_count": sub.get("result_count", 0),
                "error": sub.get("error"),
            }
            # jdoc#106: each member resolves its OWN weight (the tuner is
            # per-repo), so a group has no single number to report. Say it
            # per member or the group's ranking is unexplainable.
            if "semantic_weight" in _sub_meta:
                _entry["semantic_weight"] = _sub_meta["semantic_weight"]
                _entry["semantic_weight_source"] = _sub_meta.get(
                    "semantic_weight_source"
                )
            per_repo.append(_entry)
            # jdoc#93 (suite parity with jcm v1.108.169): each member's own
            # verdict is already correctly wired for the rebuilding rule, and
            # this fan-out used to discard them — so a zero-result GROUP search
            # could reach `absent` and mint a citable ref while ANY member index
            # was being rewritten underneath it. There is no single index to
            # re-stat here, so the group inherits its members' detection:
            # one rebuilding member makes the whole group's absence unprovable.
            _sub_v = (sub.get("_meta") or {}).get("verdict") or {}
            if (_sub_v.get("channels") or {}).get("index") == "rebuilding":
                member_rebuilding = True
            rows = sub.get("results") or []
            ranking = []
            for r in rows:
                sid = r.get("id")
                if sid:
                    ranking.append(sid)
                    result_pool.setdefault(sid, r)
            rankings.append(ranking)

        fused = reciprocal_rank_fusion(rankings, k=60)
        merged = []
        for sid, fused_score in fused[:max_results]:
            row = result_pool.get(sid)
            if row is not None:
                row = dict(row)
                row["_fused_score"] = float(fused_score)
                merged.append(row)

        merged = project(merged, compact=compact, fields=fields,
                         extra_keep=frozenset({"repo", "_fused_score"}))

        return {
            "repo_group": repo_group,
            "members": member_repos,
            "query": query,
            "results": merged,
            "result_count": len(merged),
            "per_repo": per_repo,
            "_meta": {
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "fusion": "rrf_k60",
                "lexical_engine": lexical_engine,
                "verdict": build_verdict(
                    result_count=len(merged),
                    semantic_requested=bool(semantic is True or semantic_only),
                    index_changed=member_rebuilding,
                ),
            },
        }

    if not repo:
        return {"error": "Either repo or repo_group is required."}

    store = DocStore(base_path=storage_path)
    owner, name = store._resolve_repo(repo)
    index = store.load_index(owner, name)

    if not index:
        return {"error": f"Repo not found: {repo}"}

    has_emb = index._has_embeddings()

    # v1.23.0: when the caller leaves semantic_weight unset, ask the tuner
    # for a per-repo learned override. Explicit caller values always win.
    # jdoc#106: the sentinel for "unset" is None. It used to be the literal
    # default 0.5, which made a deliberate 0.5 indistinguishable from an
    # omitted argument and silently handed it to the tuner.
    from ..retrieval.tuning import SEMANTIC_WEIGHT_BOUNDS, resolve_semantic_weight
    _weight = resolve_semantic_weight(
        f"{owner}/{name}", explicit=semantic_weight, base_path=storage_path
    )
    semantic_weight = _weight["weight"]

    if semantic_only:
        mode = "semantic_only" if has_emb else "lexical"
    elif semantic is False:
        mode = "lexical"
    elif has_emb and (semantic is True or semantic is None) and 0.0 < semantic_weight <= 1.0:
        mode = "hybrid"
    else:
        mode = "lexical"

    # v1.19.0: when a role filter is requested, ask for more candidates
    # up-front so post-filter trimming doesn't starve the result set.
    fetch_n = max_results * 5 if role else max_results
    try:
        # jdoc#32: path_glob is a candidate pre-filter inside the index
        # search (next to the doc_path equality check), NOT a post-filter
        # here — post-filtering the globally ranked top-k starved any
        # single-document glob whose sections didn't make the corpus-wide
        # cut. Ranking now happens within the glob-matched set.
        results = index.search(
            query,
            doc_path=doc_path,
            max_results=fetch_n,
            semantic=semantic,
            semantic_only=semantic_only,
            semantic_weight=semantic_weight,
            lexical_engine=lexical_engine,
            path_glob=path_glob,
        )
    except ValueError as exc:
        return {"error": str(exc), "_meta": {"lexical_engine": lexical_engine}}

    # v1.45.0: optional tags filter. AND semantics — section must
    # contain every listed tag. Tag matching is case-insensitive.
    if tags:
        wanted = {t.strip().lower() for t in tags if isinstance(t, str) and t.strip()}
        if wanted:
            results = [
                r for r in results
                if wanted.issubset({
                    str(t).strip().lower() for t in (r.get("tags") or [])
                })
            ]

    # v1.52.0: optional roles / exclude_roles filters (ANY-match).
    # `role` (singular, v1.19) is unchanged — it's a hard exact-match
    # post-filter applied later. These plural variants run on the
    # candidate list before that and can stack with each other.
    if roles:
        wanted_roles = {r.strip().lower() for r in roles
                        if isinstance(r, str) and r.strip()}
        if wanted_roles:
            results = [
                r for r in results
                if ((r.get("metadata") or {}).get("role") or "").strip().lower()
                in wanted_roles
            ]
    if exclude_roles:
        unwanted_roles = {r.strip().lower() for r in exclude_roles
                          if isinstance(r, str) and r.strip()}
        if unwanted_roles:
            results = [
                r for r in results
                if ((r.get("metadata") or {}).get("role") or "").strip().lower()
                not in unwanted_roles
            ]

    # v1.53.0: optional byte-length range filter. Drops stub sections
    # (one-line definitions) or oversized dumps. Computed from
    # byte_end - byte_start.
    if min_byte_length is not None or max_byte_length is not None:
        def _len_ok(r: dict) -> bool:
            bs = int(r.get("byte_start", 0) or 0)
            be = int(r.get("byte_end", 0) or 0)
            length = max(0, be - bs)
            if min_byte_length is not None and length < min_byte_length:
                return False
            if max_byte_length is not None and length > max_byte_length:
                return False
            return True
        results = [r for r in results if _len_ok(r)]

    # v1.51.0: optional exclude_tags filter. ANY-match semantics —
    # drop section if it contains any listed tag.
    if exclude_tags:
        unwanted = {t.strip().lower() for t in exclude_tags
                    if isinstance(t, str) and t.strip()}
        if unwanted:
            results = [
                r for r in results
                if not unwanted & {
                    str(t).strip().lower() for t in (r.get("tags") or [])
                }
            ]

    # v1.44.0: optional heading-level range filter. Restricts results
    # to sections whose `level` falls within [min_level, max_level].
    # min/max are inclusive; either may be omitted independently.
    if min_level is not None or max_level is not None:
        def _in_range(r: dict) -> bool:
            lvl = r.get("level")
            if not isinstance(lvl, int):
                return False
            if min_level is not None and lvl < min_level:
                return False
            if max_level is not None and lvl > max_level:
                return False
            return True
        results = [r for r in results if _in_range(r)]

    # v1.34.0: optional dedup pass — collapse cluster members to their
    # representative; record suppressed members for transparency. Runs
    # BEFORE role/profile filtering so the limit math stays right.
    deduped_map: dict[str, list[str]] = {}
    if dedupe:
        from ..retrieval.dedup import build_member_to_rep, load as _load_dupes
        clusters = _load_dupes(storage_path, owner, name)
        if clusters:
            mem_to_rep = build_member_to_rep(clusters)
            seen_reps: set[str] = set()
            kept = []
            for r in results:
                sid = r.get("id", "")
                rep = mem_to_rep.get(sid, sid)
                if rep in seen_reps:
                    deduped_map.setdefault(rep, []).append(sid)
                    continue
                seen_reps.add(rep)
                kept.append(r)
            results = kept

    if role:
        role_norm = role.strip().lower()
        results = [r for r in results
                   if (r.get("metadata") or {}).get("role") == role_norm][:max_results]
    elif profile_def:
        # Profile mode: stable-sort the candidate list so sections in the
        # boost set move ahead of sections that are not, while preserving
        # within-set ordering by BM25/RRF score.
        boost = profile_def["boost_roles"]
        in_boost = []
        out_boost = []
        for r in results:
            r_role = (r.get("metadata") or {}).get("role") or ""
            (in_boost if r_role in boost else out_boost).append(r)
        results = (in_boost + out_boost)[:max_results]
    else:
        results = results[:max_results]

    # v1.16.0: per-section freshness + retrieval confidence.
    from ..retrieval.freshness import FreshnessProbe
    from ..retrieval.confidence import (
        attach_confidence,
        ceiling_for_mode as _ceiling_for_mode,
    )

    probe = FreshnessProbe(store, owner, name, index)
    for sec in results:
        probe.annotate(sec)
    freshness_summary = probe.summary(results)

    # Calculate token savings: matched docs full bytes vs summary-only response
    matched_doc_paths = {r.get("doc_path") for r in results}
    raw_bytes = sum(
        len(s.get("content", "").encode("utf-8"))
        for s in index.sections
        if s.get("doc_path") in matched_doc_paths
    )
    # jdoc#101: response_bytes is measured on the SERVED rows, so it is
    # computed after projection/snippets at the bottom of this function —
    # not here, where the rows still carry fields the caller may never see.
    latency_ms = int((time.perf_counter() - t0) * 1000)
    meta = {
        "latency_ms": latency_ms,
        "sections_returned": len(results),
        "search_mode": mode,
    }
    if mode == "hybrid":
        meta["semantic_weight"] = semantic_weight
        # jdoc#106: the value alone does not tell a user whether retrieval is
        # running on their number, a learned one, or the stock default — and
        # that ambiguity is what made a bad weight read as bad embeddings.
        meta["semantic_weight_source"] = _weight["source"]
        if _weight["clamped"]:
            meta["semantic_weight_clamped_to"] = list(SEMANTIC_WEIGHT_BOUNDS)
    meta["lexical_engine"] = lexical_engine
    # jdoc#109: the stored vectors and the live encoder disagree on width, so
    # the semantic lane contributed nothing. Say so, and name the fix — this
    # used to surface as a raw numpy matmul error and read as a corrupt index.
    _width = getattr(index, "_embedding_width_mismatch", None)
    if _width:
        meta["embedding_stale"] = {
            **_width,
            "semantic_disabled": True,
            "reason": "stored embeddings were built by a different model",
            "fix": "re-index with --rebuild (or index_local(incremental=False)) "
                   "to re-embed the corpus under the active model",
        }
    meta["freshness"] = freshness_summary
    if index.head_sha:
        meta["head_sha"] = index.head_sha
    meta["source_dirty"] = bool(index.source_dirty)
    meta["sha_certified"] = bool(index.sha_certified)
    if role:
        meta["role_filter"] = role.strip().lower()
    if profile_norm:
        meta["profile"] = profile_norm
        meta["profile_boost_roles"] = sorted(profile_def["boost_roles"])
    if dedupe:
        meta["dedupe"] = True
        if deduped_map:
            meta["deduped"] = deduped_map
    if path_glob:
        meta["path_glob"] = path_glob
    if min_level is not None:
        meta["min_level"] = int(min_level)
    if max_level is not None:
        meta["max_level"] = int(max_level)
    if tags:
        meta["tags_filter"] = sorted({t.strip().lower() for t in tags if isinstance(t, str) and t.strip()})
    if exclude_tags:
        meta["exclude_tags_filter"] = sorted({
            t.strip().lower() for t in exclude_tags if isinstance(t, str) and t.strip()
        })
    if roles:
        meta["roles_filter"] = sorted({r.strip().lower() for r in roles
                                       if isinstance(r, str) and r.strip()})
    if exclude_roles:
        meta["exclude_roles_filter"] = sorted({r.strip().lower() for r in exclude_roles
                                               if isinstance(r, str) and r.strip()})
    if min_byte_length is not None:
        meta["min_byte_length"] = int(min_byte_length)
    if max_byte_length is not None:
        meta["max_byte_length"] = int(max_byte_length)
    # jdoc#106 follow-up: confidence's `strength` term reads a RAW top-1
    # score, and BM25 / RRF / cosine differ by three orders of magnitude.
    # Scoring them all on the BM25 curve made hybrid confidence ~7x too low,
    # which disqualified hybrid searches from absence evidence and drove the
    # weight tuner monotonically to its floor.
    attach_confidence(
        query, results, meta,
        score_ceiling=_ceiling_for_mode(mode),
    )

    # v1.33.0: per-result answerability + quotability scores. Read content
    # via the same byte-range lookup the BM25 engine uses (so we don't
    # re-load files we already touched during scoring).
    try:
        from ..retrieval.scoring import attach_scores

        def _loader(row: dict) -> str:
            sid = row.get("id")
            sec = index.get_section(sid) if sid else None
            if not sec:
                return ""
            return index._ensure_content(sec) if hasattr(index, "_ensure_content") else (sec.get("content") or "")

        for row in results:
            attach_scores(row, text_loader=_loader, query=query)
    except Exception:
        pass

    # v1.42.0: optional answerability/quotability gates. Applied after
    # attach_scores so the per-result fields are available. Sections
    # whose loader returned empty text get _answerability=0/quotability=0
    # via attach_scores; we honor that by filtering them out too when a
    # threshold is set.
    quality_filtered = 0
    if min_answerability is not None or min_quotability is not None:
        kept = []
        for row in results:
            ans = row.get("_answerability")
            quo = row.get("_quotability")
            if min_answerability is not None and (
                not isinstance(ans, (int, float)) or ans < min_answerability
            ):
                quality_filtered += 1
                continue
            if min_quotability is not None and (
                not isinstance(quo, (int, float)) or quo < min_quotability
            ):
                quality_filtered += 1
                continue
            kept.append(row)
        results = kept
        meta["sections_returned"] = len(results)
        if min_answerability is not None:
            meta["min_answerability"] = float(min_answerability)
        if min_quotability is not None:
            meta["min_quotability"] = float(min_quotability)
        if quality_filtered:
            meta["quality_filtered"] = quality_filtered

    # v1.23.0: append a ranking event for offline tuning.
    try:
        from ..storage.token_tracker import record_ranking_event
        scores = [r.get("_score") for r in results if isinstance(r.get("_score"), (int, float))]
        record_ranking_event(
            repo=f"{owner}/{name}",
            tool="search_sections",
            query=query,
            mode=mode,
            semantic_used=mode in ("hybrid", "semantic_only"),
            semantic_weight=semantic_weight,
            top1_score=scores[0] if len(scores) >= 1 else None,
            top2_score=scores[1] if len(scores) >= 2 else None,
            confidence=meta.get("confidence"),
            result_count=len(results),
            base_path=storage_path,
        )
    except Exception:
        pass

    # v1.28.0: opt-in retrieval-replay log capture (grep-friendly JSONL).
    try:
        from ..storage import replay_log
        scores = [r.get("_score") for r in results if isinstance(r.get("_score"), (int, float))]
        top1 = results[0] if results else None
        replay_log.append(
            repo=f"{owner}/{name}",
            query=query,
            mode=mode,
            semantic_used=mode in ("hybrid", "semantic_only"),
            semantic_weight=semantic_weight,
            top1_id=top1.get("id") if top1 else None,
            top1_score=scores[0] if scores else None,
            confidence=meta.get("confidence"),
            result_count=len(results),
            base_path=storage_path,
        )
    except Exception:
        pass
    if not has_emb and mode == "lexical":
        # The old tip named a flag and not the missing piece: on a default
        # install there is no provider, so use_embeddings=True alone does nothing.
        meta["tip"] = (
            "This index has no embeddings, so search matched words only. For semantic search: "
            'pip install "jdocmunch-mcp[fastembed]" (offline, no key), then re-index with '
            "index_local(path=..., use_embeddings=True)."
        )

    # Suite-parity honesty verdict. degraded = caller asked for semantic on an
    # index with no embeddings; absent = zero matches; low_confidence keys off
    # the confidence attach_confidence just computed.
    meta["verdict"] = build_verdict(
        result_count=len(results),
        confidence=meta.get("confidence"),
        semantic_requested=bool(semantic is True or semantic_only),
        semantic_available=has_emb,
        lexical_used=(mode != "semantic_only"),
        index_stale=bool(index.source_dirty),
        index_changed=index_changed_since_load(index),
        did_you_mean=suggest_docs(query, index.sections) if not results else None,
        # v1.103.0: absence discloses what index-time discovery excluded.
        # None for legacy indexes (coverage unknown, never fabricated);
        # attached only on absent/degraded states.
        coverage=index_coverage_meta(index),
    )

    # jdoc#101: snippets + projection, LAST. Every filter, attach_scores, the
    # ranking/replay logs and the verdict above read fields compact drops, so
    # this has to run downstream of all of them.
    if snippet_bytes and snippet_bytes > 0:
        def _snippet_loader(row: dict) -> str:
            sid = row.get("id")
            sec = index.get_section(sid) if sid else None
            if not sec:
                return ""
            return (index._ensure_content(sec)
                    if hasattr(index, "_ensure_content")
                    else (sec.get("content") or ""))

        attach_snippets(results, snippet_bytes=snippet_bytes,
                        loader=_snippet_loader)
        meta["snippet_bytes"] = int(snippet_bytes)

    if compact or fields:
        # snippet survives an explicit whitelist that asked for it; a caller
        # passing both fields= and snippet_bytes= wants the snippet.
        extra = frozenset({"snippet", "snippet_truncated"}) if snippet_bytes else frozenset()
        results = project(results, compact=compact, fields=fields,
                          extra_keep=extra)
        if compact:
            meta["compact"] = True
        if fields:
            meta["fields"] = [str(f) for f in fields
                              if isinstance(f, str) and f.strip()]

    response_bytes = sum(len(str(r).encode("utf-8")) for r in results)
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total = record_savings(tokens_saved, storage_path)
    meta["tokens_saved"] = tokens_saved
    meta.update(cost_avoided(tokens_saved, total))

    payload = {
        "repo": f"{owner}/{name}",
        "query": query,
        "results": results,
        "result_count": len(results),
        "_meta": meta,
    }
    if index.repo_at_sha:
        payload["repo_at_sha"] = index.repo_at_sha
    return payload
