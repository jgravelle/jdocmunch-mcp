"""Discovery's per-file cost on a large tree.

`discover_doc_files` ran an lstat (twice), two `resolve()` calls and 2 x 25
fnmatch calls on EVERY file in the tree before asking whether any parser reads
it. On a 128k-file folder (cloned corpora plus virtualenvs) with 107k non-doc
files the walk took 80.5 s, and `index_local` timed out in an MCP client. After
these changes the same walk took 18.5 s and discovered the same 20,816 files.

These tests pin the behaviour, not the timing: a non-doc file never reaches a
filesystem check, a doc candidate still meets every check, and the compiled
secret matcher gives the per-pattern loop's verdict.
"""

import fnmatch
import os
from pathlib import Path

import pytest

from jdocmunch_mcp import security
from jdocmunch_mcp.security import SECRET_PATTERNS, is_secret_file, validate_path
from jdocmunch_mcp.tools import index_local as il


def _old_is_secret_file(file_path: str) -> bool:
    """The pre-change loop, verbatim, as the reference."""
    name = os.path.basename(file_path).lower()
    path_lower = file_path.lower()
    for pattern in SECRET_PATTERNS:
        if fnmatch.fnmatch(name, pattern):
            return True
        if fnmatch.fnmatch(path_lower, pattern):
            return True
    return False


_NAMES = [
    ".env", ".ENV", "prod.env", ".env.local", "a/b/.env.production",
    "server.pem", "KEYS/Server.PEM", "x.key", "x.p12", "x.pfx", "x.credentials",
    "x.keystore", "x.jks", "x.token", "my_secret_notes.md", "docs/Secrets/readme.md",
    "SECRET.txt", "id_rsa", "id_rsa.pub", "id_ed25519", "id_ed25519.pub", "id_dsa",
    "id_ecdsa", ".htpasswd", ".netrc", ".npmrc", ".pypirc", "credentials.json",
    "conf/credentials.json", "service-account-prod.json", "Service-Account.JSON",
    "x.secrets", "README.md", "docs/guide.md", "notes.txt", "env.md", "key.md",
    "tokens.md", "id_rsa_notes.md", "a\\b\\secret.md", "a\\b\\readme.md",
    "credentials.json.md", "my.env.md", "", "dir/", "pem", ".pem",
]


@pytest.mark.parametrize("name", _NAMES)
def test_compiled_secret_matcher_agrees_with_the_loop(name):
    assert is_secret_file(name) is _old_is_secret_file(name)


def test_validate_path_with_a_resolved_root_gives_the_same_answer(tmp_path):
    root = tmp_path.resolve()
    inside = root / "a" / "b.md"
    inside.parent.mkdir()
    inside.write_text("# x\n", encoding="utf-8")
    outside = tmp_path.parent / "elsewhere.md"
    for target in (inside, outside, root):
        assert validate_path(root, target, root_is_resolved=True) is validate_path(root, target)


@pytest.fixture
def mixed_tree(tmp_path):
    root = tmp_path / "corpus"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "guide.md").write_text("# Guide\n\nbody\n", encoding="utf-8")
    (root / "docs" / "notes.txt").write_text("notes\n", encoding="utf-8")
    (root / "docs" / "secrets.md").write_text("# s\n", encoding="utf-8")
    (root / "src").mkdir()
    for i in range(30):
        (root / "src" / f"mod{i}.py").write_text("x = 1\n", encoding="utf-8")
    (root / "src" / "server.pem").write_text("-----BEGIN-----\n", encoding="utf-8")
    (root / ".env").write_text("A=1\n", encoding="utf-8")
    return root


def test_non_doc_files_never_reach_a_filesystem_check(mixed_tree, monkeypatch):
    seen_resolve = []
    seen_lstat = []
    real_validate = il.validate_path
    real_is_symlink = Path.is_symlink

    def spy_validate(root, target, **kw):
        seen_resolve.append(Path(target).name)
        return real_validate(root, target, **kw)

    def spy_is_symlink(self):
        seen_lstat.append(self.name)
        return real_is_symlink(self)

    monkeypatch.setattr(il, "validate_path", spy_validate)
    monkeypatch.setattr(Path, "is_symlink", spy_is_symlink)

    skip_counts: dict = {}
    files, warnings, discovered, _ = il.discover_doc_files(mixed_tree, skip_counts=skip_counts)

    names = sorted(p.name for p in files)
    assert names == ["guide.md", "notes.txt"]
    # Every doc candidate met the checks, each exactly once ...
    assert sorted(seen_resolve) == ["guide.md", "notes.txt", "secrets.md"]
    assert sorted(seen_lstat) == ["guide.md", "notes.txt", "secrets.md"]
    # ... and no .py, .pem or .env file reached either one.
    assert skip_counts["unsupported_extension"] == 32
    assert skip_counts["secret_file"] == 1


def test_a_doc_extension_secret_still_warns(mixed_tree):
    _, warnings, _, _ = il.discover_doc_files(mixed_tree)
    assert any("secrets.md" in w for w in warnings)
    # A non-doc secret is never read, so it is counted as unsupported instead.
    assert not any("server.pem" in w for w in warnings)


def test_secret_regex_is_built_from_the_pattern_list():
    # A pattern added to SECRET_PATTERNS must reach the compiled matcher; the
    # regex is built at import, so this pins that it is derived, not restated.
    assert security._SECRET_RE.pattern == "|".join(
        fnmatch.translate(p) for p in SECRET_PATTERNS
    )
