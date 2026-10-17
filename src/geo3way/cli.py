"""Command-line interface.

    geo3way merge base.geojson ours.geojson theirs.geojson -o merged.geojson \\
            --report conflicts.geojson
    geo3way diff before.gpkg after.gpkg
    geo3way validate parcels.geojson
    geo3way add-ids parcels.geojson
    geo3way install-git            # use geo3way for `git merge` / `git diff` of *.geojson

Exit status: 0 = clean / no differences, 1 = conflicts / differences / invalid geometry,
2 = error (unreadable input, bad arguments).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from . import __version__
from .diff import Dataset, diff
from .geometry import validity_errors
from .io import FormatError, Meta, add_ids, detect_format, load, looks_geographic, save
from .merge import Conflict, MergeResult, merge

# Tolerances used when coordinates look like longitude/latitude degrees instead of metres:
# 1e-8 degrees is about 1 mm and 1e-5 degrees about 1 m at the equator.
GEOGRAPHIC_EPS, GEOGRAPHIC_DUP = 1e-8, 1e-5
PROJECTED_EPS, PROJECTED_DUP = 1e-3, 1.0


# --------------------------------------------------------------------------- file-level API
@dataclass
class FileMerge:
    result: MergeResult
    base: Dataset
    ours: Dataset
    theirs: Dataset
    metas: tuple[Meta, Meta, Meta]
    eps: float
    duplicate_tol: float | None
    notes: list[str]


def merge_files(base: str, ours: str, theirs: str, out: str | None = None,
                report: str | None = None, *, prefer: str = "ours", eps: float | None = None,
                duplicate_tol: float | None = None, check_validity: bool = True,
                check_duplicates: bool = True, check_overlaps: bool = True,
                id_field: str | None = None, layer: str | None = None,
                fmt: str | None = None) -> FileMerge:
    """Merge three files and optionally write the merged file and a conflict report.

    Conflicted features take the ``prefer`` side's version ("ours" or "theirs"). Tolerances
    default to 1 mm / 1 m, or their degree equivalents when the data look geographic."""
    if prefer not in ("ours", "theirs"):
        raise ValueError("prefer must be 'ours' or 'theirs'")
    (b, mb), (o, mo), (t, mt) = (load(p, id_field, layer, fmt) for p in (base, ours, theirs))
    notes = []
    geographic = all(looks_geographic(d) for d in (b, o, t) if d)
    if eps is None:
        eps = GEOGRAPHIC_EPS if geographic else PROJECTED_EPS
    if duplicate_tol is None:
        duplicate_tol = GEOGRAPHIC_DUP if geographic else PROJECTED_DUP
    if geographic:
        notes.append(f"coordinates look like degrees: eps={eps:g}, duplicate tolerance="
                     f"{duplicate_tol:g} (override with --eps / --duplicate-tol)")
    first, second = (o, t) if prefer == "ours" else (t, o)
    res = merge(b, first, second, eps=eps, check_validity=check_validity,
                duplicate_tol=duplicate_tol if check_duplicates else None,
                check_overlaps=check_overlaps)
    fm = FileMerge(res, b, o, t, (mb, mo, mt), eps, duplicate_tol if check_duplicates else None,
                   notes)
    if out:
        like, other = (mo, mt) if prefer == "ours" else (mt, mo)
        save(res.merged, out, like, other, mb)
    if report:
        write_report(fm, report)
    return fm


def _value(ds: Dataset, fid: str, key: str | None) -> Any:
    f = ds.get(fid)
    if f is None:
        return "<deleted>" if key is None else None
    if key is None:
        return f["geom"]["type"]
    return f["props"].get(key, "<missing>")


def conflict_rows(fm: FileMerge) -> list[dict[str, Any]]:
    """One row per (conflict, feature id), with the three versions of the disputed value."""
    rows = []
    for n, c in enumerate(fm.result.conflicts, 1):
        for fid in c.ids:
            key = c.detail if c.kind == "ATTRIBUTE" else None
            row = {"conflict": n, "kind": c.kind, "id": fid,
                   "other_ids": ";".join(i for i in c.ids if i != fid),
                   "detail": c.detail, "attribute": key or ""}
            for side, ds in (("base", fm.base), ("ours", fm.ours), ("theirs", fm.theirs)):
                v = _value(ds, fid, key) if c.kind in ("ATTRIBUTE", "DELETE_MODIFY") else ""
                row[side] = v
            rows.append(row)
    return rows


def write_report(fm: FileMerge, path: str) -> None:
    """Conflict report as .geojson (open it in QGIS next to the data), .csv or .json."""
    rows = conflict_rows(fm)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        cols = ["conflict", "kind", "id", "other_ids", "attribute", "base", "ours", "theirs",
                "detail"]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        return
    if ext == ".json":
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"clean": fm.result.clean, "conflicts": rows}, fh, indent=2, default=str)
            fh.write("\n")
        return
    feats = []
    for r in rows:
        fid = r["id"]
        src = next((ds[fid] for ds in (fm.result.merged, fm.ours, fm.theirs, fm.base)
                    if fid in ds), None)
        feats.append({"type": "Feature", "id": f"{r['conflict']}:{fid}",
                      "properties": {k: (v if isinstance(v, (str, int, float, bool)) or v is None
                                         else json.dumps(v, default=str)) for k, v in r.items()},
                      "geometry": None if src is None else
                      {"type": src["geom"]["type"], "coordinates": src["geom"]["coords"]}})
    doc: dict[str, Any] = {"type": "FeatureCollection", "name": "geo3way_conflicts"}
    crs = fm.metas[1].crs_name
    if crs:
        name = crs if crs.startswith("urn:") else "urn:ogc:def:crs:" + crs.replace(":", "::", 1)
        doc["crs"] = {"type": "name", "properties": {"name": name}}
    doc["features"] = feats
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, default=str)
        fh.write("\n")


def describe_diff(a: Dataset, b: Dataset, eps: float) -> list[str]:
    """Human-readable lines describing how dataset ``b`` differs from ``a``."""
    lines = []
    for op in diff(a, b, eps):
        fid = op["id"]
        if op["op"] == "insert":
            lines.append(f"+ {fid}  added {op['feature']['geom']['type']}")
        elif op["op"] == "delete":
            lines.append(f"- {fid}  deleted")
        else:
            bits = []
            for k, v in sorted(op.get("props", {}).items()):
                old = a[fid]["props"].get(k, "<missing>")
                new = "<removed>" if v == "__geo3way_missing__" else v
                bits.append(f"{k}: {json.dumps(old, default=str)} -> {json.dumps(new, default=str)}")
            gd = op.get("geom")
            if gd and "replace" in gd:
                bits.append(f"geometry replaced ({a[fid]['geom']['type']} -> "
                            f"{gd['replace']['type']})")
            elif gd:
                n = sum(len(h) for h in gd["parts"].values())
                bits.append(f"geometry: {n} edited vertex range{'s' * (n != 1)} in part"
                            f"{'s' * (len(gd['parts']) != 1)} {', '.join(sorted(gd['parts']))}")
            lines.append(f"~ {fid}  " + "; ".join(bits))
    return lines


# --------------------------------------------------------------------------- git integration
GITATTR_COMMENT = "# geo3way: geometry-aware merge and diff"


def install_git(patterns: Sequence[str], scope: str = "local", attributes: str | None = None,
                extra: str = "") -> list[str]:
    """Register geo3way as git merge driver and diff driver, and map file patterns to it."""
    exe = f'"{sys.executable}" -m geo3way'
    opts = (" " + extra.strip()) if extra.strip() else ""
    flag = ["--global"] if scope == "global" else []
    cfg = {"merge.geo3way.name": "geo3way geometry-aware merge",
           "merge.geo3way.driver": f"{exe} git-merge-driver{opts} %O %A %B %P",
           "diff.geo3way.command": f"{exe} git-diff{opts}"}
    done = []
    for k, v in cfg.items():
        subprocess.run(["git", "config", *flag, k, v], check=True)
        done.append(f"git config {' '.join(flag + [k])} '{v}'")
    if attributes is None:
        if scope == "global":
            out = subprocess.run(["git", "config", "--global", "core.attributesFile"],
                                 capture_output=True, text=True).stdout.strip()
            attributes = os.path.expanduser(out or "~/.config/git/attributes")
        else:
            top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                                 text=True, check=True).stdout.strip()
            attributes = os.path.join(top, ".gitattributes")
    os.makedirs(os.path.dirname(attributes) or ".", exist_ok=True)
    existing = open(attributes, encoding="utf-8").read() if os.path.exists(attributes) else ""
    add = [f"{p} merge=geo3way diff=geo3way" for p in patterns
           if f"{p} merge=geo3way diff=geo3way" not in existing]
    if add:
        with open(attributes, "a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            if GITATTR_COMMENT not in existing:
                fh.write(GITATTR_COMMENT + "\n")
            fh.write("\n".join(add) + "\n")
        done.append(f"added to {attributes}: " + ", ".join(add))
    return done


def _git_merge_driver(a: argparse.Namespace) -> int:
    """git calls: driver %O %A %B %P. The result must be written to %A; non-zero exit marks
    the path as conflicted. A conflict report is written next to the file."""
    fmt = detect_format(a.path)
    tmp_out = a.ours + ".geo3way" + os.path.splitext(a.path)[1]
    report = a.path + ".conflicts.geojson"
    fm = merge_files(a.base, a.ours, a.theirs, None, None, prefer="ours", eps=a.eps,
                     duplicate_tol=a.duplicate_tol, id_field=a.id_field, layer=a.layer,
                     fmt=fmt)
    save(fm.result.merged, tmp_out, fm.metas[1], fm.metas[2], fm.metas[0])
    os.replace(tmp_out, a.ours)
    if fm.result.clean:
        if os.path.exists(report):
            os.remove(report)
        print(f"geo3way: merged {a.path} cleanly", file=sys.stderr)
        return 0
    write_report(fm, report)
    print(f"geo3way: {len(fm.result.conflicts)} conflict(s) in {a.path}; conflicted features "
          f"keep our version. See {report}", file=sys.stderr)
    for c in fm.result.conflicts[:20]:
        print(f"  {c.kind:<16} {', '.join(c.ids)}  {c.detail}", file=sys.stderr)
    return 1


def _git_diff(a: argparse.Namespace) -> int:
    """GIT_EXTERNAL_DIFF protocol: path old-file old-hex old-mode new-file new-hex new-mode."""
    path, old, new = a.args[0], a.args[1], a.args[4]
    fmt = detect_format(path)

    def ld(p: str) -> Dataset:
        return {} if p == "/dev/null" else load(p, a.id_field, a.layer, fmt)[0]
    da, db = ld(old), ld(new)
    eps = a.eps or (GEOGRAPHIC_EPS if looks_geographic(da or db) else PROJECTED_EPS)
    lines = describe_diff(da, db, eps)
    print(f"geo3way diff a/{path} b/{path}")
    print("\n".join(lines) if lines else "(no feature changes; formatting or order only)")
    return 0


# --------------------------------------------------------------------------- argparse
def _common(p: argparse.ArgumentParser, tolerances: bool = True) -> None:
    p.add_argument("--id-field", help="attribute holding the stable feature id "
                   "(default: GeoJSON 'id' member / GeoPackage primary key)")
    p.add_argument("--layer", help="GeoPackage layer (table) name")
    if tolerances:
        p.add_argument("--eps", type=float, help="coordinate tolerance; differences below it are "
                       "ignored (default 0.001 = 1 mm in metres, or 1e-8 for degrees)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="geo3way", description="Geometry-aware diff and "
                                 "three-way merge for vector data (GeoJSON, GeoPackage).")
    ap.add_argument("--version", action="version", version=f"geo3way {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("merge", help="three-way merge of base, ours and theirs")
    m.add_argument("base")
    m.add_argument("ours")
    m.add_argument("theirs")
    m.add_argument("-o", "--output", required=True, help="merged file (.geojson, .geojsonl, .gpkg)")
    m.add_argument("-r", "--report", help="conflict report (.geojson, .csv or .json)")
    m.add_argument("--prefer", choices=["ours", "theirs"], default="ours",
                   help="version kept for conflicted features (default ours)")
    m.add_argument("--duplicate-tol", type=float, help="Hausdorff distance below which two new "
                   "features count as duplicates (default 1 m, or 1e-5 for degrees)")
    m.add_argument("--no-validity", action="store_true", help="skip the invalid-geometry check")
    m.add_argument("--no-duplicates", action="store_true", help="skip the duplicate check")
    m.add_argument("--no-overlaps", action="store_true", help="skip the new-overlap check")
    m.add_argument("-q", "--quiet", action="store_true")
    _common(m)

    d = sub.add_parser("diff", help="feature-level differences between two files")
    d.add_argument("a")
    d.add_argument("b")
    d.add_argument("--json", action="store_true", help="print the patch as JSON")
    _common(d)

    v = sub.add_parser("validate", help="report invalid geometries and missing/duplicate ids")
    v.add_argument("file")
    _common(v, tolerances=False)

    i = sub.add_parser("add-ids", help="give features without an id a UUID")
    i.add_argument("file")
    i.add_argument("-o", "--output", help="output file (default: modify in place)")
    _common(i, tolerances=False)

    g = sub.add_parser("install-git", help="use geo3way for git merge and git diff")
    g.add_argument("patterns", nargs="*", default=["*.geojson", "*.geojsonl", "*.gpkg"],
                   help="file patterns (default: *.geojson *.geojsonl *.gpkg)")
    g.add_argument("--global", dest="scope", action="store_const", const="global",
                   default="local", help="configure for all repositories of this user")
    g.add_argument("--options", default="", help="extra options for the driver, "
                   "e.g. \"--id-field uuid\"")

    md = sub.add_parser("git-merge-driver", help=argparse.SUPPRESS)
    md.add_argument("base")
    md.add_argument("ours")
    md.add_argument("theirs")
    md.add_argument("path")
    md.add_argument("--duplicate-tol", type=float)
    _common(md)

    gd = sub.add_parser("git-diff", help=argparse.SUPPRESS)
    gd.add_argument("args", nargs="+")
    _common(gd)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return _run(a)
    except (FormatError, OSError, ValueError, subprocess.CalledProcessError) as e:
        print(f"geo3way: error: {e}", file=sys.stderr)
        return 2


def _run(a: argparse.Namespace) -> int:
    if a.cmd == "merge":
        fm = merge_files(a.base, a.ours, a.theirs, a.output, a.report, prefer=a.prefer,
                         eps=a.eps, duplicate_tol=a.duplicate_tol,
                         check_validity=not a.no_validity, check_duplicates=not a.no_duplicates,
                         check_overlaps=not a.no_overlaps, id_field=a.id_field, layer=a.layer)
        if not a.quiet:
            for n in fm.notes:
                print(f"note: {n}", file=sys.stderr)
            _print_summary(fm, a.output, a.report)
        return 0 if fm.result.clean else 1
    if a.cmd == "diff":
        (da, _), (db, _) = load(a.a, a.id_field, a.layer), load(a.b, a.id_field, a.layer)
        eps = a.eps or (GEOGRAPHIC_EPS if looks_geographic(da or db) else PROJECTED_EPS)
        if a.json:
            ops = diff(da, db, eps)
            print(json.dumps(ops, default=str, indent=1))
            return 1 if ops else 0
        lines = describe_diff(da, db, eps)
        print("\n".join(lines) if lines else "no differences")
        return 1 if lines else 0
    if a.cmd == "validate":
        ds, _ = load(a.file, a.id_field, a.layer)
        bad = [(fid, e) for fid in ds for e in validity_errors(ds[fid]["geom"])]
        for fid, e in bad:
            print(f"{fid}: {e}")
        print(f"{len(ds)} features, {len({f for f, _ in bad})} invalid", file=sys.stderr)
        return 1 if bad else 0
    if a.cmd == "add-ids":
        added = add_ids(a.file, a.output or a.file, a.id_field, a.layer)
        print(f"added {added} id(s)", file=sys.stderr)
        return 0
    if a.cmd == "install-git":
        for line in install_git(a.patterns, a.scope, extra=a.options):
            print(line)
        return 0
    if a.cmd == "git-merge-driver":
        return _git_merge_driver(a)
    if a.cmd == "git-diff":
        return _git_diff(a)
    raise AssertionError(a.cmd)


def _print_summary(fm: FileMerge, out: str, report: str | None) -> None:
    r = fm.result
    kinds: dict[str, int] = {}
    for c in r.conflicts:
        kinds[c.kind] = kinds.get(c.kind, 0) + 1
    print(f"merged {len(r.merged)} features -> {out}", file=sys.stderr)
    if r.clean:
        print("no conflicts", file=sys.stderr)
        return
    print(f"{len(r.conflicts)} conflict(s): " +
          ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())), file=sys.stderr)
    for c in r.conflicts[:20]:
        print(f"  {c.kind:<16} {', '.join(c.ids)}  {c.detail}", file=sys.stderr)
    if len(r.conflicts) > 20:
        print(f"  ... and {len(r.conflicts) - 20} more", file=sys.stderr)
    if report:
        print(f"report: {report}", file=sys.stderr)


__all__ = ["Conflict", "FileMerge", "build_parser", "conflict_rows", "describe_diff",
           "install_git", "main", "merge_files", "write_report"]


if __name__ == "__main__":
    raise SystemExit(main())
