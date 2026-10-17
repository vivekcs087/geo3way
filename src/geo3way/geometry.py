"""Planar geometry primitives used for spatial conflict detection.

Geometries are plain dicts: {"type": "Point", "coords": [x, y]},
{"type": "LineString", "coords": [[x, y], ...]},
{"type": "Polygon", "coords": [ring, ...]} with closed rings (first vertex == last vertex).
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

Coord = Sequence[float]
Geometry = dict[str, Any]


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
    if t == "LineString":
        return list(c)
    if t == "Polygon":
        return [p for ring in c for p in ring]
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


def validity_errors(g: Geometry) -> list[str]:
    """Simple-feature validity checks; an empty list means valid."""
    t, c = g.get("type"), g.get("coords")
    errs: list[str] = []
    if t == "Point":
        if not (isinstance(c, (list, tuple)) and len(c) == 2 and all(map(math.isfinite, c))):
            errs.append("point must have two finite coordinates")
    elif t == "LineString":
        if len(c) < 2:
            errs.append("linestring needs at least 2 vertices")
    elif t == "Polygon":
        if not c:
            errs.append("polygon has no rings")
        for k, ring in enumerate(c):
            if len(ring) < 4:
                errs.append(f"ring {k} has fewer than 4 coordinates")
                continue
            if list(ring[0]) != list(ring[-1]):
                errs.append(f"ring {k} is not closed")
                continue
            if abs(ring_area(ring)) == 0:
                errs.append(f"ring {k} has zero area")
            if not ring_is_simple(ring):
                errs.append(f"ring {k} self-intersects")
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


def polygons_overlap(a: Geometry, b: Geometry) -> bool:
    """True if the interiors of two polygons (outer rings) overlap.

    Robust to degenerate contacts (a vertex exactly on the other's edge, shared edges,
    identical polygons): besides proper edge crossings, it tests whether any vertex, or any
    point just inside an edge, of one polygon lies strictly inside the other."""
    ra, rb = a["coords"][0], b["coords"][0]
    if not bbox_intersects(bbox(a), bbox(b)):
        return False
    for i in range(len(ra) - 1):
        for j in range(len(rb) - 1):
            if segments_properly_cross(ra[i], ra[i + 1], rb[j], rb[j + 1]):
                return True
    for p, q in ((ra, rb), (rb, ra)):
        if any(strictly_inside(v, q) for v in p[:-1]):
            return True
        if any(strictly_inside(s, q) for s in _interior_samples(p)):
            return True
    return False


def hausdorff(a: Geometry, b: Geometry) -> float:
    """Discrete Hausdorff distance between the vertex sets of two geometries."""
    va, vb = vertices(a), vertices(b)

    def directed(p: list[Coord], q: list[Coord]) -> float:
        return max(min(math.dist(x, y) for y in q) for x in p)
    return max(directed(va, vb), directed(vb, va))
