"""`init` offers offline embeddings once, default No, and never installs unasked.

The offer is the one place jdocmunch runs pip or starts a download for the user,
so most of this file is about the paths where it must NOT.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from jdocmunch_mcp.cli import embeddings_offer as eo
from jdocmunch_mcp.cli import init as init_mod

REPO = Path(__file__).resolve().parent.parent


class Recorder:
    """Stand-in for subprocess.run. Records every command; scripted results."""

    def __init__(self, pip_probe_rc=0, install_rc=0, raises=None):
        self.calls, self.pip_probe_rc, self.install_rc, self.raises = [], pip_probe_rc, install_rc, raises

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        if self.raises:
            raise self.raises
        rc = self.pip_probe_rc if cmd[-1] == "--version" else self.install_rc
        return SimpleNamespace(returncode=rc, stdout="", stderr="ERROR: no matching distribution")


@pytest.fixture
def no_provider(monkeypatch):
    # Pinned: "auto" would read this machine's site-packages, and CI installs
    # neither offline provider while a dev box often has one.
    monkeypatch.setattr(eo, "_resolved_provider", lambda: None)


@pytest.fixture
def downloads(monkeypatch):
    seen = []
    monkeypatch.setattr(eo, "_download_model", lambda: (seen.append(1) or (True, "")))
    return seen


def run_offer(**kw):
    out, asked = [], []
    kw.setdefault("interactive", False)
    kw.setdefault("with_embeddings", False)
    kw.setdefault("dry_run", False)
    answer = kw.pop("answer", False)

    def prompt(message, default):
        asked.append((message, default))
        return answer

    result = eo.offer_embeddings(prompt_yn=prompt, out=out.append, **kw)
    return result, "\n".join(out), asked


# --- the figures shown to the user ------------------------------------------

def test_quoted_figures_are_pinned():
    # RESTATED from jdoc-rerank-bench results/baseline-gap-2026-09-19.md and
    # results/fastembed-setup-2026-09-19.md. Changing a constant without a new
    # measurement must fail here.
    assert (eo.BENCH_TOP5_LEXICAL_PCT, eo.BENCH_TOP5_HYBRID_PCT) == (34, 47)
    assert (eo.BENCH_QUESTIONS, eo.BENCH_DOC_SETS) == (492, 6)
    assert (eo.FASTEMBED_INSTALL_MB, eo.MODEL_DOWNLOAD_MB) == (160, 87)
    assert eo.BENCH_URL == "https://github.com/jgravelle/jdoc-rerank-bench"


def test_prompt_discloses_what_is_installed_and_from_where():
    text = eo.offer_text()
    for needle in ("34%", "47%", "492 questions", "fastembed", "160 MB", "87 MB",
                   "huggingface.co", "No text from your documents is sent anywhere",
                   "github.com/jgravelle/jdoc-rerank-bench"):
        assert needle in text, needle


def test_readme_discloses_the_same_behaviour_and_numbers():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    section = readme.split("### Background behavior, fully disclosed", 1)[1]
    for needle in ("only if you say yes to `init`", "The default answer is\nno", "fastembed>=0.8.0",
                   "87 MB", "160 MB", "huggingface.co", "--with-embeddings", "`--yes` does **not**",
                   "47%", "34%", "jdoc-rerank-bench"):
        assert needle in section, needle


# --- paths that must not install anything -----------------------------------

def test_existing_provider_means_no_prompt_and_no_pip(monkeypatch):
    monkeypatch.setattr(eo, "_resolved_provider", lambda: "sentence-transformers")
    run = Recorder()
    ok, out, asked = run_offer(interactive=True, run=run)
    assert ok is True and not asked and not run.calls
    assert "Semantic search: on (sentence-transformers)" in out


def test_default_answer_is_no(no_provider, downloads):
    run = Recorder()
    ok, out, asked = run_offer(interactive=True, answer=False, run=run)
    assert asked and asked[0][1] is False          # the prompt's default is No
    assert ok is False and not run.calls and not downloads
    assert 'pip install "jdocmunch-mcp[fastembed]"' in out and "pipx inject" in out


def test_non_interactive_without_the_flag_installs_nothing(no_provider, downloads):
    run = Recorder()
    ok, out, asked = run_offer(interactive=False, run=run)
    assert ok is False and not asked and not run.calls and not downloads


def test_dry_run_prints_sizes_and_touches_nothing(no_provider, downloads):
    run = Recorder()
    ok, out, asked = run_offer(interactive=True, with_embeddings=True, dry_run=True, run=run)
    assert ok is False and not asked and not run.calls and not downloads
    assert "160 MB" in out and "87 MB" in out and "huggingface.co" in out


def test_yes_flag_does_not_opt_in(no_provider, downloads, monkeypatch, tmp_path):
    """--yes accepts config edits. It must not reach pip."""
    run = Recorder()
    monkeypatch.setattr(eo.subprocess, "run", run)
    monkeypatch.setattr(init_mod, "_detect_clients", lambda: [])
    monkeypatch.setattr(init_mod, "install_claude_md", lambda *a, **k: " skipped in test")
    monkeypatch.setattr(init_mod, "install_hooks", lambda *a, **k: " skipped in test")
    indexed = {}
    monkeypatch.setattr(init_mod, "run_index", lambda **k: indexed.update(k) or " ok")
    monkeypatch.chdir(tmp_path)
    assert init_mod.run_init(yes=True, index=True, clients=["none"]) == 0
    assert not run.calls and not downloads
    assert indexed["use_embeddings"] is False


# --- the opt-in path ---------------------------------------------------------

def test_with_embeddings_runs_exactly_the_printed_command(no_provider, downloads):
    run = Recorder()
    ok, out, asked = run_offer(with_embeddings=True, run=run)
    assert ok is True and not asked and downloads
    assert run.calls[-1] == eo.pip_command()
    assert run.calls[-1][1:] == ["-m", "pip", "install", "fastembed>=0.8.0"]
    assert " ".join(eo.pip_command()) in out       # printed before it ran
    assert out.index("Running:") < out.index("Downloading")


def test_interactive_yes_installs(no_provider, downloads):
    run = Recorder()
    ok, _, asked = run_offer(interactive=True, answer=True, run=run)
    assert ok is True and asked and run.calls[-1] == eo.pip_command()


@pytest.mark.parametrize("run", [
    Recorder(pip_probe_rc=1),                     # uv tool / pipx: no pip at all
    Recorder(install_rc=1),                       # pip ran and failed
    Recorder(raises=PermissionError("locked")),   # environment refuses the spawn
])
def test_install_failure_falls_back_to_lexical_and_never_raises(no_provider, downloads, run):
    ok, out, _ = run_offer(with_embeddings=True, run=run)
    assert ok is False and not downloads
    assert "Continuing with word matching only" in out
    assert "uv tool install" in out and "pipx inject" in out


def test_download_failure_indexes_without_embeddings(no_provider, monkeypatch):
    monkeypatch.setattr(eo, "_download_model", lambda: (False, "connection reset"))
    ok, out, _ = run_offer(with_embeddings=True, run=Recorder())
    # False, not "auto": fastembed is installed now, so auto would retry the
    # download inside the index step, silently.
    assert ok is False and "connection reset" in out


def test_pip_is_invoked_from_one_module_only():
    """Ratchet: nothing else under src/ may run pip."""
    offenders = [p.relative_to(REPO).as_posix() for p in (REPO / "src").rglob("*.py")
                 if '"pip"' in p.read_text(encoding="utf-8") and p.name != "embeddings_offer.py"]
    assert offenders == []


# --- surfaces that tell an existing user their index is words-only ----------

def _index(tmp_path, monkeypatch):
    monkeypatch.setenv("JDOCMUNCH_EMBEDDING_PROVIDER", "none")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# Alpha\n\nRefresh tokens expire after an hour.\n", encoding="utf-8")
    from jdocmunch_mcp.tools.index_local import index_local
    store = str(tmp_path / "store")
    assert index_local(path=str(docs), name="offer", storage_path=store, use_ai_summaries=False,
                       use_embeddings=False).get("success")
    return store


def test_list_repos_reports_embedding_status(tmp_path, monkeypatch):
    from jdocmunch_mcp.embeddings.cache import _cache_path
    from jdocmunch_mcp.tools.list_repos import list_repos
    store = _index(tmp_path, monkeypatch)
    out = list_repos(storage_path=store)
    row = out["repos"][0]
    assert row["has_embeddings"] is False
    assert "1 of 1 indexes have no embeddings" in out["_meta"]["embeddings_tip"]
    sidecar = _cache_path(store, row["owner"], row["name"])
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text("{}\n", encoding="utf-8")
    out = list_repos(storage_path=store)
    assert out["repos"][0]["has_embeddings"] is True and "embeddings_tip" not in out["_meta"]


def test_lexical_search_tip_names_the_missing_piece(tmp_path, monkeypatch):
    from jdocmunch_mcp.tools.search_sections import search_sections
    store = _index(tmp_path, monkeypatch)
    tip = search_sections(repo="local/offer", query="refresh tokens", storage_path=store)["_meta"]["tip"]
    assert "jdocmunch-mcp[fastembed]" in tip and "use_embeddings=True" in tip
