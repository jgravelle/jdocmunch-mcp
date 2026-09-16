"""BM25-Okapi scoring with per-field weighting and heading-path boost.

The legacy v1.0–v1.11 ``_score_section`` was a fixed-weight bag with no
IDF, no length normalization, and no TF saturation. It also silently scored
content as zero on every loaded index because ``Section.to_dict`` drops
content (B1 fixed the latter; this module fixes the rest).

Three fields participate:

- **title** (weight 3.0)
- **summary** (weight 1.5)
- **content** (weight 1.0) — read lazily for the top-K candidates only

The heading-path boost adds ``0.5 * BM25(ancestor_path)`` so a query for
"authentication" under ``Security/Auth/Tokens`` outranks the same heading
under ``Tutorials/Hello``. The ancestor chain is recovered from the
hierarchical slug stored in the section ID.

Index-time corpus statistics live under ``DocIndex._bm25_stats``:

    {
      "N": int,                      # total sections
      "avgdl": {"title": f, "summary": f, "content": f},
      "df": {term: count}            # capped at top 5000 by frequency
    }

Tunable via env (defaults match the BM25-Okapi paper):

- ``JDOCMUNCH_BM25_K1`` — saturation parameter (default 1.2)
- ``JDOCMUNCH_BM25_B``  — length-normalization parameter (default 0.75)
"""

from __future__ import annotations

import math
import os
from typing import Optional

from .tokenize import term_frequencies, tokenize

# Field weights — tuned on the self-fixture before shipping. Title hits
# dominate, summary is moderate, content is the long-tail signal.
FIELD_WEIGHTS = {"title": 3.0, "summary": 1.5, "content": 1.0}
HEADING_PATH_WEIGHT = 0.5

# Cap on the persisted df dictionary. Keeps index-file growth bounded;
# unseen tokens at query time fall back to a default IDF computed from the
# total document count.
DF_TOP_K = 5000


def cap_df(df: dict) -> dict:
    """Trim ``df`` to the ``DF_TOP_K`` most frequent terms, deterministically.

    ⚠⚠ The ONE producer of a capped df block, shared by ``compute_corpus_stats``
    and by ``posting_cache.RowSet.corpus_stats``. Two paths that cap the same
    counts differently would disagree about which terms survive the boundary,
    and the difference would show up only as a ranking change on rare terms --
    the hardest kind of drift to attribute.

    ⚠ The tie-break is the TERM, not insertion order. Terms arrive from a
    ``set`` iteration, whose order follows string hashes and therefore
    PYTHONHASHSEED, so the previous ``sorted(..., reverse=True)`` over a
    dict built that way chose a different boundary set from run to run on the
    same corpus. Counts were never affected -- only which equally-frequent
    terms made the cut.
    """
    if len(df) <= DF_TOP_K:
        return df
    top = sorted(df.items(), key=lambda kv: (-kv[1], kv[0]))[:DF_TOP_K]
    return dict(top)


def _k1() -> float:
    try:
        return float(os.environ.get("JDOCMUNCH_BM25_K1", "1.2"))
    except ValueError:
        return 1.2


def _b() -> float:
    try:
        return float(os.environ.get("JDOCMUNCH_BM25_B", "0.75"))
    except ValueError:
        return 0.75


def _idf(df: int, N: int) -> float:
    """Robertson-Spärck-Jones IDF with the +0.5 / +0.5 / +1 smoothing.

    Returns a non-negative score: max(0, log((N - df + 0.5) / (df + 0.5) + 1)).
    """
    if N <= 0:
        return 0.0
    numer = N - df + 0.5
    denom = df + 0.5
    if denom <= 0:
        return 0.0
    return max(0.0, math.log((numer / denom) + 1.0))


def _bm25_field(
    query_terms: list[str],
    field_text: str,
    field_avgdl: float,
    df: dict,
    N: int,
) -> float:
    """Score a single field against query_terms with BM25-Okapi."""
    if not field_text:
        return 0.0
    field_tokens = tokenize(field_text)
    if not field_tokens:
        return 0.0
    dl = len(field_tokens)
    avgdl = field_avgdl if field_avgdl > 0 else dl or 1.0
    tf = term_frequencies(field_tokens)
    k1 = _k1()
    b = _b()

    score = 0.0
    for term in query_terms:
        if term not in tf:
            continue
        term_df = df.get(term, 0)
        if term_df == 0:
            # Unseen term — assume it's rare. Use df=1 so IDF is meaningful but
            # bounded. Better than dropping the signal entirely.
            term_df = 1
        idf = _idf(term_df, N)
        if idf <= 0:
            continue
        f = tf[term]
        norm = 1 - b + b * (dl / avgdl)
        score += idf * (f * (k1 + 1)) / (f + k1 * norm)
    return score


def _field(sec, key: str, default=""):
    """Read ``key`` from either a dict-shaped or Section-object section."""
    if isinstance(sec, dict):
        return sec.get(key, default)
    return getattr(sec, key, default)


def compute_corpus_stats(sections: list, content_loader=None) -> dict:
    """Compute the bm25_stats block from a list of Section objects or dicts.

    Each section provides title/summary; content is loaded via ``content_loader``
    (signature ``loader(doc_path, byte_start, byte_end) -> str``) — kept lazy
    so save_index doesn't double-read every file. When no loader is supplied
    or content is empty, falls back to the inline ``content`` attribute.

    Returns a dict ready to JSON-serialize and stash on the index.
    """
    N = len(sections)
    title_lens = []
    summary_lens = []
    content_lens = []
    df: dict[str, int] = {}

    for sec in sections:
        title_tokens = tokenize(_field(sec, "title", "") or "")
        summary_tokens = tokenize(_field(sec, "summary", "") or "")
        content_text = _field(sec, "content", "") or ""
        if not content_text and content_loader is not None:
            try:
                content_text = content_loader(
                    _field(sec, "doc_path", "") or "",
                    int(_field(sec, "byte_start", 0) or 0),
                    int(_field(sec, "byte_end", 0) or 0),
                ) or ""
            except Exception:
                content_text = ""
        content_tokens = tokenize(content_text)

        title_lens.append(len(title_tokens))
        summary_lens.append(len(summary_tokens))
        content_lens.append(len(content_tokens))

        # df counts a term once per section across all three fields combined —
        # this matches "document frequency" as Robertson defines it: a term
        # is either in the document or not.
        all_terms = set(title_tokens) | set(summary_tokens) | set(content_tokens)
        for term in all_terms:
            df[term] = df.get(term, 0) + 1

    # Cap df: keep the top K by frequency (see cap_df for the tie-break).
    df = cap_df(df)

    def _avg(xs: list[int]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    return {
        "N": N,
        "avgdl": {
            "title": _avg(title_lens),
            "summary": _avg(summary_lens),
            "content": _avg(content_lens),
        },
        "df": df,
    }


def _ancestor_titles_from_id(section_id: str) -> list[str]:
    """Recover the ancestor chain titles from a hierarchical slug.

    Section IDs use the format ``{repo}::{doc_path}::{slug}#{level}`` where
    ``slug`` is hierarchical (``security/auth/tokens``). The slug carries
    enough signal for boosting — we don't need the literal ancestor titles.
    Returns ``["security", "auth"]`` for ``...::security/auth/tokens#3``.
    """
    if "::" not in section_id:
        return []
    slug = section_id.rsplit("::", 1)[-1].split("#", 1)[0]
    parts = slug.split("/")
    if len(parts) <= 1:
        return []
    return [p.replace("-", " ") for p in parts[:-1]]


def score_section(
    sec: dict,
    query: str,
    *,
    stats: Optional[dict] = None,
    content_loader=None,
) -> float:
    """Compute the v1.12 BM25 score for one section against ``query``.

    ``stats`` is the persisted ``bm25_stats`` block. When absent (legacy index
    pre-stats), we degrade to a single-document corpus where every term has
    df=1, avgdl=dl. That keeps queries working but loses IDF discrimination —
    re-index to recover full quality.

    ``content_loader`` is invoked lazily for the content channel when ``sec``
    does not carry inline content (the common case post-load).
    """
    query_terms = tokenize(query)
    if not query_terms:
        return 0.0

    if stats is None or not isinstance(stats, dict):
        stats = {"N": 1, "avgdl": {"title": 1.0, "summary": 1.0, "content": 1.0}, "df": {}}

    N = int(stats.get("N", 1)) or 1
    avgdl = stats.get("avgdl", {})
    df = stats.get("df", {}) or {}

    title_score = _bm25_field(
        query_terms, sec.get("title", ""), float(avgdl.get("title", 1.0)), df, N
    )
    summary_score = _bm25_field(
        query_terms, sec.get("summary", ""), float(avgdl.get("summary", 1.0)), df, N
    )

    # Lazy content load.
    content_text = sec.get("content", "") or ""
    if not content_text and content_loader is not None:
        try:
            content_text = content_loader(
                sec.get("doc_path", ""),
                int(sec.get("byte_start", 0)),
                int(sec.get("byte_end", 0)),
            ) or ""
        except Exception:
            content_text = ""
    content_score = _bm25_field(
        query_terms, content_text, float(avgdl.get("content", 1.0)), df, N
    )

    score = (
        FIELD_WEIGHTS["title"] * title_score
        + FIELD_WEIGHTS["summary"] * summary_score
        + FIELD_WEIGHTS["content"] * content_score
    )

    # Heading-path boost.
    ancestor_titles = _ancestor_titles_from_id(sec.get("id", ""))
    if ancestor_titles:
        ancestor_text = " ".join(ancestor_titles)
        # Treat ancestors as a synthetic field with avgdl≈3 (slug words tend
        # to be short). Falling back to the title avgdl is also reasonable.
        ancestor_avgdl = max(float(avgdl.get("title", 1.0)), 3.0)
        ancestor_score = _bm25_field(query_terms, ancestor_text, ancestor_avgdl, df, N)
        score += HEADING_PATH_WEIGHT * ancestor_score

    return score
