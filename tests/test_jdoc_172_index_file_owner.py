"""jdoc#172: `index-file` found the owning index by ancestor FOLDER NAME.

It never read the `source_root` each index stores, so:

1. an index built with `--name` was never found ("File not in any index"),
   and the PostToolUse edit hook, which runs `index_file`, never refreshed it;
2. an ancestor folder sharing an index's name re-rooted the file there, and
   the file's sections were added a second time under the wrong path.

The owner is now the index whose stored root contains the file, longest root
first, the rule #171's Read-hook probe already uses.
"""
from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.tools.index_file import index_file
from jdocmunch_mcp.tools.index_local import index_local


def _index(path, name, store):
    r = index_local(path=str(path), name=name, use_embeddings=False,
                    use_ai_summaries=False, storage_path=str(store))
    assert r.get("success"), r


def _write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _index_of(store, name):
    return DocStore(base_path=str(store)).load_index("local", name)


def _refresh(path, store):
    return index_file(str(path), storage_path=str(store), use_ai_summaries=False)


def test_custom_named_index_is_found(tmp_path):
    docs, store = tmp_path / "docs", tmp_path / "store"
    _write(docs / "a.md", "# A\n\nalpha\n")
    _index(docs, "handbook", store)
    _write(docs / "a.md", "# A\n\nbetaquokka\n")

    r = _refresh(docs / "a.md", store)

    assert r["success"] is True, r
    assert (r["repo"], r["file"], r["is_new"]) == ("local/handbook", "a.md", False)
    ds = DocStore(base_path=str(store))
    idx = ds.load_index("local", "handbook")
    bodies = [ds.get_section_content("local", "handbook", s["id"], _index=idx) or ""
              for s in idx.sections]
    assert any("betaquokka" in b for b in bodies)


def test_ancestor_sharing_the_name_does_not_reroot(tmp_path):
    corpus, store = tmp_path / "eq" / "corpus", tmp_path / "store"
    _write(corpus / "GUIDE.md", "# Guide\n\none\n\n## Two\n\ntwo\n")
    _index(corpus, "eq", store)
    before = _index_of(store, "eq")
    _write(corpus / "GUIDE.md", "# Guide\n\none\n\n## Two\n\nedited\n")

    r = _refresh(corpus / "GUIDE.md", store)

    assert r["success"] is True, r
    assert r["file"] == "GUIDE.md" and r["is_new"] is False
    after = _index_of(store, "eq")
    assert after.doc_paths == before.doc_paths == ["GUIDE.md"]
    assert len(after.sections) == len(before.sections)


def test_nested_index_wins_over_the_outer_one(tmp_path):
    outer, store = tmp_path / "outer", tmp_path / "store"
    inner = outer / "sub"
    _write(outer / "top.md", "# Top\n\nt\n")
    _write(inner / "deep.md", "# Deep\n\nd\n")
    _index(outer, "outer", store)
    _index(inner, "inner", store)

    r = _refresh(inner / "deep.md", store)

    assert (r["repo"], r["file"]) == ("local/inner", "deep.md"), r


def test_new_file_under_a_custom_root_joins_that_index(tmp_path):
    docs, store = tmp_path / "docs", tmp_path / "store"
    _write(docs / "a.md", "# A\n\nalpha\n")
    _index(docs, "handbook", store)
    _write(docs / "guides" / "b.md", "# B\n\nbeta\n")

    r = _refresh(docs / "guides" / "b.md", store)

    assert (r["repo"], r["file"], r["is_new"]) == ("local/handbook", "guides/b.md", True), r


def test_file_outside_every_root_is_still_refused(tmp_path):
    docs, store = tmp_path / "docs", tmp_path / "store"
    _write(docs / "a.md", "# A\n\nalpha\n")
    _index(docs, "handbook", store)
    _write(tmp_path / "elsewhere" / "x.md", "# X\n\nx\n")

    r = _refresh(tmp_path / "elsewhere" / "x.md", store)

    assert r["success"] is False and r["exit_code"] == 1


def test_folder_name_fallback_without_a_summary_sidecar(tmp_path):
    """An index saved before jdoc#77 has no summary; the name walk still finds
    it when the folder IS its name."""
    docs, store = tmp_path / "manual", tmp_path / "store"
    _write(docs / "a.md", "# A\n\nalpha\n")
    _index(docs, "manual", store)
    (store / "local" / "manual.summary.json").unlink()

    r = _refresh(docs / "a.md", store)

    assert (r["success"], r["repo"], r["file"]) == (True, "local/manual", "a.md"), r


def test_edit_hook_refreshes_a_custom_named_index(tmp_path, monkeypatch):
    """The path that was silent: the hook ignores index_file's failure."""
    from jdocmunch_mcp.cli import hooks
    docs, store = tmp_path / "docs", tmp_path / "store"
    _write(docs / "a.md", "# A\n\nalpha\n")
    _index(docs, "handbook", store)
    _write(docs / "b.md", "# B\n\nbeta\n")
    monkeypatch.setenv("DOC_INDEX_PATH", str(store))

    assert hooks.run_hook_reindex(str(docs / "b.md")) == 0

    assert "b.md" in _index_of(store, "handbook").doc_paths
