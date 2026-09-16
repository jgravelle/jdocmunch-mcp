"""Measure what the posting-row sidecar changes: a cold first search and an
incremental save, with and without it.

Reproduce (the corpus is this repository at a release tag, so anyone can
rebuild it byte for byte):

    git archive --format=tar v1.145.3 | tar -x -C <corpus-dir>
    PYTHONPATH=src python benchmarks/posting_rows/measure.py \\
        --corpus <corpus-dir> --label "jdocmunch-mcp v1.145.3"

Writes `results.json` beside this file. Quote that file, never a figure typed
into prose; the two diverge silently.

⚠⚠ Every timed run is a FRESH subprocess, and its return code and the tool's
own `success` flag are recorded. A run that failed is counted beside the
median and never folded into it: a crash is the fastest possible
implementation (CLAUDE.md, "A harness without an exit-code check is not a
harness").

What is timed is the tool call itself, inside the subprocess, after imports:

* `cold_search`: the first `search_sections` call in a new process. With the
  sidecar the posting index is read from disk; with
  `JDOCMUNCH_POSTING_CACHE=0` it is rebuilt by re-reading every body. The
  posting index's own `source` field is recorded, so a run that silently fell
  back to a rebuild cannot pass for a sidecar hit.
* `incremental_save`: one `index_local` call after appending a line to one
  document. Both regimes recompute the statistics exactly: `rows` (cache on:
  the kept sections' terms come from the stored rows) and `recompute`
  (`JDOCMUNCH_POSTING_CACHE=0`: every body is read and tokenized again).
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
NAME = "postingbench"

_INDEX = """
import json, sys, time
from jdocmunch_mcp.tools.index_local import index_local
corpus, store, incremental = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
t = time.perf_counter()
r = index_local(path=corpus, name=%(name)r, use_ai_summaries=False,
                use_embeddings=False, storage_path=store, incremental=incremental)
ms = (time.perf_counter() - t) * 1000
print(json.dumps({"ms": ms, "success": bool(r.get("success")), "repo": r.get("repo")}))
""" % {"name": NAME}

_SEARCH = """
import json, sys, time
from jdocmunch_mcp.tools.search_sections import search_sections
from jdocmunch_mcp.storage.doc_store import DocStore
store, repo, query = sys.argv[1], sys.argv[2], sys.argv[3]
t = time.perf_counter()
r = search_sections(repo=repo, query=query, storage_path=store, semantic=False)
ms = (time.perf_counter() - t) * 1000
owner, name = repo.split("/", 1)
idx = DocStore(base_path=store).load_index(owner, name)
posting = getattr(idx, "_posting_index", None)
print(json.dumps({"ms": ms, "success": "results" in r,
                  "source": getattr(posting, "source", None)}))
"""


def _run(code: str, args: list, env_extra: dict) -> dict:
    env = dict(os.environ)
    env.pop("JDOCMUNCH_POSTING_CACHE", None)
    env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, "-c", code, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    out = {"rc": proc.returncode}
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    if proc.returncode == 0 and lines:
        out.update(json.loads(lines[-1]))
    else:
        out["stderr_tail"] = proc.stderr.strip().splitlines()[-1:] or [""]
    return out


def _summary(runs: list, extra_key: str = None) -> dict:
    ok = [r for r in runs if r["rc"] == 0 and r.get("success")]
    times = [r["ms"] for r in ok]
    out = {
        "runs": len(runs),
        "failed": len(runs) - len(ok),
        "median_ms": round(statistics.median(times), 1) if times else None,
        "min_ms": round(min(times), 1) if times else None,
        "max_ms": round(max(times), 1) if times else None,
    }
    if extra_key:
        out[extra_key] = sorted({str(r.get(extra_key)) for r in ok})
    return out


def _index_json(store: Path) -> dict:
    hits = sorted(store.glob(f"*/{NAME}*.json"))
    hits = [h for h in hits if h.name.count(".") == 1]
    return json.loads(hits[0].read_text(encoding="utf-8")) if hits else {}


def _encodings(store: Path, repo: str) -> dict:
    """What the same postings would weigh written the other two ways.

    The sidecar stores FORWARD rows: each section id once, each term once (in
    the vocabulary). The alternatives are the inverted postings, with section
    ids written out in full or replaced by integers. All three are compact
    JSON, so the comparison is between encodings, not formatters.
    """
    from jdocmunch_mcp.retrieval import posting_cache
    from jdocmunch_mcp.storage.doc_store import DocStore

    owner, name = repo.split("/", 1)
    index = DocStore(base_path=str(store)).load_index(owner, name)
    rows = posting_cache.load(str(store), owner, name)
    if index is None or rows is None:
        return {}
    postings = rows.posting_index(index.sections).postings
    number = {s["id"]: i for i, s in enumerate(index.sections)}
    compact = (",", ":")
    full = json.dumps({t: sorted(ids) for t, ids in postings.items()}, separators=compact)
    ints = json.dumps({t: sorted(number[s] for s in ids) for t, ids in postings.items()},
                      separators=compact)
    return {
        "terms": len(postings),
        "posting_entries": sum(len(ids) for ids in postings.values()),
        "postings_full_id_bytes": len(full.encode("utf-8")),
        "postings_integer_id_bytes": len(ints.encode("utf-8")),
    }


def measure(corpus: Path, label: str, runs: int, edit_target: str) -> dict:
    work = Path(tempfile.mkdtemp(prefix="postingbench-"))
    try:
        src = work / "corpus"
        shutil.copytree(corpus, src)
        store = work / "store"
        target = src / edit_target

        full = _run(_INDEX, [str(src), str(store), "0"], {})
        if full["rc"] != 0 or not full.get("success"):
            raise SystemExit(f"full index failed: {full}")
        repo = full["repo"]
        doc = _index_json(store)
        sections = len(doc.get("sections") or [])
        sidecars = sorted(store.glob(f"*/{NAME}*.postings.jsonl"))
        monoliths = [h for h in store.glob(f"*/{NAME}*.json") if h.name.count(".") == 1]
        sizes = {
            "index_monolith_bytes": monoliths[0].stat().st_size if monoliths else None,
            "posting_sidecar_bytes": sidecars[0].stat().st_size if sidecars else None,
            **_encodings(store, repo),
        }

        cold = {}
        for mode, env in (("sidecar", {}), ("no_sidecar", {"JDOCMUNCH_POSTING_CACHE": "0"})):
            cold[mode] = _summary(
                [_run(_SEARCH, [str(store), repo, QUERY], env) for _ in range(runs)],
                "source",
            )

        incremental = {}
        regimes = (
            ("recompute", {"JDOCMUNCH_POSTING_CACHE": "0"}),
            ("rows", {}),
        )
        for mode, env in regimes:
            results = []
            for i in range(runs):
                with target.open("a", encoding="utf-8") as fh:
                    fh.write(f"\nbenchmark edit {mode} {i}\n")
                r = _run(_INDEX, [str(src), str(store), "1"], env)
                results.append(r)
            incremental[mode] = _summary(results)

        return {
            "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "corpus": label,
            "sections": sections,
            "edit_target": edit_target,
            "query": QUERY,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "sizes": sizes,
            "cold_search": cold,
            "incremental_save": incremental,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--label", required=True, help="what the corpus is, e.g. a tag")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--edit-target", default="README.md",
                    help="document, relative to the corpus, that each save edits")
    args = ap.parse_args()
    result = measure(args.corpus, args.label, args.runs, args.edit_target)
    # newline="\n": the repository is LF, and text mode on Windows writes CRLF.
    OUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
