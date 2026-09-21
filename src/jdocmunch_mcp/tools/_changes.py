"""The file change set, shared by `index_local` and `index_repo` (jdoc#135).

Extracted from `index_local` so the ordering, the cap and the truncation
disclosure are one implementation rather than two that drift. Neither function
touches the filesystem: times arrive as a parameter.

⚠⚠ **`mtime` has two producers and they render time differently, on purpose.**

  - `index_local` passes POSIX timestamps and gets **naive local** ISO strings
    (``2026-09-18T14:18:02``), matching `indexed_at`. That shipped in 1.142.0 and
    cannot change on 1.x.
  - `index_repo` passes the head commit date and gets an **offset-aware** ISO
    string with a NUMERIC offset (``2026-09-20T13:15:39+00:00``).

A value is self-describing: an offset is present or it is not. A caller that
compares across a local index and a repository index must read the offset, since
the two are otherwise wrong by the local UTC offset.

⚠⚠ **Never emit a bare ``Z``.** `datetime.fromisoformat` gained ``Z`` support in
3.11 and this package declares ``requires-python = ">=3.10"``, with 3.10 in the
CI matrix. Measured on 3.10.11::

    '2026-09-20T13:15:39Z'      -> ValueError: Invalid isoformat string
    '2026-09-20T13:15:39+00:00' -> OK  tzinfo=UTC

No developer box newer than 3.10 can see that failure, which is why
`normalize_commit_date` converts rather than passing GitHub's string through.
"""

from __future__ import annotations

from datetime import datetime, timezone

# `changes` is a recently-edited map, not an inventory: at most this many
# entries, newest first. `changes_total` carries the uncapped count.
CHANGES_CAP = 50


def normalize_commit_date(raw) -> str | None:
    """GitHub's ``commit.committer.date`` as an offset-aware ISO 8601 string.

    Returns None for anything unparseable. ⚠ None means *could not establish*,
    never *this file is old*; callers omit the key rather than guessing.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    # Accept the Z form on the way IN (it is what the API sends) and never emit
    # it on the way OUT.
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def build_changes_list(
    new: list,
    changed: list,
    deleted: list,
    mtimes_by_relpath: dict,
) -> list:
    """Build the file change set for an index response.

    Returns one entry per affected file:
      {"doc_path": str, "status": "new"|"changed"|"deleted",
       "mtime": ISO 8601 string | None}

    Sorted by mtime descending. Entries without an mtime (deleted files, or a
    file whose mtime is missing) come last. doc_path ascending breaks ties, so
    the order is stable.

    ⚠ `mtimes_by_relpath` values may be POSIX timestamps (int/float, rendered as
    naive local time) or ISO strings already normalized by the caller. A string
    is passed through untouched, which is how `index_repo` supplies the commit
    date without this function knowing anything about commits.
    """
    entries: list = []

    def _iso(dp):
        mt = mtimes_by_relpath.get(dp)
        if mt is None:
            return None
        if isinstance(mt, str):
            return mt
        return datetime.fromtimestamp(mt).isoformat()

    for dp in new:
        entries.append({"doc_path": dp, "status": "new", "mtime": _iso(dp)})
    for dp in changed:
        entries.append({"doc_path": dp, "status": "changed", "mtime": _iso(dp)})
    for dp in deleted:
        entries.append({"doc_path": dp, "status": "deleted", "mtime": None})

    dated = [e for e in entries if e["mtime"] is not None]
    undated = [e for e in entries if e["mtime"] is None]
    # Two stable sorts: doc_path ascending first, then mtime descending.
    dated.sort(key=lambda e: e["doc_path"])
    dated.sort(key=lambda e: e["mtime"], reverse=True)
    undated.sort(key=lambda e: e["doc_path"])
    return dated + undated


def changes_fields(entries: list) -> dict:
    """Cap a sorted change set for the response, disclosing what was cut.

    A plain head cut: deleted entries sort last, so they are dropped first. The
    `deleted` count field is the authority for deletions, as the `new` /
    `changed` counts are for theirs.
    """
    return {
        "changes": entries[:CHANGES_CAP],
        "changes_total": len(entries),
        "changes_truncated": len(entries) > CHANGES_CAP,
    }
