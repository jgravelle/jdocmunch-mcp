"""Optional OCR for scanned PDF pages (the ``[ocr]`` extra).

A modest, prose-first reader. When a PDF page has no text layer, and the user
has turned OCR on, the page is rendered to an image and read by an OCR engine
that is ALREADY on the machine: Tesseract (found, never installed) or Windows'
built-in ``Windows.Media.Ocr``. Every section built from OCR text is marked
``text_source: "ocr"``, and every OCR'd document carries a visible note that
tables and forms may come out scrambled. (A per-page table detector was
built, pre-registered and tested; it failed on real scans, so it was dropped
with J's approval on 2026-10-19. See the design doc's amendments.)

⚠⚠ Two keys, both required (the privacy gate): the user enables OCR in config
(``JDOCMUNCH_OCR=1``) AND the call asks for it (``index_local(ocr=True)`` /
``index-local --ocr``). An agent can't turn OCR on by itself, and a user who
enabled it in config still gets it only on the calls that ask.

⚠ Nothing is installed or downloaded. Local only: Tesseract is a local
subprocess, Windows.Media.Ocr is an on-device API.

Evidence and design: the OCR bake-off reports and
``jdoc-ocr-modest-reader-design.md`` (2026-10-18/19).
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

#: Budget and cap defaults (design section 5). MCP calls get a time budget;
#: the CLI passes 0 = unlimited.
DEFAULT_BUDGET_SECONDS = 60.0
DEFAULT_MAX_PAGES = 50
#: Render resolution for scanned pages. Round 3's scans were read at about
#: 200-250 dpi; the long side is capped to bound memory and time.
RENDER_DPI = 250
RENDER_MAX_SIDE = 3500
PAGE_TIMEOUT_SECONDS = 120
#: Bump when the OCR text format changes, so cached pages re-read.
OCR_FORMAT_VERSION = 1

NOTE_PREFIX = "> Text below was produced by OCR"

_TRUTHY = ("1", "true", "yes", "on")


# --------------------------------------------------------------------------
# The two-key gate
# --------------------------------------------------------------------------

def ocr_enabled_in_config(env: Optional[dict] = None) -> bool:
    """The user's key: ``JDOCMUNCH_OCR`` set truthy in their (MCP client) config."""
    raw = (env if env is not None else os.environ).get("JDOCMUNCH_OCR", "")
    return str(raw).strip().lower() in _TRUTHY


def ocr_active(requested: bool, env: Optional[dict] = None) -> bool:
    """OCR runs only when the call asked for it AND config enables it."""
    return bool(requested) and ocr_enabled_in_config(env)


# --------------------------------------------------------------------------
# Engines
# --------------------------------------------------------------------------

@dataclass
class OcrLine:
    text: str
    left: float      # px
    top: float
    width: float
    height: float
    conf: Optional[float] = None   # 0-1, mean of the line's words (Tesseract only)


@dataclass
class OcrPage:
    text: str
    lines: list
    page_width: float
    mean_conf: Optional[float] = None


class _Engine:
    name = ""
    version = ""
    lang = ""

    @property
    def identity(self) -> str:
        return f"{self.name} {self.version} {self.lang}".strip()

    def read(self, image) -> OcrPage:  # pragma: no cover - interface
        raise NotImplementedError


def _no_window_kwargs() -> dict:
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


class TesseractEngine(_Engine):
    """Tesseract via its CLI: ``tsv`` output gives words, confidence and boxes."""

    name = "tesseract"

    def __init__(self, exe: str, version: str, lang: str = "eng"):
        self.exe = exe
        self.version = version
        self.lang = lang

    def read(self, image) -> OcrPage:
        with tempfile.TemporaryDirectory(prefix="jdoc-ocr-") as tmp:
            src = Path(tmp) / "page.png"
            image.save(src)
            r = subprocess.run(
                [self.exe, str(src), "stdout", "-l", self.lang, "--psm", "3", "tsv"],
                capture_output=True, timeout=PAGE_TIMEOUT_SECONDS,
                # Inside the MCP server, stdin is the JSON-RPC channel (jcm#392).
                stdin=subprocess.DEVNULL, **_no_window_kwargs(),
            )
        if r.returncode != 0:
            raise RuntimeError(
                f"tesseract exited {r.returncode}: {r.stderr.decode('utf-8', 'replace')[-300:]}"
            )
        return parse_tesseract_tsv(r.stdout.decode("utf-8", "replace"), image.width)


def parse_tesseract_tsv(tsv: str, page_width: float) -> OcrPage:
    """Turn Tesseract TSV into lines (reading order) and paragraph-spaced text."""
    rows = tsv.splitlines()
    words: dict = {}
    order: list = []
    for raw in rows[1:]:
        parts = raw.split("\t")
        if len(parts) < 12 or parts[0] != "5":
            continue
        try:
            block, par, line = int(parts[2]), int(parts[3]), int(parts[4])
            left, top, w, h = (float(x) for x in parts[6:10])
            conf = float(parts[10])
        except ValueError:
            continue
        text = parts[11].strip()
        if not text:
            continue
        key = (block, par, line)
        if key not in words:
            words[key] = []
            order.append(key)
        words[key].append((text, left, top, w, h, conf))
    lines: list = []
    out: list = []
    last_par = None
    confs: list = []
    for key in order:
        ws = words[key]
        txt = " ".join(w[0] for w in ws)
        lefts = [w[1] for w in ws]
        tops = [w[2] for w in ws]
        rights = [w[1] + w[3] for w in ws]
        bottoms = [w[2] + w[4] for w in ws]
        wc = [w[5] / 100.0 for w in ws if w[5] >= 0]
        confs.extend(wc)
        lines.append(OcrLine(txt, min(lefts), min(tops), max(rights) - min(lefts),
                             max(bottoms) - min(tops), sum(wc) / len(wc) if wc else None))
        par_key = key[:2]
        if last_par is not None and par_key != last_par:
            out.append("")
        out.append(txt)
        last_par = par_key
    return OcrPage("\n".join(out), lines, float(page_width),
                   sum(confs) / len(confs) if confs else None)


class WindowsOcrEngine(_Engine):
    """Windows.Media.Ocr through the pywinrt bindings (Windows only, no confidence)."""

    name = "windows.media.ocr"

    def __init__(self, engine, lang: str):
        self._engine = engine
        self.version = ""
        self.lang = lang

    def read(self, image) -> OcrPage:
        # The MCP server runs an asyncio loop; asyncio.run() can't nest inside
        # it, so the WinRT call gets a private loop on a worker thread.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(self._read_sync, image).result(timeout=PAGE_TIMEOUT_SECONDS)

    def _read_sync(self, image) -> OcrPage:
        import asyncio
        return asyncio.run(self._read_async(image))

    async def _read_async(self, image) -> OcrPage:
        from PIL import Image
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.storage.streams import DataWriter

        rgba = image.convert("RGBA")
        r, g, b, a = rgba.split()
        bgra = Image.merge("RGBA", (b, g, r, a))
        writer = DataWriter()
        writer.write_bytes(bgra.tobytes())
        bmp = SoftwareBitmap.create_copy_from_buffer(
            writer.detach_buffer(), BitmapPixelFormat.BGRA8, image.width, image.height)
        result = await self._engine.recognize_async(bmp)
        lines: list = []
        for line in result.lines:
            rects = [w.bounding_rect for w in line.words]
            if rects:
                left = min(rc.x for rc in rects)
                top = min(rc.y for rc in rects)
                right = max(rc.x + rc.width for rc in rects)
                bottom = max(rc.y + rc.height for rc in rects)
            else:
                left = top = right = bottom = 0.0
            lines.append(OcrLine(line.text, left, top, right - left, bottom - top, None))
        return OcrPage("\n".join(ln.text for ln in lines), lines, float(image.width), None)


_DEFAULT_TESSERACT_PATHS = {
    "win32": [r"C:\Program Files\Tesseract-OCR\tesseract.exe",
              r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"],
    # A GUI-launched MCP client on macOS doesn't inherit the shell's PATH,
    # which is why Homebrew's prefixes are checked by path.
    "darwin": ["/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"],
    "linux": ["/usr/bin/tesseract", "/usr/local/bin/tesseract"],
}


def _default_tesseract_paths(env, platform: str) -> list:
    paths = list(_DEFAULT_TESSERACT_PATHS.get(platform, []))
    if platform == "win32":
        # The UB Mannheim installer's "just for me" choice.
        local = (env.get("LOCALAPPDATA") or "").strip()
        if local:
            paths.append(str(Path(local) / "Programs" / "Tesseract-OCR" / "tesseract.exe"))
    return paths


def _tesseract_version(exe: str) -> Optional[str]:
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, timeout=20,
                           stdin=subprocess.DEVNULL, **_no_window_kwargs())
    except (OSError, subprocess.SubprocessError):
        return None
    out = (r.stdout or b"") + (r.stderr or b"")
    m = re.search(rb"tesseract\s+v?([\w.\-]+)", out, flags=re.I)
    return m.group(1).decode("ascii", "replace") if (r.returncode == 0 and m) else None


def find_tesseract(env: Optional[dict] = None, which=shutil.which,
                   platform: str = sys.platform) -> Optional[tuple]:
    """``(exe, version)`` of a Tesseract already on this machine, or None.

    Order: ``JDOCMUNCH_TESSERACT_PATH``, then PATH, then the platform's default
    install locations. Each candidate must answer ``--version``. Never installs.
    """
    env = env if env is not None else os.environ
    candidates: list = []
    explicit = (env.get("JDOCMUNCH_TESSERACT_PATH") or "").strip()
    if explicit:
        candidates.append(explicit)
    on_path = which("tesseract")
    if on_path:
        candidates.append(on_path)
    candidates.extend(_default_tesseract_paths(env, platform))
    seen = set()
    for exe in candidates:
        if exe in seen or not Path(exe).is_file():
            continue
        seen.add(exe)
        version = _tesseract_version(exe)
        if version:
            return exe, version
    return None


def _windows_ocr_engine() -> Optional[WindowsOcrEngine]:
    if sys.platform != "win32":
        return None
    try:
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine
    except Exception:
        return None
    try:
        tags = [lang.language_tag for lang in OcrEngine.available_recognizer_languages]
        english = [t for t in tags if t.lower().startswith("en")]
        if not english:
            return None
        tag = "en-US" if "en-US" in english else english[0]
        engine = OcrEngine.try_create_from_language(Language(tag))
        return WindowsOcrEngine(engine, tag) if engine is not None else None
    except Exception:
        logger.debug("Windows.Media.Ocr unavailable", exc_info=True)
        return None


def detect_engine(env: Optional[dict] = None) -> tuple:
    """``(engine or None, reason)``. ``JDOCMUNCH_OCR_ENGINE=auto|tesseract|windows``."""
    env = env if env is not None else os.environ
    pref = (env.get("JDOCMUNCH_OCR_ENGINE") or "auto").strip().lower()
    lang = (env.get("JDOCMUNCH_OCR_LANG") or "eng").strip() or "eng"
    if pref in ("auto", "tesseract"):
        found = find_tesseract(env)
        if found:
            return TesseractEngine(found[0], found[1], lang), "tesseract"
        if pref == "tesseract":
            return None, "JDOCMUNCH_OCR_ENGINE=tesseract but no Tesseract was found"
    if pref in ("auto", "windows"):
        eng = _windows_ocr_engine()
        if eng is not None:
            return eng, "windows"
        if pref == "windows":
            return None, "JDOCMUNCH_OCR_ENGINE=windows but Windows OCR with an English language is not available"
    return None, "no OCR engine found (install Tesseract, or use Windows with an English OCR language)"


# --------------------------------------------------------------------------
# PDF pages -> text with OCR spans
# --------------------------------------------------------------------------

class Budget:
    """A time budget shared by every document in one indexing call."""

    def __init__(self, seconds: Optional[float]):
        self.unlimited = not seconds or seconds <= 0
        self.deadline = None if self.unlimited else time.monotonic() + float(seconds)
        self.spent = 0.0

    def exhausted(self) -> bool:
        return not self.unlimited and time.monotonic() >= self.deadline


@dataclass
class OcrDocResult:
    text: str
    ocr_pages: list = field(default_factory=list)        # 1-based page numbers read by OCR
    spans: list = field(default_factory=list)            # [{page, byte_start, byte_end, conf}]
    engine: str = ""
    seconds: float = 0.0
    pages_total: int = 0
    pages_capped: int = 0                                # pages past max_pages, not read
    complete: bool = True                                # False when the budget ran out mid-document


def _escape_heading(line: str) -> str:
    return "\\" + line if line.lstrip().startswith("#") else line


def _page_cache_path(cache_dir: Optional[Path], file_sha: str, page_no: int, engine: _Engine) -> Optional[Path]:
    if cache_dir is None:
        return None
    key = hashlib.sha256(
        f"{OCR_FORMAT_VERSION}\0{file_sha}\0{page_no}\0{engine.identity}".encode("utf-8")
    ).hexdigest()
    return Path(cache_dir) / "ocr" / f"{key}.json"


def ocr_pdf(path: Path, engine: _Engine, budget: Budget, *,
            max_pages: int = DEFAULT_MAX_PAGES, cache_dir: Optional[Path] = None,
            render: Optional[Callable] = None) -> OcrDocResult:
    """Read a PDF page by page: keep each page's own text layer, OCR the rest.

    Returns the document as Markdown with one ``## Page N`` section per page,
    plus byte spans of the OCR'd pages. ``complete`` is False when the budget
    ran out before every needed page was read; pages already read are cached
    per page, so the next call resumes.
    """
    import pypdfium2 as pdfium

    data = Path(path).read_bytes()
    file_sha = hashlib.sha256(data).hexdigest()
    pdf = pdfium.PdfDocument(data)
    total = len(pdf)
    limit = min(total, max_pages) if max_pages and max_pages > 0 else total
    parts: list = []
    result = OcrDocResult(text="", engine=engine.identity, pages_total=total,
                          pages_capped=max(0, total - limit))
    t0 = time.monotonic()
    page_texts: list = []
    for i in range(limit):
        page = pdf[i]
        own = ""
        try:
            textpage = page.get_textpage()
            own = textpage.get_text_bounded() or ""
        except Exception:
            own = ""
        if any(ch.isalnum() for ch in own):
            page_texts.append((i + 1, own, None))
            continue
        cpath = _page_cache_path(cache_dir, file_sha, i + 1, engine)
        cached = None
        if cpath is not None and cpath.exists():
            try:
                cached = json.loads(cpath.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cached = None
        if cached is None:
            if budget.exhausted():
                result.complete = False
                break
            img = render(page) if render else _render(page)
            ocr = engine.read(img)
            cached = {"text": ocr.text, "conf": ocr.mean_conf}
            if cpath is not None:
                try:
                    cpath.parent.mkdir(parents=True, exist_ok=True)
                    cpath.write_text(json.dumps(cached), encoding="utf-8")
                except OSError:
                    logger.debug("ocr page cache write failed", exc_info=True)
        page_texts.append((i + 1, cached["text"], cached))
    result.seconds = round(time.monotonic() - t0, 2)
    if not result.complete:
        return result

    any_ocr = any(c is not None for _, _, c in page_texts)
    out = ""
    if any_ocr:
        out = (f"{NOTE_PREFIX} ({engine.identity}) from scanned pages and may contain "
               "errors. Tables and forms may come out scrambled.\n\n")
    for page_no, text, meta in page_texts:
        body = "\n".join(_escape_heading(ln) for ln in text.splitlines()).strip()
        block = f"## Page {page_no}\n\n{body}\n\n"
        start = len(out.encode("utf-8"))
        out += block
        if meta is not None:
            result.ocr_pages.append(page_no)
            result.spans.append({"page": page_no, "byte_start": start,
                                 "byte_end": len(out.encode("utf-8")),
                                 "conf": meta.get("conf")})
    result.text = out
    return result


def _render(page):
    w, h = page.get_size()  # points
    scale = RENDER_DPI / 72.0
    if max(w, h) * scale > RENDER_MAX_SIDE:
        scale = RENDER_MAX_SIDE / max(w, h)
    return page.render(scale=scale).to_pil().convert("RGB")


def mark_sections(sections: list, result: OcrDocResult) -> None:
    """Stamp OCR fields on every section that lies inside an OCR'd page span."""
    if not result.spans:
        return
    # The note runs up to the first page heading. A text-layer page before
    # the first scanned page is NOT part of it.
    note_end = 0
    if result.text.startswith(NOTE_PREFIX):
        note_end = result.text.encode("utf-8").find(b"## Page ")
    for sec in sections:
        start = getattr(sec, "byte_start", 0)
        if start < note_end:
            # The root section holds the visible OCR note itself.
            sec.text_source = "ocr"
            sec.ocr_engine = result.engine
            continue
        for span in result.spans:
            if span["byte_start"] <= start < span["byte_end"]:
                sec.text_source = "ocr"
                sec.ocr_engine = result.engine
                sec.ocr_pages = [span["page"]]
                if span.get("conf") is not None:
                    sec.ocr_confidence = round(float(span["conf"]), 3)
                break


def pdf_has_textless_pages(path, max_pages: int = DEFAULT_MAX_PAGES) -> bool:
    """Whether any of the first ``max_pages`` pages has no readable text layer.

    Used only when OCR is active, to send a mixed PDF (some pages scanned)
    through ``ocr_pdf`` while an all-text PDF keeps its normal conversion.
    """
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(Path(path).read_bytes())
    limit = min(len(pdf), max_pages) if max_pages and max_pages > 0 else len(pdf)
    for i in range(limit):
        try:
            text = pdf[i].get_textpage().get_text_bounded() or ""
        except Exception:
            text = ""
        if not any(ch.isalnum() for ch in text):
            return True
    return False
