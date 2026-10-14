"""Count and time the content-path checks behind a cold `search_sections`.

Reproduce (the corpus is this repository at a release tag):

    git archive --format=tar v1.145.3 | tar -x -C <corpus-dir>
    PYTHONPATH=src python benchmarks/content_paths/measure.py \\
        --corpus <corpus-dir> --label "jdocmunch-mcp v1.145.3" \\
        --baseline-src <a checkout of the base commit>/src

Writes `results.json` beside this file. Quote that file, not prose.

The first search in a process builds the posting index by reading every
section body through the index's content loader, and every read goes through
`DocStore._safe_content_path`. Each timed run is a FRESH subprocess that wraps
that method with a counter, runs one search and reports the call count, the
time spent inside the method and the whole call. ⚠ Return codes are recorded,
and a failed run is counted beside the median, never folded into it.
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
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).with_name("results.json")
QUERY = "install configuration"

_INDEX = """
import json, sys
from jdocmunch_mcp.tools.index_local import index_local
r = index_local(path=sys.argv[1], name="pathbench", use_ai_summaries=False,
                use_embeddings=False, storage_path=sys.argv[2], incremental=False)
print(json.dumps({"success": bool(r.get("success")), "repo": r.get("repo")}))
"""

_SEARCH = """
import json, sys, time
from jdocmunch_mcp.storage import doc_store
from jdocmunch_mcp.tools.search_sections import search_sections
calls = [0, 0.0]
orig = doc_store.DocStore._safe_content_path
def counted(self, *a, **k):
    t = time.perf_counter()
    try:
        return orig(self, *a, **k)
    finally:
        calls[0] += 1
        calls[1] += time.perf_counter() - t
doc_store.DocStore._safe_content_path = counted
t = time.perf_counter()
r = search_sections(repo=sys.argv[2], query=sys.argv[3], storage_path=sys.argv[1],
                    semantic=False)
ms = (time.perf_counter() - t) * 1000
print(json.dumps({"success": "results" in r, "ms": ms, "calls": calls[0],
                  "check_ms": calls[1] * 1000}))
"""


def _run(code: str, args: list, src: str = None) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "JDOCMUNCH_POSTING_CACHE"}
    if src:
        env["PYTHONPATH"] = src
    proc = subprocess.run([sys.executable, "-c", code, *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=env)
    out = {"rc": proc.returncode}
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    if proc.returncode == 0 and lines:
        out.update(json.loads(lines[-1]))
    else:
        out["stderr_tail"] = proc.stderr.strip().splitlines()[-1:] or [""]
    return out


def _searches(store: Path, repo: str, runs: int, src: str = None) -> dict:
    results = [_run(_SEARCH, [str(store), repo, QUERY], src) for _ in range(runs)]
    ok = [r for r in results if r["rc"] == 0 and r.get("success")]

    def med(key):
        return round(statistics.median(r[key] for r in ok), 1) if ok else None

    return {
        "runs": len(results),
        "failed": len(results) - len(ok),
        "median_ms": med("ms"),
        "median_check_ms": med("check_ms"),
        "checks": sorted({r["calls"] for r in ok}),
    }


def measure(corpus: Path, label: str, runs: int, baseline_src: str = None) -> dict:
    work = Path(tempfile.mkdtemp(prefix="pathbench-"))
    try:
        store = work / "store"
        full = _run(_INDEX, [str(corpus), str(store)])
        if full["rc"] != 0 or not full.get("success"):
            raise SystemExit(f"full index failed: {full}")
        repo = full["repo"]
        monolith = next(h for h in store.glob("*/pathbench*.json") if h.name.count(".") == 1)
        sections = len(json.loads(monolith.read_text(encoding="utf-8")).get("sections") or [])
        return {
            "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "corpus": label,
            "sections": sections,
            "query": QUERY,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cold_search": _searches(store, repo, runs),
            "cold_search_baseline": (
                _searches(store, repo, runs, baseline_src) if baseline_src else None
            ),
            "baseline_src_note": (
                "the same searches against the index above, importing the base "
                "commit's package" if baseline_src else None
            ),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--label", required=True)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--baseline-src", help="a base commit's src/ to time the same searches with")
    args = ap.parse_args()
    result = measure(args.corpus, args.label, args.runs, args.baseline_src)
    # newline="\n": the repository is LF, and text mode on Windows writes CRLF.
    OUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
