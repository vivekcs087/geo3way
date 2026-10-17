"""Line-based baselines: how a dataset looks to git, and a diff3 three-way text merge.

Three serialisations are provided, each with a per-line map to the owning feature id:
  geojsonseq   one GeoJSON feature per line (newline-delimited GeoJSON), sorted by id
  geojson      pretty-printed GeoJSON FeatureCollection (indent=2), sorted by id
  csvwkt       CSV with one row per feature and a WKT geometry column, sorted by id
"""
from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

from .diff import Dataset

FORMATS = ("geojsonseq", "geojson", "csvwkt")


# ----------------------------------------------------------------------------- serialise
def _gj_feature(fid: str, f: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "id": fid, "properties": f["props"],
            "geometry": {"type": f["geom"]["type"], "coordinates": f["geom"]["coords"]}}


def _wkt(g: dict[str, Any]) -> str:
    def pts(seq: list[list[float]]) -> str:
        return ", ".join(f"{p[0]!r} {p[1]!r}" for p in seq)
    t, c = g["type"], g["coords"]
    if t == "Point":
        return f"POINT ({c[0]!r} {c[1]!r})"
    if t == "LineString":
        return f"LINESTRING ({pts(c)})"
    return "POLYGON (" + ", ".join(f"({pts(r)})" for r in c) + ")"


def _parse_wkt(s: str) -> dict[str, Any]:
    t = s.split("(", 1)[0].strip()
    nums = lambda txt: [[float(v) for v in p.split()] for p in txt.split(",")]  # noqa: E731
    if t == "POINT":
        return {"type": "Point", "coords": nums(s[s.index("(") + 1: s.rindex(")")])[0]}
    if t == "LINESTRING":
        return {"type": "LineString", "coords": nums(s[s.index("(") + 1: s.rindex(")")])}
    if t == "POLYGON":
        rings = re.findall(r"\(([^()]*)\)", s)
        return {"type": "Polygon", "coords": [nums(r) for r in rings]}
    raise ValueError(f"bad WKT {s[:30]!r}")


def serialise(ds: Dataset, fmt: str, order: list[str] | None = None
              ) -> tuple[list[str], list[str | None]]:
    """Return (lines, owner) where owner[i] is the feature id that line i belongs to.
    Features are written sorted by id unless an explicit ``order`` is given."""
    ids = sorted(ds) if order is None else [i for i in order if i in ds]
    lines: list[str] = []
    owner: list[str | None] = []
    if fmt == "geojsonseq":
        for fid in ids:
            lines.append(json.dumps(_gj_feature(fid, ds[fid]), separators=(",", ":"),
                                    sort_keys=True))
            owner.append(fid)
    elif fmt == "geojson":
        lines += ['{', '  "type": "FeatureCollection",', '  "features": [']
        owner += [None, None, None]
        for n, fid in enumerate(ids):
            block = json.dumps(_gj_feature(fid, ds[fid]), indent=2, sort_keys=True).split("\n")
            block = ["    " + ln for ln in block]
            if n < len(ids) - 1:
                block[-1] += ","
            lines += block
            owner += [fid] * len(block)
        lines += ['  ]', '}']
        owner += [None, None]
    elif fmt == "csvwkt":
        keys = sorted({k for f in ds.values() for k in f["props"]})
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["id", *keys, "wkt"])
        owner.append(None)
        for fid in ids:
            w.writerow([fid, *[json.dumps(ds[fid]["props"].get(k)) for k in keys],
                        _wkt(ds[fid]["geom"])])
            owner.append(fid)
        lines = buf.getvalue().rstrip("\n").split("\n")
    else:
        raise ValueError(fmt)
    return lines, owner


def parse(lines: list[str], fmt: str) -> Dataset:
    """Inverse of :func:`serialise`; raises ValueError on malformed text."""
    try:
        if fmt == "geojsonseq":
            feats = [json.loads(ln) for ln in lines if ln.strip()]
        elif fmt == "geojson":
            feats = json.loads("\n".join(lines))["features"]
        elif fmt == "csvwkt":
            rows = list(csv.reader(io.StringIO("\n".join(lines))))
            head = rows[0]
            out: Dataset = {}
            for r in rows[1:]:
                if len(r) != len(head):
                    raise ValueError("ragged CSV row")
                props = {k: json.loads(v) for k, v in zip(head[1:-1], r[1:-1], strict=True)}
                props = {k: v for k, v in props.items() if v is not None}
                if r[0] in out:
                    raise ValueError("duplicate id")
                out[r[0]] = {"props": props, "geom": _parse_wkt(r[-1])}
            return out
        else:
            raise ValueError(fmt)
        out = {}
        for f in feats:
            if f["id"] in out:
                raise ValueError("duplicate id")
            out[f["id"]] = {"props": f["properties"],
                            "geom": {"type": f["geometry"]["type"],
                                     "coords": f["geometry"]["coordinates"]}}
        return out
    except (KeyError, TypeError, json.JSONDecodeError, IndexError) as e:
        raise ValueError(f"unparseable merge result: {e}") from e


# ----------------------------------------------------------------------------- git
_ID_RE = {"geojsonseq": re.compile(r'"id":"([0-9a-f]+)"'),
          "geojson": re.compile(r'^\s*"id": "([0-9a-f]+)",?$'),
          "csvwkt": re.compile(r"^([0-9a-f]{12}),")}


def _owners(lines: list[str], fmt: str) -> list[str | None]:
    """Feature id owning each line of a (possibly merged) text."""
    rx = _ID_RE[fmt]
    if fmt != "geojson":
        return [(m.group(1) if (m := rx.search(ln)) else None) for ln in lines]
    owner: list[str | None] = [None] * len(lines)
    starts = [i for i, ln in enumerate(lines) if ln == "    {"] + [len(lines)]
    for a, b in zip(starts, starts[1:]):
        fid = next((m.group(1) for ln in lines[a:b] if (m := rx.match(ln))), None)
        for i in range(a, b):
            owner[i] = fid
    return owner


def _run_git(args: list[str], files: dict[str, list[str]]) -> tuple[int, str]:
    import os
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        for name, ls in files.items():
            with open(os.path.join(d, name), "w") as fh:
                fh.write("\n".join(ls) + "\n")
        r = subprocess.run(["git", *args], cwd=d, capture_output=True, text=True)
        return r.returncode, r.stdout


def line_merge(base: Dataset, ours: Dataset, theirs: Dataset, fmt: str,
               theirs_order: list[str] | None = None) -> dict[str, Any]:
    """Merge the serialised datasets with real `git merge-file` (default merge style), resolve every
    conflict hunk to 'ours', parse the result, and report which features the hunks touch."""
    files = {"ours": serialise(ours, fmt)[0], "base": serialise(base, fmt)[0],
             "theirs": serialise(theirs, fmt, theirs_order)[0]}
    code, text = _run_git(["merge-file", "-p", "-L", "ours", "-L", "base",
                           "-L", "theirs", "ours", "base", "theirs"], files)
    resolved: list[str] = []
    blocks: list[tuple[int, int, list[str]]] = []   # (start, end) in resolved, all block lines
    state, cur_ours, cur_all = "text", [], []
    for ln in text.split("\n")[:-1]:
        if state == "text":
            if ln.startswith("<<<<<<< ours"):
                state, cur_ours, cur_all = "ours", [], []
            else:
                resolved.append(ln)
        elif state == "ours":
            if ln == "=======":
                state = "theirs"
            else:
                cur_ours.append(ln)
                cur_all.append(ln)
        else:
            if ln.startswith(">>>>>>> theirs"):
                blocks.append((len(resolved), len(resolved) + len(cur_ours), cur_all))
                resolved += cur_ours
                state = "text"
            else:
                cur_all.append(ln)
    own = _owners(resolved, fmt)
    rx = _ID_RE[fmt]
    conflicted: set[str] = set()
    for a, b, all_lines in blocks:
        ids = {m.group(1) for ln in all_lines if (m := rx.search(ln))}
        if not ids:
            near = [o for o in own[max(0, a - 1):b + 1] if o]
            ids = set(near[:1])
        conflicted |= ids
    try:
        merged: Dataset | None = parse(resolved, fmt)
    except ValueError:
        merged = None
    return {"n_conflicts": len(blocks), "git_exit": code, "conflicted_ids": conflicted,
            "merged": merged, "text": resolved}


def text_diff_bytes(old: Dataset, new: Dataset, fmt: str, order: list[str] | None = None,
                    new_order: list[str] | None = None) -> int:
    """Size in bytes of `git diff` (3 lines of context) between two serialisations."""
    lo = serialise(old, fmt, order)[0]
    ln = serialise(new, fmt, new_order)[0]
    _, out = _run_git(["diff", "--no-index", "--no-color", "-U3", "a", "b"], {"a": lo, "b": ln})
    return len(out.encode())


def text_diff_gzip_bytes(old: Dataset, new: Dataset, fmt: str, order: list[str] | None = None,
                         new_order: list[str] | None = None) -> int:
    """gzip-compressed (level 9) size of the same `git diff` output."""
    import gzip
    lo = serialise(old, fmt, order)[0]
    ln = serialise(new, fmt, new_order)[0]
    _, out = _run_git(["diff", "--no-index", "--no-color", "-U3", "a", "b"], {"a": lo, "b": ln})
    return len(gzip.compress(out.encode(), 9))
