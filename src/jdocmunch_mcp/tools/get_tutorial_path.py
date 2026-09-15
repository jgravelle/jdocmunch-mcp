"""get_tutorial_path — reconstruct an ordered tutorial chain (v1.22.0).

A tutorial in real docs is rarely tagged as one — authors thread it via
``Next:`` / ``Previous:`` links, frontmatter ``next:`` / ``prev:`` keys,
or simply ordered filenames (``01-intro.md`` / ``02-setup.md``).

This tool walks any of those signals from a starting section and returns
the linear chain. Three detection strategies, tried in order:

  1. **Frontmatter next/prev** — when the section's owning doc has YAML
     frontmatter with ``next:`` and/or ``prev:`` keys pointing at other
     doc paths.
  2. **Inline Next:/Previous: links** — markdown link lines beginning
     with ``Next:``, ``Next →``, ``Previous:``, or ``← Prev`` whose
     target resolves to another doc in the index.
  3. **Ordered filename prefix** — files named ``NN-name.<ext>`` where
     NN is a 2+ digit prefix; the chain runs in numeric order.

Returns ``{start, chain:[{section_id, doc_path, title}], strategy,
truncated}``. Cycles are broken once a doc repeats. Length cap at 50
keeps pathological inputs bounded.
"""

from __future__ import annotations

import posixpath
import re
import time
from typing import Optional

from ..storage import DocStore

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n", re.DOTALL)
# Matches lines like "Next: [Setup](setup.md)" or "Next →: foo.md".
_NEXT_LINK_RE = re.compile(
    r"^\s*(?:next|→\s*next|next\s*→)\s*[:→]?\s*(?:\[[^\]]*\]\(([^)]+)\)|(\S+\.\w+))",
    re.IGNORECASE | re.MULTILINE,
)
_PREV_LINK_RE = re.compile(
    r"^\s*(?:prev(?:ious)?|←\s*prev|prev\s*←)\s*[:←]?\s*(?:\[[^\]]*\]\(([^)]+)\)|(\S+\.\w+))",
    re.IGNORECASE | re.MULTILINE,
)
_NUMBER_PREFIX_RE = re.compile(r"^(\d{2,})[-_.]")
_MAX_CHAIN = 50


def _frontmatter(text: str) -> dict:
    if not text:
        return {}
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    try:
        import yaml  # type: ignore
    except Exception:
        return {}
    try:
        data = yaml.safe_load(m.group(1))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _section_for_doc(doc_path: str, sections: list) -> Optional[dict]:
    """Return the level-1 (or first) section of ``doc_path``."""
    candidates = [s for s in sections if s.get("doc_path") == doc_path]
    if not candidates:
        return None
    candidates.sort(key=lambda s: (s.get("level", 99), s.get("byte_start", 0)))
    return candidates[0]


def _resolve_link(target: str, source_doc: str, doc_paths: set) -> Optional[str]:
    """Resolve a link target against the set of indexed doc paths."""
    if not target:
        return None
    target = target.split("#", 1)[0].strip()
    if not target:
        return None
    # Same-doc anchors are not tutorial links.
    if target in doc_paths:
        return target
    # Relative resolution.
    import posixpath
    src_dir = posixpath.dirname(source_doc.replace("\\", "/"))
    joined = posixpath.normpath(posixpath.join(src_dir, target.replace("\\", "/")))
    if joined in doc_paths:
        return joined
    return None


def _content_of(store, owner: str, name: str, doc_path: str) -> str:
    file_path = store._safe_content_path(store._content_dir(owner, name), doc_path)
    if not file_path or not file_path.exists():
        return ""
    try:
        return file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _frontmatter_chain(store, owner: str, name: str, start_doc: str, doc_paths: set) -> list[str]:
    """Walk frontmatter next: links from start_doc forward."""
    chain = [start_doc]
    seen = {start_doc}
    cur = start_doc
    while len(chain) < _MAX_CHAIN:
        text = _content_of(store, owner, name, cur)
        fm = _frontmatter(text)
        nxt = fm.get("next")
        if not isinstance(nxt, str):
            break
        resolved = _resolve_link(nxt, cur, doc_paths)
        if not resolved or resolved in seen:
            break
        chain.append(resolved)
        seen.add(resolved)
        cur = resolved
    return chain if len(chain) > 1 else []


def _inline_link_chain(store, owner: str, name: str, start_doc: str, doc_paths: set) -> list[str]:
    """Walk inline Next:/Previous: links from start_doc forward."""
    chain = [start_doc]
    seen = {start_doc}
    cur = start_doc
    while len(chain) < _MAX_CHAIN:
        text = _content_of(store, owner, name, cur)
        m = _NEXT_LINK_RE.search(text)
        if not m:
            break
        target = m.group(1) or m.group(2)
        resolved = _resolve_link(target or "", cur, doc_paths)
        if not resolved or resolved in seen:
            break
        chain.append(resolved)
        seen.add(resolved)
        cur = resolved
    return chain if len(chain) > 1 else []


# Sphinx ``.. toctree::`` directive — captures the indented entry block.
# Entries can be plain doc names (without extension), referenced labels,
# or "label <doc>" forms. Common options (``:maxdepth:``, ``:caption:``,
# ``:hidden:``, ``:glob:``) appear before the entry list and must be
# tolerated.
_TOCTREE_RE = re.compile(
    r"(?m)^\.\.\s+toctree::\s*\n((?:[ \t]+.*\n?|\n)+)",
)


def _toctree_chain(store, owner: str, name: str, start_doc: str, doc_paths: set) -> list[str]:
    """Sphinx-style ``.. toctree::`` chain.

    Strategy: scan start_doc for a toctree block, extract its entries,
    resolve them against the index (try doc_path verbatim, with .rst,
    with .md, with /index.rst, etc.), and return the chain. Recursion
    into the resolved docs would be possible (toctrees can chain) but
    we keep it simple: one toctree, one chain.
    """
    text = _content_of(store, owner, name, start_doc)
    if not text:
        return []
    m = _TOCTREE_RE.search(text)
    if not m:
        return []
    block = m.group(1)
    chain = [start_doc]
    seen = {start_doc}
    for raw in block.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        # Skip directive options like ":maxdepth: 2", ":hidden:", ":glob:".
        if stripped.startswith(":") and stripped.endswith(":"):
            continue
        if stripped.startswith(":"):
            continue
        # "Display label <real-doc>" — extract the angle-bracket target.
        if "<" in stripped and stripped.endswith(">"):
            stripped = stripped.rsplit("<", 1)[1].rstrip(">").strip()
        # Try a series of likely resolutions.
        candidates = [
            stripped,
            stripped + ".rst",
            stripped + ".md",
            posixpath.join(stripped, "index.rst") if "/" not in stripped else stripped,
            posixpath.join(stripped, "index.md") if "/" not in stripped else stripped,
        ]
        # Resolve relative to start_doc's dir as well.
        src_dir = posixpath.dirname(start_doc.replace("\\", "/"))
        more_candidates = []
        for c in candidates:
            if src_dir:
                more_candidates.append(posixpath.normpath(posixpath.join(src_dir, c)))
        candidates.extend(more_candidates)

        resolved = None
        for c in candidates:
            if c in doc_paths:
                resolved = c
                break
        if not resolved or resolved in seen:
            continue
        chain.append(resolved)
        seen.add(resolved)
        if len(chain) >= _MAX_CHAIN:
            break
    return chain if len(chain) > 1 else []


def _vuepress_chain(store, owner: str, name: str, start_doc: str, doc_paths: set, *, index=None) -> list[str]:
    """VuePress-style sidebar config chain.

    Resolution order for the config payload:
      1. v1.30+: when ``index.source_root`` is populated, re-read the
         original ``.vuepress/config.json`` directly from disk so nested
         dict structures (grouped-dict sidebar form) survive.
      2. The cached file (which ``convert_json`` may have flattened to
         markdown — only the flat-string sidebar form survives that).
    """
    import json

    # Walk parents to find a `.vuepress/config.json`.
    src_norm = start_doc.replace("\\", "/")
    parts = src_norm.split("/")
    cfg_paths_to_try = []
    for i in range(len(parts), 0, -1):
        prefix = "/".join(parts[:i - 1]) if i > 1 else ""
        cand = posixpath.join(prefix, ".vuepress", "config.json").lstrip("/")
        cfg_paths_to_try.append(cand)
    cfg_paths_to_try.append(".vuepress/config.json")

    cfg_text = None
    # v1.30: try the raw on-disk file first via source_root.
    source_root = getattr(index, "source_root", "") if index is not None else ""
    if source_root:
        from pathlib import Path as _P
        for cand in cfg_paths_to_try:
            raw_path = _P(source_root) / cand
            if raw_path.exists():
                try:
                    cfg_text = raw_path.read_text(encoding="utf-8")
                    break
                except OSError:
                    continue

    # Fallback: cached/converted form.
    if not cfg_text:
        for cand in cfg_paths_to_try:
            if cand in doc_paths:
                cfg_text = _content_of(store, owner, name, cand)
                if cfg_text:
                    break
    if not cfg_text:
        return []

    sidebar_paths: list[str] = []

    # Fast path: cfg_text is still raw JSON.
    parsed_json = None
    try:
        parsed_json = json.loads(cfg_text)
    except Exception:
        parsed_json = None

    if isinstance(parsed_json, dict):
        sidebar = (parsed_json.get("themeConfig") or {}).get("sidebar")
        if sidebar is None:
            sidebar = parsed_json.get("sidebar")
        if sidebar is None:
            return []

        def _walk(node):
            if isinstance(node, str):
                sidebar_paths.append(node)
            elif isinstance(node, dict):
                link = node.get("link") or node.get("path")
                if isinstance(link, str):
                    sidebar_paths.append(link)
                children = node.get("children") or node.get("items")
                if isinstance(children, list):
                    for c in children:
                        _walk(c)
            elif isinstance(node, list):
                for c in node:
                    _walk(c)

        _walk(sidebar)
    else:
        # Slow path: ``convert_json`` rewrote the .json file into a markdown
        # document at index time. Walk the lines after a ``### sidebar`` (or
        # similarly-named) heading and pull bullet-list entries.
        in_sidebar = False
        for line in cfg_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            # Heading line — toggle in_sidebar based on whether this is
            # the section we want.
            if stripped.startswith("#"):
                title = stripped.lstrip("#").strip().lower()
                in_sidebar = title.endswith("sidebar")
                continue
            if not in_sidebar:
                continue
            # Bullet entry. ``convert_json`` emits dict child entries as
            # nested headings, so the flat-string sidebar form lands as
            # plain bullets here.
            if stripped.startswith("-"):
                value = stripped.lstrip("-").strip()
                if value.startswith("`") and value.endswith("`"):
                    value = value.strip("`").strip()
                if value:
                    sidebar_paths.append(value)

    # Map each sidebar entry to an indexed doc_path. VuePress paths are
    # often "/", "/install/", or "/api/auth.html" — try several resolutions.
    def _resolve(p: str) -> Optional[str]:
        p = p.lstrip("/")
        candidates = [
            p,
            p + ".md",
            posixpath.join(p, "README.md") if not p.endswith(".md") else p,
            posixpath.join(p, "index.md") if not p.endswith(".md") else p,
            p.rstrip("/") + ".md",
        ]
        for c in candidates:
            if c in doc_paths:
                return c
        return None

    resolved_paths: list[str] = []
    for raw in sidebar_paths:
        resolved = _resolve(raw)
        if resolved and resolved not in resolved_paths:
            resolved_paths.append(resolved)

    if start_doc not in resolved_paths:
        return []
    start_idx = resolved_paths.index(start_doc)
    return resolved_paths[start_idx:]


def _ordered_filename_chain(start_doc: str, doc_paths: set) -> list[str]:
    """Build the chain from start_doc by ordered numeric filename prefix."""
    import posixpath
    start_norm = start_doc.replace("\\", "/")
    src_dir = posixpath.dirname(start_norm)
    siblings = [
        p for p in doc_paths
        if posixpath.dirname(p.replace("\\", "/")) == src_dir
    ]
    numbered: list[tuple[int, str]] = []
    for p in siblings:
        base = posixpath.basename(p)
        m = _NUMBER_PREFIX_RE.match(base)
        if m:
            numbered.append((int(m.group(1)), p))
    if len(numbered) < 2:
        return []
    numbered.sort()
    ordered_paths = [p for _, p in numbered]
    if start_norm not in ordered_paths:
        return []
    start_idx = ordered_paths.index(start_norm)
    return ordered_paths[start_idx:]


def get_tutorial_path(
    repo: str,
    section_id: str,
    storage_path: Optional[str] = None,
) -> dict:
    t0 = time.perf_counter()
    store = DocStore(base_path=storage_path)
    owner, name = store._resolve_repo(repo)
    index = store.load_index(owner, name)
    if not index:
        return {"error": f"Repo not found: {repo}"}

    sec = index.get_section(section_id)
    # v1.147.0: an ambiguous `doc.md#anchor` reference is disclosed, never
    # resolved silently -- on the miss path as well as the hit path.
    disclosure = index.anchor_disclosure(section_id)
    if not sec:
        if disclosure:
            return {"error": disclosure["warning"], **disclosure}
        return {"error": f"Section not found: {section_id}"}

    start_doc = sec.get("doc_path", "")
    if not start_doc:
        return {"error": "Section has no doc_path"}

    doc_paths_set = set(index.doc_paths)

    chain_paths: list[str] = []
    strategy = "none"
    for fn, label in (
        (_frontmatter_chain, "frontmatter"),
        (_inline_link_chain, "inline_link"),
        (_toctree_chain, "sphinx_toctree"),
        (_vuepress_chain, "vuepress_sidebar"),
    ):
        if label == "vuepress_sidebar":
            result = fn(store, owner, name, start_doc, doc_paths_set, index=index)
        else:
            result = fn(store, owner, name, start_doc, doc_paths_set)
        if result:
            chain_paths = result
            strategy = label
            break
    if not chain_paths:
        result = _ordered_filename_chain(start_doc, doc_paths_set)
        if result:
            chain_paths = result
            strategy = "ordered_filename"

    chain: list[dict] = []
    for dp in chain_paths:
        s = _section_for_doc(dp, index.sections)
        if not s:
            continue
        chain.append(
            {
                "section_id": s.get("id"),
                "doc_path": dp,
                "title": s.get("title", ""),
            }
        )

    out = {
        "repo": f"{owner}/{name}",
        "start": {"section_id": section_id, "doc_path": start_doc},
        "chain": chain,
        "strategy": strategy,
        "truncated": len(chain_paths) >= _MAX_CHAIN,
        "_meta": {
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "chain_length": len(chain),
        },
    }
    if disclosure:
        out.update(disclosure)
    return out
