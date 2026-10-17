"""Planar geometry primitives used for spatial conflict detection.

Geometries are plain dicts: {"type": "Point", "coords": [x, y]},
{"type": "LineString", "coords": [[x, y], ...]},
{"type": "Polygon", "coords": [shell, hole, ...]} with closed rings (first vertex == last),
and the Multi* equivalents (MultiPoint, MultiLineString, MultiPolygon) as in GeoJSON.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

Coord = Sequence[float]
Geometry = dict[str, Any]
AREAL = ("Polygon", "MultiPolygon")


def polygons(g: Geometry) -> list[list[Any]]:
    """Member polygons (each a list of rings) of a Polygon or MultiPolygon."""
    if g["type"] == "Polygon":
        return [g["coords"]]
    if g["type"] == "MultiPolygon":
        return list(g["coords"])
    return []


def bbox(g: Geometry) -> tuple[float, float, float, float]:
    pts = list(vertices(g))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def bbox_intersects(a: tuple[float, ...], b: tuple[float, ...], pad: float = 0.0) -> bool:
    return not (a[2] + pad < b[0] or b[2] + pad < a[0] or a[3] + pad < b[1] or b[3] + pad < a[1])


def vertices(g: Geometry) -> list[Coord]:
    t, c = g["type"], g["coords"]
    if t == "Point":
        return [c]
    if t in ("LineString", "MultiPoint"):
        return list(c)
    if t in ("Polygon", "MultiLineString"):
        return [p for ring in c for p in ring]
    if t == "MultiPolygon":
        return [p for poly in c for ring in poly for p in ring]
    raise ValueError(f"unsupported geometry type {t!r}")


def _orient(a: Coord, b: Coord, c: Coord) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_cross(p1: Coord, p2: Coord, q1: Coord, q2: Coord) -> bool:
    """True if the closed segments p1-p2 and q1-q2 intersect (including touching)."""
    d1, d2 = _orient(q1, q2, p1), _orient(q1, q2, p2)
    d3, d4 = _orient(p1, p2, q1), _orient(p1, p2, q2)
    if ((d1 > 0) != (d2 > 0)) and d1 != 0 and d2 != 0 and \
            ((d3 > 0) != (d4 > 0)) and d3 != 0 and d4 != 0:
        return True

    def on_seg(a: Coord, b: Coord, c: Coord) -> bool:
        return (min(a[0], b[0]) <= c[0] <= max(a[0], b[0])
                and min(a[1], b[1]) <= c[1] <= max(a[1], b[1]))
    return ((d1 == 0 and on_seg(q1, q2, p1)) or (d2 == 0 and on_seg(q1, q2, p2))
            or (d3 == 0 and on_seg(p1, p2, q1)) or (d4 == 0 and on_seg(p1, p2, q2)))


def segments_properly_cross(p1: Coord, p2: Coord, q1: Coord, q2: Coord) -> bool:
    """True if the segments cross at a single interior point of both."""
    d1, d2 = _orient(q1, q2, p1), _orient(q1, q2, p2)
    d3, d4 = _orient(p1, p2, q1), _orient(p1, p2, q2)
    return d1 * d2 < 0 and d3 * d4 < 0


def ring_area(ring: Sequence[Coord]) -> float:
    return 0.5 * sum(ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
                     for i in range(len(ring) - 1))


def ring_is_simple(ring: Sequence[Coord]) -> bool:
    """No two non-adjacent edges of the closed ring intersect."""
    n = len(ring) - 1  # number of edges
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue  # adjacent edges share a vertex
            if segments_cross(ring[i], ring[i + 1], ring[j], ring[j + 1]):
                return False
    return True


def _finite_point(p: Any) -> bool:
    return isinstance(p, (list, tuple)) and len(p) >= 2 and all(
        isinstance(v, (int, float)) and math.isfinite(v) for v in p)


def _polygon_errors(rings: Any, prefix: str = "") -> list[str]:
    errs: list[str] = []
    if not rings:
        return [f"{prefix}polygon has no rings"]
    for k, ring in enumerate(rings):
        if len(ring) < 4:
            errs.append(f"{prefix}ring {k} has fewer than 4 coordinates")
            continue
        if not all(_finite_point(p) for p in ring):
            errs.append(f"{prefix}ring {k} has non-finite coordinates")
            continue
        if list(ring[0]) != list(ring[-1]):
            errs.append(f"{prefix}ring {k} is not closed")
            continue
        if abs(ring_area(ring)) == 0:
            errs.append(f"{prefix}ring {k} has zero area")
        if not ring_is_simple(ring):
            errs.append(f"{prefix}ring {k} self-intersects")
    return errs


def validity_errors(g: Geometry) -> list[str]:
    """Simple-feature validity checks; an empty list means valid.

    Checked: coordinate counts, finiteness, closed rings, non-zero ring area, simple
    (non-self-intersecting) rings. Not checked: hole-in-shell containment and ring-ring
    intersections between different rings."""
    t, c = g.get("type"), g.get("coords")
    errs: list[str] = []
    if c is None:
        return ["geometry has no coordinates"]
    if t == "Point":
        if not (_finite_point(c) and len(c) == 2):
            errs.append("point must have two finite coordinates")
    elif t == "MultiPoint":
        if not c or not all(_finite_point(p) for p in c):
            errs.append("multipoint needs at least one point with finite coordinates")
    elif t == "LineString":
        if len(c) < 2:
            errs.append("linestring needs at least 2 vertices")
    elif t == "MultiLineString":
        if not c:
            errs.append("multilinestring has no lines")
        errs += [f"line {k} needs at least 2 vertices" for k, ln in enumerate(c) if len(ln) < 2]
    elif t == "Polygon":
        errs += _polygon_errors(c)
    elif t == "MultiPolygon":
        if not c:
            errs.append("multipolygon has no polygons")
        for k, poly in enumerate(c):
            errs += _polygon_errors(poly, f"polygon {k}: ")
    else:
        errs.append(f"unsupported geometry type {t!r}")
    return errs


def point_in_ring(p: Coord, ring: Sequence[Coord]) -> bool:
    """Ray casting; points exactly on the boundary may go either way."""
    x, y = p[0], p[1]
    inside = False
    for i in range(len(ring) - 1):
        (x1, y1), (x2, y2) = ring[i][:2], ring[i + 1][:2]
        if (y1 > y) != (y2 > y):
            xin = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if xin > x:
                inside = not inside
    return inside


def _on_segment(p: Coord, a: Coord, b: Coord, tol: float) -> bool:
    """True if p lies within tol of segment a-b."""
    ax, ay, bx, by, px, py = a[0], a[1], b[0], b[1], p[0], p[1]
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy)) <= tol


def strictly_inside(p: Coord, ring: Sequence[Coord], tol: float = 1e-9) -> bool:
    """Inside the ring and not on its boundary."""
    if any(_on_segment(p, ring[i], ring[i + 1], tol) for i in range(len(ring) - 1)):
        return False
    return point_in_ring(p, ring)


def _interior_samples(ring: Sequence[Coord]) -> list[list[float]]:
    """Points just inside the ring, one per edge (edge midpoint nudged along the inward normal)."""
    sign = 1.0 if ring_area(ring) > 0 else -1.0  # counter-clockwise: interior on the left
    pts = []
    for i in range(len(ring) - 1):
        (x1, y1), (x2, y2) = ring[i][:2], ring[i + 1][:2]
        L = math.hypot(x2 - x1, y2 - y1)
        if L == 0:
            continue
        h = min(1e-3, L * 1e-3)
        nx, ny = -(y2 - y1) / L * sign, (x2 - x1) / L * sign
        pts.append([(x1 + x2) / 2 + h * nx, (y1 + y2) / 2 + h * ny])
    return pts


def _strictly_in_polygon(p: Coord, rings: Sequence[Sequence[Coord]], tol: float = 1e-9) -> bool:
    """Strictly inside the shell and not inside (or on the boundary of) any hole."""
    if not strictly_inside(p, rings[0], tol):
        return False
    return not any(point_in_ring(p, h) or any(_on_segment(p, h[i], h[i + 1], tol)
                                              for i in range(len(h) - 1)) for h in rings[1:])


def _ring_bbox(ring: Sequence[Coord]) -> tuple[float, float, float, float]:
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def _single_polygons_overlap(pa: Sequence[Sequence[Coord]], pb: Sequence[Sequence[Coord]]) -> bool:
    if not bbox_intersects(_ring_bbox(pa[0]), _ring_bbox(pb[0])):
        return False
    for ra in pa:
        for rb in pb:
            for i in range(len(ra) - 1):
                for j in range(len(rb) - 1):
                    if segments_properly_cross(ra[i], ra[i + 1], rb[j], rb[j + 1]):
                        return True
    for p, q in ((pa, pb), (pb, pa)):
        for ring in p:
            if any(_strictly_in_polygon(v, q) for v in ring[:-1]):
                return True
            if any(_strictly_in_polygon(s, q) and _strictly_in_polygon(s, p)
                   for s in _interior_samples(ring) + _interior_samples(list(reversed(ring)))):
                return True
    return False


def polygons_overlap(a: Geometry, b: Geometry) -> bool:
    """True if the interiors of two (multi)polygons overlap. Holes are respected.

    Robust to degenerate contacts (a vertex exactly on the other's edge, shared edges,
    identical polygons): besides proper edge crossings, it tests whether any vertex, or any
    point just beside an edge, of one polygon lies strictly inside both polygons."""
    if not bbox_intersects(bbox(a), bbox(b)):
        return False
    return any(_single_polygons_overlap(pa, pb) for pa in polygons(a) for pb in polygons(b))


def _edges(g: Geometry) -> list[tuple[Coord, Coord]]:
    t, c = g["type"], g["coords"]
    if t in ("Point", "MultiPoint"):
        pts = [c] if t == "Point" else list(c)
        return [(p, p) for p in pts]
    seqs = {"LineString": lambda: [c], "MultiLineString": lambda: list(c),
            "Polygon": lambda: list(c),
            "MultiPolygon": lambda: [r for poly in c for r in poly]}[t]()
    return [(s[i], s[i + 1]) for s in seqs for i in range(len(s) - 1)] or \
        [(p, p) for s in seqs for p in s]


def _point_seg_dist(p: Coord, a: Coord, b: Coord) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def _samples(edges: list[tuple[Coord, Coord]], step: float, cap: int = 64) -> list[Coord]:
    pts: list[Coord] = []
    for a, b in edges:
        n = min(cap, max(1, int(math.ceil(math.dist(a[:2], b[:2]) / step)))) if step > 0 else 1
        pts.extend([a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n]
                   for k in range(n))
        pts.append(b)
    return pts


def hausdorff(a: Geometry, b: Geometry, step: float | None = None) -> float:
    """Hausdorff distance between the boundaries (vertex/edge sets) of two geometries.

    Points of each geometry (vertices, plus edge points every ``step``) are measured to the
    other geometry's edges, so differences in vertex count or placement along an edge do not
    inflate the distance. With ``step=None`` edges are sampled at 1/50 of the larger bbox
    diagonal; the result is then accurate to within that sampling distance."""
    ea, eb = _edges(a), _edges(b)
    if step is None:
        ba, bb = bbox(a), bbox(b)
        diag = max(math.hypot(x[2] - x[0], x[3] - x[1]) for x in (ba, bb))
        step = diag / 50

    def directed(src: list[tuple[Coord, Coord]], dst: list[tuple[Coord, Coord]]) -> float:
        return max(min(_point_seg_dist(p, u, v) for u, v in dst) for p in _samples(src, step))
    return max(directed(ea, eb), directed(eb, ea))
