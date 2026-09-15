"""Markdown parser: ATX + setext heading splitter with byte offsets."""

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# MDX pre-processor
# ---------------------------------------------------------------------------

_MDX_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n?", re.DOTALL)
_MDX_DISCARD_FENCE_RE = re.compile(r":::js\n.*?(?=\n:::|\Z)", re.DOTALL)
_MDX_FENCE_DELIM_RE = re.compile(r"^:::(?:python|js)\s*$|^:::\s*$", re.MULTILINE)
_MDX_API_LINK_BACKTICK_RE = re.compile(r"@\[`([^`]+)`\]")
_MDX_API_LINK_RE = re.compile(r"@\[([^\]]+)\]")
_MDX_MERMAID_RE = re.compile(r"```mermaid\n.*?```", re.DOTALL)
_MDX_BLANK_LINES_RE = re.compile(r"\n{3,}")

_BLOCK_TAGS = (
    r"Note|Tip|Warning|Info|Accordion|Steps?|Cards?|CardGroup|Tabs?|Tab|CodeGroup"
)
_MDX_OPEN_TAG_RE = re.compile(r"<(?:" + _BLOCK_TAGS + r")(?:\s[^>]*)?>", re.MULTILINE)
_MDX_CLOSE_TAG_RE = re.compile(r"</(?:" + _BLOCK_TAGS + r")>", re.MULTILINE)
_MDX_SELF_CLOSE_KNOWN_RE = re.compile(r"<(?:" + _BLOCK_TAGS + r")\s*/>")
_MDX_SELF_CLOSE_UNKNOWN_RE = re.compile(r"<[A-Z][A-Za-z]*(?:\s[^>]*)?\s*/>")
_MDX_IMPORT_EXPORT_RE = re.compile(r"^(?:import|export)\s+.*$", re.MULTILINE)


def strip_mdx(content: str) -> str:
    """Strip MDX-specific syntax from content, leaving clean Markdown.

    Keeps Python code fences (:::python) and discards JavaScript (:::js).
    JSX component tags are removed; their inner text is preserved.

    Args:
        content: Raw MDX file content.

    Returns:
        Clean Markdown string suitable for the standard parser.
    """
    # Frontmatter (file-start) and mermaid (already fence-targeted) are removed
    # over the whole document. The remaining MDX substitutions must NOT reach
    # inside fenced code blocks: CommonMark fence content is a literal leaf, so
    # deleting import/export lines or JSX there mutilates the code examples that
    # are MDX docs' primary payload (#46). Segment on fences and strip only the
    # plain (non-fence) regions.
    content = _MDX_FRONTMATTER_RE.sub("", content)
    content = _MDX_MERMAID_RE.sub("", content)

    out: list = []
    plain: list = []
    in_fence = False
    fence_marker = ""

    def _flush_plain() -> None:
        if plain:
            out.append(_strip_mdx_plain("".join(plain)))
            plain.clear()

    for line in content.splitlines(keepends=True):
        bare = line.rstrip("\n").rstrip("\r")
        if not in_fence and _FENCE_OPEN_RE.match(bare):
            _flush_plain()
            in_fence = True
            fence_marker = bare.lstrip()[0] * 3
            out.append(line)
        elif in_fence:
            out.append(line)
            if bare.lstrip().startswith(fence_marker):
                in_fence = False
        else:
            plain.append(line)
    _flush_plain()
    return "".join(out).strip()


def _strip_mdx_plain(content: str) -> str:
    """Apply the MDX substitution pipeline to a non-fence text region (#46)."""
    content = _MDX_DISCARD_FENCE_RE.sub("", content)
    content = _MDX_FENCE_DELIM_RE.sub("", content)
    content = _MDX_API_LINK_BACKTICK_RE.sub(r"\1", content)
    content = _MDX_API_LINK_RE.sub(r"\1", content)
    content = _MDX_OPEN_TAG_RE.sub("", content)
    content = _MDX_CLOSE_TAG_RE.sub("", content)
    content = _MDX_SELF_CLOSE_KNOWN_RE.sub("", content)
    content = _MDX_SELF_CLOSE_UNKNOWN_RE.sub("", content)
    content = _MDX_IMPORT_EXPORT_RE.sub("", content)
    content = _MDX_BLANK_LINES_RE.sub("\n\n", content)
    return content

from .sections import (
    Section,
    slugify,
    split_explicit_id,
    make_section_id,
    make_hierarchical_slug,
    compute_content_hash,
    extract_references,
    extract_tags,
    extract_inline_code,
)

_ATX_RE = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+#+\s*)?$")
_SETEXT_H1_RE = re.compile(r"^=+\s*$")
_SETEXT_H2_RE = re.compile(r"^-+\s*$")
# Code-fence delimiters per CommonMark 4.5: 3+ backticks or 3+ tildes, with an
# arbitrary info string. A backtick fence's info string may not contain a
# backtick (the negative lookahead enforces that); a tilde fence's may.
_FENCE_OPEN_RE = re.compile(r"^(`{3,}(?!.*`)|~{3,}).*$")
# Block starters that are NOT paragraph text, so a following setext underline
# is not a heading (#44). Fences, ATX headings, and frontmatter are handled by
# their own branches; these cover list items, blockquotes, and thematic breaks.
_LIST_ITEM_RE = re.compile(r"^\s*([-*+]|\d{1,9}[.)])\s+")
_BLOCKQUOTE_RE = re.compile(r"^\s*>")
_THEMATIC_BREAK_RE = re.compile(r"^ {0,3}([-*_])[ \t]*(?:\1[ \t]*){2,}$")


def _prose_view(seg_bytes: bytes, seg_start: int, blocks: list, fm_byte_end: int) -> str:
    """Return a prose-only view of a section's bytes for tag extraction (#57).

    Blanks out fenced-code byte ranges and (for the root section) the
    frontmatter span, so code tokens (#include, #fff) and YAML values don't
    become tags. Newlines are preserved so the ``(?:^|\\s)#`` tag regex still
    anchors. Operates on a copy: Section.content and content_hash are untouched,
    so byte accuracy and the verify invariant are preserved.
    """
    buf = bytearray(seg_bytes)
    seg_end = seg_start + len(seg_bytes)

    def blank(abs_start: int, abs_end: int) -> None:
        lo = max(abs_start, seg_start) - seg_start
        hi = min(abs_end, seg_end) - seg_start
        if lo < hi:
            buf[lo:hi] = bytes(buf[lo:hi]).translate(_BLANK_TABLE)

    for blk in blocks:
        blank(blk.get("byte_start", 0), blk.get("byte_end", 0))
    if fm_byte_end:
        blank(0, fm_byte_end)
    return buf.decode("utf-8", errors="replace")


# Maps every byte to a space except newlines, so blanking a region preserves
# line structure for the tag regex's ``(?:^|\s)#`` anchor.
_BLANK_TABLE = bytes(c if c in (0x0A, 0x0D) else 0x20 for c in range(256))


# --- CommonMark HTML blocks (#45) ------------------------------------------
# Lines inside an HTML block are raw HTML, so ATX/setext detection must be
# suppressed there (the same principle the fenced-code state machine uses).
_HTML_TYPE1_OPEN = re.compile(r"^</?(?:script|pre|style|textarea)(?:\s|>|$)", re.I)
_HTML_TYPE1_CLOSE = re.compile(r"</(?:script|pre|style|textarea)>", re.I)
_HTML_BLOCK_TAGS = (
    "address|article|aside|base|basefont|blockquote|body|caption|center|col|"
    "colgroup|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|"
    "form|frame|frameset|h1|h2|h3|h4|h5|h6|head|header|hr|html|iframe|legend|li|"
    "link|main|menu|menuitem|nav|noframes|ol|optgroup|option|p|param|search|"
    "section|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul"
)
_HTML_TYPE6_OPEN = re.compile(r"^</?(?:" + _HTML_BLOCK_TAGS + r")(?:\s|/?>|$)", re.I)
_HTML_TYPE7_OPEN = re.compile(
    r"^(?:<[A-Za-z][A-Za-z0-9-]*(?:\s[^>]*)?/?>|</[A-Za-z][A-Za-z0-9-]*\s*>)\s*$"
)
_HTML_TYPE4_OPEN = re.compile(r"^<![A-Za-z]")


def _html_block_start(text: str):
    """Classify an HTML-block opener per CommonMark (text has <=3 leading
    spaces already removed). Returns (type_num, end_kind, marker, open_len) or
    None. end_kind: 'inline' (marker substring ends the block, inclusive),
    'regex' (marker regex ends it, inclusive), 'blank' (first blank line ends
    it, exclusive)."""
    if text.startswith("<!--"):
        return (2, "inline", "-->", 4)
    if text.startswith("<?"):
        return (3, "inline", "?>", 2)
    if text.startswith("<![CDATA["):
        return (5, "inline", "]]>", 9)
    if _HTML_TYPE4_OPEN.match(text):
        return (4, "inline", ">", 2)
    if _HTML_TYPE1_OPEN.match(text):
        return (1, "regex", _HTML_TYPE1_CLOSE, 0)
    if _HTML_TYPE6_OPEN.match(text):
        return (6, "blank", None, 0)
    if _HTML_TYPE7_OPEN.match(text):
        return (7, "blank", None, 0)
    return None


def _frontmatter_end_line(lines: list) -> int | None:
    """Return the closing line index for top-of-file frontmatter.

    Recognizes YAML (``---``) and TOML (``+++``, Hugo's default, #60); the
    closer must use the same delimiter as the opener.
    """
    if not lines:
        return None
    opener = lines[0].strip()
    if opener not in ("---", "+++"):
        return None
    # A '---' opener followed by a blank line is a thematic break, not a YAML
    # metadata block (pandoc's discriminator); real frontmatter starts its
    # key:value body immediately. Without this, a document that opens with a
    # '---' horizontal rule and uses a later bare '---' as a section separator
    # silently folds every heading in between into the root section (#56).
    # '+++' has no thematic-break collision, so it needs no such guard.
    if opener == "---" and (len(lines) < 2 or not lines[1].strip()):
        return None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == opener:
            return i
    return None


def parse_markdown(content: str, doc_path: str, repo: str) -> list:
    """Parse a markdown file into a list of Section objects.

    Handles both ATX headings (# Heading) and setext headings (underline style).
    Tracks byte offsets per line. Content before the first heading becomes a
    level-0 root section.

    Args:
        content: Raw markdown text.
        doc_path: Relative path of the document (used in section IDs).
        repo: Repository identifier (used in section IDs).

    Returns:
        List of Section objects, in document order, without hierarchy wiring.
    """
    lines = content.splitlines(keepends=True)
    # Byte view of the document. Section bodies are derived from this by byte
    # range (#55) so content, byte range, and content_hash cannot diverge:
    # sha256(content_bytes[byte_start:byte_end]) == content_hash holds by
    # construction, which is the invariant the whole verify family relies on.
    content_bytes = content.encode("utf-8")
    used_slugs: dict = {}
    slug_stack: list = []
    # v1.147.0: two more slug chains, built the way earlier releases built them,
    # so an anchored heading's descendants keep answering to the ids they had.
    # `legacy_*` slugifies the raw title with its {#...} marker, as v1.146.0
    # did; `plain_*` slugifies the marker-free title with no anchors applied.
    legacy_used: dict = {}
    legacy_stack: list = []
    plain_used: dict = {}
    plain_stack: list = []
    sections = []

    # State for the current open section
    current_title: str = Path(doc_path).stem  # fallback for level-0
    current_level: int = 0
    current_slug: str = ""
    current_byte_start: int = 0
    # v1.147.0: author-written {#explicit-id} on the current heading, and the
    # slugs this section answers to besides its own.
    current_anchor: str = ""
    current_alias_ids: list = []

    byte_cursor = 0
    # Byte offset where top-of-file frontmatter ends (#57): the root section's
    # prose view blanks this span so YAML values don't become tags.
    fm_byte_end = 0

    # Per-section buffer of code blocks parsed inside the current section
    # (v1.17.0). Each entry is a dict {lang, content, byte_start, byte_end};
    # block_id is stamped at _finalize_section once the section_id is known.
    current_code_blocks: list = []

    def _open_heading(raw_title: str, level: int) -> tuple:
        """(title, anchor, slug, alias slugs) for a heading as written."""
        title, explicit_id = split_explicit_id(raw_title)
        anchor = explicit_id or ""
        slug = make_hierarchical_slug(
            title, level, slug_stack, used_slugs, explicit_id=anchor
        )
        legacy = make_hierarchical_slug(raw_title, level, legacy_stack, legacy_used)
        plain = make_hierarchical_slug(title, level, plain_stack, plain_used)
        aliases = [a for a in dict.fromkeys((legacy, plain)) if a != slug]
        return title, anchor, slug, aliases

    def _finalize_section(byte_end: int) -> None:
        """Close the current open section and append it to sections."""
        nonlocal current_slug, current_code_blocks, current_anchor, current_alias_ids
        # Derive the body from the byte range. Section starts and ends always
        # fall on line boundaries (the cursor advances by whole-line byte
        # lengths), so the slice is on a UTF-8 char boundary and decodes safely.
        seg_bytes = content_bytes[current_byte_start:byte_end]
        body = seg_bytes.decode("utf-8")
        slug = current_slug or slugify(current_title)
        section_id = make_section_id(repo, doc_path, slug, current_level)
        # Stamp block_ids ("section_id::code#0", "::code#1", …).
        finalized_blocks = []
        for n, blk in enumerate(current_code_blocks):
            finalized_blocks.append(
                {
                    "block_id": f"{section_id}::code#{n}",
                    "lang": blk.get("lang", ""),
                    "content": blk.get("content", ""),
                    "byte_start": blk.get("byte_start", 0),
                    "byte_end": blk.get("byte_end", 0),
                }
            )
        sec = Section(
            id=section_id,
            repo=repo,
            doc_path=doc_path,
            title=current_title,
            content=body,
            level=current_level,
            parent_id="",      # wired later by hierarchy.py
            children=[],       # wired later by hierarchy.py
            byte_start=current_byte_start,
            byte_end=byte_end,
            summary="",
            code_blocks=finalized_blocks,
            anchor=current_anchor,
            alias_ids=[
                make_section_id(repo, doc_path, a, current_level)
                for a in current_alias_ids
            ],
        )
        sec.content_hash = compute_content_hash(body)
        # References, tags, and inline code come from a prose-only view: fenced
        # code and frontmatter are blanked so code tokens / YAML+TOML values
        # don't pollute the taxonomy (#57), fenced code isn't double-counted as
        # inline (#59), and frontmatter URLs / in-code link syntax don't become
        # references (#60, #47 follow-on). content/content_hash are untouched.
        prose = _prose_view(seg_bytes, current_byte_start, finalized_blocks, fm_byte_end)
        sec.references = extract_references(prose)
        sec.tags = extract_tags(prose)
        sec.inline_code = extract_inline_code(prose)
        sections.append(sec)
        current_code_blocks = []

    # Paragraph block state for CommonMark setext detection (#44). A setext
    # underline (===/---) forms a heading only when the block immediately above
    # it is a paragraph. We accumulate the open paragraph's lines (so a
    # multi-line setext title is captured whole) and the byte offset where it
    # began; every non-paragraph context (blank line, ATX heading, fence,
    # frontmatter, list item, blockquote, thematic break) clears it, so a
    # following ---/=== is treated as content, not a fabricated heading.
    para_lines: list = []
    para_byte_start: int = 0

    # Fenced-code-block state (B2 + v1.17.0). When inside a fence, ATX and
    # setext detection are suppressed so '# comment' inside code does not
    # become a phantom section. v1.17.0 also captures the body bytes + lang
    # of every fenced block for the find_code_examples tool.
    in_fence: bool = False
    fence_char: str = ""
    fence_len: int = 0
    fence_lang: str = ""
    fence_body_byte_start: int = 0
    fence_body_lines: list = []

    # HTML-block state (#45). Heading detection is suppressed inside an HTML
    # block, like inside a fence, so '# x' / '---' in raw HTML don't fabricate
    # sections. `html_end_kind` is 'inline'/'regex'/'blank'; `html_marker` is
    # the substring or regex that ends inline/regex blocks.
    in_html_block: bool = False
    html_end_kind: str = ""
    html_marker = None

    frontmatter_end_line = _frontmatter_end_line(lines)

    for i, line in enumerate(lines):
        line_bytes = len(line.encode("utf-8"))
        line_stripped = line.rstrip("\n").rstrip("\r")

        # Frontmatter region: root content; YAML metadata never seeds setext.
        if frontmatter_end_line is not None and i <= frontmatter_end_line:
            para_lines = []
            byte_cursor += line_bytes
            if i == frontmatter_end_line:
                fm_byte_end = byte_cursor  # span end for the prose view (#57)
            continue

        # --- HTML-block state machine (#45) ---
        if in_html_block:
            para_lines = []
            if html_end_kind == "blank":
                if not line_stripped.strip():  # type 6/7 end at first blank line
                    in_html_block = False
            elif html_end_kind == "inline":
                if html_marker in line_stripped:
                    in_html_block = False
            else:  # regex (type 1 raw-text)
                if html_marker.search(line_stripped):
                    in_html_block = False
            byte_cursor += line_bytes
            continue
        # --- end HTML-block handling ---

        # --- Fence state machine (B2 + v1.17.0 capture) ---
        if in_fence:
            # Match a closing fence: same char, length >= opening length.
            stripped_left = line_stripped.lstrip()
            is_close = False
            if stripped_left and stripped_left[0] == fence_char:
                run = len(stripped_left) - len(stripped_left.lstrip(fence_char))
                if run >= fence_len and stripped_left[run:].strip() == "":
                    is_close = True
            if is_close:
                # Emit the captured code block: body byte range excludes the
                # fence delimiters themselves.
                body_text = "".join(fence_body_lines)
                current_code_blocks.append(
                    {
                        "lang": fence_lang,
                        "content": body_text,
                        "byte_start": fence_body_byte_start,
                        "byte_end": byte_cursor,
                    }
                )
                in_fence = False
                fence_char = ""
                fence_len = 0
                fence_lang = ""
                fence_body_lines = []
            else:
                fence_body_lines.append(line)
            # Code is not paragraph text; an underline after the fence is content.
            para_lines = []
            byte_cursor += line_bytes
            continue

        # Fence open. Tolerant of any leading indent so list-nested and
        # indented fences open (#43); the close side already lstrips. The
        # tradeoff is the rare 4-space indented-code block that begins with a
        # backtick/tilde run, which is read as a fence instead.
        fence_probe = line_stripped.lstrip(" ")
        fence_open_match = _FENCE_OPEN_RE.match(fence_probe)
        if fence_open_match:
            marker = fence_open_match.group(1)
            in_fence = True
            fence_char = marker[0]
            fence_len = len(marker)
            # Info string after the fence run = language tag (e.g. ```python).
            # First whitespace-delimited token; strip RMarkdown braces so
            # ```{r} filters as `r`.
            _info = fence_probe[len(marker):].strip()
            fence_lang = _info.split()[0].strip("{}") if _info else ""
            fence_body_lines = []
            # Body starts at the byte cursor for the NEXT line after this fence opener.
            fence_body_byte_start = byte_cursor + line_bytes
            para_lines = []
            byte_cursor += line_bytes
            continue
        # --- end fence handling ---

        # Block detection allows up to 3 leading spaces per CommonMark; 4+ is
        # indented code, not a heading/HTML block (#43). Dedent the detection
        # view only; byte offsets are unchanged.
        indent = len(line_stripped) - len(line_stripped.lstrip(" "))
        dedented = line_stripped.lstrip(" ") if indent <= 3 else line_stripped

        # HTML block open (#45). Type 7 cannot interrupt an open paragraph.
        if indent <= 3 and dedented.startswith("<"):
            _html = _html_block_start(dedented)
            if _html is not None and not (_html[0] == 7 and para_lines):
                _type, _kind, _marker, _open_len = _html
                # A single-line block (opener + closer on the same line) never
                # enters the multi-line state.
                if _kind == "inline":
                    one_line = _marker in dedented[_open_len:]
                elif _kind == "regex":
                    one_line = bool(_marker.search(dedented))
                else:
                    one_line = False
                if not one_line:
                    in_html_block = True
                    html_end_kind = _kind
                    html_marker = _marker
                para_lines = []
                byte_cursor += line_bytes
                continue

        # Setext heading: only when the block above is an open paragraph (#44).
        heading_text = None
        heading_level = None
        if para_lines:
            para_text = " ".join(p.strip() for p in para_lines).strip()
            if _SETEXT_H1_RE.match(dedented):
                heading_text = para_text
                heading_level = 1
            elif _SETEXT_H2_RE.match(dedented) and "|" not in para_text:
                # Narrow pipe guard: keeps a GFM pipe-table header
                # (e.g. 'Name | Age' over '-----') from becoming a phantom H2.
                # H1 (===) needs no such guard — tables never use '='.
                heading_text = para_text
                heading_level = 2

        if heading_text is not None:
            # Close the previous section where the heading paragraph began,
            # then open the setext section spanning the paragraph + underline.
            _finalize_section(byte_end=para_byte_start)
            current_level = heading_level
            current_title, current_anchor, current_slug, current_alias_ids = (
                _open_heading(heading_text, heading_level)
            )
            current_byte_start = para_byte_start
            para_lines = []
            byte_cursor += line_bytes
            continue

        # ATX heading: the current line is the heading.
        atx_match = _ATX_RE.match(dedented)
        if atx_match:
            _finalize_section(byte_end=byte_cursor)
            current_level = len(atx_match.group(1))
            current_title, current_anchor, current_slug, current_alias_ids = (
                _open_heading(atx_match.group(2).strip(), current_level)
            )
            current_byte_start = byte_cursor
            # ATX is not paragraph text; a following === is body, not a heading.
            para_lines = []
            byte_cursor += line_bytes
            continue

        # Plain content line. Maintain paragraph state for setext detection:
        # arm only on real paragraph text, reset on blanks and non-paragraph
        # block starters (list items, blockquotes, thematic breaks).
        if (
            line_stripped.strip()
            and not _LIST_ITEM_RE.match(line_stripped)
            and not _BLOCKQUOTE_RE.match(line_stripped)
            and not _THEMATIC_BREAK_RE.match(line_stripped)
        ):
            if not para_lines:
                para_byte_start = byte_cursor
            para_lines.append(line_stripped)
        else:
            para_lines = []
        byte_cursor += line_bytes

    # CommonMark closes an unterminated fence at end of document; flush the
    # buffered block so the code-block tools see it (#51). The body byte range
    # already excludes the opener; at EOF there is no closer to exclude.
    if in_fence:
        current_code_blocks.append(
            {
                "lang": fence_lang,
                "content": "".join(fence_body_lines),
                "byte_start": fence_body_byte_start,
                "byte_end": byte_cursor,
            }
        )

    # Finalize last open section
    _finalize_section(byte_end=byte_cursor)

    return sections
