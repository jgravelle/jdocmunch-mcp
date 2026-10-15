"""Paging and a byte ceiling for the whole-index listing tools (jdoc#176).

`get_toc`, `get_toc_tree` and `list_docs` answered with the whole index and
nothing bounded one response: 14.0 MB for `get_toc` on 36,000 sections, 7.2 MB
for `get_toc_tree`. An agent pays for that in context, and many clients refuse
it anyway.

Two mechanisms, shared here so the three tools cannot drift:

* **Paging** (`limit` / `offset`). Opt-in. Every response carries
  ``<unit>_total``, ``<unit>_truncated`` and ``next_offset`` (None on the last
  page), including an unpaged one, so a missing key never has to be read as
  "complete".
* **A byte ceiling**, ``JDOCMUNCH_RESPONSE_MAX_BYTES`` (default 1 MiB, ``0``
  turns it off). Over the ceiling the tool REFUSES and names the parameters that
  narrow the call. ⚠⚠ **It never truncates.** A shortened list with no limit
  asked for reads as the whole index, which is the failure paging's disclosure
  keys exist to prevent. jcodemunch refuses for the same reason
  (``JCODEMUNCH_RESPONSE_MAX_BYTES``).

⚠ On 1.x this turns a call that used to return data into an error, for
indexes large enough to cross the ceiling. jjg accepted that on 2026-10-15
(#176); ``JDOCMUNCH_RESPONSE_MAX_BYTES=0`` restores the old behaviour, and the
CHANGELOG says so.
"""

from __future__ import annotations

import json
import os
from typing import Optional

RESPONSE_MAX_BYTES_ENV = "JDOCMUNCH_RESPONSE_MAX_BYTES"
DEFAULT_RESPONSE_MAX_BYTES = 1024 * 1024


def response_max_bytes() -> int:
    """The ceiling in bytes, read at CALL time; 0 means no ceiling.

    A default argument or a module constant would bind at import, so a changed
    environment would not take effect until restart. An unparseable or negative
    value falls back to the default rather than switching the ceiling off: a
    typo must not silently remove the bound.
    """
    raw = os.environ.get(RESPONSE_MAX_BYTES_ENV, "").strip()
    if not raw:
        return DEFAULT_RESPONSE_MAX_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_RESPONSE_MAX_BYTES
    return value if value >= 0 else DEFAULT_RESPONSE_MAX_BYTES


def paging_args(limit, offset) -> tuple:
    """Validate ``limit`` / ``offset``. Returns ``(limit, offset, error)``.

    ``limit`` None means no limit. Both must be integers (a bool is not one);
    ``limit`` at least 1, ``offset`` at least 0.
    """
    def _int(v):
        return isinstance(v, int) and not isinstance(v, bool)

    if limit is not None and (not _int(limit) or limit < 1):
        return None, 0, f"limit must be an integer >= 1, got {limit!r}"
    if offset is None:
        offset = 0
    if not _int(offset) or offset < 0:
        return None, 0, f"offset must be an integer >= 0, got {offset!r}"
    return limit, offset, None


def page(items: list, limit: Optional[int], offset: int, unit: str) -> tuple:
    """Slice ``items`` and build the disclosure keys for it.

    Returns ``(page_items, fields)``. ``fields`` holds ``<unit>_total`` (the
    uncapped count, and the authority), ``<unit>_truncated`` (items exist past
    this page) and ``next_offset`` (where the next page starts, or None).
    """
    total = len(items)
    end = total if limit is None else min(total, offset + limit)
    chunk = items[offset:end]
    more = end < total
    return chunk, {
        f"{unit}_total": total,
        f"{unit}_truncated": more,
        "next_offset": end if more else None,
    }


def response_bytes(result: dict) -> int:
    """Size of ``result`` as the server sends it (compact JSON, UTF-8)."""
    return len(json.dumps(result, separators=(",", ":")).encode("utf-8"))


def ceiling_refusal(
    result: dict,
    *,
    tool: str,
    unit: str,
    item_count: int,
    narrow_with: list,
) -> Optional[dict]:
    """An error response when ``result`` is over the ceiling, else None.

    ``item_count`` is the number of ``unit`` items in ``result``. The
    ``suggested_limit`` is how many items fit under the ceiling at this
    response's average size, with a margin, so one retry usually succeeds.
    """
    ceiling = response_max_bytes()
    if ceiling == 0:
        return None
    size = response_bytes(result)
    if size <= ceiling:
        return None
    per_item = size / max(item_count, 1)
    suggested = max(1, int(ceiling * 0.8 / per_item))
    total = result.get(f"{unit}_total", item_count)
    return {
        "error": (
            f"{tool} response would be {size:,} bytes, over the {ceiling:,}-byte "
            f"ceiling ({RESPONSE_MAX_BYTES_ENV}). Narrow the call with "
            f"{' or '.join(narrow_with)}; limit={suggested} fits. Nothing was "
            f"truncated: this is a refusal, not a partial answer."
        ),
        "response_bytes": size,
        "response_max_bytes": ceiling,
        f"{unit}_total": total,
        "narrow_with": list(narrow_with),
        "suggested_limit": suggested,
    }
