"""v1.73.0 - CLI/config ergonomics batch (#36-41).

#36: JDOCMUNCH_DISABLED_TOOLS was enforced against the literal incoming name,
so a deprecated alias (index_repo/list_repos) reached the canonical handler
unchecked. #37: DOC_INDEX_PATH was honored only on the MCP dispatch path; CLI
and hooks hard-defaulted to ~/.doc-index. #38: index-file owner detection was
name-inference only, with no override for custom names / non-path-safe folders.
#39: init --hooks wrote bare command names that fail under Claude Code's
minimal-PATH shell. #40: no --version flag. #41: no delete-index subcommand.
"""

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from jdocmunch_mcp.server import main, call_tool, _ALIAS_TO_CANONICAL
from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.cli.init import _hook_invocation, _enforcement_hooks
from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.index_file import index_file


# --- #40: --version ------------------------------------------------------------

@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_flag_exits_zero(flag, capsys):
    with pytest.raises(SystemExit) as exc:
        main([flag])
    assert exc.value.code == 0
    assert "jdocmunch-mcp" in capsys.readouterr().out


# --- #41: delete-index CLI subcommand -----------------------------------------

def test_delete_index_cli_roundtrip(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        store = os.path.join(tmp, "store")
        monkeypatch.setenv("DOC_INDEX_PATH", store)
        corpus = Path(tmp, "corpus")
        corpus.mkdir()
        (corpus / "doc.md").write_text("# Title\n\nBody.\n", encoding="utf-8")
        index_local(path=str(corpus), name="del-repro", storage_path=store,
                    use_ai_summaries=False, use_embeddings=False)
        assert DocStore(base_path=store).load_index("local", "del-repro") is not None

        with pytest.raises(SystemExit) as exc:
            main(["delete-index", "--repo", "local/del-repro"])
        assert exc.value.code == 0
        assert DocStore(base_path=store).load_index("local", "del-repro") is None


# --- #36: disabled-tools gate covers deprecated aliases -----------------------

def _call(name, args, store):
    return asyncio.run(call_tool(name, args))[0].text


def test_alias_blocked_when_canonical_disabled(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("DOC_INDEX_PATH", tmp)
        monkeypatch.setenv("JDOCMUNCH_DISABLED_TOOLS", "doc_list_repos")
        # canonical name blocked
        assert "disabled" in _call("doc_list_repos", {}, tmp)
        # deprecated alias dispatching to the same handler ALSO blocked (#36)
        assert "disabled" in _call("list_repos", {}, tmp)


def test_alias_executes_when_not_disabled(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("DOC_INDEX_PATH", tmp)
        monkeypatch.delenv("JDOCMUNCH_DISABLED_TOOLS", raising=False)
        assert "disabled" not in _call("list_repos", {}, tmp)


def test_alias_map_shape():
    assert _ALIAS_TO_CANONICAL == {
        "index_repo": "doc_index_repo",
        "list_repos": "doc_list_repos",
    }


# --- #37: DOC_INDEX_PATH honored by the store default -------------------------

def test_doc_index_path_honored_by_store_default(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "alt-index")
        monkeypatch.setenv("DOC_INDEX_PATH", target)
        assert DocStore().base_path == Path(target)
        # explicit base_path still wins
        explicit = os.path.join(tmp, "explicit")
        assert DocStore(base_path=explicit).base_path == Path(explicit)


# --- #38: index-file --name override (custom name / spaced folder) ------------

def test_index_file_name_override_on_spaced_folder():
    with tempfile.TemporaryDirectory() as tmp:
        store = os.path.join(tmp, "store")
        corpus = Path(tmp, "spaced folder repro")
        corpus.mkdir()
        doc = corpus / "doc.md"
        doc.write_text("# Title\n\nBody.\n", encoding="utf-8")
        index_local(path=str(corpus), name="spaced-repro", storage_path=store,
                    use_ai_summaries=False, use_embeddings=False)

        # #172: detection reads the stored source_root, so a spaced folder and a
        # custom name resolve without --name. (Until 1.146.0 it matched folder
        # names and failed here, which this test used to pin.)
        res = index_file(str(doc), storage_path=store)
        assert res.get("success"), res
        assert res["repo"] == "local/spaced-repro"
        # Explicit name still resolves it.
        res = index_file(str(doc), storage_path=store, name="local/spaced-repro")
        assert res.get("success"), res
        assert res["repo"] == "local/spaced-repro"


# --- #39: hook commands use an absolute (or quoted) executable path -----------

def test_hook_commands_built_from_resolved_executable():
    exe = _hook_invocation()
    hooks = _enforcement_hooks()
    cmds = [h["command"] for rules in hooks.values() for r in rules for h in r["hooks"]]
    assert {c.rsplit(" ", 1)[1] for c in cmds} == {
        "hook-pretooluse", "hook-posttooluse", "hook-precompact", "hook-sessionstart",
    }
    for c in cmds:
        assert c.startswith(exe)
    # No backslashes survive on any platform (bash eats them).
    assert all("\\" not in c for c in cmds)
