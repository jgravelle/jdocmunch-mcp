"""Office/binary document conversion (.pdf, .docx, .pptx, .epub) via markitdown.

Optional capability: ``pip install jdocmunch-mcp[office]``. Without the extra,
office files are skipped at discovery with a distinct skip reason
(``office_extra_not_installed``) so coverage reporting stays honest.

Conversion is 100% local. markitdown's optional cloud converters (LLM image
description, Azure Document Intelligence, YouTube transcription) are never
enabled: the converter is constructed with ``enable_plugins=False`` and no
``llm_client``/``docintel_endpoint``, so no network request can originate here.

Converted Markdown is cached under the doc-index storage root keyed by a
sha256 of the file bytes + the installed markitdown version, so a corpus
refresh never re-converts an unchanged document.
"""

import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Deliberately excludes tabular formats (.csv/.xlsx) — the jMunch suite routes
# tabular data to jdatamunch-mcp, not the doc indexer.
OFFICE_EXTENSIONS: set = {".pdf", ".docx", ".pptx", ".epub"}

# Binary office documents are legitimately far larger than text docs (the
# corpus-wide DEFAULT_MAX_FILE_SIZE is 500KB); real-world PDFs/decks run tens
# of MB while their extracted Markdown stays small. Discovery applies this cap
# to office extensions instead.
OFFICE_MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB

_AVAILABLE: Optional[bool] = None
_CONVERTER = None


#: Skip reason for an office document whose conversion yielded no readable
#: text: in practice a scanned (image-only) PDF, which has no text layer.
NO_TEXT_REASON = "no_extractable_text"


def has_readable_text(text: str) -> bool:
    """Whether converted text holds anything a reader or a search could use.

    A scanned PDF converts to ``''`` (or only whitespace and form feeds) with
    no exception. Before this check it was stored as an empty document and
    counted as indexed, so a search found nothing and nothing said why.
    """
    return any(ch.isalnum() for ch in text or "")


def no_text_warning(rel_path: str) -> str:
    return (
        f"No readable text in {rel_path}: it looks like a scanned or "
        "image-only document. It was not indexed. jDocMunch does not run "
        "OCR, so its contents cannot be searched."
    )


def office_available() -> bool:
    """True when the optional markitdown dependency is importable."""
    global _AVAILABLE
    if _AVAILABLE is None:
        import importlib.util

        _AVAILABLE = importlib.util.find_spec("markitdown") is not None
    return _AVAILABLE


def _reset_for_tests() -> None:
    global _AVAILABLE, _CONVERTER
    _AVAILABLE = None
    _CONVERTER = None


def _get_converter():
    global _CONVERTER
    if _CONVERTER is None:
        from markitdown import MarkItDown

        # enable_plugins=False + no llm_client/docintel_endpoint = local
        # converters only; nothing here can reach the network.
        _CONVERTER = MarkItDown(enable_plugins=False)
    return _CONVERTER


def _markitdown_version() -> str:
    try:
        from importlib.metadata import version

        return version("markitdown")
    except Exception:
        return "unknown"


def office_cache_dir(base_path) -> Path:
    """Conversion-cache directory under the doc-index storage root."""
    return Path(base_path) / ".office_cache"


def convert_office(file_path: Path, cache_dir: Optional[Path] = None) -> str:
    """Convert one office document to Markdown text.

    Raises on unreadable/unconvertible input — callers warn and skip the file,
    matching the existing read-failure handling.
    """
    data = Path(file_path).read_bytes()
    cached_path = None
    if cache_dir is not None:
        key = hashlib.sha256(
            _markitdown_version().encode("utf-8") + b"\x00" + data
        ).hexdigest()
        cached_path = Path(cache_dir) / f"{key}.md"
        if cached_path.exists():
            try:
                return cached_path.read_text(encoding="utf-8")
            except OSError:
                pass  # unreadable cache entry — reconvert below

    result = _get_converter().convert(str(file_path))
    text = getattr(result, "text_content", "") or ""

    if cached_path is not None:
        try:
            cached_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = cached_path.parent / f"{cached_path.name}.{os.getpid()}.tmp"
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, cached_path)
        except OSError as e:
            logger.debug("office cache write failed for %s: %s", file_path, e)

    return text
