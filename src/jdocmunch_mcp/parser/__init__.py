"""Parser dispatcher for doc files."""

from .markdown_parser import parse_markdown, strip_mdx
from .rst_parser import parse_rst
from .asciidoc_parser import parse_asciidoc
from .org_parser import parse_org
from .notebook_parser import convert_notebook
from .html_parser import convert_html
from .openapi_parser import convert_openapi, sniff_openapi
from .openapi_structured import parse_openapi_structured
from .json_parser import convert_json
from .xml_parser import convert_xml
from .text_parser import parse_text
from .godot_parser import convert_godot
from .office import OFFICE_EXTENSIONS
from .hierarchy import wire_hierarchy


# Supported extensions -> parser key
ALL_EXTENSIONS = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".mdx": "markdown",  # MDX = Markdown + JSX; stripped before parsing
    ".txt": "text",
    ".rst": "rst",
    ".adoc": "asciidoc",
    ".asciidoc": "asciidoc",
    ".asc": "asciidoc",
    ".org": "org",
    ".ipynb": "notebook",
    ".html": "html",
    ".htm": "html",
    ".yaml": "openapi",  # indexed only when content sniff confirms OpenAPI/Swagger
    ".yml": "openapi",
    ".json": "openapi",  # OpenAPI specs get full treatment; plain JSON falls back to json parser
    ".jsonc": "json",
    ".xml": "xml",
    ".svg": "xml",
    ".xhtml": "xml",
    ".tscn": "godot",
    ".tres": "godot",
}


def preprocess_content(content: str, doc_path: str) -> str:
    """Preprocess file content before parsing and storage.

    Converts structured formats to Markdown so that parse_file() can use the
    Markdown parser uniformly:
    - .mdx → clean Markdown via strip_mdx() (removes frontmatter, imports, JSX)
    - .ipynb / .html → Markdown via notebook/HTML converters
    - .xml / .svg / .xhtml → Markdown via XML converter
    - .jsonc → Markdown via JSON converter (JSONC comments stripped)
    - .json (OpenAPI/Swagger) → Markdown via OpenAPI converter
    - .json (plain) → Markdown via JSON converter
    - .yaml / .yml (OpenAPI/Swagger) → Markdown via OpenAPI converter
    - All other formats → returned unchanged

    Args:
        content: Raw file content.
        doc_path: Relative file path (used to detect extension).

    Returns:
        Content ready for parse_file() and for storage as the raw file.
    """
    import os
    # Strip a leading UTF-8 BOM (U+FEFF) (#53). It survives both ingestion legs
    # (read_text and httpx response.text) and is not Python whitespace, so it
    # silently breaks every first-line detector (ATX heading, frontmatter fence,
    # setext underline). Stripping here fixes all formats at once and keeps the
    # stored content mirror aligned with the parsed string.
    if content.startswith("\ufeff"):
        content = content[1:]
    ext = os.path.splitext(doc_path)[1].lower()
    if ext == ".mdx":
        return strip_mdx(content)
    if ext == ".ipynb":
        return convert_notebook(content)
    if ext in (".html", ".htm"):
        return convert_html(content)
    if ext in (".xml", ".svg", ".xhtml"):
        return convert_xml(content, doc_path)
    if ext in (".tscn", ".tres"):
        return convert_godot(content, doc_path)
    if ext == ".jsonc":
        return convert_json(content, doc_path)
    if sniff_openapi(content, ext):
        # v1.18.0: keep raw spec; parse_file dispatches to the structured
        # parser. (Pre-v1.18 we converted to markdown here.)
        return content
    if ext == ".json":
        return convert_json(content, doc_path)
    return content


def parse_file(content: str, doc_path: str, repo: str) -> list:
    """Parse a document file into Section objects with hierarchy wired.

    Args:
        content: Raw file content (already preprocessed by preprocess_content).
        doc_path: Relative file path (used in IDs and section metadata).
        repo: Repository identifier.

    Returns:
        List of Section objects with parent_id/children populated.
    """
    import os
    _, ext = os.path.splitext(doc_path)
    ext = ext.lower()
    doc_type = ALL_EXTENSIONS.get(ext, "text")

    if ext in OFFICE_EXTENSIONS:
        # Binary office formats (.pdf/.docx/.pptx/.epub) are converted to
        # Markdown at read time (parser/office.py) — the content arriving
        # here is already Markdown text.
        sections = parse_markdown(content, doc_path, repo)
    elif doc_type == "markdown":
        # .mdx already stripped by preprocess_content(); parse directly
        sections = parse_markdown(content, doc_path, repo)
    elif doc_type == "rst":
        sections = parse_rst(content, doc_path, repo)
    elif doc_type == "asciidoc":
        sections = parse_asciidoc(content, doc_path, repo)
    elif doc_type == "org":
        sections = parse_org(content, doc_path, repo)
    elif doc_type in ("notebook", "html"):
        # content already preprocessed to markdown by preprocess_content()
        sections = parse_markdown(content, doc_path, repo)
    elif doc_type == "openapi":
        # v1.18.0: structured OpenAPI parser when the content is still raw
        # YAML/JSON. Plain `.json` files come through here pre-converted to
        # markdown (preprocess_content's convert_json branch) — detect that
        # and parse as markdown directly.
        if content.lstrip().startswith("# "):
            sections = parse_markdown(content, doc_path, repo)
        else:
            sections = parse_openapi_structured(content, doc_path, repo)
            if not sections:
                md = convert_openapi(content)
                if md and md.lstrip().startswith("# "):
                    sections = parse_markdown(md, doc_path, repo)
                else:
                    sections = []
    elif doc_type in ("json", "xml", "godot"):
        # content already preprocessed to markdown by preprocess_content()
        if content.lstrip().startswith("# "):
            sections = parse_markdown(content, doc_path, repo)
        else:
            sections = []
    else:
        sections = parse_text(content, doc_path, repo)

    return wire_hierarchy(sections)
