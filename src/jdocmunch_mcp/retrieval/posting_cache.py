"""Persisted per-section term rows: the input to the Stage-A posting index.

``PostingIndex.build`` re-reads every section body through a byte-range loader
and re-tokenizes it, so every cold search paid a full rebuild. That cost is the
same tokenization the save path already performs for
``bm25.compute_corpus_stats``, so the work is done twice and thrown away twice.
``benchmarks/posting_rows/measure.py`` measures both paths on a public corpus
and writes ``results.json``; quote that file, not this docstring.

This module stores it once, in a ``<name>.postings.jsonl`` sidecar beside the
monolith (jdoc#75's placement argument: the monolith is rewritten whole on
every save, so anything that does not have to travel with it should not).

WHAT IS STORED, AND WHY IT IS A FORWARD INDEX
---------------------------------------------
One row per section::

    ["<section_id>", "<row_key>", dl_title, dl_summary, dl_content, [term_ids]]

``term_ids`` index the ``vocab`` array carried in the header line, NOT the term
strings themselves. ⚠⚠ The encoding difference is the single biggest size fact
here. A section id is a path plus a heading slug, often well over a hundred
characters, and each one sits in many posting lists, so any encoding that
repeats an id per posting entry is many times the size of the index it serves
(``results.json`` records the three encodings side by side). A forward index
writes each id exactly ONCE and each term string exactly once (in the vocab),
which is also why the rows -- not the postings -- are the persisted form:
inverting rows into ``PostingIndex.postings`` is far cheaper than re-reading
every body, and the forward direction is the only one that can be invalidated
and patched per section.

⚠ ``dl_title`` / ``dl_summary`` / ``dl_content`` are the per-field TOKEN COUNTS.
They are free at build time (we tokenize those fields anyway) and they are the
other half of ``bm25_stats``: with them, the whole stats block -- ``N``,
``avgdl`` and ``df`` -- is derivable from the rows without reading a single
body. See :meth:`RowSet.corpus_stats`.

WHY A STALE ROW CANNOT BE SERVED
--------------------------------
⚠⚠ A stale posting sidecar is worse than no sidecar: it silently returns wrong
search results, where rebuilding every time is merely slow. So the sidecar is
given NO authority of its own. Every row carries a ``row_key`` --
``sha256(title \\0 summary \\0 content_hash)`` -- and a row is used only when
that key equals the key recomputed from the LIVE section dict at load time.
The index is the authority; the rows are a cache keyed by their own inputs.

That one rule covers every failure mode without a purge branch, an identity
handshake or a write ordering requirement:

* a torn or partial write leaves rows that simply do not match -> rebuilt;
* a monolith updated by anything that does not maintain the sidecar (an older
  release, a hand edit) invalidates exactly the sections that changed;
* a row for a deleted section matches no live section and is ignored;
* duplicated or out-of-order rows are idempotent (last one wins, then keyed).

⚠ Two inputs the row key cannot see are handled by fail-closed rules rather
than by trusting the cache:

1. **The tokenizer itself.** A change to ``retrieval/tokenize.py`` (stop-words,
   CamelCase splitting, CJK bigrams) changes every row while every key stays
   valid. The header therefore carries a tokenizer identity derived from the
   SOURCE of that module, so an edit invalidates the file automatically rather
   than relying on someone bumping a constant. Where source is unreadable
   (frozen/zipped installs) the package version stands in -- a release can then
   be told apart, which is the only granularity such an install can change at.
2. **A body we failed to read.** ``content_hash`` says what the body is, not
   that the loader could reach it. A row built while the content mirror was
   missing would cache an under-tokenized section forever, since its key stays
   valid. So a row whose body came back EMPTY while its ``content_hash`` says
   otherwise is built but never persisted: it is rebuilt on every load, exactly
   as today. (Genuinely empty sections hash to ``sha256("")`` and persist
   normally.)

⚠ An absent, corrupt or foreign-tokenizer sidecar degrades to today's behaviour
(rebuild in memory), never to an error. Existing indexes have no sidecar and
keep working untouched.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from .tokenize import tokenize

logger = logging.getLogger(__name__)

_FILENAME = "{name}.postings.jsonl"
_LOCK = threading.Lock()

# Bumped when the ROW SHAPE changes. A reader that does not recognise the
# format ignores the whole file and rebuilds -- never guesses at a field.
FORMAT_VERSION = 1

# sha256 of the empty string: the content_hash a legitimately empty section
# carries, and the one value that lets "this section has no body" be told apart
# from "this section's body could not be read" (see the module docstring).
_EMPTY_CONTENT_HASH = hashlib.sha256(b"").hexdigest()

_ENV_DISABLE = "JDOCMUNCH_POSTING_CACHE"

# Compact the vocabulary when fewer than this fraction of its entries are still
# referenced by a row. Compaction renumbers every term id and so forfeits the
# verbatim-line reuse that makes an incremental write cheap; below this ratio
# the header's dead weight is the larger cost.
_VOCAB_LIVE_FRACTION = 0.75


def enabled() -> bool:
    """Whether the sidecar may be read or written.

    Resolved at CALL time, not bound at import: a default argument would read
    an env var set afterwards as absent (the same rule as ``bm25._env_float``).
    Anything other than an explicit off switch means on, so a typo cannot
    silently disable the cache.
    """
    raw = (os.environ.get(_ENV_DISABLE) or "").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def _path(base_path: Optional[str], owner: str, name: str) -> Path:
    """Sidecar path, matching the other index-owned sidecars' placement."""
    from ..storage.paths import resolve_root  # jdoc#146
    root = resolve_root(base_path)
    safe_owner = (owner or "").strip().replace("/", "_").replace("\\", "_") or "_"
    safe_name = (name or "").strip().replace("/", "_").replace("\\", "_") or "_"
    return root / safe_owner / _FILENAME.format(name=safe_name)


_TOKENIZER_IDENTITY: Optional[str] = None


def tokenizer_identity() -> str:
    """Identity of the tokenizer these rows were produced by.

    ⚠⚠ Derived from the SOURCE of ``retrieval/tokenize.py``, not from a
    hand-maintained constant. The rows are a function of that module and of
    nothing else it imports (it has no project imports and no I/O), and a
    hand-bumped version is exactly the kind of thing an unrelated stop-word
    tweak forgets. A comment-only edit costs one rebuild; a forgotten bump
    costs silently wrong postings, and only one of those two is acceptable.
    """
    global _TOKENIZER_IDENTITY
    if _TOKENIZER_IDENTITY is not None:
        return _TOKENIZER_IDENTITY
    digest = None
    try:
        from . import tokenize as _tok_mod
        source = Path(_tok_mod.__file__).read_bytes()
        digest = "src-" + hashlib.sha256(source).hexdigest()[:16]
    except Exception as exc:  # pragma: no cover - frozen / zipped installs
        # Fail to a coarser but still CHANGING identity rather than to a
        # constant: a release bump is the finest granularity an install whose
        # source cannot be read is able to change at.
        logger.debug("posting cache: tokenizer source unreadable (%s)", exc)
        try:
            from .. import __version__ as _ver
            digest = f"ver-{_ver}"
        except Exception:
            digest = "unknown"
    _TOKENIZER_IDENTITY = digest
    return digest


def _field(sec, key: str, default=""):
    """Read ``key`` from either a dict-shaped or Section-object section.

    Both shapes reach this module: ``save_index`` holds Section objects that
    still carry inline content, every other path holds the dicts.
    """
    if isinstance(sec, dict):
        return sec.get(key, default)
    return getattr(sec, key, default)


def row_key(sec) -> str:
    """Key over exactly the inputs a row is built from, or "" when unkeyable.

    ``content_hash`` identifies the body (every parser computes it from the
    section body it emits), and
    title/summary are the two fields the loader never sees -- a summarizer pass
    changes them with the body untouched. 16 hex characters is 64 bits over a
    few thousand rows; there is no adversary here, only accidents.

    ⚠ Returns "" when ``content_hash`` is absent, which makes the row
    unpersistable rather than merely unverified. Every parser sets the field,
    so this is a guard, not a regime.
    """
    content_hash = _field(sec, "content_hash", "") or ""
    if not content_hash:
        return ""
    h = hashlib.sha256()
    h.update((_field(sec, "title", "") or "").encode("utf-8", "replace"))
    h.update(b"\0")
    h.update((_field(sec, "summary", "") or "").encode("utf-8", "replace"))
    h.update(b"\0")
    h.update(content_hash.encode("utf-8", "replace"))
    return h.hexdigest()[:16]


class RowSet:
    """Per-section term rows plus the vocabulary their ids point into.

    ``rows`` maps section id -> ``[row_key, dl_title, dl_summary, dl_content,
    [term_ids]]`` in section order. ``raw`` keeps the verbatim serialized line
    for rows that came off disk under THIS vocabulary, so a save that changes
    one document re-encodes only that document's rows; re-emitting a string we
    already hold costs nothing like re-encoding the row.
    """

    __slots__ = ("vocab", "_term_id", "rows", "raw", "reused", "built",
                 "unpersistable")

    def __init__(self, vocab: Optional[list] = None) -> None:
        self.vocab: list = list(vocab or [])
        self._term_id: dict = {t: i for i, t in enumerate(self.vocab)}
        self.rows: dict = {}
        self.raw: dict = {}
        self.reused = 0
        self.built = 0
        self.unpersistable = 0

    # -- construction --------------------------------------------------------

    def _intern(self, term: str) -> int:
        tid = self._term_id.get(term)
        if tid is None:
            tid = len(self.vocab)
            self.vocab.append(term)
            self._term_id[term] = tid
        return tid

    @classmethod
    def build(cls, sections: list, content_loader=None,
              reuse: Optional["RowSet"] = None) -> "RowSet":
        """Rows for every section in ``sections``, reusing what ``reuse`` proves.

        A row is reused only when its key equals the key recomputed from the
        live section here and now; anything else is tokenized. The result
        therefore covers the whole corpus and is exact whatever state the
        sidecar was in.
        """
        out = cls(reuse.vocab if reuse is not None else None)
        reuse_rows = reuse.rows if reuse is not None else {}
        reuse_raw = reuse.raw if reuse is not None else {}
        for sec in sections:
            sid = _field(sec, "id", "") or ""
            if not sid:
                continue
            key = row_key(sec)
            if key:
                old = reuse_rows.get(sid)
                if old is not None and old[0] == key:
                    out.rows[sid] = old
                    raw_line = reuse_raw.get(sid)
                    if raw_line is not None:
                        out.raw[sid] = raw_line
                    out.reused += 1
                    continue

            title = _field(sec, "title", "") or ""
            summary = _field(sec, "summary", "") or ""
            content = _field(sec, "content", "") or ""
            loaded_empty = False
            if not content and content_loader is not None:
                try:
                    content = content_loader(
                        _field(sec, "doc_path", "") or "",
                        int(_field(sec, "byte_start", 0) or 0),
                        int(_field(sec, "byte_end", 0) or 0),
                    ) or ""
                except Exception:
                    content = ""
                loaded_empty = not content

            title_tokens = tokenize(title)
            summary_tokens = tokenize(summary)
            content_tokens = tokenize(content)
            terms = set(title_tokens)
            terms.update(summary_tokens)
            terms.update(content_tokens)

            if loaded_empty and (
                (_field(sec, "content_hash", "") or "") != _EMPTY_CONTENT_HASH
            ):
                # The body was supposed to exist and we could not read it.
                # Persisting this row would freeze the failure behind a key
                # that stays valid forever; rebuilding it every time is what
                # happens today and costs nothing when the mirror is healthy.
                key = ""

            row = [
                key,
                len(title_tokens),
                len(summary_tokens),
                len(content_tokens),
                sorted(out._intern(t) for t in terms),
            ]
            out.rows[sid] = row
            out.built += 1
            if not key:
                out.unpersistable += 1
        return out

    def compact(self) -> bool:
        """Drop vocabulary entries no row references. Returns True if it did.

        ⚠ The vocabulary grows monotonically across incremental saves, because
        reusing a row means reusing the term ids it was written with. Terms
        whose last section was deleted or edited away would otherwise sit in
        the header forever, and the header is read in full on every load.
        Compaction renumbers, so every verbatim line is invalidated and the
        whole file is re-encoded -- which is why it waits until a quarter of the vocabulary is dead rather than
        running on every save.
        """
        used: set = set()
        for row in self.rows.values():
            used.update(row[4])
        if len(self.vocab) == 0 or len(used) > _VOCAB_LIVE_FRACTION * len(self.vocab):
            return False
        remap: dict = {}
        new_vocab: list = []
        for tid in sorted(used):
            remap[tid] = len(new_vocab)
            new_vocab.append(self.vocab[tid])
        # New row objects, never in-place: a reused row is the SAME list object
        # the RowSet we were built from still holds, and renumbering it there
        # would leave that set pointing into a vocabulary it does not have.
        self.rows = {
            sid: [row[0], row[1], row[2], row[3], [remap[t] for t in row[4]]]
            for sid, row in self.rows.items()
        }
        self.vocab = new_vocab
        self._term_id = {t: i for i, t in enumerate(new_vocab)}
        self.raw = {}
        return True

    # -- consumers -----------------------------------------------------------

    def posting_index(self, sections: list):
        """Invert the rows into a :class:`~.prune.PostingIndex`.

        ⚠ ``all_section_ids`` is taken from ``sections``, never from the rows:
        the fallback path ("query had no in-vocabulary term, scan everything")
        must enumerate the live corpus even if the sidecar knew nothing about
        half of it.
        """
        from .prune import PostingIndex

        idx = PostingIndex()
        vocab = self.vocab
        postings = idx.postings
        ids = idx.all_section_ids
        rows = self.rows
        for sec in sections:
            sid = _field(sec, "id", "") or ""
            if not sid:
                continue
            ids.append(sid)
            row = rows.get(sid)
            if row is None:
                continue
            for tid in row[4]:
                term = vocab[tid]
                bucket = postings.get(term)
                if bucket is None:
                    postings[term] = bucket = set()
                bucket.add(sid)
        return idx

    def corpus_stats(self) -> dict:
        """The ``bm25_stats`` block, derived without reading a single body.

        Identical by construction to ``bm25.compute_corpus_stats`` over the
        same sections, when every section has a distinct, non-empty id (which
        every index the store writes does; ``build`` skips an id-less section
        and keeps one row per id, so ``N`` here is the row count): ``df`` counts a term once per section across the three
        fields combined (which is what a row's term list already is), and
        ``avgdl`` is the mean of the per-field token counts stored beside it.
        ``tests/test_posting_sidecar.py`` pins the two against each other
        rather than trusting this paragraph.
        """
        from .bm25 import cap_df

        vocab = self.vocab
        n = len(self.rows)
        title_total = summary_total = content_total = 0
        df_by_id: dict = {}
        for row in self.rows.values():
            title_total += row[1]
            summary_total += row[2]
            content_total += row[3]
            for tid in row[4]:
                df_by_id[tid] = df_by_id.get(tid, 0) + 1
        df = {vocab[tid]: count for tid, count in df_by_id.items()}
        return {
            "N": n,
            "avgdl": {
                "title": (title_total / n) if n else 0.0,
                "summary": (summary_total / n) if n else 0.0,
                "content": (content_total / n) if n else 0.0,
            },
            "df": cap_df(df),
        }


# -- persistence -------------------------------------------------------------


def load(base_path: Optional[str], owner: str, name: str) -> Optional[RowSet]:
    """``load_path`` for the sidecar belonging to one index."""
    try:
        path = _path(base_path, owner, name)
    except Exception as exc:
        logger.debug("posting cache: unusable path for %s/%s (%s)", owner, name, exc)
        return None
    return load_path(path)


def _is_count(value) -> bool:
    """A non-negative int that is not a bool (``True`` is an ``int`` in Python)."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _decode_line(raw: bytes) -> Optional[str]:
    """One line of the file as text, or None when it is not valid UTF-8."""
    try:
        return raw.decode("utf-8", errors="strict").rstrip("\r\n")
    except UnicodeDecodeError:
        return None


def load_path(sidecar_path) -> Optional[RowSet]:
    """Read the sidecar, or return None when it cannot be trusted as-is.

    None means absent, unreadable, a format this release does not know, or a
    different tokenizer -- every one of which degrades to a rebuild. Individual
    malformed rows are skipped and counted rather than failing the file: the
    rows are independent of each other, so one torn line costs one section.

    ⚠ Nothing in the file is trusted to have the shape the writer gives it.
    The file is read as BYTES and decoded one line at a time, so a byte that is
    not UTF-8 costs the row it sits in (or the whole file, in the header)
    instead of raising out of the iterator. Every field of a row is
    type-checked before use, so a string or ``null`` where a count belongs is a
    skipped row, not a ``ValueError`` inside ``search_sections`` or an
    incremental save. ``tests/test_posting_sidecar.py`` writes each of these.
    """
    if not enabled():
        return None
    path = Path(sidecar_path)
    if not path.exists():
        return None

    want_tokenizer = tokenizer_identity()
    skipped = 0
    try:
        with path.open("rb") as fh:
            header_line = _decode_line(fh.readline())
            try:
                header = json.loads(header_line) if header_line is not None else None
            except (ValueError, RecursionError):  # RecursionError: absurd nesting
                header = None
            if header is None:
                logger.warning(
                    "posting cache: unreadable header in %s; rebuilding", path.name
                )
                return None
            if not isinstance(header, dict) or header.get("_header") is not True:
                return None
            if header.get("format") != FORMAT_VERSION:
                logger.debug(
                    "posting cache: format %r != %d in %s; rebuilding",
                    header.get("format"), FORMAT_VERSION, path.name,
                )
                return None
            if header.get("tokenizer") != want_tokenizer:
                logger.debug(
                    "posting cache: tokenizer %r != %r in %s; rebuilding",
                    header.get("tokenizer"), want_tokenizer, path.name,
                )
                return None
            vocab = header.get("vocab")
            # A term that is not a string, or a term listed twice, would make
            # the vocabulary map ambiguous (or unhashable), so the whole file
            # is refused: every row's term ids point into this one array.
            if (not isinstance(vocab, list)
                    or not all(isinstance(term, str) for term in vocab)
                    or len(set(vocab)) != len(vocab)):
                logger.warning(
                    "posting cache: malformed vocabulary in %s; rebuilding", path.name
                )
                return None

            out = RowSet(vocab)
            vocab_size = len(out.vocab)
            for raw in fh:
                line = _decode_line(raw)
                if line is None:
                    skipped += 1
                    continue
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, RecursionError):
                    skipped += 1
                    continue
                if (not isinstance(row, list) or len(row) != 6
                        or not isinstance(row[0], str) or not row[0]
                        or not isinstance(row[1], str) or not row[1]
                        or not all(_is_count(n) for n in row[2:5])
                        or not isinstance(row[5], list)):
                    skipped += 1
                    continue
                if any(not _is_count(t) or t >= vocab_size for t in row[5]):
                    skipped += 1
                    continue
                out.rows[row[0]] = [row[1], row[2], row[3], row[4], row[5]]
                out.raw[row[0]] = line
    except OSError as exc:
        # ⚠ Not a bare `except: pass`. A read failure and an empty cache are
        # different events and the log has to be able to tell them apart
        # (v1.131.0); the CALLER still degrades to a rebuild either way.
        logger.warning("posting cache: cannot read %s (%s); rebuilding", path.name, exc)
        return None
    if skipped:
        logger.warning(
            "posting cache: skipped %d malformed row(s) in %s; those sections rebuild",
            skipped, path.name,
        )
    return out


def _encode_row(sid: str, row: list) -> str:
    return json.dumps([sid, row[0], row[1], row[2], row[3], row[4]],
                      separators=(",", ":"))


def write(base_path: Optional[str], owner: str, name: str,
          rowset: RowSet) -> int:
    """Atomically rewrite the sidecar. Returns the number of rows written.

    Rewrite, not append: the file is a rewrite of what the monolith beside it
    already rewrites whole, and re-emitting the verbatim lines of unchanged
    rows makes that nearly free. An append-only journal would need tombstones,
    a compaction rule and a duplicate-resolution order for no measured gain.

    ⚠ Rows with no key are dropped here rather than at build time, so the
    in-memory RowSet stays complete (it is what ``corpus_stats`` sums over)
    while the persisted set stays verifiable.
    """
    if not enabled():
        return 0
    try:
        path = _path(base_path, owner, name)
    except Exception as exc:
        logger.debug("posting cache: unusable path for %s/%s (%s)", owner, name, exc)
        return 0
    rowset.compact()
    header = json.dumps(
        {"_header": True, "format": FORMAT_VERSION,
         "tokenizer": tokenizer_identity(), "vocab": rowset.vocab},
        separators=(",", ":"),
    )
    written = 0
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            with tmp.open("w", encoding="utf-8") as fh:
                fh.write(header + "\n")
                for sid, row in rowset.rows.items():
                    if not row[0]:
                        continue
                    line = rowset.raw.get(sid)
                    if line is None:
                        line = _encode_row(sid, row)
                    fh.write(line + "\n")
                    written += 1
            tmp.replace(path)
    except OSError as exc:
        # Never fail a save over the cache: the index is authoritative and a
        # missing sidecar is a slow search, not a wrong one. Logged, not
        # swallowed -- a write that keeps failing must be visible somewhere.
        logger.warning("posting cache: cannot write %s (%s)", path.name, exc)
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return 0
    return written
