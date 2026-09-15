"""get_broken_links tool: Detect internal cross-references that no longer resolve."""

import os
import posixpath
import re
import time
from typing import Optional

from ..storage import DocStore
from ..parser import ALL_EXTENSIONS
from ..parser.sections import SAFE_ANCHOR_ID_RE, split_explicit_id

# Links that start with these are external — skip them
_EXTERNAL_SCHEMES = ("http://", "https://", "ftp://", "mailto:", "tel:")
_EMAIL_RE = re.compile(r"^[^\s/@]+@[^\s/@]+\.[^\s/@]+$")
# A URL scheme prefix (scheme:) — used to flag typo'd/unknown schemes (#47.6).
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")

# RST cross-reference patterns: :ref:`target`, :doc:`target`
_RST_REF_RE = re.compile(r":(?:ref|doc):`([^`]+)`")

# RST explicit hyperlink targets: `text <target>`_
_RST_HYPERLINK_RE = re.compile(r"`[^`]+\s+<([^>]+)>`_")

# --- Rendered-anchor namespace (#50, #64) -----------------------------------
# Section titles preserve the raw inline markdown, but a renderer emits anchors
# from the heading's rendered TEXT content, then github-slugger rules. Validate
# #anchor links against the anchors a renderer actually emits, NOT jdocmunch's
# private section slugs: the private slug is an internal index artifact no
# renderer ever produces, so trusting it only hid genuinely broken links (#64).
_GH_REDUCTIONS = [
    (re.compile(r"!\[([^\]]*)\]\([^)]*\)"), r"\1"),   # inline image -> alt
    (re.compile(r"!\[([^\]]*)\]\[[^\]]*\]"), r"\1"),  # reference image -> alt
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),    # inline link -> label
    (re.compile(r"\[([^\]]*)\]\[[^\]]*\]"), r"\1"),   # reference link -> label
    (re.compile(r"`([^`]+)`"), r"\1"),                # code span -> content
    (re.compile(r"\*\*([^*]+)\*\*"), r"\1"),          # strong -> inner
    (re.compile(r"__([^_]+)__"), r"\1"),
    (re.compile(r"\*([^*]+)\*"), r"\1"),              # emphasis -> inner
]
_GH_SLUG_STRIP_RE = re.compile(r"[^\w\- ]")


def _rendered_text(title: str) -> str:
    """Reduce raw inline markdown to the text content a renderer emits."""
    text = title
    for _ in range(8):  # fixed point; handles nesting like [**x**](y)
        before = text
        for pattern, repl in _GH_REDUCTIONS:
            text = pattern.sub(repl, text)
        if text == before:
            break
    return text


def _github_slug(text: str) -> str:
    """github-slugger base rules: lowercase, drop punctuation except - and _,
    spaces to hyphens; underscores and hyphen runs are preserved."""
    return _GH_SLUG_STRIP_RE.sub("", text.lower()).replace(" ", "-")


# Explicit heading ids: a trailing {#custom-id} (Kramdown / Python-Markdown /
# SSG attribute syntax). The id is a rendered anchor in its own right; the
# marker is NOT part of the generated text slug, so it is stripped before
# slugging — a heading "Foo {#bar}" renders anchors `bar` and the text slug
# `foo`, never the marker-polluted `foo-bar`.
# Raw HTML anchor targets embedded in doc bodies: <a id=>, <a name=>, <h* id=>.
# Every Markdown engine that allows inline HTML renders these as real anchors.
_HTML_ANCHOR_RE = re.compile(r"""<a\b[^>]*?\b(?:id|name)\s*=\s*["'](?P<id>[^"']+)["']""", re.I)
_HTML_HEADING_ID_RE = re.compile(r"""<h[1-6]\b[^>]*?\bid\s*=\s*["'](?P<id>[^"']+)["']""", re.I)
# A renderer-safe explicit/HTML id: starts with a letter, id-safe chars only. A
# leading digit or whitespace means no renderer emits a usable fragment
# (e.g. id="bad id with spaces", {#1-invalid} both fall back to a text slug).
_SAFE_ANCHOR_ID_RE = SAFE_ANCHOR_ID_RE
# Code/comment scrubbing so an <a id=> shown inside a fenced example or an HTML
# comment is not mistaken for a real anchor target.
_FENCE_TOGGLE_RE = re.compile(r"^\s{0,3}(```+|~~~+)")
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


# v1.147.0: the splitter moved to parser/sections.py so the INDEXER and this
# checker cannot drift apart. Re-exported under the old private name because
# tests import it from here.
_split_explicit_id = split_explicit_id


def _scrub_code(text: str) -> str:
    """Blank fenced-code blocks, inline code spans, and HTML comments so an
    anchor that only appears inside a code example or a comment is not collected
    as a live target."""
    text = _HTML_COMMENT_RE.sub(" ", text)
    kept = []
    in_fence = False
    for line in text.splitlines():
        if _FENCE_TOGGLE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            kept.append(line)
    return _INLINE_CODE_RE.sub(" ", "\n".join(kept))


def _build_rendered_anchors(sections: list, raw_by_doc: Optional[dict] = None) -> dict:
    """Map each doc_path to the set of anchors a Markdown renderer would emit.

    The namespace (NOT jdocmunch's private section slugs, #64):
      - generated heading anchors: rendered heading text (explicit-id marker
        stripped) via github-slugger, duplicates suffixed -1/-2 in doc order;
      - explicit ``{#custom-id}`` heading ids (Kramdown / Python-Markdown / SSG);
      - raw HTML ``<a id=>`` / ``<a name=>`` / ``<h* id=>`` anchors in the body;
      - GitHub ``user-content-`` aliases for explicit and HTML ids.

    ``sections`` supplies the heading anchors (titles are persisted). ``raw_by_doc``
    maps doc_path -> raw source text and supplies the HTML anchors (section
    bodies are NOT persisted — only byte ranges — so the raw cached file is the
    source). Omitting it simply yields no HTML anchors for that doc.

    Models GitHub-flavored Markdown plus the explicit-id / HTML-anchor surface
    common to static-site generators (MkDocs, Docusaurus, Hugo, Jekyll, ...).
    Non-GitHub slug dialects (GitLab hyphen-collapse, Bitbucket
    ``markdown-header-`` prefix, Obsidian wikilinks) are deliberately out of
    scope — modeling them would only widen acceptance and re-hide broken links.
    """
    by_doc: dict = {}
    occ: dict = {}

    def _add(doc: str, anchor: str) -> None:
        if anchor:
            by_doc.setdefault(doc, set()).add(anchor.lower())

    # Generated + explicit heading anchors (headings only; the synthetic level-0
    # doc root has no heading of its own).
    for sec in sections:
        if sec.get("level", 0) == 0:
            continue
        doc = sec.get("doc_path", "")
        # v1.147.0: the indexer now strips the marker and persists the id on
        # the section, so the stored ``anchor`` is the authority. Splitting the
        # title is the LEGACY path and must stay: an index built before this
        # version has no ``anchor`` field and still carries the marker verbatim
        # in its title, and it must keep validating without a reindex.
        text = sec.get("title", "")
        explicit_id = sec.get("anchor") or None
        if not explicit_id:
            text, explicit_id = _split_explicit_id(text)
        if explicit_id:
            _add(doc, explicit_id)
            _add(doc, f"user-content-{explicit_id}")
        base = _github_slug(_rendered_text(text))
        if base:
            d_occ = occ.setdefault(doc, {})
            if base in d_occ:
                d_occ[base] += 1
                _add(doc, f"{base}-{d_occ[base]}")
            else:
                d_occ[base] = 0
                _add(doc, base)

    # Raw HTML anchors from the cached source (prose view only — fenced/inline
    # code and HTML comments are scrubbed so example/commented anchors don't
    # count as live targets).
    for doc, raw in (raw_by_doc or {}).items():
        body = _scrub_code(raw or "")
        for rx in (_HTML_ANCHOR_RE, _HTML_HEADING_ID_RE):
            for m in rx.finditer(body):
                anchor_id = m.group("id").strip()
                if _SAFE_ANCHOR_ID_RE.match(anchor_id):
                    _add(doc, anchor_id)
                    _add(doc, f"user-content-{anchor_id}")

    return by_doc


def _is_external(href: str) -> bool:
    return any(href.startswith(s) for s in _EXTERNAL_SCHEMES) or bool(_EMAIL_RE.match(href))


def _split_href(href: str) -> tuple:
    """Split href into (file_part, anchor_part). Either may be empty string."""
    if "#" in href:
        file_part, anchor = href.split("#", 1)
    else:
        file_part, anchor = href, ""
    return file_part.strip(), anchor.strip()


def _resolve_file_path(source_doc: str, target_file: str) -> str:
    """Resolve a relative link target against the source document's directory.

    source_doc: e.g.  'docs/guide/install.md'
    target_file: e.g. '../api.md'
    Returns: normalized path like 'docs/api.md'
    """
    if target_file.startswith("/"):
        # Absolute path within the repo root
        return target_file.lstrip("/")
    source_dir = posixpath.dirname(source_doc.replace("\\", "/"))
    joined = posixpath.join(source_dir, target_file.replace("\\", "/"))
    return posixpath.normpath(joined)


def _anchor_matches_section(anchor: str, rendered_anchors: Optional[set]) -> bool:
    """Return True if ``anchor`` is in the document's rendered-anchor namespace.

    Matching is case-insensitive and preserves hyphens and underscores —
    'foo-bar' must NOT match 'foobar'. jdocmunch's private section slugs are
    deliberately NOT consulted (#64): the private slug (underscore flattening,
    hyphen-run collapse, hierarchical leaf, parse-time slugify) is an internal
    index artifact no Markdown renderer emits, so accepting it only ever hid
    genuinely broken links. ``rendered_anchors`` is the per-document set built
    by ``_build_rendered_anchors``.
    """
    if not rendered_anchors:
        return False
    return anchor.strip().lower() in rendered_anchors


def get_broken_links(
    repo: str,
    storage_path: Optional[str] = None,
) -> dict:
    """Scan indexed doc files for internal cross-references that no longer resolve.

    Checks:
    - Markdown links [text](target) with relative file paths
    - RST :ref: and :doc: directives
    - Anchor-only links (#heading) within the same doc

    External links (http/https/mailto) are skipped.
    Output: list of {source_file, source_section, source_section_id, target, reason}
    """
    t0 = time.perf_counter()
    store = DocStore(base_path=storage_path)
    owner, name = store._resolve_repo(repo)
    index = store.load_index(owner, name)

    if not index:
        return {"error": f"Repo not found: {repo}"}

    doc_path_set = set(index.doc_paths)
    sections = index.sections
    src_root = getattr(index, "source_root", "") or ""

    # Raw source per doc, from the on-disk content cache (populated for local and
    # GitHub indexes alike). Section bodies aren't persisted — only byte ranges —
    # so this is how the HTML-anchor namespace (#64) is recovered.
    raw_by_doc: dict = {}
    content_dir = store._content_dir(owner, name)
    for doc in doc_path_set:
        cached = store._safe_content_path(content_dir, doc)
        if cached and cached.exists():
            try:
                raw_by_doc[doc] = cached.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    rendered_by_doc = _build_rendered_anchors(sections, raw_by_doc)  # #50, #64
    broken: list = []

    for sec in sections:
        source_doc = sec.get("doc_path", "")
        sec_id = sec.get("id", "")
        sec_title = sec.get("title", "")
        refs = sec.get("references", [])

        # Collect internal refs from the stored references list
        internal_refs = [r for r in refs if r and not _is_external(r)]

        # Also scan content for RST patterns if content is present
        content = sec.get("content", "")
        if content:
            for m in _RST_REF_RE.finditer(content):
                ref = m.group(1).strip()
                if not _is_external(ref) and ref not in internal_refs:
                    internal_refs.append(ref)
            for m in _RST_HYPERLINK_RE.finditer(content):
                ref = m.group(1).strip()
                if not _is_external(ref) and ref not in internal_refs:
                    internal_refs.append(ref)

        for href in internal_refs:
            file_part, anchor = _split_href(href)

            # Anchor-only link (e.g. #installation): relative to the current document
            if not file_part and anchor:
                if not _anchor_matches_section(anchor, rendered_by_doc.get(source_doc)):
                    broken.append({
                        "source_file": source_doc,
                        "source_section": sec_title,
                        "source_section_id": sec_id,
                        "target": href,
                        "reason": "anchor_not_found",
                    })
                continue

            # Skip non-file refs (bare words like "external-project", RST directives without paths)
            if not file_part:
                continue

            # A scheme prefix means a URL. Known external schemes were already
            # filtered; anything still here is an unrecognized/typo'd scheme —
            # a genuinely dead link, not something to silently drop (#47.6).
            if _SCHEME_RE.match(file_part):
                broken.append({
                    "source_file": source_doc,
                    "source_section": sec_title,
                    "source_section_id": sec_id,
                    "target": href,
                    "reason": "unknown_scheme",
                })
                continue

            resolved = _resolve_file_path(source_doc, file_part)

            if resolved not in doc_path_set:
                # Not an indexed doc — but it may be an existing non-doc file
                # (image, LICENSE, source). Stat the filesystem before flagging
                # it missing (#49). With no source_root (e.g. GitHub indexes) we
                # can't stat, so don't claim missing for non-doc extensions.
                if src_root:
                    if os.path.exists(os.path.join(src_root, resolved)):
                        continue
                else:
                    ext = os.path.splitext(resolved)[1].lower()
                    if ext and ext not in ALL_EXTENSIONS:
                        continue
                broken.append({
                    "source_file": source_doc,
                    "source_section": sec_title,
                    "source_section_id": sec_id,
                    "target": href,
                    "reason": "file_not_found",
                })
                continue

            # File exists; now check anchor if present
            if anchor and not _anchor_matches_section(anchor, rendered_by_doc.get(resolved)):
                broken.append({
                    "source_file": source_doc,
                    "source_section": sec_title,
                    "source_section_id": sec_id,
                    "target": href,
                    "reason": "section_not_found",
                })

    return {
        "result": {
            "repo": f"{owner}/{name}",
            "docs_scanned": len(doc_path_set),
            "sections_scanned": len(sections),
            "broken_link_count": len(broken),
            "broken_links": broken,
        },
        "_meta": {
            "timing_ms": round((time.perf_counter() - t0) * 1000, 1),
        },
    }
