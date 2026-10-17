"""Three-way merge of vector datasets with structural and semantic spatial conflict detection.

Structural conflicts (decided per feature, attribute and vertex range):
  ATTRIBUTE      both sides set the same attribute to different values
  GEOMETRY       both sides edited overlapping vertex ranges of the same part differently
  GEOMETRY_TYPE  both sides changed the geometry in incompatible ways (type or part count)
  DELETE_MODIFY  one side deleted a feature the other modified
  INSERT_INSERT  both sides inserted different features with the same id
Semantic spatial conflicts (checked on the merged result; each side alone was fine):
  INVALID_GEOMETRY  the combined geometry is invalid although each side's version is valid
  DUPLICATE         both sides inserted (different ids) what is spatially the same object
  OVERLAP           two polygons overlap in the merge but in none of base, ours or theirs
"""
from __future__ import annotations

import copy
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .diff import (
    _MISSING,
    Dataset,
    Hunk,
    from_parts,
    geom_diff,
    geom_equal,
    parts,
    seq_hunks,
)
from .geometry import AREAL, bbox, bbox_intersects, hausdorff, polygons_overlap, validity_errors


@dataclass
class Conflict:
    kind: str
    ids: tuple[str, ...]
    detail: str = ""


@dataclass
class MergeResult:
    merged: Dataset
    conflicts: list[Conflict] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.conflicts

    def conflicted_ids(self) -> set[str]:
        return {i for c in self.conflicts for i in c.ids}


def _hunks_clash(a: Hunk, b: Hunk) -> bool:
    """Two hunks on the same base sequence clash if their ranges overlap, or if one inserts
    at a position strictly inside (or at the same insertion point as) the other."""
    (a1, a2, _), (b1, b2, _) = a, b
    if a1 == a2 and b1 == b2:
        return a1 == b1
    if a1 == a2:
        return b1 < a1 < b2
    if b1 == b2:
        return a1 < b1 < a2
    return a1 < b2 and b1 < a2


def merge_sequences(base: list[list[float]], ours: list[list[float]], theirs: list[list[float]],
                    eps: float) -> tuple[list[list[float]] | None, str]:
    """Vertex-level three-way merge. Returns (merged, "") or (None, reason)."""
    ho, ht = seq_hunks(base, ours, eps), seq_hunks(base, theirs, eps)
    keep: list[Hunk] = list(ho)
    for h in ht:
        clash = [g for g in ho if _hunks_clash(g, h)]
        if not clash:
            keep.append(h)
        elif len(clash) == 1 and clash[0][:2] == h[:2] and \
                [tuple(round(v / eps) for v in p) for p in clash[0][2]] == \
                [tuple(round(v / eps) for v in p) for p in h[2]]:
            continue  # identical change on both sides
        else:
            return None, f"overlapping vertex edits at base range {h[0]}:{h[1]}"
    out, pos = [], 0
    for i1, i2, new in sorted(keep, key=lambda h: (h[0], h[1])):
        out.extend(base[pos:i1])
        out.extend(new)
        pos = max(pos, i2)
    out.extend(base[pos:])
    return out, ""


def _merge_props(b: dict[str, Any], o: dict[str, Any], t: dict[str, Any]
                 ) -> tuple[dict[str, Any], list[str]]:
    out, bad = {}, []
    for k in dict.fromkeys([*o, *t, *b]):               # stable key order: ours first
        vb, vo, vt = b.get(k, _MISSING), o.get(k, _MISSING), t.get(k, _MISSING)
        if vo == vt:
            v = vo
        elif vo == vb:
            v = vt
        elif vt == vb:
            v = vo
        else:
            bad.append(k)
            v = vo
        if v != _MISSING:
            out[k] = v
    return out, sorted(bad)


def _merge_geom(b: dict[str, Any], o: dict[str, Any], t: dict[str, Any], eps: float
                ) -> tuple[dict[str, Any], Conflict | None, str]:
    do, dt = geom_diff(b, o, eps), geom_diff(b, t, eps)
    if do is None:
        return copy.deepcopy(t), None, ""
    if dt is None or geom_equal(o, t, eps):
        return copy.deepcopy(o), None, ""
    if "replace" in do or "replace" in dt:
        return copy.deepcopy(o), Conflict("GEOMETRY_TYPE", (), "type or part count changed"), ""
    pb, po, pt = parts(b), parts(o), parts(t)
    merged = []
    for k in range(len(pb)):
        m, why = merge_sequences(pb[k], po[k], pt[k], eps)
        if m is None:
            return copy.deepcopy(o), Conflict("GEOMETRY", (), f"part {k}: {why}"), ""
        merged.append(m)
    return from_parts(b["type"], merged, like=b), None, "combined"


def merge(base: Dataset, ours: Dataset, theirs: Dataset, eps: float = 1e-3,
          check_validity: bool = True, duplicate_tol: float | None = 1.0,
          check_overlaps: bool = True) -> MergeResult:
    """Three-way merge. Conflicted features keep ``ours``; all conflicts are reported."""
    res = MergeResult(merged={})
    combined_geoms: list[str] = []
    for fid in sorted(set(base) | set(ours) | set(theirs)):
        b, o, t = base.get(fid), ours.get(fid), theirs.get(fid)
        if b is None:                                   # inserted on one or both sides
            if o is not None and t is not None:
                same = o["props"] == t["props"] and geom_equal(o["geom"], t["geom"], eps)
                if not same:
                    res.conflicts.append(Conflict("INSERT_INSERT", (fid,)))
                res.merged[fid] = copy.deepcopy(o)
            else:
                only = o if o is not None else t
                assert only is not None
                res.merged[fid] = copy.deepcopy(only)
            continue
        changed_o = o is None or o["props"] != b["props"] or not geom_equal(o["geom"], b["geom"], eps)
        changed_t = t is None or t["props"] != b["props"] or not geom_equal(t["geom"], b["geom"], eps)
        if o is None or t is None:                      # deleted on at least one side
            if o is None and t is None:
                continue
            survivor, survivor_changed = (t, changed_t) if o is None else (o, changed_o)
            assert survivor is not None
            if survivor_changed:
                res.conflicts.append(Conflict("DELETE_MODIFY", (fid,)))
                res.merged[fid] = copy.deepcopy(survivor)
            continue
        if not changed_t:
            res.merged[fid] = copy.deepcopy(o)
            continue
        if not changed_o:
            res.merged[fid] = copy.deepcopy(t)
            continue
        props, bad = _merge_props(b["props"], o["props"], t["props"])
        for k in bad:
            res.conflicts.append(Conflict("ATTRIBUTE", (fid,), k))
        geom, gconf, how = _merge_geom(b["geom"], o["geom"], t["geom"], eps)
        if gconf is not None:
            res.conflicts.append(Conflict(gconf.kind, (fid,), gconf.detail))
        elif how == "combined":
            combined_geoms.append(fid)
        res.merged[fid] = {"props": props, "geom": geom}

    if check_validity:
        for fid in combined_geoms:
            errs = validity_errors(res.merged[fid]["geom"])
            sides_valid = not validity_errors(ours[fid]["geom"]) and \
                not validity_errors(theirs[fid]["geom"])
            if errs and sides_valid:
                res.conflicts.append(Conflict("INVALID_GEOMETRY", (fid,), "; ".join(errs)))
    if duplicate_tol is not None:
        res.conflicts.extend(_duplicates(base, ours, theirs, duplicate_tol))
    if check_overlaps:
        dup_pairs = {tuple(sorted(c.ids)) for c in res.conflicts if c.kind == "DUPLICATE"}
        res.conflicts.extend(c for c in _new_overlaps(base, ours, theirs, res.merged, eps)
                             if tuple(sorted(c.ids)) not in dup_pairs)
    return res


def _duplicates(base: Dataset, ours: Dataset, theirs: Dataset, tol: float) -> list[Conflict]:
    ins_o = [k for k in ours if k not in base and k not in theirs]
    ins_t = [k for k in theirs if k not in base and k not in ours]
    out = []
    boxes_t = {k: bbox(theirs[k]["geom"]) for k in ins_t}
    for a in ins_o:
        ga, ba = ours[a]["geom"], bbox(ours[a]["geom"])
        for b in ins_t:
            gb = theirs[b]["geom"]
            if ga["type"] == gb["type"] and bbox_intersects(ba, boxes_t[b], pad=tol) \
                    and hausdorff(ga, gb, step=tol / 2) <= tol:
                out.append(Conflict("DUPLICATE", (a, b), f"Hausdorff <= {tol}"))
    return out


def _overlapped_in(v: Dataset, a: str, b: str) -> bool:
    return (a in v and b in v and v[a]["geom"]["type"] in AREAL
            and v[b]["geom"]["type"] in AREAL
            and polygons_overlap(v[a]["geom"], v[b]["geom"]))


def _new_overlaps(base: Dataset, ours: Dataset, theirs: Dataset, merged: Dataset,
                  eps: float) -> list[Conflict]:
    polys = {k: f for k, f in merged.items() if f["geom"]["type"] in AREAL}
    if not polys:
        return []
    touched = {k for k in polys
               if any(k not in v or not geom_equal(v[k]["geom"], polys[k]["geom"], eps)
                      for v in (base, ours, theirs))}
    boxes = {k: bbox(f["geom"]) for k, f in polys.items()}
    size = max(1e-9, sum(bx[2] - bx[0] + bx[3] - bx[1] for bx in boxes.values()) / len(boxes))
    grid: dict[tuple[int, int], list[str]] = defaultdict(list)
    for k, bx in boxes.items():
        for i in range(int(bx[0] // size), int(bx[2] // size) + 1):
            for j in range(int(bx[1] // size), int(bx[3] // size) + 1):
                grid[(i, j)].append(k)
    seen, out = set(), []
    for a in sorted(touched):
        bx = boxes[a]
        cands = {c for i in range(int(bx[0] // size), int(bx[2] // size) + 1)
                 for j in range(int(bx[1] // size), int(bx[3] // size) + 1) for c in grid[(i, j)]}
        for b in sorted(cands - {a}):
            pair = (min(a, b), max(a, b))
            if pair in seen:
                continue
            seen.add(pair)
            if not bbox_intersects(bx, boxes[b]) or not polygons_overlap(polys[a]["geom"], polys[b]["geom"]):
                continue

            if not any(_overlapped_in(v, a, b) for v in (base, ours, theirs)):
                out.append(Conflict("OVERLAP", pair, "polygons overlap only after merging"))
    return out
