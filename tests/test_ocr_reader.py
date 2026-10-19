"""The [ocr] extra: scanned PDF pages read by an engine already on the machine.

CI has no Tesseract and no pypdfium2, so both are stubbed: a fake
``pypdfium2`` module stands in for the renderer and a fake engine for the OCR
call. What is under test is jDocMunch's own behaviour around them: the
two-key gate, the response's ``ocr`` block, ``text_source`` marking, the
budget and page cap, the stored per-index choice, zero savings credit, and
that OCR text never reaches a warning.
"""

from __future__ import annotations

import json
import sys
import types

import pytest

from jdocmunch_mcp.parser import ocr, office

SECRET = "ZEBRAQUOKKA"  # a token only the OCR engine "reads"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeImage:
    def __init__(self, key):
        self.key = key
        self.width = 1000

    def convert(self, _mode):
        return self


class _FakePage:
    def __init__(self, key, text):
        self.key, self.text = key, text

    def get_textpage(self):
        page = self

        class _TP:
            def get_text_bounded(self):
                return page.text
        return _TP()

    def get_size(self):
        return (612.0, 792.0)

    def render(self, scale):
        page = self

        class _B:
            def to_pil(self):
                return _FakeImage(page.key)
        return _B()


# PDF bytes -> list of (page key, own text layer)
_PDFS: dict = {}


def _fake_pdfium():
    mod = types.ModuleType("pypdfium2")

    class PdfDocument(list):
        def __init__(self, data):
            spec = _PDFS[bytes(data)]
            super().__init__(_FakePage(k, t) for k, t in spec)

    mod.PdfDocument = PdfDocument
    return mod


class _FakeEngine(ocr._Engine):
    name, version, lang = "tesseract", "5.4.0", "eng"

    def __init__(self):
        self.calls = []

    def read(self, image):
        self.calls.append(image.key)
        text = f"Scanned {image.key}\n\nThe {SECRET} paragraph on {image.key}."
        lines = [ocr.OcrLine(t, 0, 0, 10, 10, 0.91) for t in text.splitlines() if t]
        return ocr.OcrPage(text, lines, 1000.0, 0.91)


class _Converter:
    def convert(self, path):
        class _R:
            text_content = ""   # markitdown reads nothing from a scan
        return _R()


@pytest.fixture
def env(monkeypatch, tmp_path):
    office._reset_for_tests()
    monkeypatch.setattr(office, "_AVAILABLE", True)
    monkeypatch.setattr(office, "_get_converter", lambda: _Converter())
    monkeypatch.setitem(sys.modules, "pypdfium2", _fake_pdfium())
    monkeypatch.setenv("JDOCMUNCH_SHARE_SAVINGS", "0")
    monkeypatch.delenv("JDOCMUNCH_OCR", raising=False)
    engine = _FakeEngine()
    monkeypatch.setattr(ocr, "detect_engine", lambda env=None: (engine, "tesseract"))
    _PDFS.clear()
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "note.md").write_text("# Note\n\nPlain text.\n", encoding="utf-8")
    yield types.SimpleNamespace(docs=docs, store=tmp_path / "store", engine=engine, mp=monkeypatch)
    office._reset_for_tests()


def _pdf(docs, name, pages):
    data = f"%PDF-1.4 {name} {len(pages)} {pages!r}".encode()
    _PDFS[data] = pages
    (docs / name).write_bytes(data)


def _index(e, **kw):
    from jdocmunch_mcp.tools.index_local import index_local
    return index_local(path=str(e.docs), name="ocrt", use_ai_summaries=False,
                       use_embeddings=False, storage_path=str(e.store), **kw)


def _sections(e, doc_path):
    from jdocmunch_mcp.storage.doc_store import DocStore
    store = DocStore(base_path=str(e.store))
    index = store.load_index("local", "ocrt")
    return index, [s for s in index.sections if s.get("doc_path") == doc_path]


# ---------------------------------------------------------------------------
# The two-key gate (CoS point 6)
# ---------------------------------------------------------------------------

def test_off_by_default_even_when_config_enables_it(env):
    env.mp.setenv("JDOCMUNCH_OCR", "1")
    _pdf(env.docs, "scan.pdf", [("p1", "")])
    r = _index(env)
    assert "ocr" not in r
    assert r["skip_counts"]["no_extractable_text"] == 1
    assert env.engine.calls == []


def test_call_alone_is_not_enough(env):
    _pdf(env.docs, "scan.pdf", [("p1", "")])
    r = _index(env, ocr=True)
    assert env.engine.calls == []
    assert r["ocr"]["requested"] is True
    assert r["ocr"]["enabled_in_config"] is False
    assert r["ocr"]["used"] is False
    assert r["skip_counts"]["no_extractable_text"] == 1
    assert any("JDOCMUNCH_OCR is not set" in w for w in r["warnings"])


def test_both_keys_read_the_scan_and_say_so(env):
    env.mp.setenv("JDOCMUNCH_OCR", "1")
    _pdf(env.docs, "scan.pdf", [("p1", ""), ("p2", "")])
    r = _index(env, ocr=True)
    assert "scan.pdf" in r["files"]
    assert r["coverage_complete"] is True
    block = r["ocr"]
    assert block["used"] is True and block["pages"] == 2
    assert block["documents"] == ["scan.pdf"] and block["engine"] == "tesseract 5.4.0 eng"
    assert any("tables and forms may come out scrambled" in w for w in r["warnings"])

    _, secs = _sections(env, "scan.pdf")
    assert secs and all(s.get("text_source") == "ocr" for s in secs)
    pages = sorted(p for s in secs for p in s.get("ocr_pages", []))
    assert pages == [1, 2]
    assert any(s.get("ocr_confidence") == 0.91 for s in secs)
    # The note is visible in the document itself, not only in metadata.
    root = [s for s in secs if not s.get("ocr_pages")]
    assert root and root[0]["text_source"] == "ocr"


def test_no_ocr_text_in_warnings_or_the_ocr_block(env):
    env.mp.setenv("JDOCMUNCH_OCR", "1")
    _pdf(env.docs, "scan.pdf", [("p1", "")])
    r = _index(env, ocr=True)
    assert SECRET not in json.dumps(r.get("warnings", []))
    assert SECRET not in json.dumps(r["ocr"])


def test_a_text_pdf_is_never_sent_to_ocr(env):
    env.mp.setenv("JDOCMUNCH_OCR", "1")
    env.mp.setattr(office, "_get_converter", lambda: types.SimpleNamespace(
        convert=lambda p: types.SimpleNamespace(text_content="# Real\n\nText layer.\n")))
    _pdf(env.docs, "real.pdf", [("p1", "Text layer.")])
    r = _index(env, ocr=True)
    assert env.engine.calls == []
    assert r["ocr"]["used"] is False


def test_mixed_pdf_marks_only_its_scanned_pages(env):
    env.mp.setenv("JDOCMUNCH_OCR", "1")
    env.mp.setattr(office, "_get_converter", lambda: types.SimpleNamespace(
        convert=lambda p: types.SimpleNamespace(text_content="Typed page.")))
    _pdf(env.docs, "mixed.pdf", [("p1", "Typed page one."), ("p2", "")])
    _index(env, ocr=True)
    assert env.engine.calls == ["p2"]
    _, secs = _sections(env, "mixed.pdf")
    page1 = [s for s in secs if s["title"] == "Page 1"]
    page2 = [s for s in secs if s["title"] == "Page 2"]
    assert page1 and not page1[0].get("text_source")
    assert page2 and page2[0]["text_source"] == "ocr" and page2[0]["ocr_pages"] == [2]


# ---------------------------------------------------------------------------
# The stored per-index choice
# ---------------------------------------------------------------------------

def test_refresh_inherits_the_choice_and_false_turns_it_off(env):
    env.mp.setenv("JDOCMUNCH_OCR", "1")
    _pdf(env.docs, "scan.pdf", [("p1", "")])
    _index(env, ocr=True)
    index, _ = _sections(env, "scan.pdf")
    assert index.ocr_requested is True
    on_disk = json.loads(next(env.store.rglob("ocrt.json")).read_text(encoding="utf-8"))
    assert on_disk.get("ocr_requested") is True

    _pdf(env.docs, "scan2.pdf", [("q1", "")])
    r = _index(env)                       # said nothing: inherit
    assert r["new"] == 1 and r["ocr"]["used"] is True

    _pdf(env.docs, "scan3.pdf", [("z1", "")])
    r = _index(env, ocr=False)
    # Off means off: every scan is unread again, named, and left out.
    assert r["skip_counts"]["no_extractable_text"] == 3
    index, _ = _sections(env, "scan.pdf")
    assert index.ocr_requested is False
    assert not {"scan.pdf", "scan2.pdf", "scan3.pdf"} & set(index.doc_paths)


def test_index_file_inherits_the_stored_choice(env):
    from jdocmunch_mcp.tools.index_file import index_file
    env.mp.setenv("JDOCMUNCH_OCR", "1")
    _pdf(env.docs, "scan.pdf", [("p1", "")])
    _index(env, ocr=True)
    _pdf(env.docs, "scan.pdf", [("p1", ""), ("p9", "")])
    out = index_file(file_path=str(env.docs / "scan.pdf"), storage_path=str(env.store),
                     use_ai_summaries=False, name="ocrt")
    assert out["success"] is True and not out.get("skipped")
    assert out["ocr"]["used"] is True and out["ocr"]["pages"] == 2


# ---------------------------------------------------------------------------
# Budgets, the page cap, and a missing engine
# ---------------------------------------------------------------------------

def test_budget_exhausted_defers_the_document_and_resumes_from_cache(env):
    env.mp.setenv("JDOCMUNCH_OCR", "1")
    _pdf(env.docs, "scan.pdf", [("p1", ""), ("p2", "")])
    checks = {"n": 0}

    def exhausted(self):
        checks["n"] += 1
        return checks["n"] > 1            # one page fits, the second does not

    env.mp.setattr(ocr.Budget, "exhausted", exhausted)
    r = _index(env, ocr=True)
    assert "scan.pdf" not in r["files"]
    assert r["skip_counts"]["ocr_budget_exhausted"] == 1
    assert r["coverage_complete"] is False
    assert r["ocr"]["deferred_documents"] == ["scan.pdf"]
    assert any("index-local --ocr" in w for w in r["warnings"])
    assert env.engine.calls == ["p1"]

    env.mp.setattr(ocr.Budget, "exhausted", lambda self: False)
    _index(env, ocr=True)
    index, secs = _sections(env, "scan.pdf")
    assert "scan.pdf" in index.doc_paths and secs
    assert env.engine.calls == ["p1", "p2"]   # page 1 came from the cache


def test_pages_past_the_cap_are_reported(env):
    env.mp.setenv("JDOCMUNCH_OCR", "1")
    _pdf(env.docs, "long.pdf", [("a", ""), ("b", ""), ("c", "")])
    r = _index(env, ocr=True, ocr_max_pages=2)
    assert "long.pdf" in r["files"]
    assert env.engine.calls == ["a", "b"]
    assert r["skip_counts"]["ocr_pages_capped"] == 1
    assert r["coverage_complete"] is False
    assert any("1 more were not read" in w for w in r["warnings"])


def test_no_engine_keeps_the_skip_and_names_why(env):
    env.mp.setenv("JDOCMUNCH_OCR", "1")
    env.mp.setattr(ocr, "detect_engine", lambda env=None: (None, "no OCR engine found (x)"))
    _pdf(env.docs, "scan.pdf", [("p1", "")])
    r = _index(env, ocr=True)
    assert r["skip_counts"]["no_extractable_text"] == 1
    assert r["ocr"]["engine_unavailable"] == "no OCR engine found (x)"
    assert any("never installs one" in w for w in r["warnings"])


# ---------------------------------------------------------------------------
# Retrieval: zero savings, and the search count
# ---------------------------------------------------------------------------

def test_ocr_sections_credit_no_savings_and_search_counts_them(env):
    from jdocmunch_mcp.tools.get_section import get_section
    from jdocmunch_mcp.tools.search_sections import search_sections
    env.mp.setenv("JDOCMUNCH_OCR", "1")
    _pdf(env.docs, "scan.pdf", [("p1", "")])
    _index(env, ocr=True)
    res = search_sections(repo="local/ocrt", query=SECRET.lower(), storage_path=str(env.store))
    hits = [h for h in res["results"] if h.get("text_source") == "ocr"]
    assert hits and res["_meta"]["ocr_results"] == len(hits)
    got = get_section(repo="local/ocrt", section_id=hits[0]["id"], storage_path=str(env.store))
    assert SECRET in got["section"]["content"]
    assert got["_meta"]["tokens_saved"] == 0


# ---------------------------------------------------------------------------
# Units: engine detection, the TSV parser, the cache key, Section fields
# ---------------------------------------------------------------------------

def _detect(monkeypatch, env, which, files, versions, platform="win32"):
    monkeypatch.setattr(ocr.Path, "is_file", lambda self: str(self) in files)
    monkeypatch.setattr(ocr, "_tesseract_version", lambda exe: versions.get(exe))
    return ocr.find_tesseract(env=env, which=lambda _n: which, platform=platform)


def test_detection_order_env_then_path_then_defaults(monkeypatch):
    d = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    all_ok = {"X:\\t.exe": "5.1", "P:\\t.exe": "5.2", d: "5.4"}
    assert _detect(monkeypatch, {"JDOCMUNCH_TESSERACT_PATH": "X:\\t.exe"}, "P:\\t.exe",
                   set(all_ok), all_ok) == ("X:\\t.exe", "5.1")
    assert _detect(monkeypatch, {}, "P:\\t.exe", set(all_ok), all_ok) == ("P:\\t.exe", "5.2")
    assert _detect(monkeypatch, {}, None, set(all_ok), all_ok) == (d, "5.4")
    # A candidate that won't answer --version is passed over.
    assert _detect(monkeypatch, {}, "P:\\t.exe", set(all_ok), {d: "5.4"}) == (d, "5.4")
    assert _detect(monkeypatch, {}, None, set(), all_ok) is None


def test_per_user_windows_install_is_found(monkeypatch):
    exe = str(ocr.Path(r"C:\Users\u\AppData\Local") / "Programs" / "Tesseract-OCR" / "tesseract.exe")
    assert _detect(monkeypatch, {"LOCALAPPDATA": r"C:\Users\u\AppData\Local"}, None,
                   {exe}, {exe: "5.3"}) == (exe, "5.3")


def test_engine_preference(monkeypatch):
    monkeypatch.setattr(ocr, "find_tesseract", lambda env=None: None)
    monkeypatch.setattr(ocr, "_windows_ocr_engine", lambda: None)
    eng, why = ocr.detect_engine({"JDOCMUNCH_OCR_ENGINE": "tesseract"})
    assert eng is None and "no Tesseract" in why
    monkeypatch.setattr(ocr, "find_tesseract", lambda env=None: ("t.exe", "5.4.0"))
    eng, _ = ocr.detect_engine({"JDOCMUNCH_OCR_LANG": "deu+eng"})
    assert eng.identity == "tesseract 5.4.0 deu+eng"


def test_parse_tesseract_tsv():
    head = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    rows = [
        "5\t1\t1\t1\t1\t1\t10\t10\t40\t12\t96\tHello",
        "5\t1\t1\t1\t1\t2\t60\t10\t40\t12\t90\tworld",
        "5\t1\t1\t2\t1\t1\t10\t40\t40\t12\t-1\t ",
        "5\t1\t1\t2\t1\t2\t10\t40\t40\t12\t80\tNext",
    ]
    page = ocr.parse_tesseract_tsv("\n".join([head] + rows), 500)
    assert page.text == "Hello world\n\nNext"
    assert [ln.text for ln in page.lines] == ["Hello world", "Next"]
    assert page.mean_conf == pytest.approx((0.96 + 0.90 + 0.80) / 3)


def test_cache_key_changes_with_the_engine(tmp_path):
    a, b = _FakeEngine(), _FakeEngine()
    b.version = "5.5.0"
    assert ocr._page_cache_path(tmp_path, "sha", 1, a) != ocr._page_cache_path(tmp_path, "sha", 1, b)
    assert ocr._page_cache_path(None, "sha", 1, a) is None


def test_section_fields_are_omitted_when_empty_and_round_trip():
    from jdocmunch_mcp.parser.sections import Section
    plain = Section(id="r::a.md::x", repo="r", doc_path="a.md", title="x", content="y",
                    level=1, parent_id="", children=[], byte_start=0, byte_end=1)
    d = plain.to_dict()
    assert not {"text_source", "ocr_engine", "ocr_pages", "ocr_confidence"} & set(d)
    plain.text_source, plain.ocr_engine = "ocr", "tesseract 5.4.0 eng"
    plain.ocr_pages, plain.ocr_confidence = [3], 0.912
    back = Section.from_dict(plain.to_dict())
    assert (back.text_source, back.ocr_pages, back.ocr_confidence) == ("ocr", [3], 0.912)


@pytest.mark.parametrize("raw,on", [("", False), ("0", False), ("no", False),
                                    ("1", True), ("true", True), (" ON ", True)])
def test_config_key(raw, on):
    assert ocr.ocr_enabled_in_config({"JDOCMUNCH_OCR": raw}) is on
    assert ocr.ocr_active(True, {"JDOCMUNCH_OCR": raw}) is on
    assert ocr.ocr_active(False, {"JDOCMUNCH_OCR": raw}) is False


@pytest.mark.parametrize("argv,expected", [
    ([], (None, 0, None)),
    (["--ocr"], (True, 0, None)),
    (["--no-ocr"], (False, 0, None)),
    (["--ocr", "--ocr-budget", "30", "--ocr-max-pages", "9"], (True, 30.0, 9)),
])
def test_cli_flags_reach_index_local(monkeypatch, tmp_path, capsys, argv, expected):
    from jdocmunch_mcp.cli import main as cli
    from jdocmunch_mcp.tools import index_local as il
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return {"success": True}

    monkeypatch.setattr(il, "index_local", fake)
    monkeypatch.setattr(sys, "argv", ["jdocmunch-mcp", "index-local", "--path", str(tmp_path)] + argv)
    cli.main()
    assert (seen["ocr"], seen["ocr_budget_seconds"], seen["ocr_max_pages"]) == expected


def test_the_ocr_module_never_installs_or_downloads():
    """Design condition 5: engines are found, never fetched. The module names
    no network library and no installer, so neither can be reached from it."""
    import re
    from pathlib import Path
    src = Path(ocr.__file__).read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""|#[^\n]*', "", src, flags=re.S)
    for banned in ("urllib", "http", "requests", "socket", "pip", "winget", "choco", "brew"):
        assert not re.search(rf"\b{banned}\b", code), banned
