"""Synthetic city + concurrent-edit scenarios with ground truth known by construction.

Every scenario is a list of *episodes*. An episode edits fresh features (never shared with
another episode) on the 'ours' branch, the 'theirs' branch, or both, and carries a label:

  must merge cleanly                     true conflicts
  NC1 different features                 C1 same attribute, different values
  NC2 same feature, different attributes C2 same vertex / same edge edited differently
  NC3 same feature, attribute + geometry C3 delete vs modify
  NC4 same geometry, distant vertices    C4 same new building digitised twice (DUPLICATE)
  NC5 both insert distinct features      C5 neighbours moved into each other (OVERLAP)
  NC6 identical edit on both sides       C6 vertex edits that jointly self-intersect (INVALID)

The expected merged value of every feature in a clean episode is built directly from the
base geometry and the edit list (not with the merge tool), so it is an independent oracle.
"""
from __future__ import annotations

import copy
import random
from typing import Any

R = 3  # coordinates rounded to millimetres


def rnd(v: float) -> float:
    return round(v, R)


def rect_ring(x0: float, y0: float, w: float, h: float, kx: int, ky: int) -> list[list[float]]:
    """Closed rectangle ring, edges subdivided into kx (horizontal) and ky (vertical) parts."""
    pts = []
    for i in range(kx):
        pts.append([x0 + w * i / kx, y0])
    for i in range(ky):
        pts.append([x0 + w, y0 + h * i / ky])
    for i in range(kx):
        pts.append([x0 + w - w * i / kx, y0 + h])
    for i in range(ky):
        pts.append([x0, y0 + h - h * i / ky])
    pts = [[rnd(x), rnd(y)] for x, y in pts]
    return pts + [list(pts[0])]


class City:
    """Rows of rectangular buildings with gaps, roads between rows, POIs, and an empty
    'expansion zone' above the city where new features are digitised."""

    def __init__(self, n_buildings: int, seed: int) -> None:
        self.rng = random.Random(seed)
        r = self.rng
        self.ds: dict[str, Any] = {}
        self.rows: list[list[str]] = []
        per_row = max(4, int(n_buildings ** 0.5))
        y = 0.0
        made = 0
        while made < n_buildings:
            x, row = 0.0, []
            hmax = 0.0
            for _ in range(per_row):
                if made >= n_buildings:
                    break
                w, h = r.uniform(10, 30), r.uniform(10, 25)
                fid = self.new_id()
                self.ds[fid] = {"props": {"kind": "building", "use": r.choice(
                    ["residential", "retail", "office", "school", "industrial"]),
                    "height": rnd(r.uniform(3, 60)), "floors": r.randint(1, 15),
                    "name": f"B{made}"},
                    "geom": {"type": "Polygon", "coords": [rect_ring(
                        x, y, w, h, r.randint(2, 5), r.randint(1, 4))]}}
                row.append(fid)
                x += w + r.uniform(6, 12)
                hmax = max(hmax, h)
                made += 1
            self.rows.append(row)
            # a road along the row, then a POI
            ry = y + hmax + 7
            for seg in range(3):  # three road segments along the row
                n = r.randint(10, 40)
                x0, x1 = x * seg / 3, x * (seg + 1) / 3
                rid = self.new_id()
                self.ds[rid] = {"props": {"kind": "road", "class": r.choice(
                    ["primary", "secondary", "residential"]), "lanes": r.randint(1, 4),
                    "name": f"R{len(self.rows)}-{seg}"},
                    "geom": {"type": "LineString", "coords": [
                        [rnd(x0 + (x1 - x0) * i / (n - 1)), rnd(ry + r.uniform(-0.5, 0.5))]
                        for i in range(n)]}}
            pid = self.new_id()
            self.ds[pid] = {"props": {"kind": "poi", "category": r.choice(
                ["cafe", "bank", "pharmacy", "bus_stop"]), "name": f"P{len(self.rows)}"},
                "geom": {"type": "Point", "coords": [rnd(r.uniform(0, x)), rnd(ry + 3)]}}
            y = ry + 14
        self.top = y + 50
        self.width = max(sum(1 for _ in row) for row in self.rows) * 42
        self.next_slot = 0

    def new_id(self) -> str:
        return "%012x" % self.rng.getrandbits(48)

    def slot(self) -> tuple[float, float]:
        """A fresh location in the expansion zone, 60 m from any other slot."""
        i = self.next_slot
        self.next_slot += 1
        cols = max(1, int(self.width // 60))
        return 60.0 * (i % cols), self.top + 60.0 * (i // cols)


# ----------------------------------------------------------------------------- edits
def parts_of(g: dict[str, Any]) -> list[list[list[float]]]:
    if g["type"] == "Polygon":
        return [r[:-1] for r in g["coords"]]
    return [g["coords"]]


def build_geom(gtype: str, base_parts: list[list[list[float]]],
               moves: dict[int, list[float]], inserts: dict[int, list[list[float]]]
               ) -> dict[str, Any]:
    """Oracle: rebuild part 0 from base vertices, applying vertex moves (by base index) and
    inserting vertices on edges (edge i = after base vertex i)."""
    ring = []
    for i, p in enumerate(base_parts[0]):
        ring.append(list(moves.get(i, p)))
        ring.extend(list(q) for q in inserts.get(i, []))
    ps = [ring] + [list(map(list, r)) for r in base_parts[1:]]
    if gtype == "Polygon":
        return {"type": "Polygon", "coords": [r + [list(r[0])] for r in ps]}
    return {"type": "LineString", "coords": ps[0]}


class Scenario:
    def __init__(self, city: City, seed: int) -> None:
        self.city = city
        self.base = city.ds
        self.ours = copy.deepcopy(city.ds)
        self.theirs = copy.deepcopy(city.ds)
        self.rng = random.Random(seed)
        self.used: set[str] = set()
        self.episodes: list[dict[str, Any]] = []
        self.expected: dict[str, Any] = {}          # fid -> feature or None (deleted)

    # helpers ----------------------------------------------------------------
    def pick(self, kind: str, min_vertices: int = 0) -> str:
        cands = [k for k, f in self.base.items() if f["props"]["kind"] == kind and k not in self.used
                 and len(parts_of(f["geom"])[0]) >= min_vertices]
        fid = self.rng.choice(cands)
        self.used.add(fid)
        return fid

    def pick_neighbours(self) -> tuple[str, str]:
        while True:
            row = self.rng.choice([r for r in self.city.rows if len(r) >= 2])
            i = self.rng.randrange(len(row) - 1)
            a, b = row[i], row[i + 1]
            if a not in self.used and b not in self.used:
                self.used |= {a, b}
                return a, b

    def attr_value(self, fid: str, key: str, salt: int) -> Any:
        v = self.base[fid]["props"][key]
        return v + 1 + salt if isinstance(v, (int, float)) else f"{v}-edit{salt}"

    def episode(self, label: str, conflict: bool, ids: list[str]) -> None:
        self.episodes.append({"label": label, "conflict": conflict, "ids": ids})

    def vertex_edit(self, fid: str, kind: str, i: int, salt: int
                    ) -> tuple[dict[int, list[float]], dict[int, list[list[float]]]]:
        """A small vertex edit at base index i: 'move' vertex i, or 'insert' on edge i."""
        p = parts_of(self.base[fid]["geom"])[0]
        d = 0.3 + 0.2 * salt
        if kind == "move":
            return {i: [rnd(p[i][0] + d), rnd(p[i][1] - d)]}, {}
        j = (i + 1) % len(p)
        mid = [rnd((p[i][0] + p[j][0]) / 2 + d / 3), rnd((p[i][1] + p[j][1]) / 2 - d / 3)]
        return {}, {i: [mid]}

    def set_geom(self, branch: dict[str, Any], fid: str, moves: dict[int, list[float]],
                 inserts: dict[int, list[list[float]]]) -> None:
        g = self.base[fid]["geom"]
        branch[fid]["geom"] = build_geom(g["type"], parts_of(g), moves, inserts)

    # episode generators -------------------------------------------------------
    def nc1(self) -> None:
        a, b = self.pick("building"), self.pick("road")
        self.ours[a]["props"]["use"] = "mixed"
        self.theirs[b]["props"]["lanes"] = self.base[b]["props"]["lanes"] + 1
        self.expected[a] = copy.deepcopy(self.ours[a])
        self.expected[b] = copy.deepcopy(self.theirs[b])
        self.episode("NC1", False, [a, b])

    def nc2(self) -> None:
        a = self.pick("building")
        self.ours[a]["props"]["height"] = self.attr_value(a, "height", 0)
        self.theirs[a]["props"]["name"] = self.attr_value(a, "name", 0)
        e = copy.deepcopy(self.base[a])
        e["props"]["height"] = self.ours[a]["props"]["height"]
        e["props"]["name"] = self.theirs[a]["props"]["name"]
        self.expected[a] = e
        self.episode("NC2", False, [a])

    def nc3(self) -> None:
        kind = self.rng.choice(["building", "road"])
        a = self.pick(kind)
        n = len(parts_of(self.base[a]["geom"])[0])
        mv, ins = self.vertex_edit(a, "move", self.rng.randrange(n), 0)
        self.set_geom(self.ours, a, mv, ins)
        key = "use" if kind == "building" else "class"
        self.theirs[a]["props"][key] = self.attr_value(a, key, 0)
        e = copy.deepcopy(self.ours[a])
        e["props"][key] = self.theirs[a]["props"][key]
        self.expected[a] = e
        self.episode("NC3", False, [a])

    def nc4(self) -> None:
        kind = self.rng.choice(["building", "road"])
        a = self.pick(kind, min_vertices=8)
        n = len(parts_of(self.base[a]["geom"])[0])
        i = self.rng.randrange(n - 4) if kind == "road" else self.rng.randrange(n)
        j = (i + self.rng.randint(3, max(3, n // 2))) % n
        if kind == "road" and j < i:
            i, j = j, i
        ko, kt = self.rng.choice([("move", "move"), ("insert", "move"), ("move", "insert"),
                                  ("insert", "insert")])
        if kind == "road" and "insert" in (ko, kt):
            i, j = min(i, n - 2), min(j, n - 2)
            if abs(i - j) < 2:
                ko, kt = "move", "move"
        mo, io = self.vertex_edit(a, ko, i, 0)
        mt, it = self.vertex_edit(a, kt, j, 1)
        self.set_geom(self.ours, a, mo, io)
        self.set_geom(self.theirs, a, mt, it)
        e = copy.deepcopy(self.base[a])
        g = self.base[a]["geom"]
        e["geom"] = build_geom(g["type"], parts_of(g), {**mo, **mt}, {**io, **it})
        self.expected[a] = e
        self.episode("NC4", False, [a])

    def new_building(self, x: float, y: float, noise: float = 0.0) -> dict[str, Any]:
        ring = rect_ring(x, y, 20, 15, 2, 1)
        if noise:
            ring = [[rnd(p[0] + self.rng.uniform(-noise, noise)),
                     rnd(p[1] + self.rng.uniform(-noise, noise))] for p in ring[:-1]]
            ring.append(list(ring[0]))
        return {"props": {"kind": "building", "use": "residential", "height": 9.0,
                          "floors": 3, "name": "new"}, "geom": {"type": "Polygon", "coords": [ring]}}

    def nc5(self) -> None:
        a, b = self.city.new_id(), self.city.new_id()
        self.ours[a] = self.new_building(*self.city.slot())
        self.theirs[b] = self.new_building(*self.city.slot())
        self.expected[a], self.expected[b] = copy.deepcopy(self.ours[a]), copy.deepcopy(self.theirs[b])
        self.episode("NC5", False, [a, b])

    def nc6(self) -> None:
        a = self.pick("poi")
        for br in (self.ours, self.theirs):
            br[a]["props"]["category"] = "library"
        self.expected[a] = copy.deepcopy(self.ours[a])
        self.episode("NC6", False, [a])

    def c1(self) -> None:
        a = self.pick("building")
        self.ours[a]["props"]["floors"] = self.attr_value(a, "floors", 0)
        self.theirs[a]["props"]["floors"] = self.attr_value(a, "floors", 1)
        self.episode("C1", True, [a])

    def c2(self) -> None:
        kind = self.rng.choice(["building", "road"])
        a = self.pick(kind, min_vertices=4)
        n = len(parts_of(self.base[a]["geom"])[0])
        k = self.rng.choice(["move", "insert"])
        i = self.rng.randrange(n - 1)
        mo, io = self.vertex_edit(a, k, i, 0)
        mt, it = self.vertex_edit(a, k, i, 2)
        self.set_geom(self.ours, a, mo, io)
        self.set_geom(self.theirs, a, mt, it)
        self.episode("C2", True, [a])

    def c3(self) -> None:
        a = self.pick(self.rng.choice(["building", "road", "poi"]))
        del self.ours[a]
        self.theirs[a]["props"]["name"] = self.attr_value(a, "name", 3)
        self.episode("C3", True, [a])

    def c4(self) -> None:
        x, y = self.city.slot()
        a, b = self.city.new_id(), self.city.new_id()
        self.ours[a] = self.new_building(x, y, noise=0.3)
        self.theirs[b] = self.new_building(x, y, noise=0.3)
        self.episode("C4", True, [a, b])

    def c5(self) -> None:
        a, b = self.pick_neighbours()
        ra, rb = self.base[a]["geom"]["coords"][0], self.base[b]["geom"]["coords"][0]
        gap = min(p[0] for p in rb) - max(p[0] for p in ra)
        shift = 0.7 * gap
        for br, fid, dx in ((self.ours, a, shift), (self.theirs, b, -shift)):
            br[fid]["geom"] = {"type": "Polygon", "coords": [
                [[rnd(p[0] + dx), p[1]] for p in self.base[fid]["geom"]["coords"][0]]]}
        self.episode("C5", True, [a, b])

    def c6(self) -> None:
        """Push a top-edge vertex down and the bottom-edge vertex below it up; each alone
        leaves a valid polygon, together the edges cross."""
        while True:
            a = self.pick("building")
            ring = parts_of(self.base[a]["geom"])[0]
            ys = sorted({p[1] for p in ring})
            y0, y1 = ys[0], ys[-1]
            xs0 = {p[0]: k for k, p in enumerate(ring) if p[1] == y0}
            xs1 = {p[0]: k for k, p in enumerate(ring) if p[1] == y1}
            inner = [x for x in xs0 if x in xs1 and x != min(xs0) and x != max(xs0)]
            if inner:
                break
        x = self.rng.choice(inner)
        h = y1 - y0
        i_bot, i_top = xs0[x], xs1[x]
        self.set_geom(self.ours, a, {i_top: [x, rnd(y1 - 0.7 * h)]}, {})
        self.set_geom(self.theirs, a, {i_bot: [x, rnd(y0 + 0.7 * h)]}, {})
        self.episode("C6", True, [a])


GENERATORS = ["nc1", "nc2", "nc3", "nc4", "nc5", "nc6", "c1", "c2", "c3", "c4", "c5", "c6"]


def make_scenario(n_buildings: int, per_type: int, seed: int) -> Scenario:
    city = City(n_buildings, seed)
    sc = Scenario(city, seed + 1)
    order = [g for g in GENERATORS for _ in range(per_type)]
    sc.rng.shuffle(order)
    for g in order:
        getattr(sc, g)()
    return sc
