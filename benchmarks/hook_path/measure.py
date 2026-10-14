"""Time the `hook-pretooluse` process and the imports behind it.

Reproduce (the corpus is this repository at a release tag, and the baseline is
a worktree of the commit this change was built on):

    git archive --format=tar v1.145.3 | tar -x -C <corpus-dir>
    git worktree add --detach <base-dir> 91e24a8
    PYTHONPATH=src python benchmarks/hook_path/measure.py \\
        --corpus <corpus-dir> --label "jdocmunch-mcp v1.145.3" \\
        --baseline-src <base-dir>/src

Writes `results.json` beside this file. Quote that file, not prose.

⚠ PROVENANCE. A timing that does not say which tree it timed is two numbers.
`results.json` records, for the checkout measured and for the baseline: the
commit SHA, whether the tree had uncommitted changes (`"dirty"`, ignoring this
benchmark's own `results.json`), the SHA of its `src/` tree, and the package
version in its `pyproject.toml`. The `src/` tree SHA is what ties a record to a
later commit: a commit that changes only docs or this file leaves
`git rev-parse <commit>:src` equal to the recorded value. `--baseline-sha`
states the baseline's SHA when it is not a git worktree; when it is one, a
mismatch with what git reports is an error.

Claude Code fires `jdocmunch-mcp hook-pretooluse` as a fresh process on every
`Read`. Every timed run here is therefore a FRESH subprocess, and the arms are
INTERLEAVED round by round so a drifting machine moves them together instead of
favouring whichever ran first. ⚠ Return codes are recorded and a failed run is
counted beside the median, never folded into it.

Arms (`hook_*` run `python -m jdocmunch_mcp hook-pretooluse` against a real
index of the corpus, reading a real indexed file; stdin is the Read payload):

    interpreter            `python -c pass`, the floor every other arm sits on
    hook_before            the base commit's package (`--baseline-src`)
    hook_after             this checkout's package, file unchanged since indexing
    hook_after_touched     this checkout's package, file touched but byte-identical,
                           so the staleness gate compares content
    import_server_before   `import jdocmunch_mcp.server` on the base commit
    import_server_after    `import jdocmunch_mcp.server` on this checkout
    import_cli_main_after  `import jdocmunch_mcp.cli.main` on this checkout
    integrity_check_after  one `verify_package_integrity()` call, in-process

The `-m` entry point is what the PostToolUse spawn fallback and the watch
service run, and on the base commit it imports `.server` exactly as the old
console script did, so `hook_before` is the pre-change cost of the same hook.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).with_name("results.json")

_INDEX = """
import json, sys
from jdocmunch_mcp.tools.index_local import index_local
r = index_local(path=sys.argv[1], name="hookbench", use_ai_summaries=False,
                use_embeddings=False, storage_path=sys.argv[2], incremental=False)
print(json.dumps({"success": bool(r.get("success"))}))
"""

_IMPORT = """
import json, sys, time
t = time.perf_counter()
__import__(sys.argv[1])
print(json.dumps({"ms": (time.perf_counter() - t) * 1000}))
"""

_INTEGRITY = """
import json, time
from jdocmunch_mcp.security import verify_package_integrity
t = time.perf_counter()
verify_package_integrity()
print(json.dumps({"ms": (time.perf_counter() - t) * 1000}))
"""


def _git(root: Path, *args: str) -> str | None:
    """One git query against `root`; None when git or the repository is absent."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", env=env)
    except OSError:
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _pyproject_version(root: Path) -> str | None:
    try:
        for line in (root / "pyproject.toml").read_text(encoding="utf-8").splitlines():
            if line.startswith("version"):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return None


def _provenance(src: str, declared_sha: str | None = None) -> dict:
    """Which tree this is: SHA, dirty or clean, `src/` tree SHA, version.

    `src` is a tree's `src/` directory; the repository root is its parent.
    """
    root = Path(src).resolve().parent
    sha = _git(root, "rev-parse", "HEAD")
    if declared_sha and sha and not sha.startswith(declared_sha) \
            and not declared_sha.startswith(sha):
        raise SystemExit(f"--baseline-sha {declared_sha} does not match the tree "
                         f"at {root}, which is at {sha}")
    sha = sha or declared_sha
    if not sha:
        raise SystemExit(f"cannot establish the commit of {root}: not a git "
                         f"worktree, and no SHA was declared")
    status = _git(root, "status", "--porcelain", "--", ".",
                  ":(exclude)benchmarks/hook_path/results.json")
    return {
        "sha": sha,
        "state": "dirty" if status else "clean",
        "src_tree": _git(root, "rev-parse", "HEAD:src") if status is not None else None,
        "version": _pyproject_version(root),
        "version_source": "pyproject.toml",
    }


def _env(src: str | None, store: str | None) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "DOC_INDEX_PATH", "JDOCMUNCH_HOOK_LOG")}
    if src:
        env["PYTHONPATH"] = src
    if store:
        env["DOC_INDEX_PATH"] = store
    return env


def _timed(argv: list, env: dict, stdin: str = "") -> dict:
    """Wall time of one fresh process, its return code and its stdout size."""
    t = time.perf_counter()
    proc = subprocess.run(argv, input=stdin, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)
    ms = (time.perf_counter() - t) * 1000
    out = {"rc": proc.returncode, "ms": ms, "stdout_bytes": len(proc.stdout.encode("utf-8"))}
    if proc.returncode != 0:
        out["stderr_tail"] = proc.stderr.strip().splitlines()[-1:] or [""]
    return out


def _inproc(code: str, args: list, env: dict) -> dict:
    """A run that times itself and prints one JSON line (import cost, in-process)."""
    proc = subprocess.run([sys.executable, "-c", code, *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=env)
    out = {"rc": proc.returncode}
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    if proc.returncode == 0 and lines:
        out.update(json.loads(lines[-1]))
    else:
        out["stderr_tail"] = proc.stderr.strip().splitlines()[-1:] or [""]
    return out


def _summary(results: list) -> dict:
    ok = [r for r in results if r["rc"] == 0 and "ms" in r]
    return {
        "runs": len(results),
        "failed": len(results) - len(ok),
        "median_ms": round(statistics.median(r["ms"] for r in ok), 1) if ok else None,
        "min_ms": round(min(r["ms"] for r in ok), 1) if ok else None,
        "max_ms": round(max(r["ms"] for r in ok), 1) if ok else None,
        "stdout_bytes": sorted({r["stdout_bytes"] for r in ok if "stdout_bytes" in r}),
    }


def measure(corpus: Path, label: str, runs: int, baseline_src: str,
            baseline_sha: str | None = None) -> dict:
    here_src = str(Path(__file__).resolve().parents[2] / "src")
    checkout = _provenance(here_src)
    baseline = _provenance(baseline_src, baseline_sha)
    if checkout["state"] == "dirty":
        print("WARNING: the checkout has uncommitted changes; the record says "
              "\"dirty\" and its SHA does not describe what was measured.",
              file=sys.stderr)
    work = Path(tempfile.mkdtemp(prefix="hookbench-"))
    try:
        # Index a COPY: the touched arm changes a source file's mtime.
        tree, store = work / "tree", work / "store"
        shutil.copytree(corpus, tree)
        built = _inproc(_INDEX, [str(tree), str(store)], _env(here_src, None))
        if built["rc"] != 0 or not built.get("success"):
            raise SystemExit(f"index failed: {built}")
        target = tree / "CHANGELOG.md"
        payload = json.dumps({"tool_input": {"file_path": str(target)}})

        hook = [sys.executable, "-m", "jdocmunch_mcp", "hook-pretooluse"]
        before_env = _env(baseline_src, str(store))
        after_env = _env(here_src, str(store))
        plain = [sys.executable, "-c", "pass"]

        raw: dict = {k: [] for k in (
            "interpreter", "hook_before", "hook_after", "hook_after_touched",
            "import_server_before", "import_server_after",
            "import_cli_main_after", "integrity_check_after")}
        stat = target.stat()
        for _ in range(runs):
            raw["interpreter"].append(_timed(plain, _env(None, None)))
            os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            raw["hook_before"].append(_timed(hook, before_env, payload))
            raw["hook_after"].append(_timed(hook, after_env, payload))
            future = time.time() + 3600
            os.utime(target, (future, future))
            raw["hook_after_touched"].append(_timed(hook, after_env, payload))
            os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            raw["import_server_before"].append(
                _inproc(_IMPORT, ["jdocmunch_mcp.server"], _env(baseline_src, None)))
            raw["import_server_after"].append(
                _inproc(_IMPORT, ["jdocmunch_mcp.server"], _env(here_src, None)))
            raw["import_cli_main_after"].append(
                _inproc(_IMPORT, ["jdocmunch_mcp.cli.main"], _env(here_src, None)))
            raw["integrity_check_after"].append(
                _inproc(_INTEGRITY, [], _env(here_src, None)))

        sections = None
        monolith = next((h for h in store.glob("*/hookbench*.json")
                         if h.name.count(".") == 1), None)
        if monolith:
            sections = len(json.loads(monolith.read_text(encoding="utf-8")).get("sections") or [])
        return {
            "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "checkout": checkout,
            "baseline": baseline,
            "corpus": label,
            "sections": sections,
            "target_file": "CHANGELOG.md",
            "target_bytes": stat.st_size,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "runs_per_arm": runs,
            "arms": {name: _summary(rs) for name, rs in raw.items()},
            "note": (
                "Each run is a fresh process; arms are interleaved per round. "
                "hook_* are wall time of `python -m jdocmunch_mcp hook-pretooluse` "
                "including interpreter start; import_* and integrity_check_* are "
                "in-process times inside a fresh interpreter."
            ),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--label", required=True)
    ap.add_argument("--runs", type=int, default=15)
    ap.add_argument("--baseline-src", required=True,
                    help="a base commit's src/, timed against this checkout's")
    ap.add_argument("--baseline-sha",
                    help="the baseline's commit SHA, if --baseline-src is not a "
                         "git worktree (checked against git when it is one)")
    args = ap.parse_args()
    result = measure(args.corpus, args.label, args.runs, args.baseline_src,
                     args.baseline_sha)
    # newline="\n": the repository is LF, and text mode on Windows writes CRLF.
    OUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
