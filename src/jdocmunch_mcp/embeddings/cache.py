"""Content-hash-keyed embedding cache (v1.15.0).

Sidecar at ``~/.doc-index/<owner>/<name>.embeddings.jsonl``. One JSON line
per cached vector keyed by section ``content_hash``. The first line is a
header line containing the provider/model identity — provider rotation
purges the cache automatically on next load.

Cache schema:

    Line 0 (header):  {"_header": true, "provider": "...", "model": "...", "dim": 384}
    Line 1+:          {"hash": "<sha256>", "vector": [f, f, ...]}

Why JSONL not SQLite:

- Append-only. Every embed pass adds a few hundred lines; no schema migration.
- Diff-friendly. Reviewers can inspect what changed across releases.
- Simple recovery. Truncated files are still partially usable — corrupt
  lines are skipped on load.

Cache hits short-circuit ``provider.embed_texts`` for unchanged sections,
which dominates the cost on a typical incremental re-index (most sections
unchanged).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Iterable, Optional

_CACHE_FILE = "{name}.embeddings.jsonl"
_CACHE_LOCK = threading.Lock()

# jdoc#140: the keys on disk, remembered per sidecar so one run does not parse
# a large file several times. An incremental index read it in full three
# times: `load` in the embed pass, the rescan in `append_entries`, and
# `stored_hashes` for the coverage report.
#
# ⚠⚠ An entry is trusted only while the file's (size, mtime_ns) still match
# what was recorded, so a reader still sees what is ON DISK. If anything else
# rewrites or appends to the file, the stat moves and the reader parses again.
# That matters most for `stored_hashes`, whose caller (#107) exists to report
# what actually reached disk. Our own writers update the entry after writing,
# so their writes do not force a re-read.
_KEY_MEMO: dict = {}   # str(path) -> (stat_sig, header_present, frozenset(keys))
_KEY_MEMO_MAX = 8


def _stat_sig(path: Path):
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


def _memo_get(path: Path):
    """Return ``(header_present, keys)`` if the memo still describes the file."""
    entry = _KEY_MEMO.get(str(path))
    if entry is None:
        return None
    sig, header_present, keys = entry
    if sig is None or sig != _stat_sig(path):
        _KEY_MEMO.pop(str(path), None)
        return None
    return header_present, keys


def _memo_put(path: Path, sig, header_present: bool, keys) -> None:
    """Record keys read or written under ``sig``, but only if the file still
    has that signature, i.e. nothing changed it while we were reading."""
    if sig is None or sig != _stat_sig(path):
        _KEY_MEMO.pop(str(path), None)
        return
    if len(_KEY_MEMO) >= _KEY_MEMO_MAX and str(path) not in _KEY_MEMO:
        _KEY_MEMO.pop(next(iter(_KEY_MEMO)))
    _KEY_MEMO[str(path)] = (sig, header_present, frozenset(keys))


def _memo_drop(path: Path) -> None:
    _KEY_MEMO.pop(str(path), None)


def _cache_path(base_path: Optional[str], owner: str, name: str) -> Path:
    from ..storage.paths import resolve_root  # jdoc#146
    root = resolve_root(base_path)
    safe_owner = owner.strip().replace("/", "_").replace("\\", "_")
    safe_name = name.strip().replace("/", "_").replace("\\", "_")
    if not safe_owner or not safe_name:
        raise ValueError(f"Invalid cache target: owner={owner!r} name={name!r}")
    return root / safe_owner / _CACHE_FILE.format(name=safe_name)


def _legacy_read_path(base_path: Optional[str], owner: str, name: str, path: Path) -> Path:
    """Where to READ a sidecar that is not yet at ``path`` (jdoc#146).

    Before #146 a CLI run with ``DOC_INDEX_PATH`` set wrote this sidecar under
    the HOME root while the index went to ``DOC_INDEX_PATH``. The path is now
    correct, and on the first pass after upgrading the sidecar is absent there.
    ⚠⚠ Reading that as "no vectors" would re-embed the whole corpus. So
    ``load`` falls back to the home copy for reading only. The embed pass then
    finds its cache hits, and ``identity`` (which does NOT fall back) reports
    no sidecar at the new path, so the pass takes the full rewrite and writes
    every vector next to the index in one pass, header first. The home copy is
    never modified, since it may belong to a separate home-root index with the
    same name.

    ⚠⚠ Giving ``identity`` the fallback too would let #141's append path match
    the home header and append rows to a new file that has no header.

    Only for ``base_path=None`` with the variable set. An explicit
    ``base_path`` (the MCP server's own calls, and every test) never reads
    outside the root it named.
    """
    if base_path or path.exists():
        return path
    from ..storage.paths import default_root, home_root
    if default_root() == home_root():
        return path
    legacy = _cache_path(str(home_root()), owner, name)
    return legacy if legacy.exists() else path


# jdoc#111: sidecars written before the char cap joined the identity have no
# `embed_chars` field. They were all built at 1000, so that is what a missing
# field means. ⚠⚠ Reading absence as "mismatch" instead would escalate EVERY
# existing index to a full re-embed on its next run — a corpus-wide bill for
# users who changed nothing. Absence is the default, not unknown.
_LEGACY_EMBED_CHARS = 1000


def _identity(provider: str, model: str, dim: Optional[int],
              embed_chars: Optional[int] = None) -> dict:
    return {
        "_header": True,
        "provider": provider,
        "model": model,
        "dim": int(dim) if dim is not None else None,
        "embed_chars": int(embed_chars) if embed_chars is not None
        else _LEGACY_EMBED_CHARS,
    }


def _header_embed_chars(entry: dict) -> int:
    raw = entry.get("embed_chars")
    return int(raw) if isinstance(raw, int) and raw > 0 else _LEGACY_EMBED_CHARS


def load(
    base_path: Optional[str],
    owner: str,
    name: str,
    *,
    provider: str,
    model: str,
    dim: Optional[int],
    embed_chars: Optional[int] = None,
) -> dict[str, list]:
    """Return ``{content_hash: vector}`` for the matching identity.

    Identity mismatch (provider/model/dim/embed_chars) ⇒ empty dict (caller
    will re-embed and rewrite the cache). Missing file ⇒ empty dict.
    Corrupt lines are silently skipped.

    ⚠ ``embed_chars`` is checked here and not only at the key level (jdoc#111).
    Salting the per-section key alone leaves the header untouched, so the old
    entries load, merge with the new ones, and the sidecar accumulates BOTH
    derivations instead of replacing one with the other.
    """
    path = _legacy_read_path(base_path, owner, name, _cache_path(base_path, owner, name))
    if not path.exists():
        return {}

    sig = _stat_sig(path)
    out: dict[str, list] = {}
    header_ok = False
    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("_header") is True:
                    if (
                        entry.get("provider") == provider
                        and entry.get("model") == model
                        and (dim is None or entry.get("dim") == dim)
                        and (embed_chars is None
                             or _header_embed_chars(entry) == embed_chars)
                    ):
                        header_ok = True
                        continue
                    # Identity mismatch — bail; caller rewrites.
                    return {}
                if not header_ok:
                    # Body line before a matching header — file is from an
                    # older identity, treat as miss.
                    return {}
                h = entry.get("hash")
                vec = entry.get("vector")
                if isinstance(h, str) and isinstance(vec, list):
                    out[h] = vec
    except OSError:
        return {}
    if header_ok:
        with _CACHE_LOCK:
            _memo_put(path, sig, True, out.keys())
    return out


def identity(base_path: Optional[str], owner: str, name: str) -> Optional[dict]:
    """Return the sidecar's stored ``{provider, model, dim}``, or None (jdoc#109).

    ⚠⚠ ``load()`` collapses "no sidecar" and "sidecar under a DIFFERENT model"
    into the same empty dict, so no caller could tell a first index from a
    model rotation. That is why rotation went undetected: the incremental path
    saw ``{}``, embedded the zero changed sections it had, and wrote nothing —
    leaving 384-dim vectors on disk under a 768-dim query encoder.

    Returning None for absent and a dict for present is the whole point;
    do not "simplify" this back to a falsy-on-both signature.
    """
    path = _cache_path(base_path, owner, name)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    return None
                if entry.get("_header") is True:
                    return {
                        "provider": entry.get("provider"),
                        "model": entry.get("model"),
                        "dim": entry.get("dim"),
                        # Normalized, so a pre-jdoc#111 sidecar reports the
                        # 1000 it was actually built at rather than None.
                        "embed_chars": _header_embed_chars(entry),
                    }
                # A body line before any header: pre-header file, unknown identity.
                return None
    except OSError:
        return None
    return None


def identity_matches(stored: Optional[dict], provider: str, model: str,
                     dim: Optional[int], embed_chars: Optional[int] = None) -> bool:
    """True when ``stored`` describes the active provider/model/dim/embed_chars.

    Mirrors the header comparison inside ``load()`` exactly, including its
    ``dim is None`` tolerance and the legacy ``embed_chars`` default, so the
    two can never drift apart.
    """
    if not stored:
        return False
    return (
        stored.get("provider") == provider
        and stored.get("model") == model
        and (dim is None or stored.get("dim") == dim)
        and (embed_chars is None or _header_embed_chars(stored) == embed_chars)
    )


def write(
    base_path: Optional[str],
    owner: str,
    name: str,
    *,
    provider: str,
    model: str,
    dim: Optional[int],
    entries: Iterable[tuple[str, list]],
    embed_chars: Optional[int] = None,
) -> None:
    """Atomically rewrite the cache for this index.

    ``entries`` is an iterable of ``(content_hash, vector)`` pairs. Order
    is not significant. Writes via tmp file + replace so a crash mid-write
    leaves the previous cache intact.
    """
    path = _cache_path(base_path, owner, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with _CACHE_LOCK:
        written: set = set()
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(_identity(provider, model, dim, embed_chars)) + "\n")
            for h, vec in entries:
                if not isinstance(h, str) or not isinstance(vec, list):
                    continue
                fh.write(json.dumps({"hash": h, "vector": vec}) + "\n")
                written.add(h)
        tmp.replace(path)
        _memo_put(path, _stat_sig(path), True, written)


def stored_hashes(base_path: Optional[str], owner: str, name: str) -> set:
    """Return the set of BARE content hashes this sidecar holds (jdoc#107).

    Identity-agnostic and vector-free: reads keys only, so a 26k-section
    corpus costs no memory. The ``#pv<N>`` embed-text-version salt is
    stripped so keys compare directly against a section's ``content_hash``.

    Exists so a caller can report embedding coverage. #107's reporter had to
    compute exactly this externally — comparing sidecar rows against
    ``section_count`` after every reindex — because the tool emitted no
    signal, which is why a total vector loss could exit 0 unnoticed.
    """
    try:
        path = _cache_path(base_path, owner, name)
    except ValueError:
        return set()
    if not path.exists():
        return set()
    with _CACHE_LOCK:
        memo = _memo_get(path)
    if memo is not None:
        return {h.rsplit("#", 1)[0] for h in memo[1]}
    sig = _stat_sig(path)
    full: set = set()
    header_present = False
    out: set = set()
    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("_header") is True:
                    header_present = True
                    continue
                h = entry.get("hash")
                if isinstance(h, str) and h:
                    full.add(h)
                    out.add(h.rsplit("#", 1)[0])
    except OSError:
        return set()
    with _CACHE_LOCK:
        _memo_put(path, sig, header_present, full)
    return out


def append_entries(
    base_path: Optional[str],
    owner: str,
    name: str,
    *,
    entries: Iterable[tuple[str, list]],
    identity_if_new: tuple[str, str, Optional[int]],
) -> int:
    """Add vectors to a sidecar WITHOUT rewriting what is already there.

    Returns the number of entries actually written.

    ⚠ Unlike :func:`write`, this never removes anything and never touches an
    existing header — the caller may not know the true provider identity
    (``_ensure_sidecar_from_sections`` writes a ``__inline__`` placeholder),
    and stamping that over a real one would make the next embed pass see an
    identity mismatch and purge every vector on disk. Keys already present
    are skipped, so an existing vector always wins over a late arrival.

    jdoc#107: added because the safety net's only options used to be "bail"
    or "clobber", so a sidecar that existed could never be extended.
    """
    path = _cache_path(base_path, owner, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _CACHE_LOCK:
        existing: set = set()
        header_present = False
        memo = _memo_get(path) if path.exists() else None
        if memo is not None:
            header_present, existing = memo[0], set(memo[1])
        elif path.exists():
            try:
                with path.open("r", encoding="utf-8") as fh:
                    for raw in fh:
                        line = raw.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except Exception:
                            continue
                        if entry.get("_header") is True:
                            header_present = True
                            continue
                        h = entry.get("hash")
                        if isinstance(h, str):
                            existing.add(h)
            except OSError:
                return 0

        pending = [
            (h, vec) for h, vec in entries
            if isinstance(h, str) and isinstance(vec, list) and h and h not in existing
        ]
        if not pending:
            return 0

        # Append-only: a JSONL sidecar tolerates a torn trailing line (both
        # readers skip unparseable lines), so this needs no tmp-file dance
        # and cannot lose the rows already on disk.
        try:
            with path.open("a", encoding="utf-8") as fh:
                if not header_present:
                    prov, model, dim = identity_if_new
                    fh.write(json.dumps(_identity(prov, model, dim)) + "\n")
                for h, vec in pending:
                    fh.write(json.dumps({"hash": h, "vector": vec}) + "\n")
        except OSError:
            _memo_drop(path)
            return 0
        # `existing` is complete on every path that reaches here: from the
        # memo, from the full scan above, or empty because there was no file.
        _memo_put(path, _stat_sig(path), True, existing | {h for h, _ in pending})
        return len(pending)


def append_rows(
    base_path: Optional[str],
    owner: str,
    name: str,
    rows: Iterable[tuple[str, list]],
) -> int:
    """Append rows to a sidecar whose header the caller has ALREADY matched (jdoc#141).

    ``embed_sections`` uses this instead of :func:`write` when the sidecar's
    identity matches and the pass is not pruning. In that case a rewrite
    produces the same key set as an append, since ``entries`` starts from
    every cached row, and it costs a full rewrite of the file.

    Unlike :func:`append_entries` this does not rescan the file for existing
    keys, because the caller already holds them from :func:`load`. A key that
    is already present may be written again with a new vector. :func:`load`
    keeps the LAST row for a key, so the newer one wins.

    ⚠ A torn trailing line (a crash mid-append) is terminated first.
    Otherwise the first new row would be glued onto the fragment, fail to
    parse, and be dropped silently.
    """
    pending = [
        (h, vec) for h, vec in rows
        if isinstance(h, str) and h and isinstance(vec, list)
    ]
    if not pending:
        return 0
    path = _cache_path(base_path, owner, name)
    with _CACHE_LOCK:
        memo = _memo_get(path)
        with path.open("ab+") as fh:
            fh.seek(0, 2)
            if fh.tell() > 0:
                fh.seek(-1, 2)
                if fh.read(1) != b"\n":
                    fh.write(b"\n")
            for h, vec in pending:
                fh.write((json.dumps({"hash": h, "vector": vec}) + "\n").encode("utf-8"))
        if memo is not None:
            _memo_put(path, _stat_sig(path), memo[0], set(memo[1]) | {h for h, _ in pending})
        else:
            _memo_drop(path)
    return len(pending)


def purge(base_path: Optional[str], owner: str, name: str) -> bool:
    """Delete the cache for one index. Returns True on success."""
    try:
        path = _cache_path(base_path, owner, name)
    except ValueError:
        return False
    if path.exists():
        try:
            path.unlink()
            return True
        except OSError:
            return False
    return False
