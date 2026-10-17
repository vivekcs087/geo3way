"""Feature- and vertex-level differencing of vector datasets.

A dataset is a mapping ``feature_id -> {"props": {...}, "geom": Geometry}``.
Coordinates are compared after snapping to a tolerance grid (``eps``), so floating-point
noise below ``eps`` (re-projection round trips, re-export) produces no change.
"""
from __future__ import annotations

import copy
import json
from difflib import SequenceMatcher
from typing import Any

Feature = dict[str, Any]
Dataset = dict[str, Feature]
Hunk = tuple[int, int, list[list[float]]]   # replace base[i1:i2] with coords

_MISSING = "__geo3way_missing__"


def snap(c: Any, eps: float) -> tuple[int, ...]:
    return tuple(int(round(v / eps)) for v in c)


GEOMETRY_TYPES = ("Point", "LineString", "Polygon", "MultiPoint", "MultiLineString",
                  "MultiPolygon")


def parts(geom: dict[str, Any]) -> list[list[list[float]]]:
    """Vertex sequences of a geometry, flattened; polygon rings are returned open (no closing
    vertex). A MultiPoint is one sequence of points; a MultiPolygon is all rings of all
    member polygons in order (see :func:`layout`)."""
    t, c = geom["type"], geom["coords"]
    if t == "Point":
        return [[list(c)]]
    if t in ("LineString", "MultiPoint"):
        return [[list(p) for p in c]]
    if t == "MultiLineString":
        return [[list(p) for p in line] for line in c]
    if t == "Polygon":
        return [[list(p) for p in ring[:-1]] for ring in c]
    if t == "MultiPolygon":
        return [[list(p) for p in ring[:-1]] for poly in c for ring in poly]
    raise ValueError(f"unsupported geometry type {t!r}")


def layout(geom: dict[str, Any]) -> tuple[Any, ...]:
    """Structure of a geometry: two geometries with the same type and layout have
    corresponding parts, so they can be merged vertex by vertex."""
    t, c = geom["type"], geom["coords"]
    if t == "MultiPolygon":
        return (t, tuple(len(poly) for poly in c))
    return (t, len(parts(geom)))


def from_parts(gtype: str, ps: list[list[list[float]]],
               like: dict[str, Any] | None = None) -> dict[str, Any]:
    """Inverse of :func:`parts`. ``like`` (a geometry with the same layout) is needed for
    MultiPolygon, to know how many rings each member polygon has."""
    if gtype == "Point":
        return {"type": "Point", "coords": ps[0][0]}
    if gtype in ("LineString", "MultiPoint"):
        return {"type": gtype, "coords": ps[0]}
    if gtype == "MultiLineString":
        return {"type": gtype, "coords": ps}
    rings = [r + [r[0]] if r else [] for r in ps]
    if gtype == "Polygon":
        return {"type": "Polygon", "coords": rings}
    if gtype == "MultiPolygon":
        if like is None:
            raise ValueError("from_parts needs a template geometry for MultiPolygon")
        out, k = [], 0
        for n in layout(like)[1]:
            out.append(rings[k:k + n])
            k += n
        return {"type": gtype, "coords": out}
    raise ValueError(f"unsupported geometry type {gtype!r}")


def seq_hunks(a: list[list[float]], b: list[list[float]], eps: float) -> list[Hunk]:
    """Minimal-ish edit script turning vertex list a into b (difflib alignment on snapped coords)."""
    sa, sb = [snap(p, eps) for p in a], [snap(p, eps) for p in b]
    sm = SequenceMatcher(a=sa, b=sb, autojunk=False)
    return [(i1, i2, [list(p) for p in b[j1:j2]])
            for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]


def geom_equal(g1: dict[str, Any], g2: dict[str, Any], eps: float) -> bool:
    if g1["type"] != g2["type"] or layout(g1) != layout(g2):
        return False
    p1, p2 = parts(g1), parts(g2)
    return len(p1) == len(p2) and all(
        len(r1) == len(r2) and all(snap(x, eps) == snap(y, eps) for x, y in zip(r1, r2, strict=True))
        for r1, r2 in zip(p1, p2, strict=True))


def geom_diff(g0: dict[str, Any], g1: dict[str, Any], eps: float) -> dict[str, Any] | None:
    """None if equal; a full replacement if the type or part count changed; else vertex hunks."""
    if geom_equal(g0, g1, eps):
        return None
    p0, p1 = parts(g0), parts(g1)
    if layout(g0) != layout(g1):
        return {"replace": g1}
    out = {}
    for k, (a, b) in enumerate(zip(p0, p1, strict=True)):
        h = seq_hunks(a, b, eps)
        if h:
            out[str(k)] = h
    return {"parts": out}


def props_diff(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Keys whose value changed; removed keys map to the _MISSING sentinel."""
    out = {}
    for k in set(a) | set(b):
        va, vb = a.get(k, _MISSING), b.get(k, _MISSING)
        if va != vb:
            out[k] = vb
    return out


def diff(base: Dataset, new: Dataset, eps: float = 1e-3) -> list[dict[str, Any]]:
    """Ordered list of operations turning ``base`` into ``new``."""
    ops: list[dict[str, Any]] = []
    for fid in sorted(set(base) - set(new)):
        ops.append({"op": "delete", "id": fid})
    for fid in sorted(set(new) - set(base)):
        ops.append({"op": "insert", "id": fid, "feature": new[fid]})
    for fid in sorted(set(base) & set(new)):
        pd = props_diff(base[fid]["props"], new[fid]["props"])
        gd = geom_diff(base[fid]["geom"], new[fid]["geom"], eps)
        if pd or gd:
            op: dict[str, Any] = {"op": "update", "id": fid}
            if pd:
                op["props"] = pd
            if gd:
                op["geom"] = gd
            ops.append(op)
    return ops


def apply_hunks(seq: list[list[float]], hunks: list[Hunk]) -> list[list[float]]:
    """Apply non-overlapping hunks (expressed on the original indices) to a vertex list."""
    out, pos = [], 0
    for i1, i2, new in sorted(hunks, key=lambda h: (h[0], h[1])):
        if i1 < pos:
            raise ValueError("overlapping hunks")
        out.extend(seq[pos:i1])
        out.extend([list(p) for p in new])
        pos = i2
    out.extend(seq[pos:])
    return out


def apply_geom(g: dict[str, Any], gd: dict[str, Any]) -> dict[str, Any]:
    if "replace" in gd:
        return copy.deepcopy(gd["replace"])
    ps = parts(g)
    for k, hunks in gd["parts"].items():
        ps[int(k)] = apply_hunks(ps[int(k)], [tuple(h) for h in hunks])  # type: ignore[misc]
    return from_parts(g["type"], ps, like=g)


def apply_props(p: dict[str, Any], pd: dict[str, Any]) -> dict[str, Any]:
    out = dict(p)
    for k, v in pd.items():
        if v == _MISSING:
            out.pop(k, None)
        else:
            out[k] = v
    return out


def apply(base: Dataset, ops: list[dict[str, Any]]) -> Dataset:
    out = copy.deepcopy(base)
    for op in ops:
        fid = op["id"]
        if op["op"] == "delete":
            del out[fid]
        elif op["op"] == "insert":
            out[fid] = copy.deepcopy(op["feature"])
        else:
            f = out[fid]
            if "props" in op:
                f["props"] = apply_props(f["props"], op["props"])
            if "geom" in op:
                f["geom"] = apply_geom(f["geom"], op["geom"])
    return out


def patch_bytes(ops: list[dict[str, Any]]) -> bytes:
    """Compact, canonical JSON encoding of a patch (used to measure diff size)."""
    return json.dumps(ops, separators=(",", ":"), sort_keys=True).encode()


def datasets_equal(a: Dataset, b: Dataset, eps: float = 1e-3) -> bool:
    return (a.keys() == b.keys()
            and all(a[k]["props"] == b[k]["props"] and geom_equal(a[k]["geom"], b[k]["geom"], eps)
                    for k in a))
