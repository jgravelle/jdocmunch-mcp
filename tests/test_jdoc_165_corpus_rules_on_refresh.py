"""jdoc#165: two refresh paths re-admitted files the corpus rules exclude.

Reported by @randyhbh with a repro for each path.

1. The watcher. `discover_local_doc_repos` hands over the durable handle
   `local/<name>`, and `_corpus_filters` passed it straight to
   `load_index("local", ...)`. That looked up `local/local/<name>`, found
   nothing, and the stored `corpus_shape_patterns` (jdoc#116) never applied.
   ⚠ The jdoc#115 tests passed the BARE name into `_handle_changes`, which is
   why they stayed green: no test fed the watcher the name discovery produces.
   The tests here build `roots_map` from discovery itself.

2. The PostToolUse hook. `index_file` checked only the extension, so an agent's
   Edit/Write of an excluded file added it. The hook now asks `index_file` to
   apply the corpus rules. A caller naming the file itself (`index-file
   <path>`) keeps the explicit-path bypass, the same split jdoc#115 drew
   between the watcher and `index_local(paths=...)`.
"""
import asyncio
from pathlib import Path


def _fixture(tmp_path):
    fx = tmp_path / "repro"
    (fx / "gen").mkdir(parents=True)
    (fx / ".gitignore").write_text("gen/\n", encoding="utf-8")
    (fx / "README.md").write_text("# Visible\n\nIndexed.\n", encoding="utf-8")
    (fx / "TEMPLATE.md").write_text("# Template\n\nExcluded.\n", encoding="utf-8")
    (fx / "gen" / "NOISE.md").write_text("# Noise\n\nIgnored.\n", encoding="utf-8")
    store = tmp_path / "store"
    store.mkdir()
    from jdocmunch_mcp.tools.index_local import index_local
    index_local(path=str(fx), name="repro", use_embeddings=False,
                use_ai_summaries=False, storage_path=str(store),
                extra_ignore_patterns=["TEMPLATE.md"])
    return fx, store


def _doc_paths(store):
    from jdocmunch_mcp.storage.doc_store import DocStore
    return sorted(DocStore(base_path=str(store)).load_index("local", "repro").doc_paths)


def _fire_discovered(paths, store):
    """Run the watcher's handler with the roots map discovery produces."""
    from jdocmunch_mcp import watch as W
    roots = dict(W.discover_local_doc_repos(str(store)))
    assert list(roots.values()) == ["local/repro"]  # the shape that broke
    asyncio.run(W._handle_changes(
        [(1, str(Path(p).resolve())) for p in paths],
        roots, str(store), False, True, None))


class TestWatcherWithDiscoveredName:
    def test_stored_patterns_load_for_the_discovered_handle(self, tmp_path):
        from jdocmunch_mcp import watch as W
        fx, store = _fixture(tmp_path)
        root = str(fx.resolve())
        assert W._corpus_filters(root, "local/repro", str(store))[1] is not None
        assert W._corpus_filters(root, "repro", str(store))[1] is not None

    def test_pattern_excluded_file_is_not_readmitted(self, tmp_path):
        fx, store = _fixture(tmp_path)
        before = _doc_paths(store)
        assert "TEMPLATE.md" not in before
        (fx / "TEMPLATE.md").write_text("# Template\n\nEdited.\n", encoding="utf-8")
        _fire_discovered([fx / "TEMPLATE.md"], store)
        assert _doc_paths(store) == before

    def test_visible_file_still_refreshes(self, tmp_path):
        fx, store = _fixture(tmp_path)
        (fx / "NEW.md").write_text("# New\n\nBody.\n", encoding="utf-8")
        _fire_discovered([fx / "NEW.md"], store)
        assert "NEW.md" in _doc_paths(store)


class TestHookAppliesCorpusRules:
    def _hook(self, path, store, monkeypatch):
        from jdocmunch_mcp.cli import hooks
        monkeypatch.setenv("DOC_INDEX_PATH", str(store))
        assert hooks.run_hook_reindex(str(path)) == 0

    def test_pattern_excluded_edit_is_not_admitted(self, tmp_path, monkeypatch):
        fx, store = _fixture(tmp_path)
        self._hook(fx / "TEMPLATE.md", store, monkeypatch)
        assert "TEMPLATE.md" not in _doc_paths(store)

    def test_gitignored_edit_is_not_admitted(self, tmp_path, monkeypatch):
        fx, store = _fixture(tmp_path)
        self._hook(fx / "gen" / "NOISE.md", store, monkeypatch)
        assert "gen/NOISE.md" not in _doc_paths(store)

    def test_visible_edit_is_still_admitted(self, tmp_path, monkeypatch):
        fx, store = _fixture(tmp_path)
        (fx / "NEW.md").write_text("# New\n\nBody.\n", encoding="utf-8")
        self._hook(fx / "NEW.md", store, monkeypatch)
        assert "NEW.md" in _doc_paths(store)

    def test_skip_is_reported_not_an_error(self, tmp_path):
        from jdocmunch_mcp.tools.index_file import index_file
        fx, store = _fixture(tmp_path)
        r = index_file(str(fx / "TEMPLATE.md"), storage_path=str(store),
                       use_ai_summaries=False, apply_corpus_rules=True)
        assert r["success"] is True and r["skipped"] is True
        assert r["reason"] == "excluded_by_corpus_rules"
        assert r["file"] == "TEMPLATE.md"


class TestExplicitIndexFileStillBypasses:
    """The contract this fix must NOT change: `index-file <path>` names a file
    on purpose, like `index_local(paths=...)`, and gets it."""

    def test_named_file_is_indexed_without_the_flag(self, tmp_path):
        from jdocmunch_mcp.tools.index_file import index_file
        fx, store = _fixture(tmp_path)
        r = index_file(str(fx / "TEMPLATE.md"), storage_path=str(store),
                       use_ai_summaries=False)
        assert r["success"] is True and not r.get("skipped")
        assert "TEMPLATE.md" in _doc_paths(store)
