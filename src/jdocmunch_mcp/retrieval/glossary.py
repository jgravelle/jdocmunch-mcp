"""Glossary extractor (v1.19.0).

Detects definition patterns in section bodies and builds a per-repo
``terms.json`` index keyed by the canonical term. Used by:

  - ``lookup_term(repo, term)`` — direct term resolution.
  - ``list_terms(repo)`` — enumerate the glossary.
  - search ranking — single-word queries can fast-path through the
    glossary before BM25 (deferred to a later release).

Patterns supported:

  1. Markdown bold definition: ``**Term** — definition...`` (em-dash, en-
     dash, hyphen, or colon all accepted as the separator).
  2. Definition list: ``Term: short definition`` (single colon, no
     surrounding markdown markup; bounded by the leading-of-line + a
     short text length).
  3. RST glossary directive: ``.. glossary::`` block followed by indented
     ``Term`` headers and indented definitions.
  4. RST "definition list": ``Term`` on its own line followed by a
     leading-whitespace-indented definition.

The extractor is conservative — false positives in glossary lookups are
worse than false negatives. We require either explicit emphasis (bold
markdown) or the RST directive to capture a term as canonical.

Output is a list of ``GlossaryEntry`` dicts:

    {term, definition, section_id, source}

with ``source`` ∈ {"markdown_bold", "rst_glossary", "rst_def_list"}.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Optional

# Patterns -------------------------------------------------------------------

# **Term** [—|–|-|:] definition...
# Term must be 2+ chars, mostly alphanumeric / spaces / hyphens.
_MARKDOWN_BOLD_RE = re.compile(
    r"(?m)^\s*\*\*([A-Za-z][A-Za-z0-9 _\-./]{1,80})\*\*\s*[—–\-:]\s+(.{4,400}?)$",
)

# RST glossary directive — capture the entire indented block following.
_RST_GLOSSARY_RE = re.compile(
    r"(?m)^\.\.\s+glossary::\s*\n((?:[ \t]+.*\n?|\n)+)",
)

# Term within a glossary block: line at column 0+ but indented less than
# its definition. We split the block content by leading whitespace tiers.
_RST_GLOSSARY_TERM_RE = re.compile(r"(?m)^(\s+)(\S[^\n]*?)$")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _extract_markdown_bold(content: str, section_id: str) -> Iterable[dict]:
    for m in _MARKDOWN_BOLD_RE.finditer(content):
        term = _clean(m.group(1))
        definition = _clean(m.group(2))
        if not term or not definition:
            continue
        if len(term) < 2 or len(definition) < 4:
            continue
        yield {
            "term": term,
            "definition": definition[:400],
            "section_id": section_id,
            "source": "markdown_bold",
        }


def _extract_rst_glossary(content: str, section_id: str) -> Iterable[dict]:
    for m in _RST_GLOSSARY_RE.finditer(content):
        block = m.group(1)
        # Split into (indent, body) lines. Term lines have one indent level;
        # definition lines have a deeper indent.
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        # Determine the minimum indent used for terms.
        indents = sorted({len(ln) - len(ln.lstrip()) for ln in lines})
        if not indents:
            continue
        term_indent = indents[0]
        current_term: str = ""
        current_def: list[str] = []

        def _flush():
            if current_term:
                yield {
                    "term": _clean(current_term),
                    "definition": _clean(" ".join(current_def))[:400],
                    "section_id": section_id,
                    "source": "rst_glossary",
                }

        for ln in lines:
            indent_n = len(ln) - len(ln.lstrip())
            text = ln.strip()
            if indent_n == term_indent:
                # New term; flush previous.
                if current_term:
                    yield from _flush()
                current_term = text
                current_def = []
            else:
                current_def.append(text)
        # Final flush.
        if current_term:
            yield from _flush()


def extract_glossary(sections: list) -> list:
    """Walk every section and return the deduplicated glossary list.

    Sections are dict-shaped (post-load) or Section objects (in-memory).
    """
    out: list = []
    seen: set[tuple[str, str]] = set()  # (term_lower, source)

    for sec in sections:
        sid = (sec.get("id", "") if isinstance(sec, dict)
               else getattr(sec, "id", ""))
        content = (sec.get("content", "") if isinstance(sec, dict)
                   else getattr(sec, "content", "")) or ""
        for entry in _extract_markdown_bold(content, sid):
            key = (entry["term"].lower(), entry["source"])
            if key in seen:
                continue
            seen.add(key)
            out.append(entry)
        for entry in _extract_rst_glossary(content, sid):
            key = (entry["term"].lower(), entry["source"])
            if key in seen:
                continue
            seen.add(key)
            out.append(entry)
    return out


# Persistence ---------------------------------------------------------------

def _terms_path(base_path: Optional[str], owner: str, name: str) -> Path:
    from ..storage.paths import resolve_root  # jdoc#146
    root = resolve_root(base_path)
    safe_owner = (owner or "").strip().replace("/", "_").replace("\\", "_") or "_"
    safe_name = (name or "").strip().replace("/", "_").replace("\\", "_") or "_"
    return root / safe_owner / f"{safe_name}.terms.json"


def write_terms(
    base_path: Optional[str],
    owner: str,
    name: str,
    entries: Iterable[dict],
) -> int:
    """Atomically rewrite the per-repo glossary sidecar. Returns count written."""
    path = _terms_path(base_path, owner, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(entries)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"entries": rows}, indent=2), encoding="utf-8")
    tmp.replace(path)
    return len(rows)


def load_terms(base_path: Optional[str], owner: str, name: str) -> list:
    """Return the persisted glossary list, or [] when absent."""
    path = _terms_path(base_path, owner, name)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = data.get("entries") if isinstance(data, dict) else None
    return rows if isinstance(rows, list) else []


def lookup(base_path: Optional[str], owner: str, name: str, term: str) -> list:
    """Case-insensitive exact-term lookup. Returns all matching entries."""
    target = (term or "").strip().lower()
    if not target:
        return []
    return [e for e in load_terms(base_path, owner, name)
            if isinstance(e, dict) and (e.get("term") or "").strip().lower() == target]
