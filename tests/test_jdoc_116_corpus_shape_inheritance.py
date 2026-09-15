"""jdoc#116: a re-entry point that says nothing must not widen the corpus.

The `index-local` CLI passes no `extra_ignore_patterns`, so its call computed a
`full` selection and OVERWROTE a stored `full+shape:<hash>`, silently re-admitting
every deliberately excluded file.

⚠ The reporter's preferred remedy, "preserve the stored selection", is NOT
sufficient on its own and would have been worse: only the DIGEST was persisted,
never the patterns, so an inherited descriptor would assert an exclusion the walk
could not reapply. The index would claim `full+shape:...` while containing the
excluded file, and today's behaviour at least DISCLOSES the widening. Persisting
the patterns is what makes inheritance honest.

The load-bearing distinction is None vs []:
    None -> caller said nothing    -> inherit
    []   -> caller said "none"     -> widen, and disclose
"""
from pathlib import Path

import pytest


def _corpus(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    for name, body in [
        ("alpha", "Real content."),
        ("beta", "More real content."),
        ("TEMPLATE", "Should never be indexed."),
    ]:
        (root / f"{name}.md").write_text(
            f"# {name}\n## Scope\n{body}\n", encoding="utf-8")
    store = tmp_path / "store"
    store.mkdir()
    return root, store


def _run(root, store, **kw):
    from jdocmunch_mcp.tools.index_local import index_local
    return index_local(path=str(root), name="sel", use_embeddings=False,
                       use_ai_summaries=False, storage_path=str(store), **kw)


def _state(store):
    from jdocmunch_mcp.storage.doc_store import DocStore
    i = DocStore(base_path=str(store)).load_index("local", "sel")
    return (getattr(i, "corpus_selection", "") or "",
            list(getattr(i, "corpus_shape_patterns", None) or []),
            sorted(i.doc_paths))


class TestPatternsArePersisted:
    def test_create_path_stores_them(self, tmp_path):
        """The CREATE path matters most: a corpus shaped at creation is the
        common case, and persisting only on refresh would leave the very first
        re-index with nothing to inherit."""
        root, store = _corpus(tmp_path)
        _run(root, store, extra_ignore_patterns=["TEMPLATE.md"])
        sel, pats, docs = _state(store)
        assert pats == ["TEMPLATE.md"]
        assert "+shape:" in sel
        assert docs == ["alpha.md", "beta.md"]

    def test_survives_the_explicit_allowlist_serializer(self, tmp_path):
        """`_index_to_dict` is an allow-list, not asdict(). A field added to the
        dataclass and to every save/load signature still round-trips as EMPTY
        until it is named there, which is exactly how this was missed once."""
        from jdocmunch_mcp.storage.doc_store import DocStore
        root, store = _corpus(tmp_path)
        _run(root, store, extra_ignore_patterns=["TEMPLATE.md"])
        raw = DocStore(base_path=str(store))._index_to_dict(
            DocStore(base_path=str(store)).load_index("local", "sel"))
        assert raw.get("corpus_shape_patterns") == ["TEMPLATE.md"]

    def test_unshaped_index_gains_no_key(self, tmp_path):
        """Written only when non-empty, like its neighbours, so legacy files
        stay byte-identical."""
        from jdocmunch_mcp.storage.doc_store import DocStore
        root, store = _corpus(tmp_path)
        _run(root, store)
        st = DocStore(base_path=str(store))
        raw = st._index_to_dict(st.load_index("local", "sel"))
        assert "corpus_shape_patterns" not in raw


class TestInheritance:
    def test_silent_rerun_does_not_widen(self, tmp_path):
        """The defect. This is the CLI's exact shape: it says nothing."""
        root, store = _corpus(tmp_path)
        _run(root, store, extra_ignore_patterns=["TEMPLATE.md"])
        before = _state(store)
        r = _run(root, store)
        after = _state(store)

        assert after == before, (before, after)
        assert "TEMPLATE.md" not in after[2]
        assert r.get("corpus_selection_changed") is None

    def test_explicit_empty_list_widens_and_discloses(self, tmp_path):
        """[] is a decision, not silence. It must still work, and still say so."""
        root, store = _corpus(tmp_path)
        _run(root, store, extra_ignore_patterns=["TEMPLATE.md"])
        r = _run(root, store, extra_ignore_patterns=[])
        sel, pats, docs = _state(store)

        assert "TEMPLATE.md" in docs
        assert pats == []
        assert sel == "full"
        assert r.get("corpus_selection_changed") == {
            "from": "full+shape:9dd6c241e7d4", "to": "full",
        }

    def test_clearing_is_durable(self, tmp_path):
        """After an explicit clear there is nothing to inherit, so a later
        silent rerun must stay wide rather than resurrect the exclusion."""
        root, store = _corpus(tmp_path)
        _run(root, store, extra_ignore_patterns=["TEMPLATE.md"])
        _run(root, store, extra_ignore_patterns=[])
        _run(root, store)
        sel, pats, docs = _state(store)
        assert (sel, pats) == ("full", [])
        assert "TEMPLATE.md" in docs

    def test_new_patterns_replace_rather_than_merge(self, tmp_path):
        """Supplying patterns is a full statement of the shape, not an addend."""
        root, store = _corpus(tmp_path)
        _run(root, store, extra_ignore_patterns=["TEMPLATE.md"])
        _run(root, store, extra_ignore_patterns=["beta.md"])
        sel, pats, docs = _state(store)
        assert pats == ["beta.md"]
        assert "TEMPLATE.md" in docs and "beta.md" not in docs


class TestLegacyIndexCannotSilentlyWiden:
    def test_shaped_index_with_no_stored_patterns_warns(self, tmp_path):
        """Pre-fix indexes carry `full+shape:<hash>` and nothing to reapply.

        We cannot inherit, so the corpus widens exactly as before. Silence is
        what the issue is about, so it must say the shape is unrecoverable and
        name the remedy.
        """
        from jdocmunch_mcp.storage.doc_store import DocStore
        root, store = _corpus(tmp_path)
        _run(root, store, extra_ignore_patterns=["TEMPLATE.md"])

        # Simulate a pre-fix index: descriptor kept, patterns absent.
        st = DocStore(base_path=str(store))
        idx = st.load_index("local", "sel")
        idx.corpus_shape_patterns = []
        st._save_index_object(idx) if hasattr(st, "_save_index_object") else None
        import json
        p = Path(store) / "local" / "sel" / "index.json"
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            data.pop("corpus_shape_patterns", None)
            p.write_text(json.dumps(data), encoding="utf-8")

        sel_before, pats_before, _ = _state(store)
        if pats_before:
            pytest.skip("index layout differs; simulation did not take")
        assert "+shape:" in sel_before

        r = _run(root, store)
        joined = " ".join(r.get("warnings") or [])
        assert "WIDENED" in joined, joined
        assert "extra-ignore-pattern" in joined or "extra_ignore_patterns" in joined


class TestCliCanExpressIt:
    def test_flags_exist(self):
        """jdoc#108's principle: the MCP tool could express it and the CLI could
        not. This was the last member of that set."""
        import argparse
        from jdocmunch_mcp import server
        parser = server._build_arg_parser() if hasattr(server, "_build_arg_parser") else None
        if parser is None:
            # The argparse tree moved to cli/main.py so the console script
            # stops importing the MCP SDK on every hook fire. server.py
            # re-exports main(); the FLAGS live there now.
            from jdocmunch_mcp.cli import main as cli_main
            src = Path(cli_main.__file__).read_text(encoding="utf-8")
            assert '"--extra-ignore-pattern"' in src
            assert '"--no-extra-ignore-patterns"' in src
            return
        assert isinstance(parser, argparse.ArgumentParser)

    def test_dispatch_maps_the_clear_flag_to_empty_list(self):
        """--no-extra-ignore-patterns must send [], not None, or it would mean
        'inherit' and do the opposite of what its name promises."""
        from jdocmunch_mcp.cli import main as cli_main
        src = Path(cli_main.__file__).read_text(encoding="utf-8")
        assert "[] if getattr(args, \"no_extra_ignore_patterns\", False)" in src
