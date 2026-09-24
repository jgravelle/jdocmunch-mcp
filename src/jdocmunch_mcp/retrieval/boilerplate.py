"""Cross-section boilerplate detector + suppressor (v1.24.0).

Repeated content (license headers, "Edit this page on GitHub" footers,
nav menus extracted from HTML conversions) inflates token budgets and
pollutes search. This module finds those repeated lines once at index
time and writes them to a per-repo sidecar; retrieval helpers strip
matched fragments on demand when the caller passes
``strip_boilerplate=True``.

Algorithm:

  1. Tokenize each section's content into newline-delimited lines.
  2. For each line, count how many distinct sections it appears in.
  3. A line is *boilerplate* when it appears in ``min_section_ratio``
     of all sections (default 25%) AND in at least ``min_sections``
     absolute (default 3 — protects tiny indexes from false positives).
  4. Persist the deduped boilerplate set as a list of strings.

Sidecar at ``~/.doc-index/<owner>/<name>.boilerplate.json``:

    {
        "version": 1,
        "captured_at": "...",
        "fragments": ["...", "...", "..."]
    }

``strip(content, fragments)`` removes any matching line; collapses
runs of blank lines that result.

Pure-Python; no shingling library, no scikit. The line-level matcher
catches the high-frequency cases (footers, headers, "© 2025 ..." etc.)
without the overhead of MinHash/LSH.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Iterable, Optional

_FILENAME = "{name}.boilerplate.json"
_LOCK = threading.Lock()
_SCHEMA_VERSION = 1

_MIN_LINE_LEN = 8       # ignore very short lines (single-word footers)
_MAX_LINE_LEN = 240     # ignore body paragraphs that happen to repeat
_DEFAULT_MIN_SECTION_RATIO = 0.25
_DEFAULT_MIN_SECTIONS = 3
_BLANK_RUN_RE = re.compile(r"\n{3,}")


def _path(base_path: Optional[str], owner: str, name: str) -> Path:
    from ..storage.paths import resolve_root  # jdoc#146
    root = resolve_root(base_path)
    safe_owner = (owner or "").strip().replace("/", "_").replace("\\", "_") or "_"
    safe_name = (name or "").strip().replace("/", "_").replace("\\", "_") or "_"
    return root / safe_owner / _FILENAME.format(name=safe_name)


def _normalize_line(line: str) -> str:
    return line.strip()


def detect(
    sections: Iterable,
    *,
    min_section_ratio: float = _DEFAULT_MIN_SECTION_RATIO,
    min_sections: int = _DEFAULT_MIN_SECTIONS,
) -> list[str]:
    """Return the list of boilerplate lines across the given sections.

    Accepts both Section objects and dicts; reads ``content`` either way.
    Empty input or a corpus too small to have meaningful repetition
    returns ``[]``.
    """
    counts: dict[str, int] = {}
    total = 0
    for sec in sections:
        content = (sec.get("content") if isinstance(sec, dict)
                   else getattr(sec, "content", "")) or ""
        if not content:
            continue
        total += 1
        seen_in_this_section: set[str] = set()
        for raw in content.splitlines():
            line = _normalize_line(raw)
            if not line:
                continue
            if len(line) < _MIN_LINE_LEN or len(line) > _MAX_LINE_LEN:
                continue
            if line in seen_in_this_section:
                continue
            seen_in_this_section.add(line)
            counts[line] = counts.get(line, 0) + 1

    if total == 0:
        return []

    threshold = max(min_sections, int(total * min_section_ratio))
    return sorted(line for line, n in counts.items() if n >= threshold)


def write(
    base_path: Optional[str],
    owner: str,
    name: str,
    sections: Iterable,
    *,
    min_section_ratio: float = _DEFAULT_MIN_SECTION_RATIO,
    min_sections: int = _DEFAULT_MIN_SECTIONS,
) -> int:
    """Detect + persist the boilerplate set. Returns fragment count."""
    fragments = detect(
        sections,
        min_section_ratio=min_section_ratio,
        min_sections=min_sections,
    )
    path = _path(base_path, owner, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "version": _SCHEMA_VERSION,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fragments": fragments,
    }
    with _LOCK:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
    return len(fragments)


def load(base_path: Optional[str], owner: str, name: str) -> list[str]:
    """Return the persisted boilerplate list, or [] when absent."""
    path = _path(base_path, owner, name)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != _SCHEMA_VERSION:
            return []
        frags = data.get("fragments") or []
        return [f for f in frags if isinstance(f, str)]
    except Exception:
        return []


def strip(content: str, fragments: list[str]) -> tuple[str, int]:
    """Strip matching boilerplate lines from ``content``.

    Match is line-level after strip-whitespace. Returns ``(new_content,
    bytes_removed)``. Whitespace-only lines created by stripping collapse
    via the existing 3+ blank-line rule.
    """
    if not content or not fragments:
        return content, 0
    fragment_set = set(fragments)
    out_lines: list[str] = []
    removed = 0
    for raw in content.splitlines(keepends=True):
        if _normalize_line(raw) in fragment_set:
            removed += len(raw.encode("utf-8"))
            continue
        out_lines.append(raw)
    new_content = "".join(out_lines)
    new_content = _BLANK_RUN_RE.sub("\n\n", new_content)
    return new_content, removed


def purge(base_path: Optional[str], owner: str, name: str) -> bool:
    """Delete the sidecar. Returns True on success."""
    path = _path(base_path, owner, name)
    if path.exists():
        try:
            path.unlink()
            return True
        except OSError:
            return False
    return False
