"""Section dataclass, ID utilities, slug generation, hash, and content extraction."""

import hashlib
import re
from typing import Optional
from dataclasses import dataclass, field


@dataclass
class Section:
    """A section of a document, identified by heading hierarchy."""
    id: str              # "repo::doc_path::heading_slug#level"
    repo: str
    doc_path: str
    title: str
    content: str         # Full section text including subsections
    level: int           # 1-6 (heading level); 0 = pre-first-heading root
    parent_id: str       # "" if top-level
    children: list       # child IDs (list[str], but no forward ref)
    byte_start: int = 0
    byte_end: int = 0
    summary: str = ""
    tags: list = field(default_factory=list)
    references: list = field(default_factory=list)
    content_hash: str = ""
    embedding: list = field(default_factory=list)  # semantic embedding vector (empty = not embedded)
    # v1.17.0: extracted fenced code blocks. Each entry:
    #   {"block_id": str, "lang": str, "content": str,
    #    "byte_start": int, "byte_end": int}
    # block_id format: "{section_id}::code#{n}" (n is 0-based per section).
    code_blocks: list = field(default_factory=list)
    # v1.78.0 (#59): identifier-shaped inline code spans (`name`) from the
    # section's prose, deduped, for the code<->docs bridge tools. Persisted
    # only when non-empty (like code_blocks).
    inline_code: list = field(default_factory=list)
    # v1.18.0: format-specific structured metadata. Examples:
    #   metadata.openapi_op    = {method, path, operationId, summary, ...}
    #   metadata.openapi_schema = {name, type, properties, required, ...}
    # Persisted only when non-empty.
    metadata: dict = field(default_factory=dict)
    # v1.147.0 (#64 follow-on): author-written explicit heading id, e.g.
    #   ## Some heading {#ex-cache42}
    # Lowercased, marker-free. Empty when the heading carried none. When set,
    # it IS this section's slug, so the id stops being a function of the
    # heading's words and survives a rewording.
    anchor: str = ""
    # Ids this section also answers to. Holds the heading-derived id an
    # anchored section WOULD have had, so a consumer holding the pre-anchor id
    # keeps resolving after the anchor is added. Persisted only when non-empty.
    alias_ids: list = field(default_factory=list)
    # [ocr] extra (v1.149.0): set on sections built from a scanned page that
    # an OCR engine read. "ocr" is the only non-empty value. Persisted only
    # when set, so every pre-OCR index round-trips unchanged.
    text_source: str = ""
    ocr_engine: str = ""
    ocr_pages: list = field(default_factory=list)
    ocr_confidence: Optional[float] = None

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict."""
        d = {
            "id": self.id,
            "repo": self.repo,
            "doc_path": self.doc_path,
            "title": self.title,
            "level": self.level,
            "parent_id": self.parent_id,
            "children": self.children,
            "byte_start": self.byte_start,
            "byte_end": self.byte_end,
            "summary": self.summary,
            "tags": self.tags,
            "references": self.references,
            "content_hash": self.content_hash,
        }
        if self.embedding:
            d["embedding"] = self.embedding
        if self.code_blocks:
            d["code_blocks"] = self.code_blocks
        if self.inline_code:
            d["inline_code"] = self.inline_code
        if self.metadata:
            d["metadata"] = self.metadata
        if self.anchor:
            d["anchor"] = self.anchor
        if self.alias_ids:
            d["alias_ids"] = self.alias_ids
        if self.text_source:
            d["text_source"] = self.text_source
        if self.ocr_engine:
            d["ocr_engine"] = self.ocr_engine
        if self.ocr_pages:
            d["ocr_pages"] = self.ocr_pages
        if self.ocr_confidence is not None:
            d["ocr_confidence"] = self.ocr_confidence
        # Preserve inline content for sections that cannot be recovered via
        # byte-range reads (e.g. v1.18 structured-OpenAPI sections that
        # don't map to the raw spec's byte offsets).
        if self.byte_end == 0 and self.content:
            d["content"] = self.content
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Section":
        """Deserialize from a dict."""
        return cls(
            id=data["id"],
            repo=data["repo"],
            doc_path=data["doc_path"],
            title=data["title"],
            content=data.get("content", ""),
            level=data["level"],
            parent_id=data.get("parent_id", ""),
            children=data.get("children", []),
            byte_start=data.get("byte_start", 0),
            byte_end=data.get("byte_end", 0),
            summary=data.get("summary", ""),
            tags=data.get("tags", []),
            references=data.get("references", []),
            content_hash=data.get("content_hash", ""),
            embedding=data.get("embedding", []),
            code_blocks=data.get("code_blocks", []),
            inline_code=data.get("inline_code", []),
            metadata=data.get("metadata", {}),
            anchor=data.get("anchor", ""),
            alias_ids=data.get("alias_ids", []),
            text_source=data.get("text_source", ""),
            ocr_engine=data.get("ocr_engine", ""),
            ocr_pages=data.get("ocr_pages", []),
            ocr_confidence=data.get("ocr_confidence"),
        )


# Explicit heading ids: ``## Heading {#custom-id}`` (Kramdown / PHP Markdown
# Extra / Python-Markdown attr_list, honoured by Jekyll, MkDocs, Hugo and
# Docusaurus). Owned here because BOTH the indexer and the broken-link checker
# must agree on the namespace; they disagreed until v1.147.0, so a link to
# ``doc.md#custom-id`` validated as live while get_section could not resolve it.
_EXPLICIT_ID_MARKER_RE = re.compile(r"\s*\{#[^}]*\}\s*$")
_EXPLICIT_ID_CAPTURE_RE = re.compile(r"\{#(?P<id>[^}]*)\}\s*$")
_SAFE_ANCHOR_ID_RE = re.compile(r"^[A-Za-z][\w-]*$")
# Public alias: get_broken_links validates raw HTML anchor ids against the
# same rule, and a second copy of it is how the two namespaces drifted before.
SAFE_ANCHOR_ID_RE = _SAFE_ANCHOR_ID_RE


def split_explicit_id(title: str) -> tuple:
    """Split a heading title into (text_without_marker, explicit_id_or_None).

    ``explicit_id`` is returned (lowercased) only when the {#id} payload is a
    renderer-safe id; an unsafe payload still has its marker stripped from the
    text so the generated slug is the marker-free text slug, never the polluted
    'heading-text-custom-id' form (#64)."""
    m = _EXPLICIT_ID_CAPTURE_RE.search(title)
    if not m:
        return title, None
    raw_id = m.group("id").strip()
    text = _EXPLICIT_ID_MARKER_RE.sub("", title).rstrip()
    explicit = raw_id.lower() if _SAFE_ANCHOR_ID_RE.match(raw_id) else None
    return text, explicit


def slugify(text: str) -> str:
    """Convert heading text to a URL-safe slug.

    Lowercases, replaces non-alphanumeric sequences with hyphens,
    strips leading/trailing hyphens.
    """
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    text = text.strip("-")
    return text or "section"


def make_section_id(repo: str, doc_path: str, slug: str, level: int) -> str:
    """Build a stable section ID: {repo}::{doc_path}::{slug}#{level}."""
    return f"{repo}::{doc_path}::{slug}#{level}"


def duplicate_anchor_report(sections) -> list:
    """Group explicit anchors written more than once in the SAME document.

    Accepts Section objects or their serialized dicts. Duplicates ACROSS
    documents are not reported: a fragment is relative to the document that
    contains it, so the same id in two files is ordinary and flagging it would
    be a signal that always fires.

    Reported at index time because that is the moment the author can fix it --
    a duplicate found later shows up as a retrieval returning two sections,
    which is honest but arrives too late to be cheap.
    """
    def _get(sec, field):
        return sec.get(field, "") if isinstance(sec, dict) else getattr(sec, field, "")

    grouped: dict = {}
    for sec in sections or ():
        anchor = _get(sec, "anchor")
        if anchor:
            grouped.setdefault((_get(sec, "doc_path"), anchor), []).append(sec)

    report = [
        {
            "doc_path": doc_path,
            "anchor": anchor,
            "count": len(secs),
            "section_ids": [_get(x, "id") for x in secs],
            "titles": [_get(x, "title") for x in secs],
        }
        for (doc_path, anchor), secs in grouped.items()
        if len(secs) > 1
    ]
    report.sort(key=lambda r: (r["doc_path"], r["anchor"]))
    return report


# v1.147.0: the duplicate-anchor list rides on every indexing response, and on
# 1.x a list that ships unbounded cannot be capped later. The cap and the keys
# that disclose it (`duplicate_anchors_total`, `duplicate_anchors_truncated`,
# per-entry `section_ids_truncated`) arrive together.
DUPLICATE_ANCHORS_CAP = 20      # entries in `duplicate_anchors`
DUPLICATE_ANCHOR_IDS_CAP = 20   # `section_ids` / `titles` inside one entry
# How many names a human-readable warning spells out before "(+N more)".
WARNING_NAMES_SHOWN = 5


def names_with_more(names, shown: int = WARNING_NAMES_SHOWN) -> str:
    """``a, b, c (+N more)`` -- the first ``shown`` names, then the remainder's count."""
    names = list(names)
    text = ", ".join(names[:shown])
    return f"{text} (+{len(names) - shown} more)" if len(names) > shown else text


def duplicate_anchor_warning(report: list) -> str:
    """One human-readable line for a duplicate-anchor report, or "" when clean.

    A list of dicts is something a caller must decide to inspect; a sentence is
    something a model relays and a person reads. Both ship, because the list is
    what a tool acts on and the sentence is what reaches the human. Counts come
    from the FULL report, never from the capped list a response carries.
    """
    if not report:
        return ""
    shown = names_with_more(
        (f"{r['anchor']} x{r['count']} in {r['doc_path']}" for r in report), 3
    )
    return (
        f"{len(report)} duplicate anchor id(s) written in the same document: "
        f"{shown}. A reference to one is ambiguous; give each "
        f"heading a distinct {{#id}} to select one."
    )


def duplicate_anchor_fields(sections) -> dict:
    """The response keys an indexing payload carries, built from one report.

    ``duplicate_anchors`` is empty and ``duplicate_anchor_warning`` is ``""`` when
    the sections hold no same-document duplicate. A payload that uses this helper
    carries every key even when clean, because a signal present only when it
    fires cannot be told from one that was never computed.

    The list is capped at ``DUPLICATE_ANCHORS_CAP`` entries and each entry's
    ``section_ids`` / ``titles`` at ``DUPLICATE_ANCHOR_IDS_CAP``. The authority is
    the totals, not ``len()``: ``duplicate_anchors_total`` counts every entry,
    each entry's ``count`` counts every heading, and ``*_truncated`` says whether
    anything was cut. The report is sorted before the cut, so what is kept is
    stable.
    """
    report = duplicate_anchor_report(sections)
    capped = [
        {
            **r,
            "section_ids": r["section_ids"][:DUPLICATE_ANCHOR_IDS_CAP],
            "titles": r["titles"][:DUPLICATE_ANCHOR_IDS_CAP],
            "section_ids_truncated": r["count"] > DUPLICATE_ANCHOR_IDS_CAP,
        }
        for r in report[:DUPLICATE_ANCHORS_CAP]
    ]
    return {
        "duplicate_anchors": capped,
        "duplicate_anchors_total": len(report),
        "duplicate_anchors_truncated": len(report) > DUPLICATE_ANCHORS_CAP,
        "duplicate_anchor_warning": duplicate_anchor_warning(report),
    }


def make_hierarchical_slug(
    heading_text: str,
    heading_level: int,
    slug_stack: list,   # mutable list of (level: int, full_path_slug: str)
    used_slugs: dict,   # mutable collision tracker
    explicit_id: str = "",
) -> str:
    """Compute a stable, hierarchical slug for a heading.

    The slug is prefixed with the ancestor chain so that same-named headings
    under different parents are automatically distinct, e.g.::

        installation/prerequisites    (not just 'prerequisites')
        usage/configuration/advanced  (not just 'advanced')

    This prevents the collision-suffix counter from renumbering when a new
    same-named heading is inserted earlier in the document.

    Mutates ``slug_stack`` and ``used_slugs`` in place.
    Returns the full hierarchical slug to pass to ``make_section_id``.
    """
    # Drop any ancestors at the same or deeper level
    while slug_stack and slug_stack[-1][0] >= heading_level:
        slug_stack.pop()

    parent_path = slug_stack[-1][1] if slug_stack else ""
    leaf = slugify(heading_text)
    derived = f"{parent_path}/{leaf}" if parent_path else leaf
    if explicit_id:
        # An author-chosen id is the WHOLE path, not a leaf under the ancestor
        # chain. The prefix exists only to disambiguate same-named headings;
        # inheriting it would put an ancestor heading's WORDS back into an id
        # whose entire purpose is to stop depending on words, so renaming the
        # H1 would still break every pointer aimed at the H2.
        full_path = explicit_id
    else:
        full_path = derived
    full_path = resolve_slug_collision(full_path, used_slugs)

    slug_stack.append((heading_level, full_path))
    return full_path


def resolve_slug_collision(slug: str, used_slugs: dict) -> str:
    """Return a unique slug, appending -2, -3, etc. on collision.

    Args:
        slug: The desired slug.
        used_slugs: Mutable dict mapping slug -> count of uses so far.

    Returns:
        A unique slug. Updates used_slugs in place.
    """
    if slug not in used_slugs:
        used_slugs[slug] = 1
        return slug

    count = used_slugs[slug] + 1
    used_slugs[slug] = count
    candidate = f"{slug}-{count}"
    # Recurse in case the candidate is also taken (unlikely but safe)
    while candidate in used_slugs:
        count += 1
        used_slugs[slug] = count
        candidate = f"{slug}-{count}"
    used_slugs[candidate] = 1
    return candidate


def compute_content_hash(content: str) -> str:
    """SHA-256 of the section content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# --- Reference and Tag Extraction ---

_TAG_RE = re.compile(r"(?:^|\s)#([A-Za-z][A-Za-z0-9_-]*)", re.MULTILINE)

# Reference extraction (#47, #48). A proper inline-link pass with code-region
# awareness, replacing the two naive regexes that captured titles, angle
# brackets, image targets, and in-code link syntax verbatim.
_REF_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)   # drop comment regions
_REF_FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)  # drop fenced code
_REF_INLINE_CODE_RE = re.compile(r"`[^`]+`")                   # drop inline code spans
# Inline link / image: [text](dest...). Group 1 '!' marks an image; group 2 is
# the raw destination, tolerating one level of balanced parens (wiki URLs).
_REF_LINK_RE = re.compile(r"(!?)\[[^\]]*\]\(([^()]*(?:\([^()]*\)[^()]*)*)\)")
# Link reference definition: [label]: dest "optional title".
_REF_DEF_RE = re.compile(r"^[ \t]{0,3}\[[^\]]+\]:[ \t]*(\S.*?)[ \t]*$", re.MULTILINE)
# Autolink: <scheme:...> or <email>.
_REF_AUTOLINK_RE = re.compile(
    r"<([A-Za-z][A-Za-z0-9+.\-]*:[^>\s]+|[^>\s@]+@[^>\s]+)>"
)
# Bare URL (excludes <>, so it doesn't double-grab an autolink's inner URL).
_REF_BARE_URL_RE = re.compile(r"https?://[^\s)\]'\"<>]+")
_REF_TITLE_RE = re.compile(r"^(\S+)\s+[\"'(].*$")


def _clean_destination(dest: str) -> str:
    """Normalize a link destination: strip a surrounding <...>, split off an
    optional trailing title, and trim whitespace (#47)."""
    dest = dest.strip()
    if dest.startswith("<") and dest.endswith(">"):
        return dest[1:-1].strip()
    m = _REF_TITLE_RE.match(dest)
    if m:
        dest = m.group(1)
    return dest.strip()


def extract_references(content: str) -> list:
    """Extract link/reference targets from content.

    Handles inline links, reference-definition targets, autolinks, and bare
    URLs; skips images and any link syntax that appears inside fenced code,
    inline code spans, or HTML comments. Titles and angle brackets are stripped
    from destinations, and bare URLs lose trailing punctuation.
    """
    refs: list = []

    def add(ref: str) -> None:
        ref = ref.strip()
        if ref and ref not in refs:
            refs.append(ref)

    scrubbed = _REF_HTML_COMMENT_RE.sub(" ", content)
    scrubbed = _REF_FENCE_RE.sub(" ", scrubbed)
    scrubbed = _REF_INLINE_CODE_RE.sub(" ", scrubbed)

    # Reference-style definition targets (#48): [label]: target.
    for dest in _REF_DEF_RE.findall(scrubbed):
        add(_clean_destination(dest))
    # Inline links (images skipped — they are not doc links).
    for bang, dest in _REF_LINK_RE.findall(scrubbed):
        if bang:
            continue
        add(_clean_destination(dest))
    # Strip inline link/image syntax so the autolink/bare-URL passes don't
    # re-grab (and truncate at ')') a destination already handled above.
    rest = _REF_LINK_RE.sub(" ", scrubbed)
    # Autolinks: <https://...> and <user@example.com>.
    for auto in _REF_AUTOLINK_RE.findall(rest):
        add(auto)
    # Bare URLs, trailing punctuation trimmed.
    for url in _REF_BARE_URL_RE.findall(rest):
        add(url.rstrip(".,;:"))
    return refs


def extract_tags(content: str) -> list:
    """Extract #hashtag style tags from content."""
    return list(dict.fromkeys(_TAG_RE.findall(content)))


# Identifier-shaped inline code spans for the code<->docs bridge (#59).
_INLINE_SPAN_RE = re.compile(r"`([^`\n]+)`")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def extract_inline_code(content: str, cap: int = 40) -> list:
    """Extract deduped, identifier-shaped inline code spans from prose.

    Callers should pass a fenced-code-free view (fenced code is its own
    artifact). A trailing ``()`` is dropped; spans must look like an
    identifier and be >= 3 chars. Capped to keep index growth negligible.
    """
    out: list = []
    seen: set = set()
    for span in _INLINE_SPAN_RE.findall(content):
        name = span.strip()
        if name.endswith("()"):
            name = name[:-2]
        if len(name) < 3 or not _IDENT_RE.match(name):
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
        if len(out) >= cap:
            break
    return out
