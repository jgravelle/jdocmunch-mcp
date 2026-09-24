"""jdoc#146: with DOC_INDEX_PATH set, the CLI wrote sidecars under ~/.doc-index.

The CLI's ``index-local`` calls ``index_local`` without ``storage_path``.
``DocStore`` honours ``DOC_INDEX_PATH`` itself (#37), so the index went there,
but eleven modules fell back to ``~/.doc-index`` on ``base_path=None``. The
derived sidecars went to the home root and overwrote the live files of any
index there with the same name. Every root now resolves through
``storage.paths``.

The embeddings sidecar needs a migration as well as a fix: a user who ran the
CLI this way before has their vectors under the home root, and the first pass
after upgrading must not read their absence at the new root as "re-embed
everything".

Reported by @LuigiNicaPRO.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from jdocmunch_mcp.embeddings import cache as emb_cache
from jdocmunch_mcp.embeddings import provider as emb_provider
from jdocmunch_mcp.storage import doc_store as _doc_store

DIM = 8
SRC = Path(__file__).resolve().parents[1] / "src" / "jdocmunch_mcp"


class _CountingFake:
    def __init__(self):
        self.texts = 0

    def embed_texts(self, texts, task_type=None):
        self.texts += len(texts)
        return [[1.0] * DIM for _ in texts]


@pytest.fixture
def roots(tmp_path, monkeypatch):
    home = tmp_path / "home"
    env_root = tmp_path / "scratch"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("DOC_INDEX_PATH", str(env_root))
    monkeypatch.setenv("JDOCMUNCH_SHARE_SAVINGS", "0")
    fake = _CountingFake()
    monkeypatch.setattr(emb_provider, "_get_provider", lambda: fake)
    monkeypatch.setattr(emb_provider, "get_provider_name", lambda: "fake")
    monkeypatch.setattr(emb_provider, "_provider_identity", lambda n: ("fake-model", DIM))
    emb_cache._KEY_MEMO.clear()
    _doc_store._INDEX_CACHE.clear()
    src = tmp_path / "docs"
    src.mkdir()
    for i in range(12):
        (src / f"d{i}.md").write_text(
            f"# Doc {i}\n\n## Widget setup\n\nConfigure the widget timeout {i}.\n\n"
            f"## Glossary\n\n**Widget**: a thing that does work {i}.\n",
            encoding="utf-8",
        )
    return home / ".doc-index", env_root, src, fake


def _cli_index(src):
    """What the CLI does: no storage_path."""
    from jdocmunch_mcp.tools.index_local import index_local
    r = index_local(path=str(src), name="c146", use_ai_summaries=False, use_embeddings=True)
    assert r.get("success"), r
    return r


def _sidecars(root: Path) -> set:
    d = root / "local"
    return {p.name for p in d.glob("c146.*")} if d.exists() else set()


def test_cli_index_writes_every_sidecar_under_doc_index_path(roots):
    home_root, env_root, src, _ = roots
    _cli_index(src)
    written = _sidecars(env_root)
    assert "c146.json" in written and "c146.embeddings.jsonl" in written
    assert _sidecars(home_root) == set(), f"written under the home root: {_sidecars(home_root)}"


def test_first_pass_after_upgrade_reuses_vectors_under_the_home_root(roots):
    home_root, env_root, src, fake = roots
    _cli_index(src)
    full = fake.texts
    # Simulate a pre-#146 install: the vectors went to the home root.
    (home_root / "local").mkdir(parents=True)
    legacy = home_root / "local" / "c146.embeddings.jsonl"
    (env_root / "local" / "c146.embeddings.jsonl").replace(legacy)
    legacy_bytes = legacy.read_bytes()
    emb_cache._KEY_MEMO.clear()
    _doc_store._INDEX_CACHE.clear()

    (src / "d3.md").write_text("# Doc 3\n\n## Widget setup\n\nchanged\n", encoding="utf-8")
    fake.texts = 0
    r = _cli_index(src)

    assert fake.texts < full / 4, (
        f"re-embedded {fake.texts} texts after a {full}-text full index: the "
        "home-root vectors were read as absent"
    )
    assert r.get("embedding_coverage") == 1.0
    _doc_store._INDEX_CACHE.clear()
    index = _doc_store.DocStore().load_index("local", "c146")
    wanted = {s["content_hash"] for s in index.sections if s.get("content_hash")}
    migrated = emb_cache.stored_hashes(str(env_root), "local", "c146")
    assert wanted <= migrated, f"{len(wanted - migrated)} vectors did not reach the new root"
    assert legacy.read_bytes() == legacy_bytes, "the home-root copy was modified"


def test_migrated_sidecar_has_a_header(roots):
    """#141's append path must not match the home header and append headerless rows."""
    home_root, env_root, src, _ = roots
    _cli_index(src)
    (home_root / "local").mkdir(parents=True)
    (env_root / "local" / "c146.embeddings.jsonl").replace(home_root / "local" / "c146.embeddings.jsonl")
    emb_cache._KEY_MEMO.clear()
    (src / "d5.md").write_text("# Doc 5\n\nchanged\n", encoding="utf-8")
    _cli_index(src)
    first = (env_root / "local" / "c146.embeddings.jsonl").read_text(encoding="utf-8").splitlines()[0]
    assert '"_header": true' in first


def test_server_still_finds_home_root_vectors_before_any_cli_pass(roots):
    """DocStore's read fallback must keep naming the HOME root explicitly."""
    home_root, env_root, src, _ = roots
    _cli_index(src)
    (home_root / "local").mkdir(parents=True)
    legacy = home_root / "local" / "c146.embeddings.jsonl"
    (env_root / "local" / "c146.embeddings.jsonl").replace(legacy)
    _doc_store._INDEX_CACHE.clear()
    index = _doc_store.DocStore().load_index("local", "c146")
    assert Path(index._embeddings_sidecar) == legacy


def test_explicit_base_path_never_reads_the_home_root(roots, tmp_path):
    home_root, _env_root, _src, _ = roots
    emb_cache.write(str(home_root), "local", "other", provider="fake", model="fake-model",
                    dim=DIM, entries=[("a#pv1", [1.0] * DIM)])
    assert emb_cache.load(str(tmp_path / "elsewhere"), "local", "other",
                          provider="fake", model="fake-model", dim=DIM) == {}


def test_root_is_read_at_call_time(monkeypatch, tmp_path):
    from jdocmunch_mcp.storage import paths
    monkeypatch.setenv("DOC_INDEX_PATH", str(tmp_path / "a"))
    assert paths.default_root() == tmp_path / "a"
    monkeypatch.setenv("DOC_INDEX_PATH", str(tmp_path / "b"))
    assert paths.default_root() == tmp_path / "b"
    monkeypatch.delenv("DOC_INDEX_PATH")
    assert paths.default_root() == paths.home_root()


def _doc_index_literals(tree):
    """String constants naming the root, docstrings excluded."""
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                docstrings.add(id(body[0].value))
    return [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and n.value == ".doc-index" and id(n) not in docstrings
    ]


def test_only_storage_paths_names_the_home_root():
    """Ratchet: a new module that builds its own ``~/.doc-index`` fallback
    reintroduces #146 for whatever it stores."""
    offenders = []
    for py in SRC.rglob("*.py"):
        if py.name == "paths.py" and py.parent.name == "storage":
            continue
        lines = _doc_index_literals(ast.parse(py.read_text(encoding="utf-8")))
        offenders += [f"{py.relative_to(SRC)}:{ln}" for ln in lines]
    assert offenders == [], offenders


def test_ratchet_catches_the_shape_it_names():
    tree = ast.parse('from pathlib import Path\nroot = Path.home() / ".doc-index"\n')
    assert _doc_index_literals(tree) == [2]
    assert _doc_index_literals(ast.parse('"""Lives in ~/.doc-index and .doc-index."""\n')) == []
