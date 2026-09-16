"""v1.146.0 — the Stage-A posting rows are persisted, and a stale row cannot be served.

Two claims are under test and they are not the same claim:

1. **Equivalence.** Rows loaded from the sidecar must produce the SAME posting
   index, and the same ``bm25_stats`` block, as re-reading every body. Equality
   against the reference implementation is asserted, never sampled.
2. **Invalidation.** A row is served only when it still keys to its live
   section. ⚠⚠ A stale posting sidecar returns wrong search results silently,
   where the old rebuild-every-time behaviour was merely slow, so every way a
   row can go stale gets its own case: body, title, summary, tokenizer, a torn
   line, a body the loader could not read.

⚠ The fixtures here are deliberately not toys. The incremental path's whole
point is skipping work, and under a handful of documents it re-materializes
everything, which hides exactly the defects these tests exist to catch.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from jdocmunch_mcp.retrieval import bm25, posting_cache
from jdocmunch_mcp.retrieval.posting_cache import RowSet, row_key
from jdocmunch_mcp.retrieval.prune import PostingIndex, get_or_build
from jdocmunch_mcp.storage.doc_store import INDEX_OWNED_SIDECAR_SUFFIXES, DocStore
from jdocmunch_mcp.tools.index_local import index_local

WORDS = [
    "authentication", "tokens", "middleware", "dispatch", "rendering",
    "throttle", "webhook", "migration", "scheduler", "telemetry",
    "checkpoint", "quantile", "serializer", "retention", "namespace",
]


def _body(i: int) -> str:
    a = WORDS[i % len(WORDS)]
    b = WORDS[(i * 7 + 3) % len(WORDS)]
    return (
        f"The {a} pipeline resolves {b} before the request completes. "
        f"Section {i} records how {a} interacts with {b} under load.\n"
    )


def _section(i: int, *, title=None, summary=None, body=None, inline=True) -> dict:
    text = _body(i) if body is None else body
    sec = {
        "id": f"local/demo::docs/page-{i}.md::heading-{i}#2",
        "doc_path": f"docs/page-{i}.md",
        "title": f"Heading {WORDS[i % len(WORDS)]} {i}" if title is None else title,
        "summary": (f"summary of {WORDS[(i + 5) % len(WORDS)]}"
                    if summary is None else summary),
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "byte_start": 0,
        "byte_end": len(text.encode("utf-8")),
    }
    if inline:
        sec["content"] = text
    return sec


def _corpus(n: int = 150) -> list:
    return [_section(i) for i in range(n)]


def _detached(sections: list) -> tuple:
    """Sections with no inline content, plus a loader that serves the bodies.

    The shape every loaded index has: ``Section.to_dict`` drops content, so the
    posting build reads bodies back through a byte-range loader.
    """
    bodies = {s["doc_path"]: s.get("content", "") for s in sections}
    out = [{k: v for k, v in s.items() if k != "content"} for s in sections]

    def loader(doc_path, byte_start, byte_end):
        return bodies.get(doc_path, "")

    return out, loader


# ---------------------------------------------------------------------------
# 1. Equivalence with the from-scratch build
# ---------------------------------------------------------------------------

class TestEquivalence:
    def test_rows_invert_to_the_same_posting_index(self):
        secs, loader = _detached(_corpus())
        rows = RowSet.build(secs, content_loader=loader)
        reference = PostingIndex.build(secs, content_loader=loader)
        assert rows.posting_index(secs).postings == reference.postings
        assert rows.posting_index(secs).all_section_ids == reference.all_section_ids

    def test_rows_derive_the_same_corpus_stats(self):
        secs, loader = _detached(_corpus())
        derived = RowSet.build(secs, content_loader=loader).corpus_stats()
        reference = bm25.compute_corpus_stats(secs, content_loader=loader)
        assert derived == reference

    def test_stats_agree_when_df_is_capped(self, monkeypatch):
        """The cap is where two implementations of the same count can diverge.

        Real corpora are over ``DF_TOP_K`` (``benchmarks/posting_rows/
        results.json`` records the term count of a public one); a fixture that
        stays under it would never exercise the boundary at all.
        """
        monkeypatch.setattr(bm25, "DF_TOP_K", 20)
        secs, loader = _detached(_corpus())
        derived = RowSet.build(secs, content_loader=loader).corpus_stats()
        reference = bm25.compute_corpus_stats(secs, content_loader=loader)
        assert len(derived["df"]) == 20
        assert derived == reference

    def test_cap_is_independent_of_insertion_order(self, monkeypatch):
        """⚠ Terms reach ``df`` from a set, whose iteration order follows string
        hashes and therefore PYTHONHASHSEED. Two runs over one corpus used to
        keep different equally-frequent terms at the boundary."""
        monkeypatch.setattr(bm25, "DF_TOP_K", 4)
        counts = {"alpha": 3, "beta": 3, "gamma": 3, "delta": 3, "epsilon": 3,
                  "zeta": 3}
        forward = bm25.cap_df(dict(counts))
        backward = bm25.cap_df({k: counts[k] for k in reversed(list(counts))})
        assert forward == backward
        assert set(forward) == {"alpha", "beta", "delta", "epsilon"}

    def test_inline_content_sections_need_no_loader(self):
        """v1.18 structured-OpenAPI sections keep inline content (byte_end 0)."""
        secs = _corpus(40)
        rows = RowSet.build(secs)
        assert rows.posting_index(secs).postings == PostingIndex.build(secs).postings


# ---------------------------------------------------------------------------
# 2. Round trip and invalidation
# ---------------------------------------------------------------------------

def _write_and_load(tmp_path: Path, rows: RowSet) -> RowSet:
    posting_cache.write(str(tmp_path), "local", "demo", rows)
    loaded = posting_cache.load(str(tmp_path), "local", "demo")
    assert loaded is not None
    return loaded


class TestRoundTrip:
    def test_every_row_survives_a_round_trip(self, tmp_path):
        secs, loader = _detached(_corpus())
        rows = RowSet.build(secs, content_loader=loader)
        loaded = _write_and_load(tmp_path, rows)

        rebuilt = RowSet.build(secs, content_loader=loader, reuse=loaded)
        assert (rebuilt.reused, rebuilt.built) == (len(secs), 0)
        assert rebuilt.posting_index(secs).postings == rows.posting_index(secs).postings
        assert rebuilt.corpus_stats() == rows.corpus_stats()

    def test_sidecar_is_smaller_than_the_id_strings_it_avoids(self, tmp_path):
        """⚠ The encoding claim, pinned: ids appear ONCE, not once per posting.

        ``benchmarks/posting_rows/results.json`` records both sizes on a
        public corpus. Here the ratio is asserted rather than the megabytes.
        """
        secs, loader = _detached(_corpus())
        rows = RowSet.build(secs, content_loader=loader)
        posting_cache.write(str(tmp_path), "local", "demo", rows)
        on_disk = (tmp_path / "local" / "demo.postings.jsonl").stat().st_size

        postings = rows.posting_index(secs).postings
        naive = sum(len(sid) + 3 for bucket in postings.values() for sid in bucket)
        assert on_disk < naive / 4


class TestInvalidation:
    """Every way a row can go stale, and what it costs when it does."""

    def _staled(self, tmp_path, mutate) -> tuple:
        secs, loader = _detached(_corpus())
        loaded = _write_and_load(tmp_path, RowSet.build(secs, content_loader=loader))
        mutated, loader2 = _detached(mutate([dict(s) for s in _corpus()]))
        rebuilt = RowSet.build(mutated, content_loader=loader2, reuse=loaded)
        return mutated, loader2, rebuilt

    def test_a_changed_body_is_rebuilt_not_served(self, tmp_path):
        def mutate(secs):
            secs[7] = _section(7, body="a paragraph about parquet compaction\n")
            return secs

        mutated, loader, rebuilt = self._staled(tmp_path, mutate)
        assert (rebuilt.reused, rebuilt.built) == (len(mutated) - 1, 1)
        postings = rebuilt.posting_index(mutated).postings
        sid = mutated[7]["id"]
        assert sid in postings.get("parquet", set())
        # The term the STALE row carried must not survive for that section.
        assert sid not in postings.get("pipeline", set())
        assert rebuilt.posting_index(mutated).postings == PostingIndex.build(
            mutated, content_loader=loader
        ).postings

    def test_a_changed_summary_is_rebuilt(self, tmp_path):
        """content_hash cannot see this: a summarizer pass moves the summary
        with every body byte untouched."""
        def mutate(secs):
            secs[3] = _section(3, summary="recalculated blurb about lattices")
            return secs

        mutated, _loader, rebuilt = self._staled(tmp_path, mutate)
        assert (rebuilt.reused, rebuilt.built) == (len(mutated) - 1, 1)
        assert mutated[3]["id"] in rebuilt.posting_index(mutated).postings["lattices"]

    def test_a_changed_title_is_rebuilt(self, tmp_path):
        def mutate(secs):
            secs[11] = _section(11, title="Renamed Heading About Isotopes")
            return secs

        mutated, _loader, rebuilt = self._staled(tmp_path, mutate)
        assert (rebuilt.reused, rebuilt.built) == (len(mutated) - 1, 1)
        assert mutated[11]["id"] in rebuilt.posting_index(mutated).postings["isotopes"]

    def test_a_row_for_a_deleted_section_is_ignored(self, tmp_path):
        """⚠ Asserted against the LOADED RowSet, which still holds the dead
        rows, not against a rebuilt one that never saw them. A rebuilt set only
        contains sections that were passed to it, so asserting there would have
        held no matter which collection the inversion walked — measured: the
        first version of this test passed with the live corpus swapped out for
        the row dict."""
        secs, loader = _detached(_corpus())
        loaded = _write_and_load(tmp_path, RowSet.build(secs, content_loader=loader))
        survivors = secs[:-20]
        dead = {s["id"] for s in secs[-20:]}

        stale_view = loaded.posting_index(survivors)
        assert stale_view.all_section_ids == [s["id"] for s in survivors]
        assert not dead & {sid for b in stale_view.postings.values() for sid in b}

        rebuilt = RowSet.build(survivors, content_loader=loader, reuse=loaded)
        assert rebuilt.reused == len(survivors)
        index = rebuilt.posting_index(survivors)
        assert len(index.all_section_ids) == len(survivors)
        assert not dead & {sid for b in index.postings.values() for sid in b}

    def test_a_different_tokenizer_rejects_the_whole_file(self, tmp_path):
        """⚠ The one input a per-row content key cannot see. A stop-word tweak
        changes every row while every key stays valid."""
        secs, loader = _detached(_corpus())
        posting_cache.write(str(tmp_path), "local", "demo",
                            RowSet.build(secs, content_loader=loader))
        path = tmp_path / "local" / "demo.postings.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        header = json.loads(lines[0])
        header["tokenizer"] = "src-0000000000000000"
        path.write_text("\n".join([json.dumps(header)] + lines[1:]) + "\n",
                        encoding="utf-8")
        assert posting_cache.load(str(tmp_path), "local", "demo") is None

    def test_tokenizer_identity_tracks_the_module_source(self):
        """Restated from the source bytes, not imported from the module that
        computes it — a pin that reads the value it checks asserts nothing."""
        from jdocmunch_mcp.retrieval import tokenize as tok

        expected = "src-" + hashlib.sha256(
            Path(tok.__file__).read_bytes()
        ).hexdigest()[:16]
        assert posting_cache.tokenizer_identity() == expected

    def test_an_unknown_format_version_rejects_the_whole_file(self, tmp_path):
        secs, loader = _detached(_corpus())
        posting_cache.write(str(tmp_path), "local", "demo",
                            RowSet.build(secs, content_loader=loader))
        path = tmp_path / "local" / "demo.postings.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        header = json.loads(lines[0])
        header["format"] = posting_cache.FORMAT_VERSION + 1
        path.write_text("\n".join([json.dumps(header)] + lines[1:]) + "\n",
                        encoding="utf-8")
        assert posting_cache.load(str(tmp_path), "local", "demo") is None

    def test_a_torn_line_costs_one_section_not_the_file(self, tmp_path):
        secs, loader = _detached(_corpus())
        posting_cache.write(str(tmp_path), "local", "demo",
                            RowSet.build(secs, content_loader=loader))
        path = tmp_path / "local" / "demo.postings.jsonl"
        text = path.read_text(encoding="utf-8")
        path.write_text(text[: text.rindex("\n", 0, len(text) - 1) + 30],
                        encoding="utf-8")

        loaded = posting_cache.load(str(tmp_path), "local", "demo")
        assert loaded is not None
        rebuilt = RowSet.build(secs, content_loader=loader, reuse=loaded)
        assert rebuilt.built == 1 and rebuilt.reused == len(secs) - 1
        assert rebuilt.posting_index(secs).postings == PostingIndex.build(
            secs, content_loader=loader
        ).postings

    def test_a_row_pointing_outside_the_vocabulary_is_skipped(self, tmp_path):
        secs, loader = _detached(_corpus(30))
        posting_cache.write(str(tmp_path), "local", "demo",
                            RowSet.build(secs, content_loader=loader))
        path = tmp_path / "local" / "demo.postings.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        row = json.loads(lines[5])
        row[5] = [999999]
        lines[5] = json.dumps(row)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        loaded = posting_cache.load(str(tmp_path), "local", "demo")
        rebuilt = RowSet.build(secs, content_loader=loader, reuse=loaded)
        assert rebuilt.built == 1


class TestFailClosedRows:
    def test_a_body_the_loader_could_not_read_is_never_persisted(self, tmp_path):
        """⚠⚠ content_hash says what the body IS, not that we could reach it.

        A row cached while the content mirror was missing would key as valid
        forever, freezing a title-only section into the index.
        """
        secs, good_loader = _detached(_corpus(40))
        broken_id = secs[4]["id"]

        def broken_loader(doc_path, byte_start, byte_end):
            if doc_path == secs[4]["doc_path"]:
                return ""
            return good_loader(doc_path, byte_start, byte_end)

        rows = RowSet.build(secs, content_loader=broken_loader)
        assert rows.unpersistable == 1
        written = posting_cache.write(str(tmp_path), "local", "demo", rows)
        assert written == len(secs) - 1

        loaded = posting_cache.load(str(tmp_path), "local", "demo")
        repaired = RowSet.build(secs, content_loader=good_loader, reuse=loaded)
        assert repaired.built == 1
        postings = repaired.posting_index(secs).postings
        body_term = "resolves"  # appears only in the body text
        assert broken_id in postings[body_term]

    def test_a_genuinely_empty_section_persists(self, tmp_path):
        """sha256("") is the one value that tells "no body" apart from "could
        not read the body"."""
        secs, loader = _detached(_corpus(20))
        empty = _section(99, body="", inline=False)
        empty["content_hash"] = hashlib.sha256(b"").hexdigest()
        empty["byte_end"] = 0
        secs.append(empty)

        rows = RowSet.build(secs, content_loader=loader)
        assert rows.unpersistable == 0
        assert posting_cache.write(str(tmp_path), "local", "demo", rows) == len(secs)

    def test_a_section_without_a_content_hash_is_never_persisted(self, tmp_path):
        secs, loader = _detached(_corpus(20))
        secs[2] = dict(secs[2])
        secs[2]["content_hash"] = ""
        rows = RowSet.build(secs, content_loader=loader)
        assert rows.unpersistable == 1
        assert row_key(secs[2]) == ""
        assert posting_cache.write(str(tmp_path), "local", "demo", rows) == len(secs) - 1


class TestDegradation:
    def test_an_absent_sidecar_rebuilds_instead_of_raising(self, tmp_path):
        from types import SimpleNamespace

        secs, loader = _detached(_corpus(30))
        index = SimpleNamespace(
            sections=secs,
            _postings_sidecar=str(tmp_path / "local" / "never-written.postings.jsonl"),
        )
        posting = get_or_build(index, content_loader=loader)
        assert posting.source == "rebuild"
        assert posting.postings == PostingIndex.build(secs, content_loader=loader).postings

    def test_the_cache_can_be_switched_off(self, tmp_path, monkeypatch):
        secs, loader = _detached(_corpus(20))
        rows = RowSet.build(secs, content_loader=loader)
        posting_cache.write(str(tmp_path), "local", "demo", rows)
        monkeypatch.setenv("JDOCMUNCH_POSTING_CACHE", "0")
        assert posting_cache.load(str(tmp_path), "local", "demo") is None
        assert posting_cache.write(str(tmp_path), "local", "demo", rows) == 0

    def test_an_unreadable_sidecar_is_reported_not_swallowed(self, tmp_path, caplog):
        """⚠ A bare `except Exception: pass` makes a read failure and an empty
        cache indistinguishable (v1.131.0). The caller still degrades."""
        path = tmp_path / "local" / "demo.postings.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text("not json at all\n", encoding="utf-8")
        with caplog.at_level("WARNING"):
            assert posting_cache.load(str(tmp_path), "local", "demo") is None
        assert any("posting cache" in r.getMessage() for r in caplog.records)


class TestCorruptSidecar:
    """⚠⚠ "An absent, corrupt or foreign sidecar degrades to a rebuild, never
    to an error." Each case below raised out of ``load_path`` in the first
    draft: the file was read as text, so a byte that is not UTF-8 escaped the
    line iterator as ``UnicodeDecodeError``, and the three counts went to
    ``int()`` unchecked. Both reached ``search_sections`` and the incremental
    save, neither of which guards the call."""

    def _written(self, tmp_path, n=30):
        secs, loader = _detached(_corpus(n))
        posting_cache.write(str(tmp_path), "local", "demo",
                            RowSet.build(secs, content_loader=loader))
        return secs, loader, tmp_path / "local" / "demo.postings.jsonl"

    def test_a_byte_that_is_not_utf8_costs_one_row(self, tmp_path):
        secs, loader, path = self._written(tmp_path)
        lines = path.read_bytes().split(b"\n")
        lines[4] = lines[4][:10] + b"\xff\xfe" + lines[4][10:]
        path.write_bytes(b"\n".join(lines))

        loaded = posting_cache.load(str(tmp_path), "local", "demo")
        assert loaded is not None
        rebuilt = RowSet.build(secs, content_loader=loader, reuse=loaded)
        assert (rebuilt.built, rebuilt.reused) == (1, len(secs) - 1)

    def test_a_header_that_is_not_utf8_rejects_the_file(self, tmp_path, caplog):
        _, _, path = self._written(tmp_path)
        path.write_bytes(b"\xff" + path.read_bytes())
        with caplog.at_level("WARNING"):
            assert posting_cache.load(str(tmp_path), "local", "demo") is None
        assert any("unreadable header" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("bad", ["7", None, -1, True, 1.5, [3]])
    @pytest.mark.parametrize("field", [2, 3, 4])
    def test_a_count_that_is_not_a_count_skips_the_row(self, tmp_path, field, bad):
        secs, loader, path = self._written(tmp_path)
        lines = path.read_text(encoding="utf-8").splitlines()
        row = json.loads(lines[6])
        row[field] = bad
        lines[6] = json.dumps(row)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        loaded = posting_cache.load(str(tmp_path), "local", "demo")
        assert loaded is not None
        rebuilt = RowSet.build(secs, content_loader=loader, reuse=loaded)
        assert rebuilt.built == 1

    @pytest.mark.parametrize("vocab", [["ok", 3], ["ok", ["nested"]], ["dup", "dup"]])
    def test_a_malformed_vocabulary_rejects_the_file(self, tmp_path, vocab):
        _, _, path = self._written(tmp_path)
        lines = path.read_text(encoding="utf-8").splitlines()
        header = json.loads(lines[0])
        header["vocab"] = vocab
        lines[0] = json.dumps(header)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert posting_cache.load(str(tmp_path), "local", "demo") is None

    def test_absurd_nesting_skips_the_row(self, tmp_path):
        secs, loader, path = self._written(tmp_path)
        lines = path.read_text(encoding="utf-8").splitlines()
        lines[3] = "[" * 100_000 + "]" * 100_000
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        loaded = posting_cache.load(str(tmp_path), "local", "demo")
        assert loaded is not None
        assert RowSet.build(secs, content_loader=loader, reuse=loaded).built == 1


class TestVocabulary:
    def test_dead_terms_are_compacted_away_and_postings_stay_correct(self, tmp_path):
        """⚠ Reusing a row means reusing the term ids it was written with, so
        the vocabulary only ever grows until something prunes it."""
        first, loader1 = _detached(_corpus(60))
        loaded = _write_and_load(tmp_path, RowSet.build(first, content_loader=loader1))
        assert len(loaded.vocab) > 0

        # A corpus sharing only its first five sections with the old one.
        # (Rebuilt from _corpus so the bodies are inline for _detached to serve;
        # `first` has already had its content stripped.)
        second = _corpus(5) + [
            _section(i, body=f"unrelated {i} discourse on obelisks and vellum\n")
            for i in range(500, 560)
        ]
        second, loader2 = _detached(second)
        rebuilt = RowSet.build(second, content_loader=loader2, reuse=loaded)
        assert rebuilt.reused == 5
        before_vocab = len(rebuilt.vocab)
        posting_cache.write(str(tmp_path), "local", "demo", rebuilt)
        assert len(rebuilt.vocab) < before_vocab  # compaction ran

        final = posting_cache.load(str(tmp_path), "local", "demo")
        settled = RowSet.build(second, content_loader=loader2, reuse=final)
        assert settled.reused == len(second)
        assert settled.posting_index(second).postings == PostingIndex.build(
            second, content_loader=loader2
        ).postings


# ---------------------------------------------------------------------------
# 3. End to end through the store
# ---------------------------------------------------------------------------

@pytest.fixture
def doc_tree(tmp_path: Path) -> Path:
    """40 documents / 200 sections — big enough that the incremental path
    genuinely skips work rather than re-materializing everything."""
    root = tmp_path / "docs"
    root.mkdir()
    for d in range(40):
        lines = [f"# Document {d} on {WORDS[d % len(WORDS)]}\n"]
        for h in range(4):
            i = d * 4 + h
            lines.append(f"\n## Heading {WORDS[i % len(WORDS)]} {i}\n\n{_body(i)}")
        (root / f"page-{d:02d}.md").write_text("".join(lines), encoding="utf-8")
    return root


def _index(doc_tree: Path, storage: Path, name: str) -> dict:
    result = index_local(path=str(doc_tree), storage_path=str(storage),
                         use_ai_summaries=False, use_embeddings=False, name=name)
    assert result.get("success") is True, result
    return result


def _sidecar(storage: Path, name: str) -> Path:
    return storage / "local" / f"{name}.postings.jsonl"


class TestStoreIntegration:
    def test_the_suffix_is_registered_as_index_owned(self):
        """A sixth sidecar cannot exist outside the canonical list: that list
        is what ``delete_index`` and ``_leftover_artifacts`` walk (jdoc#121)."""
        assert ".postings.jsonl" in INDEX_OWNED_SIDECAR_SUFFIXES

    def test_a_full_index_writes_the_sidecar_and_search_reads_it(
        self, doc_tree, tmp_path
    ):
        storage = tmp_path / "store"
        _index(doc_tree, storage, "full")
        assert _sidecar(storage, "full").exists()

        index = DocStore(base_path=str(storage)).load_index("local", "full")
        posting = get_or_build(index, content_loader=index._content_loader)
        assert posting.source == "sidecar"
        assert posting.rows_built == 0
        assert posting.rows_reused == len(index.sections)

        reference = PostingIndex.build(index.sections,
                                       content_loader=index._content_loader)
        assert posting.postings == reference.postings
        assert index.search("throttle webhook", max_results=5)

    def test_an_incremental_save_leaves_exact_stats_and_a_valid_sidecar(
        self, doc_tree, tmp_path
    ):
        storage = tmp_path / "store"
        _index(doc_tree, storage, "incr")
        (doc_tree / "page-07.md").write_text(
            "# Document 7 rewritten\n\n## Heading about obelisks\n\n"
            "Obelisks and vellum, entirely new prose.\n",
            encoding="utf-8",
        )
        _index(doc_tree, storage, "incr")

        index = DocStore(base_path=str(storage)).load_index("local", "incr")
        reference = bm25.compute_corpus_stats(index.sections,
                                              content_loader=index._content_loader)
        stored = {k: v for k, v in index.bm25_stats.items()
                  if k in {"N", "avgdl", "df"}}
        assert stored == reference
        assert index.bm25_stats.get("stats_tokenizer") == posting_cache.tokenizer_identity()

        posting = get_or_build(index, content_loader=index._content_loader)
        assert posting.rows_built == 0
        assert "obelisks" in posting.postings

    def test_an_incremental_save_rewrites_a_missing_sidecar_and_stays_exact(
        self, doc_tree, tmp_path
    ):
        """With no rows to reuse the save tokenizes the whole corpus, as it did
        before the sidecar existed, and writes the rows back so the next cold
        search and the next save inherit them. The statistics are exact either
        way: there is no carried-forward block on any path."""
        storage = tmp_path / "store"
        _index(doc_tree, storage, "gap")
        _sidecar(storage, "gap").unlink()
        (doc_tree / "page-11.md").write_text(
            "# Document 11 edited\n\n## Heading beta\n\nProse beta.\n", encoding="utf-8"
        )
        _index(doc_tree, storage, "gap")

        assert _sidecar(storage, "gap").exists()
        index = DocStore(base_path=str(storage)).load_index("local", "gap")
        reference = bm25.compute_corpus_stats(index.sections,
                                              content_loader=index._content_loader)
        stored = {k: v for k, v in index.bm25_stats.items() if k in {"N", "avgdl", "df"}}
        assert stored == reference
        posting = get_or_build(index, content_loader=index._content_loader)
        assert posting.source == "sidecar" and posting.rows_built == 0

    def test_an_incremental_save_reads_only_the_changed_sections_with_rows(
        self, doc_tree, tmp_path, monkeypatch
    ):
        """The rows are what make an always-exact save cheap: the kept
        sections' terms come off disk, so only the edited document is read and
        tokenized."""
        storage = tmp_path / "store"
        _index(doc_tree, storage, "cheap")
        (doc_tree / "page-05.md").write_text(
            "# Document 5 edited\n\n## Heading gamma\n\nProse gamma.\n", encoding="utf-8"
        )
        built = []
        orig = posting_cache.RowSet.build.__func__

        def spy(cls, sections, content_loader=None, reuse=None):
            out = orig(cls, sections, content_loader=content_loader, reuse=reuse)
            built.append((out.reused, out.built))
            return out

        monkeypatch.setattr(posting_cache.RowSet, "build", classmethod(spy))
        _index(doc_tree, storage, "cheap")
        reused, rebuilt = built[-1]
        assert reused > 0
        assert 0 < rebuilt <= 6, (reused, rebuilt)

    def test_a_corrupt_sidecar_neither_breaks_search_nor_the_next_save(
        self, doc_tree, tmp_path
    ):
        """The producer path for ``TestCorruptSidecar``: a real store, a real
        search and a real incremental save over a damaged file."""
        from jdocmunch_mcp.storage import doc_store as _ds
        from jdocmunch_mcp.tools.search_sections import search_sections

        storage = tmp_path / "store"
        _index(doc_tree, storage, "torn")
        path = _sidecar(storage, "torn")
        lines = path.read_bytes().split(b"\n")
        lines[2] = lines[2][:5] + b"\xff" + lines[2][5:]
        row = json.loads(lines[3])
        row[2] = "seven"
        lines[3] = json.dumps(row).encode("utf-8")
        path.write_bytes(b"\n".join(lines))
        _ds._INDEX_CACHE.clear()

        found = search_sections(repo="local/torn", query="throttle webhook",
                                storage_path=str(storage), semantic=False)
        assert found.get("results"), found

        (doc_tree / "page-03.md").write_text(
            "# Document 3 rewritten\n\n## Heading on lanterns\n\nLanterns.\n",
            encoding="utf-8",
        )
        _index(doc_tree, storage, "torn")
        assert posting_cache.load(str(storage), "local", "torn") is not None

    def test_a_tokenizer_change_rewrites_the_sidecar_and_the_stamp(
        self, doc_tree, tmp_path, monkeypatch
    ):
        """A sidecar from another tokenizer is refused on load, so the next
        save tokenizes everything again, records the tokenizer now running,
        and writes rows under it."""
        storage = tmp_path / "store"
        _index(doc_tree, storage, "retok")
        before = DocStore(base_path=str(storage)).load_index("local", "retok")
        assert before.bm25_stats.get("stats_tokenizer") == posting_cache.tokenizer_identity()

        monkeypatch.setattr(posting_cache, "_TOKENIZER_IDENTITY", "src-a-later-tokenizer")
        (doc_tree / "page-19.md").write_text(
            "# Document 19 edited\n\n## Heading on quartz\n\nQuartz prose.\n",
            encoding="utf-8",
        )
        _index(doc_tree, storage, "retok")

        after = DocStore(base_path=str(storage)).load_index("local", "retok")
        assert after.bm25_stats.get("stats_tokenizer") == "src-a-later-tokenizer"
        header = json.loads(
            _sidecar(storage, "retok").read_text(encoding="utf-8").splitlines()[0]
        )
        assert header["tokenizer"] == "src-a-later-tokenizer"

    def test_an_index_written_without_a_sidecar_keeps_working(
        self, doc_tree, tmp_path
    ):
        """⚠ Every existing 1.x index on disk is this case."""
        storage = tmp_path / "store"
        _index(doc_tree, storage, "legacy")
        _sidecar(storage, "legacy").unlink()

        index = DocStore(base_path=str(storage)).load_index("local", "legacy")
        posting = get_or_build(index, content_loader=index._content_loader)
        assert posting.source == "rebuild"
        assert posting.postings == PostingIndex.build(
            index.sections, content_loader=index._content_loader
        ).postings
        assert index.search("throttle webhook", max_results=5)

    def test_deleting_the_index_removes_the_sidecar(self, doc_tree, tmp_path):
        from jdocmunch_mcp.tools.delete_index import delete_index

        storage = tmp_path / "store"
        _index(doc_tree, storage, "gone")
        assert _sidecar(storage, "gone").exists()
        result = delete_index("local/gone", storage_path=str(storage))
        assert result.get("success") is True, result
        assert not _sidecar(storage, "gone").exists()
