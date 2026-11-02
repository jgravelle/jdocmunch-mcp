"""Org-mode parser: star-headline section splitter with byte offsets (#199).

Org headlines are one or more ``*`` at the start of a line, then a space::

    * Level 1
    ** Level 2
    *** Level 3          (no cap; odd-level files use 1, 3, 5, ...)

The parser follows Org's own reading of its syntax rather than a Markdown
analogy, and every rule below names the Org behaviour it reproduces:

* A headline cannot be contained in any other element (org-element), so a
  ``* `` line inside a ``#+BEGIN_SRC`` block IS a headline and ends the block.
  Org users escape such lines with a leading comma (``,*``, ``,#+``); the
  escape is undone in the extracted ``code_blocks`` content only. Section
  content and byte ranges always hold the file as written.
* A line of 15 or more stars is an inline task (``org-inlinetask-min-level``)
  and is content, not an outline node.
* The headline title is cleaned in Org's order before the slug is built:
  TODO keyword, ``[#A]`` priority cookie, ``COMMENT``, title, trailing
  ``:tag:tag:``. Statistics cookies (``[3/7]``, ``[42%]``) are removed
  wherever they sit. So flipping TODO to DONE, or retagging, never changes a
  section id. TODO keywords default to ``TODO`` and ``DONE`` and are extended
  by every ``#+TODO:`` / ``#+SEQ_TODO:`` / ``#+TYP_TODO:`` line in the file.
* Content before the first headline is the level-0 root, titled from
  ``#+TITLE:`` when present and the file stem otherwise. ``#+FILETAGS:`` go
  to the root's tags; other preamble keywords to ``metadata["org"]["keywords"]``.
* A planning line (``SCHEDULED:`` / ``DEADLINE:`` / ``CLOSED:``) directly
  under the headline, and a ``:PROPERTIES:`` drawer directly after that, feed
  ``metadata["org"]``. ``CUSTOM_ID`` becomes the section anchor when it is a
  safe anchor id, which is how ``[[#custom-id]]`` links resolve.
* References are emitted only when the index can check them: ``file:`` and
  path-shaped targets (``::search`` suffix dropped), ``#custom-id`` targets,
  http(s)/ftp/mailto/tel URLs, angle links, bare URLs and ``#+INCLUDE:``
  paths. ``id:``, ``*Headline``, fuzzy ``[[text]]``, ``attachment:`` and
  command links (``elisp:``, ``shell:``) are not emitted; nothing here can
  resolve them, and an unverifiable reference would only ever read as broken.
* References, tags and inline code come from a prose-only view with src,
  example, export and comment blocks, fixed-width lines, ``# `` comment
  lines, planning lines and keyword lines blanked, as the Markdown parser
  does for fenced code (#57, #59, #60).

Everything else in Org (lists, checkboxes, tables, footnotes, timestamps in
prose, macros, LaTeX, entities, ``<<targets>>``, ``#+RESULTS:``) is plain
section content and needs no code.
"""

import re
from pathlib import Path

from .sections import (
    SAFE_ANCHOR_ID_RE,
    Section,
    compute_content_hash,
    extract_references,
    extract_tags,
    make_hierarchical_slug,
    make_section_id,
    slugify,
)
from .sections import _IDENT_RE

# ``org-inlinetask-min-level`` default: a line of this many stars or more is
# an inline task, not an outline node.
INLINE_TASK_MIN_STARS = 15

_HEADLINE_RE = re.compile(r"^(\*+) (.*)$")
_KEYWORD_RE = re.compile(r"^\s*#\+([A-Za-z_][A-Za-z0-9_\-]*):(?:\s+(.*?))?\s*$")
_BLOCK_OPEN_RE = re.compile(r"^\s*#\+BEGIN_([A-Za-z0-9_\-]+)(?:\s+(.*?))?\s*$", re.IGNORECASE)
_BLOCK_CLOSE_RE = re.compile(r"^\s*#\+END_([A-Za-z0-9_\-]+)\s*$", re.IGNORECASE)
_DYN_OPEN_RE = re.compile(r"^\s*#\+BEGIN:(?:\s|$)", re.IGNORECASE)
_DYN_CLOSE_RE = re.compile(r"^\s*#\+END:\s*$", re.IGNORECASE)
_FIXED_WIDTH_RE = re.compile(r"^\s*:( |$)")
_COMMENT_LINE_RE = re.compile(r"^\s*#( |$)")
_ESCAPED_LINE_RE = re.compile(r"^(\s*),(\*|#\+)")

_PRIORITY_RE = re.compile(r"^\[#([^\]\s])\]\s*")
_TAGS_RE = re.compile(r"(?:^|\s+)(:[\w@#%:]+:)\s*$")
_COOKIE_RE = re.compile(r"\s*\[(?:\d+/\d+|\d+%)\]")
_TODO_FAST_ACCESS_RE = re.compile(r"\(.*?\)$")

_TIMESTAMP = r"(?:<[^>\n]*>|\[[^\]\n]*\])(?:--(?:<[^>\n]*>|\[[^\]\n]*\]))?"
_PLANNING_ITEM_RE = re.compile(r"(SCHEDULED|DEADLINE|CLOSED):\s*(" + _TIMESTAMP + ")")
_PLANNING_LINE_RE = re.compile(
    r"^\s*(?:(?:SCHEDULED|DEADLINE|CLOSED):\s*" + _TIMESTAMP + r"\s*)+$"
)
_DRAWER_OPEN_RE = re.compile(r"^\s*:PROPERTIES:\s*$", re.IGNORECASE)
_DRAWER_CLOSE_RE = re.compile(r"^\s*:END:\s*$", re.IGNORECASE)
_PROPERTY_RE = re.compile(r"^\s*:([^\s:]+?)\+?:(?:\s+(.*?))?\s*$")

_ORG_LINK_RE = re.compile(r"\[\[([^\[\]]+?)\](?:\[[^\[\]]*\])?\]")
_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):")
_INCLUDE_RE = re.compile(r'^\s*#\+INCLUDE:\s*(?:"([^"]+)"|(\S+))', re.IGNORECASE)
_INLINE_SPAN_RE = re.compile(r"(?<![\w=~])([=~])(\S(?:[^\n]*?\S)?)\1(?![\w=~])")

# Blocks whose body is not prose: blanked before reference / tag / inline-code
# extraction. QUOTE, VERSE and CENTER blocks are prose and stay.
_NON_PROSE_BLOCKS = frozenset({"src", "example", "export", "comment"})

# File keywords that never belong in metadata["org"]["keywords"]: the ones
# the parser consumes itself, and the per-element (affiliated) keywords.
_KEYWORDS_NOT_RECORDED = frozenset({
    "todo", "seq_todo", "typ_todo", "filetags",
    "name", "caption", "header", "headers", "results", "call", "tblfm",
    "plot", "include",
})

# Link schemes the index can check (get_broken_links treats these as external
# and never reports them) or resolve (file). Everything else is dropped.
_REFERENCE_SCHEMES = frozenset({"http", "https", "ftp", "mailto", "tel"})
_FILE_SCHEMES = frozenset({"file", "file+emacs", "file+sys"})
_PATH_PREFIXES = ("./", "../", "/", "~/")


def _todo_keywords(lines: list) -> tuple:
    """(todo_keywords, done_keywords) from every ``#+TODO``-family line.

    Keywords inside blocks are block content, so the scan tracks blocks the
    same way the main walk does.
    """
    todo = ["TODO"]
    done = ["DONE"]
    custom = False
    in_block = None
    for line in lines:
        text = line.rstrip("\r\n")
        if in_block is not None:
            close = _BLOCK_CLOSE_RE.match(text)
            if _HEADLINE_RE.match(text) and not _is_inline_task(text):
                in_block = None
            elif close and close.group(1).lower() == in_block:
                in_block = None
                continue
            else:
                continue
        open_ = _BLOCK_OPEN_RE.match(text)
        if open_:
            in_block = open_.group(1).lower()
            continue
        kw = _KEYWORD_RE.match(text)
        if not kw or kw.group(1).lower() not in ("todo", "seq_todo", "typ_todo"):
            continue
        if not custom:
            todo, done, custom = [], [], True
        value = kw.group(2) or ""
        words = [_TODO_FAST_ACCESS_RE.sub("", w) for w in value.split()]
        words = [w for w in words if w and w != "|"]
        if "|" in value:
            left, right = value.split("|", 1)
            todo += [_TODO_FAST_ACCESS_RE.sub("", w) for w in left.split()]
            done += [_TODO_FAST_ACCESS_RE.sub("", w) for w in right.split()]
        elif words:
            todo += words[:-1]
            done.append(words[-1])
    if custom and not (todo or done):
        todo, done = ["TODO"], ["DONE"]
    return tuple(w for w in todo if w), tuple(w for w in done if w)


def _is_inline_task(text: str) -> bool:
    m = _HEADLINE_RE.match(text)
    return bool(m) and len(m.group(1)) >= INLINE_TASK_MIN_STARS


def _parse_filetags(value: str) -> list:
    value = value.strip()
    if value.startswith(":") and value.endswith(":"):
        return [t for t in value.split(":") if t]
    return value.split()


def _clean_headline(raw: str, todo_kw: tuple, done_kw: tuple) -> tuple:
    """(title, org_metadata, tags) for the text after the stars."""
    rest = (raw or "").strip()
    meta: dict = {}
    tags: list = []

    first, _, after = rest.partition(" ")
    if first in todo_kw or first in done_kw:
        meta["todo"] = first
        meta["todo_type"] = "todo" if first in todo_kw else "done"
        rest = after.strip()

    m = _PRIORITY_RE.match(rest)
    if m:
        meta["priority"] = m.group(1)
        rest = rest[m.end():]

    if rest == "COMMENT" or rest.startswith("COMMENT "):
        meta["commented"] = True
        rest = rest[len("COMMENT"):].strip()

    m = _TAGS_RE.search(rest)
    if m:
        tags = [t for t in m.group(1).split(":") if t]
        rest = rest[: m.start()]
        if "ARCHIVE" in tags:
            meta["archived"] = True

    rest = _COOKIE_RE.sub("", rest)
    return rest.strip(), meta, tags


def _planning(text: str) -> dict:
    if not _PLANNING_LINE_RE.match(text):
        return {}
    return {k.lower(): v for k, v in _PLANNING_ITEM_RE.findall(text)}


def _property_drawer(lines: list, start: int) -> tuple:
    """(properties, end_index_exclusive) for a drawer opening at ``start``,
    or (None, start) when the lines there are not a terminated drawer."""
    if start >= len(lines) or not _DRAWER_OPEN_RE.match(lines[start]):
        return None, start
    props: dict = {}
    for i in range(start + 1, len(lines)):
        text = lines[i]
        if _DRAWER_CLOSE_RE.match(text):
            return props, i + 1
        m = _PROPERTY_RE.match(text)
        if not m:
            return None, start
        props[m.group(1)] = (m.group(2) or "").strip()
    return None, start


def _link_target(target: str):
    """The reference a ``[[target]]`` yields, or None when unresolvable."""
    target = target.strip()
    if not target:
        return None
    if target.startswith("#"):
        return target if len(target) > 1 else None
    scheme = _SCHEME_RE.match(target)
    if scheme:
        name = scheme.group(1).lower()
        if name in _FILE_SCHEMES:
            path = target[scheme.end():]
            return path.split("::", 1)[0].strip() or None
        if name in _REFERENCE_SCHEMES:
            return target
        # A Windows drive letter is not a scheme; treat ``C:/x`` as a path.
        if len(name) == 1 and target[2:3] in ("/", "\\"):
            return target.split("::", 1)[0]
        return None
    if target.startswith(_PATH_PREFIXES):
        return target.split("::", 1)[0]
    return None


def _org_references(prose: str, include_paths: list) -> list:
    refs: list = []

    def add(ref) -> None:
        if ref and ref not in refs:
            refs.append(ref)

    for path in include_paths:
        add(path)
    for target in _ORG_LINK_RE.findall(prose):
        add(_link_target(target))
    # Angle links, bare URLs and anything Markdown-shaped, with the Org links
    # removed so a bracketed URL is not counted twice.
    for ref in extract_references(_ORG_LINK_RE.sub(" ", prose)):
        add(ref)
    return refs


def _org_inline_code(prose: str, cap: int = 40) -> list:
    """Identifier-shaped ``=verbatim=`` and ``~code~`` spans, deduped."""
    out: list = []
    for _, span in _INLINE_SPAN_RE.findall(prose):
        span = span.strip()
        if _IDENT_RE.match(span) and span not in out:
            out.append(span)
            if len(out) >= cap:
                break
    return out


def parse_org(content: str, doc_path: str, repo: str) -> list:
    """Parse an Org-mode file into Section objects.

    Args:
        content: Raw Org content.
        doc_path: Relative path of the document.
        repo: Repository identifier.

    Returns:
        List of Section objects in document order, without hierarchy wiring.
    """
    stem = Path(doc_path).stem
    lines = content.splitlines(keepends=True)
    todo_kw, done_kw = _todo_keywords(lines)
    content_bytes = content.encode("utf-8")

    used_slugs: dict = {}
    slug_stack: list = []
    sections: list = []

    # Open-section state. ``cur_lines`` holds (byte_start, text_without_eol,
    # kind) per line, where kind marks what the prose view must blank.
    cur_title: str = stem
    cur_level: int = 0
    cur_slug: str = ""
    cur_byte_start: int = 0
    cur_meta: dict = {}
    cur_tags: list = []
    cur_lines: list = []
    cur_blocks: list = []
    cur_includes: list = []
    root_keywords: dict = {}
    root_tags: list = []

    # Block state: (name, params, body_byte_start, first_body_line_index).
    block = None
    dyn_block = False

    def _finalize_section(byte_end: int) -> None:
        nonlocal cur_lines, cur_blocks, cur_includes, block, dyn_block
        body = content_bytes[cur_byte_start:byte_end].decode("utf-8")
        slug = cur_slug or slugify(cur_title)
        section_id = make_section_id(repo, doc_path, slug, cur_level)

        meta = dict(cur_meta)
        tags = list(cur_tags)
        anchor = ""

        if cur_level == 0:
            if root_keywords:
                meta["keywords"] = dict(root_keywords)
            tags = list(root_tags)
        else:
            # Planning line directly under the headline, then a drawer.
            texts = [t for _, t, _ in cur_lines]
            i = 1  # cur_lines[0] is the headline itself
            if i < len(texts):
                planning = _planning(texts[i])
                if planning:
                    meta.update(planning)
                    cur_lines[i] = (cur_lines[i][0], texts[i], "blank")
                    i += 1
            props, end = _property_drawer(texts, i)
            if props is not None:
                meta["properties"] = props
                custom_id = props.get("CUSTOM_ID", "")
                if custom_id and SAFE_ANCHOR_ID_RE.match(custom_id):
                    anchor = custom_id

        finalized_blocks = []
        for n, blk in enumerate(cur_blocks):
            finalized_blocks.append({
                "block_id": f"{section_id}::code#{n}",
                "lang": blk["lang"],
                "content": blk["content"],
                "byte_start": blk["byte_start"],
                "byte_end": blk["byte_end"],
            })

        prose = "\n".join(t if kind == "prose" else "" for _, t, kind in cur_lines)
        sec = Section(
            id=section_id,
            repo=repo,
            doc_path=doc_path,
            title=cur_title,
            content=body,
            level=cur_level,
            parent_id="",
            children=[],
            byte_start=cur_byte_start,
            byte_end=byte_end,
            summary="",
            code_blocks=finalized_blocks,
            anchor=anchor,
        )
        sec.content_hash = compute_content_hash(body)
        sec.references = _org_references(prose, cur_includes)
        for tag in extract_tags(prose):
            if tag not in tags:
                tags.append(tag)
        sec.tags = tags
        sec.inline_code = _org_inline_code(prose)
        if meta:
            sec.metadata = {"org": meta}
        sections.append(sec)
        cur_lines = []
        cur_blocks = []
        cur_includes = []
        block = None
        dyn_block = False

    byte_cursor = 0
    for raw_line in lines:
        line_bytes = len(raw_line.encode("utf-8"))
        text = raw_line.rstrip("\n").rstrip("\r")
        headline = _HEADLINE_RE.match(text)

        if headline and not _is_inline_task(text):
            _finalize_section(byte_cursor)
            title, meta, tags = _clean_headline(headline.group(2), todo_kw, done_kw)
            cur_title = title
            cur_level = len(headline.group(1))
            cur_slug = make_hierarchical_slug(title, cur_level, slug_stack, used_slugs)
            cur_byte_start = byte_cursor
            cur_meta = meta
            cur_tags = tags
            cur_lines = [(byte_cursor, text, "blank")]
            byte_cursor += line_bytes
            continue

        kind = "prose"
        if block is not None:
            name, lang, body_start, first_idx = block
            close = _BLOCK_CLOSE_RE.match(text)
            if close and close.group(1).lower() == name:
                if name == "src":
                    body_lines = [t for _, t, _ in cur_lines[first_idx:]]
                    unescaped = [_ESCAPED_LINE_RE.sub(r"\1\2", t) for t in body_lines]
                    cur_blocks.append({
                        "lang": lang,
                        "content": "\n".join(unescaped) + ("\n" if body_lines else ""),
                        "byte_start": body_start,
                        "byte_end": byte_cursor,
                    })
                block = None
            kind = "blank" if name in _NON_PROSE_BLOCKS else "prose"
        elif dyn_block:
            if _DYN_CLOSE_RE.match(text):
                dyn_block = False
        else:
            open_ = _BLOCK_OPEN_RE.match(text)
            if open_:
                name = open_.group(1).lower()
                params = (open_.group(2) or "").split()
                lang = params[0] if name == "src" and params and not params[0].startswith(":") else ""
                block = (name, lang, byte_cursor + line_bytes, len(cur_lines) + 1)
                kind = "blank" if name in _NON_PROSE_BLOCKS else "prose"
            elif _DYN_OPEN_RE.match(text):
                dyn_block = True
            elif _FIXED_WIDTH_RE.match(text) or _COMMENT_LINE_RE.match(text):
                kind = "blank"
            else:
                kw = _KEYWORD_RE.match(text)
                if kw:
                    key = kw.group(1).lower()
                    value = (kw.group(2) or "").strip()
                    if key == "include":
                        m = _INCLUDE_RE.match(text)
                        if m:
                            cur_includes.append(m.group(1) or m.group(2))
                    if cur_level == 0 and not sections:
                        if key == "title" and "title" not in root_keywords:
                            cur_title = value or stem
                        if key == "filetags":
                            root_tags = _parse_filetags(value)
                        if key not in _KEYWORDS_NOT_RECORDED and key not in root_keywords:
                            root_keywords[key] = value
                    kind = "blank"

        cur_lines.append((byte_cursor, text, kind))
        byte_cursor += line_bytes

    _finalize_section(byte_cursor)
    return sections
