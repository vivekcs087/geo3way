import copy
import random
import unittest

from geo3way import apply, datasets_equal, diff, merge, patch_bytes
from geo3way.diff import seq_hunks
from geo3way.merge import merge_sequences


def sq(x, y, s=10.0, k=3):
    pts = ([[x + s * i / k, y] for i in range(k)] + [[x + s, y + s * i / k] for i in range(k)]
           + [[x + s - s * i / k, y + s] for i in range(k)] + [[x, y + s - s * i / k] for i in range(k)])
    pts = [[round(a, 3), round(b, 3)] for a, b in pts]
    return {"type": "Polygon", "coords": [pts + [list(pts[0])]]}


def random_dataset(rng, n=30):
    ds = {}
    for i in range(n):
        kind = rng.choice(["Point", "LineString", "Polygon"])
        x, y = (i % 6) * 50.0, (i // 6) * 50.0
        if kind == "Point":
            g = {"type": "Point", "coords": [x, y]}
        elif kind == "LineString":
            g = {"type": "LineString", "coords": [[x + j, y + rng.uniform(0, 1)] for j in range(rng.randint(2, 12))]}
        else:
            g = sq(x, y)
        ds[f"f{i:03d}"] = {"props": {"a": rng.randint(0, 9), "b": rng.choice("xyz")}, "geom": g}
    return ds


def random_edit(rng, ds, tag):
    ds = copy.deepcopy(ds)
    for _ in range(rng.randint(1, 8)):
        if not ds:
            break
        fid = rng.choice(sorted(ds))
        f = ds[fid]
        op = rng.choice(["attr", "addattr", "delattr", "move", "insv", "delv", "del", "ins"])
        g = f["geom"]
        seq = g["coords"] if g["type"] == "LineString" else (
            g["coords"][0][:-1] if g["type"] == "Polygon" else None)
        if op == "attr":
            f["props"]["a"] = rng.randint(10, 99)
        elif op == "addattr":
            f["props"][f"new_{tag}"] = tag
        elif op == "delattr":
            f["props"].pop("b", None)
        elif op == "del":
            del ds[fid]
        elif op == "ins":
            pt = {"type": "Point", "coords": [rng.uniform(0, 5), 999.0]}
            ds[f"n_{tag}_{rng.randint(0, 10**6)}"] = {"props": {"a": 1}, "geom": pt}
        elif seq is not None and len(seq) > 4:
            i = rng.randrange(len(seq))
            if op == "move":
                seq[i] = [seq[i][0] + rng.uniform(0.1, 1), seq[i][1]]
            elif op == "insv":
                seq.insert(i, [seq[i][0] + 0.1, seq[i][1] + 0.1])
            elif op == "delv":
                del seq[i]
            if g["type"] == "Polygon":
                g["coords"][0] = seq + [list(seq[0])]
    return ds


class TestDiffProperties(unittest.TestCase):
    def test_apply_diff_roundtrip_random(self):
        """Property: apply(base, diff(base, new)) == new, on 200 random edit sequences."""
        for seed in range(200):
            rng = random.Random(seed)
            base = random_dataset(rng)
            new = random_edit(rng, base, "x")
            self.assertTrue(datasets_equal(apply(base, diff(base, new)), new), seed)

    def test_identity_has_empty_diff(self):
        base = random_dataset(random.Random(1))
        self.assertEqual(diff(base, copy.deepcopy(base)), [])
        self.assertEqual(patch_bytes([]), b"[]")

    def test_noise_below_tolerance_is_ignored(self):
        base = random_dataset(random.Random(2))
        noisy = copy.deepcopy(base)
        for f in noisy.values():
            g = f["geom"]
            if g["type"] == "Point":
                g["coords"] = [g["coords"][0] + 1e-7, g["coords"][1] - 1e-7]
        self.assertEqual(diff(base, noisy, eps=1e-3), [])
        self.assertNotEqual(diff(base, noisy, eps=1e-9), [])

    def test_vertex_hunks_are_local(self):
        a = [[float(i), 0.0] for i in range(100)]
        b = copy.deepcopy(a)
        b[50] = [50.0, 1.0]
        self.assertEqual(seq_hunks(a, b, 1e-3), [(50, 51, [[50.0, 1.0]])])

    def test_type_change_is_replacement(self):
        base = {"f": {"props": {}, "geom": {"type": "Point", "coords": [0, 0]}}}
        new = {"f": {"props": {}, "geom": {"type": "LineString", "coords": [[0, 0], [1, 1]]}}}
        ops = diff(base, new)
        self.assertIn("replace", ops[0]["geom"])
        self.assertTrue(datasets_equal(apply(base, ops), new))


class TestMergeLaws(unittest.TestCase):
    """Algebraic laws of a three-way merge, checked on random edits."""

    def test_laws_random(self):
        for seed in range(150):
            rng = random.Random(seed)
            b = random_dataset(rng)
            o, t = random_edit(rng, b, "o"), random_edit(rng, b, "t")
            # no change on one side -> result is the other side, cleanly
            r = merge(b, b, t)
            self.assertTrue(r.clean and datasets_equal(r.merged, t), seed)
            r = merge(b, o, b)
            self.assertTrue(r.clean and datasets_equal(r.merged, o), seed)
            # same change on both sides -> that change, cleanly
            r = merge(b, o, copy.deepcopy(o))
            self.assertTrue(r.clean and datasets_equal(r.merged, o), seed)
            # symmetry: swapping sides yields the same set of conflicted features
            self.assertEqual(merge(b, o, t).conflicted_ids(), merge(b, t, o).conflicted_ids(), seed)
            # a clean merge contains both sides' changes
            r = merge(b, o, t)
            if r.clean:
                for fid in set(o) | set(t):
                    if fid in r.merged and fid in o and fid not in b:
                        self.assertTrue(datasets_equal({fid: r.merged[fid]}, {fid: o[fid]}))


class TestMergeCases(unittest.TestCase):
    def setUp(self):
        self.b = {"A": {"props": {"h": 1, "n": "a"}, "geom": sq(0, 0)},
                  "B": {"props": {"h": 2}, "geom": sq(20, 0)},
                  "P": {"props": {"c": "x"}, "geom": {"type": "Point", "coords": [5, 50]}}}

    def edit(self, fn):
        d = copy.deepcopy(self.b)
        fn(d)
        return d

    def test_different_attributes_merge(self):
        o = self.edit(lambda d: d["A"]["props"].__setitem__("h", 5))
        t = self.edit(lambda d: d["A"]["props"].__setitem__("n", "z"))
        r = merge(self.b, o, t)
        self.assertTrue(r.clean)
        self.assertEqual(r.merged["A"]["props"], {"h": 5, "n": "z"})

    def test_same_attribute_conflicts(self):
        o = self.edit(lambda d: d["A"]["props"].__setitem__("h", 5))
        t = self.edit(lambda d: d["A"]["props"].__setitem__("h", 6))
        self.assertEqual([c.kind for c in merge(self.b, o, t).conflicts], ["ATTRIBUTE"])

    def test_attribute_removed_and_kept(self):
        o = self.edit(lambda d: d["A"]["props"].pop("n"))
        r = merge(self.b, o, self.b)
        self.assertNotIn("n", r.merged["A"]["props"])

    def test_distant_vertices_merge_and_same_vertex_conflicts(self):
        def mv(i, dy):
            def f(d):
                ring = d["A"]["geom"]["coords"][0]
                ring[i] = [ring[i][0], ring[i][1] + dy]
                if i == 0:
                    ring[-1] = list(ring[0])
            return f
        r = merge(self.b, self.edit(mv(1, 0.5)), self.edit(mv(7, -0.5)))
        self.assertTrue(r.clean)
        ring = r.merged["A"]["geom"]["coords"][0]
        self.assertEqual(ring[1][1], 0.5)
        self.assertEqual(ring[7][1], 10.0 - 0.5)
        r = merge(self.b, self.edit(mv(4, 0.5)), self.edit(mv(4, 0.7)))
        self.assertEqual([c.kind for c in r.conflicts], ["GEOMETRY"])

    def test_delete_modify_and_insert_insert(self):
        o = self.edit(lambda d: d.pop("P"))
        t = self.edit(lambda d: d["P"]["props"].__setitem__("c", "y"))
        self.assertEqual([c.kind for c in merge(self.b, o, t).conflicts], ["DELETE_MODIFY"])
        o = self.edit(lambda d: d.__setitem__("N", {"props": {}, "geom": {"type": "Point", "coords": [0, 90]}}))
        t = self.edit(lambda d: d.__setitem__("N", {"props": {}, "geom": {"type": "Point", "coords": [9, 90]}}))
        self.assertEqual([c.kind for c in merge(self.b, o, t).conflicts], ["INSERT_INSERT"])
        both_deleted = merge(self.b, self.edit(lambda d: d.pop("P")), self.edit(lambda d: d.pop("P")))
        self.assertTrue(both_deleted.clean)
        self.assertNotIn("P", both_deleted.merged)

    def test_geometry_type_conflict(self):
        o = self.edit(lambda d: d["P"].__setitem__("geom", {"type": "LineString", "coords": [[0, 0], [1, 1]]}))
        t = self.edit(lambda d: d["P"]["geom"].__setitem__("coords", [6, 50]))
        self.assertEqual([c.kind for c in merge(self.b, o, t).conflicts], ["GEOMETRY_TYPE"])

    def test_semantic_overlap(self):
        def shift(fid, dx):
            def f(d):
                d[fid]["geom"]["coords"][0] = [[p[0] + dx, p[1]] for p in d[fid]["geom"]["coords"][0]]
            return f
        o, t = self.edit(shift("A", 7)), self.edit(shift("B", -7))
        r = merge(self.b, o, t)
        self.assertEqual([c.kind for c in r.conflicts], ["OVERLAP"])
        self.assertTrue(merge(self.b, o, t, check_overlaps=False).clean)

    def test_semantic_invalid_geometry(self):
        def mv(i, y):
            def f(d):
                ring = d["A"]["geom"]["coords"][0]
                ring[i] = [ring[i][0], y]
            return f
        # vertex 1 is on the bottom edge (x=3.333), vertex 8 on the top edge (x=3.333)
        o, t = self.edit(mv(8, 3.0)), self.edit(mv(1, 7.0))
        self.assertTrue(merge(self.b, o, self.b).clean)
        r = merge(self.b, o, t)
        self.assertEqual([c.kind for c in r.conflicts], ["INVALID_GEOMETRY"])
        self.assertTrue(merge(self.b, o, t, check_validity=False).clean)

    def test_semantic_duplicate(self):
        g = sq(100, 100)
        g2 = copy.deepcopy(g)
        g2["coords"][0] = [[p[0] + 0.2, p[1]] for p in g2["coords"][0]]
        o = self.edit(lambda d: d.__setitem__("X1", {"props": {}, "geom": g}))
        t = self.edit(lambda d: d.__setitem__("X2", {"props": {}, "geom": g2}))
        kinds = [c.kind for c in merge(self.b, o, t).conflicts]
        self.assertEqual(kinds, ["DUPLICATE"])      # the overlap of the pair is not double-reported
        self.assertTrue(merge(self.b, o, t, duplicate_tol=None, check_overlaps=False).clean)


class TestSequenceMerge(unittest.TestCase):
    base = [[float(i), 0.0] for i in range(10)]

    def test_insert_same_point_conflicts(self):
        o = self.base[:5] + [[4.5, 1.0]] + self.base[5:]
        t = self.base[:5] + [[4.5, 2.0]] + self.base[5:]
        self.assertIsNone(merge_sequences(self.base, o, t, 1e-3)[0])

    def test_adjacent_edits_merge(self):
        o = copy.deepcopy(self.base)
        o[3] = [3.0, 1.0]
        t = copy.deepcopy(self.base)
        t[4] = [4.0, 1.0]
        m, _ = merge_sequences(self.base, o, t, 1e-3)
        self.assertEqual(m[3], [3.0, 1.0])
        self.assertEqual(m[4], [4.0, 1.0])

    def test_identical_edits_once(self):
        o = copy.deepcopy(self.base)
        o[2] = [2.0, 9.0]
        m, _ = merge_sequences(self.base, o, copy.deepcopy(o), 1e-3)
        self.assertEqual(m, o)


if __name__ == "__main__":
    unittest.main()
