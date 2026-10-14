"""The hook-path benchmark must record WHICH trees it timed.

`benchmarks/hook_path/results.json` is quoted in the CHANGELOG. A figure that
does not name the commit it was measured at, or the baseline it was measured
against, is two numbers. These tests pin the provenance helper on throwaway git
repositories; they do not run the benchmark.
"""

import importlib.util
import subprocess
from pathlib import Path

import pytest

MEASURE = Path(__file__).resolve().parents[1] / "benchmarks" / "hook_path" / "measure.py"


@pytest.fixture(scope="module")
def measure():
    spec = importlib.util.spec_from_file_location("hook_path_measure", MEASURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "core.autocrlf=false", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "9.8.7"\n', encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_a_clean_tree_records_sha_src_tree_and_version(measure, repo):
    got = measure._provenance(str(repo / "src"))
    assert got["sha"] == _git(repo, "rev-parse", "HEAD")
    assert got["state"] == "clean"
    assert got["src_tree"] == _git(repo, "rev-parse", "HEAD:src")
    assert got["version"] == "9.8.7"
    assert got["version_source"] == "pyproject.toml"


def test_uncommitted_changes_are_recorded_as_dirty(measure, repo):
    (repo / "src" / "mod.py").write_text("x = 2\n", encoding="utf-8")
    got = measure._provenance(str(repo / "src"))
    assert got["state"] == "dirty"
    assert got["sha"] == _git(repo, "rev-parse", "HEAD")


def test_the_benchmarks_own_results_file_does_not_make_it_dirty(measure, repo):
    out = repo / "benchmarks" / "hook_path"
    out.mkdir(parents=True)
    (out / "results.json").write_text("{}\n", encoding="utf-8")
    assert measure._provenance(str(repo / "src"))["state"] == "clean"


def test_a_declared_sha_that_disagrees_with_git_is_an_error(measure, repo):
    with pytest.raises(SystemExit):
        measure._provenance(str(repo / "src"), "0" * 40)


def test_a_declared_sha_prefix_that_agrees_is_accepted(measure, repo):
    head = _git(repo, "rev-parse", "HEAD")
    assert measure._provenance(str(repo / "src"), head[:7])["sha"] == head


def test_a_tree_that_is_not_a_worktree_needs_a_declared_sha(measure, tmp_path):
    (tmp_path / "src").mkdir()
    with pytest.raises(SystemExit):
        measure._provenance(str(tmp_path / "src"))


def test_the_src_tree_sha_survives_a_commit_that_only_touches_docs(measure, repo):
    """The property the record relies on: results.json and the CHANGELOG are
    committed AFTER the measurement, and must not invalidate it."""
    before = measure._provenance(str(repo / "src"))
    (repo / "CHANGELOG.md").write_text("notes\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "docs only")
    after = measure._provenance(str(repo / "src"))
    assert after["sha"] != before["sha"]
    assert after["src_tree"] == before["src_tree"]
