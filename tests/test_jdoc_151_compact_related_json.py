"""jdoc#151: related.json was written with indent=2.

Every reader parses it with json.loads, so the indentation served nobody. On a
36,000-section graph it cost 2.37 s against 0.33 s to write, and 76.9 MB
against 41.8 MB on disk.
"""

from __future__ import annotations

import json

from jdocmunch_mcp.retrieval import related_persist as rp


def _sections():
    out = []
    for d in range(4):
        root = f"local/r::d{d}.md::root#1"
        out.append({"id": root, "doc_path": f"d{d}.md", "title": f"Doc {d}",
                    "level": 1, "parent_id": ""})
        for j in range(3):
            out.append({"id": f"local/r::d{d}.md::s{j}#2", "doc_path": f"d{d}.md",
                        "title": f"S{j}", "level": 2, "parent_id": root})
    return out


def test_related_json_is_written_compact(tmp_path):
    rp.write(str(tmp_path), "local", "r", _sections())
    text = rp._path(str(tmp_path), "local", "r").read_text(encoding="utf-8")
    assert "\n" not in text.strip()
    assert ", " not in text and ": " not in text.replace('": "', "")


def test_compact_file_round_trips(tmp_path):
    secs = _sections()
    rp.write(str(tmp_path), "local", "r", secs)
    data = rp.load(str(tmp_path), "local", "r")
    expected = rp.build(secs)
    data.pop("captured_at")
    expected.pop("captured_at")
    assert data == expected


def test_an_indented_file_from_an_older_version_still_loads(tmp_path):
    data = rp.build(_sections())
    path = rp._path(str(tmp_path), "local", "r")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    assert rp.load(str(tmp_path), "local", "r")["section_count"] == data["section_count"]
    sid = next(iter(data["by_section"]))
    assert rp.lookup(str(tmp_path), "local", "r", sid) == data["by_section"][sid]
