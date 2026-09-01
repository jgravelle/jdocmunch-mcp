"""Persistent token savings tracker for jDocMunch.

Records cumulative tokens saved across all tool calls by comparing
raw file sizes against actual MCP response sizes.

Stored in ~/.doc-index/_savings.json
"""

import json
import os
import sqlite3
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Optional

_SAVINGS_FILE = "_savings.json"
_BYTES_PER_TOKEN = 4
_TELEMETRY_URL = "https://j.gravelle.us/APIs/savings/post.php"
_SAVINGS_LOCK = threading.Lock()

# Input-token prices, USD per token. Verified 2026-09-01 against
# platform.claude.com/docs/en/about-claude/pricing (Model pricing → Base Input
# Tokens). The retired Opus 4.0/4.1 were $15.00/1M; current Opus is $5.00/1M.
#
# ⚠ A key that names a FAMILY inherits whichever member's price someone last
# looked at. Three of the four keys below are family names, so each comment
# names the ONE model its rate belongs to.
#
# ⚠⚠ A constant written for a FUTURE date is wrong for the whole interval
# before it, and reads identically to a stale one. `claude_sonnet` sat at $3.00
# because Sonnet 5's introductory $2.00 was scheduled to rise to $3.00 on
# 2026-09-01 — an increase Anthropic then cancelled. It was never $3.00, and the
# header date made the value look checked.
PRICING = {
    "claude_opus":   5.00 / 1_000_000,  # Claude Opus 5 — $5.00 / 1M input tokens
    "claude_sonnet": 2.00 / 1_000_000,  # Claude Sonnet 5 — $2.00 / 1M input (superseded 4.6 was $3)
    "claude_haiku":  1.00 / 1_000_000,  # Claude Haiku 4.5 — $1.00 / 1M input tokens
    "gpt5_latest":  10.00 / 1_000_000,  # GPT-5.2 (latest flagship GPT) — $10.00 / 1M input tokens
}


def _savings_path(base_path: Optional[str] = None) -> Path:
    root = Path(base_path) if base_path else Path.home() / ".doc-index"
    root.mkdir(parents=True, exist_ok=True)
    return root / _SAVINGS_FILE


def _get_or_create_anon_id(data: dict) -> str:
    if "anon_id" not in data:
        data["anon_id"] = str(uuid.uuid4())
    return data["anon_id"]


def _share_savings(delta: int, anon_id: str) -> None:
    def _post() -> None:
        try:
            import httpx
            httpx.post(
                _TELEMETRY_URL,
                json={"delta": delta, "anon_id": anon_id},
                timeout=3.0,
            )
        except Exception:
            pass

    threading.Thread(target=_post, daemon=True).start()


def record_savings(tokens_saved: int, base_path: Optional[str] = None) -> int:
    """Add tokens_saved to the running total. Returns new cumulative total."""
    path = _savings_path(base_path)
    with _SAVINGS_LOCK:
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace")) if path.exists() else {}
        except Exception:
            data = {}

        delta = max(0, tokens_saved)
        total = data.get("total_tokens_saved", 0) + delta
        data["total_tokens_saved"] = total

        if delta > 0 and os.environ.get("JDOCMUNCH_SHARE_SAVINGS", "1") != "0":
            anon_id = _get_or_create_anon_id(data)
            _share_savings(delta, anon_id)

        try:
            path.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

    return total


def get_total_saved(base_path: Optional[str] = None) -> int:
    """Return the current cumulative total without modifying it."""
    path = _savings_path(base_path)
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace")).get("total_tokens_saved", 0)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# v1.104.0 — advisory session token budget (suite parity with jcm v1.108.146)
#
# Counts response tokens SERVED (the context this server injects into the
# agent) against an advisory ceiling. Never blocks or truncates — awareness
# only. Wire shape: _meta.budget = {limit, spent, state} with state
# ok / approaching (>=80%) / over (>=100%).
# ---------------------------------------------------------------------------

_SESSION_RESPONSE_LOCK = threading.Lock()
_SESSION_RESPONSE_TOKENS = 0
_BUDGET_APPROACHING_PCT = 0.8


def record_response_text(text: str) -> int:
    """Count a serialized tool response toward the session budget.

    Returns the cumulative session response tokens (bytes/4 estimate,
    same scale as the savings meter).
    """
    global _SESSION_RESPONSE_TOKENS
    try:
        tokens = len(text.encode("utf-8")) // _BYTES_PER_TOKEN
    except Exception:
        tokens = len(text) // _BYTES_PER_TOKEN
    with _SESSION_RESPONSE_LOCK:
        _SESSION_RESPONSE_TOKENS += max(0, tokens)
        return _SESSION_RESPONSE_TOKENS


def get_session_response_tokens() -> int:
    """Cumulative response tokens served this session (process lifetime)."""
    with _SESSION_RESPONSE_LOCK:
        return _SESSION_RESPONSE_TOKENS


def budget_status() -> Optional[dict]:
    """Session budget snapshot ({limit, spent, state}) or None when unset.

    Configured via ``JDOCMUNCH_SESSION_TOKEN_BUDGET`` (int response tokens;
    unset/0/garbage = disabled).
    """
    raw = os.environ.get("JDOCMUNCH_SESSION_TOKEN_BUDGET", "")
    try:
        limit = int(raw.strip())
    except (ValueError, AttributeError):
        return None
    if limit <= 0:
        return None
    spent = get_session_response_tokens()
    if spent >= limit:
        state = "over"
    elif spent >= limit * _BUDGET_APPROACHING_PCT:
        state = "approaching"
    else:
        state = "ok"
    return {"limit": limit, "spent": spent, "state": state}


def reset_session_response_tokens() -> None:
    """Test hook — clear the session response-token counter."""
    global _SESSION_RESPONSE_TOKENS
    with _SESSION_RESPONSE_LOCK:
        _SESSION_RESPONSE_TOKENS = 0


def count_tokens(text: str) -> int:
    """Count tokens in text.

    Uses ``tiktoken`` (cl100k_base) when installed for an accurate count.
    Falls back to the bytes/4 heuristic when tiktoken is not available,
    keeping it as a zero-cost optional dependency.
    """
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text.encode("utf-8")) // _BYTES_PER_TOKEN)


def estimate_savings(raw_bytes: int, response_bytes: int) -> int:
    """Estimate tokens saved: (raw - response) / bytes_per_token.

    v1.20.0: kept as a fast byte-based estimator for the hot path. For
    accurate counts (e.g. when reporting cost) callers should pass
    ``count_tokens(text)`` deltas directly. This function remains the
    fall-through used by search_sections when only byte counts are
    cheap to compute.
    """
    return max(0, (raw_bytes - response_bytes) // _BYTES_PER_TOKEN)


def estimate_savings_text(raw: str, response: str) -> int:
    """Token-accurate variant of estimate_savings using count_tokens.

    Falls back to the bytes/4 heuristic when ``tiktoken`` is unavailable
    (so behavior is bounded). Use when the caller already has the strings
    on hand and a precise number matters.
    """
    return max(0, count_tokens(raw or "") - count_tokens(response or ""))


def cost_avoided(tokens_saved: int, total_tokens_saved: int) -> dict:
    """Return cost avoided estimates for this call and the running total."""
    return {
        "cost_avoided": {
            model: round(tokens_saved * rate, 4)
            for model, rate in PRICING.items()
        },
        "total_cost_avoided": {
            model: round(total_tokens_saved * rate, 4)
            for model, rate in PRICING.items()
        },
    }


# ---------------------------------------------------------------------------
# v1.14.0 — per-tool latency telemetry
#
# Two tiers:
#
# 1. **In-memory ring** — `_tool_latencies: dict[tool, deque(maxlen=512)]`
#    + `_tool_errors: dict[tool, int]`. Free, always on. Powers the
#    `latency_stats()` view used by `analyze_perf(window="session")` and
#    by the `get_session_stats` tool.
#
# 2. **Persistent SQLite sink** — opt-in via `JDOCMUNCH_PERF_TELEMETRY=1`
#    or `perf_telemetry_enabled` config flag. Writes one row per tool call
#    to `~/.doc-index/telemetry.db` for `analyze_perf(window=1h|24h|7d|all)`.
#    Trimmed in 1k-row batches when over `JDOCMUNCH_PERF_TELEMETRY_MAX_ROWS`
#    (default 100k).
#
# Backward-compatible: nothing changes in default behavior. The ring is
# in-memory only unless the user opts into the SQLite sink.
# ---------------------------------------------------------------------------

_LATENCY_LOCK = threading.Lock()
_TOOL_LATENCIES: "dict[str, deque[float]]" = {}
_TOOL_ERRORS: "dict[str, int]" = {}
_RING_MAXLEN = 512


def _telemetry_enabled() -> bool:
    flag = os.environ.get("JDOCMUNCH_PERF_TELEMETRY", "")
    return flag.strip() not in ("", "0", "false", "False", "no")


def _telemetry_db_path(base_path: Optional[str] = None) -> Path:
    root = Path(base_path) if base_path else Path.home() / ".doc-index"
    root.mkdir(parents=True, exist_ok=True)
    return root / "telemetry.db"


def _ensure_telemetry_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tool_calls (
            ts REAL NOT NULL,
            tool TEXT NOT NULL,
            duration_ms REAL NOT NULL,
            ok INTEGER NOT NULL,
            repo TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_calls_tool_ts ON tool_calls(tool, ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_calls_ts ON tool_calls(ts)")


def _max_persisted_rows() -> int:
    try:
        return int(os.environ.get("JDOCMUNCH_PERF_TELEMETRY_MAX_ROWS", "100000"))
    except ValueError:
        return 100000


def record_tool_latency(
    tool: str,
    duration_ms: float,
    ok: bool = True,
    repo: Optional[str] = None,
    base_path: Optional[str] = None,
) -> None:
    """Record one tool-call latency.

    Always pushes onto the in-memory ring. When telemetry is enabled,
    also appends a row to the SQLite sink (autocommit; one fresh
    connection per call to keep cross-thread safety simple).
    """
    if not tool:
        return
    with _LATENCY_LOCK:
        ring = _TOOL_LATENCIES.get(tool)
        if ring is None:
            ring = deque(maxlen=_RING_MAXLEN)
            _TOOL_LATENCIES[tool] = ring
        ring.append(float(duration_ms))
        if not ok:
            _TOOL_ERRORS[tool] = _TOOL_ERRORS.get(tool, 0) + 1

    if not _telemetry_enabled():
        return

    try:
        path = _telemetry_db_path(base_path)
        conn = sqlite3.connect(str(path), isolation_level=None, timeout=2.0)
        try:
            _ensure_telemetry_schema(conn)
            conn.execute(
                "INSERT INTO tool_calls (ts, tool, duration_ms, ok, repo) VALUES (?, ?, ?, ?, ?)",
                (time.time(), tool, float(duration_ms), 1 if ok else 0, repo),
            )
            # Trim in 1k-row batches to amortize cost.
            cap = _max_persisted_rows()
            (count,) = conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()
            if count > cap:
                excess = (count - cap) + 1000
                conn.execute(
                    "DELETE FROM tool_calls WHERE rowid IN ("
                    "SELECT rowid FROM tool_calls ORDER BY ts ASC LIMIT ?)",
                    (excess,),
                )
        finally:
            conn.close()
    except Exception:
        # Telemetry must never break a working tool call.
        pass


def _percentile(sorted_xs: list, p: float) -> float:
    """Nearest-rank percentile. p in [0, 100]."""
    if not sorted_xs:
        return 0.0
    if len(sorted_xs) == 1:
        return float(sorted_xs[0])
    idx = max(0, min(len(sorted_xs) - 1, int(round((p / 100.0) * (len(sorted_xs) - 1)))))
    return float(sorted_xs[idx])


def latency_stats() -> dict:
    """Return per-tool latency stats from the in-memory ring.

    Output:
        {tool: {count, p50_ms, p95_ms, max_ms, errors, error_rate}}
    """
    with _LATENCY_LOCK:
        snapshot = {tool: list(ring) for tool, ring in _TOOL_LATENCIES.items()}
        errors_snapshot = dict(_TOOL_ERRORS)

    out: dict = {}
    for tool, samples in snapshot.items():
        if not samples:
            continue
        s = sorted(samples)
        errs = int(errors_snapshot.get(tool, 0))
        count = len(samples)
        out[tool] = {
            "count": count,
            "p50_ms": round(_percentile(s, 50), 2),
            "p95_ms": round(_percentile(s, 95), 2),
            "max_ms": round(max(s), 2),
            "errors": errs,
            "error_rate": round(errs / count, 4) if count else 0.0,
        }
    return out


def latency_db_query(window: str, base_path: Optional[str] = None) -> dict:
    """Read aggregated stats from the persistent telemetry DB.

    ``window`` ∈ {"1h", "24h", "7d", "all"} — anything else returns empty.
    Returns ``{tool: {count, p50_ms, p95_ms, max_ms, errors, error_rate}}``.
    """
    deltas = {"1h": 3600, "24h": 86400, "7d": 7 * 86400, "all": None}
    if window not in deltas:
        return {}
    path = _telemetry_db_path(base_path)
    if not path.exists():
        return {}

    delta = deltas[window]
    cutoff = time.time() - delta if delta is not None else 0.0

    try:
        conn = sqlite3.connect(str(path), timeout=2.0)
        try:
            cursor = conn.execute(
                "SELECT tool, duration_ms, ok FROM tool_calls WHERE ts >= ?",
                (cutoff,),
            )
            rows = cursor.fetchall()
        finally:
            conn.close()
    except Exception:
        return {}

    by_tool: dict[str, list] = {}
    errs_by_tool: dict[str, int] = {}
    for tool, ms, ok in rows:
        by_tool.setdefault(tool, []).append(float(ms))
        if not ok:
            errs_by_tool[tool] = errs_by_tool.get(tool, 0) + 1

    out: dict = {}
    for tool, samples in by_tool.items():
        s = sorted(samples)
        count = len(samples)
        errs = errs_by_tool.get(tool, 0)
        out[tool] = {
            "count": count,
            "p50_ms": round(_percentile(s, 50), 2),
            "p95_ms": round(_percentile(s, 95), 2),
            "max_ms": round(max(s), 2),
            "errors": errs,
            "error_rate": round(errs / count, 4) if count else 0.0,
        }
    return out


def reset_latency_state() -> None:
    """Test hook — clear the in-memory ring and error counts."""
    with _LATENCY_LOCK:
        _TOOL_LATENCIES.clear()
        _TOOL_ERRORS.clear()


# ---------------------------------------------------------------------------
# v1.23.0 — ranking-event ledger
#
# A second persistent table that captures one row per `search_sections` /
# related-tool invocation when telemetry is enabled. The columns are
# what online weight tuning needs: which repo, which mode, whether the
# semantic channel actually contributed, and the retrieval-confidence
# score. Schema is forward-compatible — new columns get added as
# nullable, never renamed.
#
# Behavior is identical to the v1.14 latency sink: free in-memory, opt-in
# SQLite via ``JDOCMUNCH_PERF_TELEMETRY=1``. Failures swallowed.
# ---------------------------------------------------------------------------

def _ensure_ranking_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ranking_events (
            ts REAL NOT NULL,
            repo TEXT,
            tool TEXT,
            query TEXT,
            mode TEXT,
            semantic_used INTEGER,
            semantic_weight REAL,
            top1_score REAL,
            top2_score REAL,
            confidence REAL,
            result_count INTEGER
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ranking_repo_ts ON ranking_events(repo, ts)")


def record_ranking_event(
    *,
    repo: Optional[str],
    tool: str,
    query: str,
    mode: str,
    semantic_used: bool,
    semantic_weight: float,
    top1_score: Optional[float] = None,
    top2_score: Optional[float] = None,
    confidence: Optional[float] = None,
    result_count: int = 0,
    base_path: Optional[str] = None,
) -> None:
    """Append one ranking event to ``~/.doc-index/telemetry.db``.

    No-op when telemetry is not enabled. Never raises — telemetry must
    not break a working tool call.
    """
    if not _telemetry_enabled():
        return
    try:
        path = _telemetry_db_path(base_path)
        conn = sqlite3.connect(str(path), isolation_level=None, timeout=2.0)
        try:
            _ensure_ranking_schema(conn)
            conn.execute(
                "INSERT INTO ranking_events (ts, repo, tool, query, mode, "
                "semantic_used, semantic_weight, top1_score, top2_score, "
                "confidence, result_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    time.time(),
                    repo,
                    tool,
                    query,
                    mode,
                    1 if semantic_used else 0,
                    float(semantic_weight),
                    float(top1_score) if top1_score is not None else None,
                    float(top2_score) if top2_score is not None else None,
                    float(confidence) if confidence is not None else None,
                    int(result_count),
                ),
            )
        finally:
            conn.close()
    except Exception:
        pass


def ranking_db_query(
    repo: Optional[str] = None,
    limit: int = 10000,
    base_path: Optional[str] = None,
    window_seconds: Optional[float] = None,
) -> list[dict]:
    """Return recent ranking events as a list of dicts.

    Filters by repo when supplied, and by recency when ``window_seconds``
    is set (events older than now − window are excluded). Empty list when
    telemetry DB doesn't exist or the table is missing. Always read-only.
    """
    path = _telemetry_db_path(base_path)
    if not path.exists():
        return []
    try:
        conn = sqlite3.connect(str(path), timeout=2.0)
        try:
            try:
                conn.execute("SELECT 1 FROM ranking_events LIMIT 1").fetchone()
            except sqlite3.OperationalError:
                return []
            params: tuple = ()
            sql = (
                "SELECT ts, repo, tool, query, mode, semantic_used, semantic_weight, "
                "top1_score, top2_score, confidence, result_count FROM ranking_events"
            )
            clauses: list[str] = []
            if repo:
                clauses.append("repo = ?")
                params = params + (repo,)
            if window_seconds is not None:
                clauses.append("ts >= ?")
                params = params + (time.time() - float(window_seconds),)
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY ts DESC LIMIT ?"
            params = params + (int(limit),)
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    except Exception:
        return []

    cols = [
        "ts", "repo", "tool", "query", "mode", "semantic_used", "semantic_weight",
        "top1_score", "top2_score", "confidence", "result_count",
    ]
    return [dict(zip(cols, r)) for r in rows]
