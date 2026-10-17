import copy
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

from geo3way import load, merge, save
from geo3way.cli import main
from geo3way.geometry import hausdorff, polygons_overlap, validity_errors
from geo3way.io import FormatError, _read_wkb, _write_wkb, gpkg_to_geom
from gpkg_fixture import make_gpkg

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(HERE, "..", "examples")
SRC = os.path.join(HERE, "..", "src")


def sq(x, y, s=10.0):
    return [[x, y], [x + s, y], [x + s, y + s], [x, y + s], [x, y]]


def fc(features, **top):
    return {"type": "FeatureCollection", **top, "features": features}


def feat(fid, props, gtype, coords, **extra):
    f = {"type": "Feature", "id": fid, **extra, "properties": props,
         "geometry": {"type": gtype, "coordinates": coords}}
    return f


def run(*argv):
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Tmp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d)

    def p(self, name):
        return os.path.join(self.d, name)

    def write(self, name, doc):
        with open(self.p(name), "w") as fh:
            json.dump(doc, fh)
        return self.p(name)


class TestMultiGeometry(unittest.TestCase):
    def test_multipolygon_members_merge_independently(self):
        g = {"type": "MultiPolygon", "coords": [[sq(0, 0)], [sq(20, 0)]]}
        b = {"a": {"props": {}, "geom": g}}
        o, t = copy.deepcopy(b), copy.deepcopy(b)
        o["a"]["geom"]["coords"][0][0][2] = [11, 10]      # member 0
        t["a"]["geom"]["coords"][1][0][1] = [31, 0]       # member 1
        r = merge(b, o, t)
        self.assertTrue(r.clean, r.conflicts)
        c = r.merged["a"]["geom"]["coords"]
        self.assertEqual((c[0][0][2], c[1][0][1]), ([11, 10], [31, 0]))
        self.assertEqual(c[1][0][0], c[1][0][-1])          # rings stay closed

    def test_layout_change_on_both_sides_conflicts(self):
        g = {"type": "MultiPolygon", "coords": [[sq(0, 0)], [sq(20, 0)]]}
        b = {"a": {"props": {}, "geom": g}}
        o, t = copy.deepcopy(b), copy.deepcopy(b)
        o["a"]["geom"]["coords"].append([sq(40, 0)])
        t["a"]["geom"]["coords"][0][0][2] = [11, 10]
        self.assertEqual([c.kind for c in merge(b, o, t).conflicts], ["GEOMETRY_TYPE"])

    def test_holes_and_multipolygon_overlap(self):
        donut = {"type": "Polygon", "coords": [sq(0, 0, 30), list(reversed(sq(10, 10)))]}
        inside_hole = {"type": "Polygon", "coords": [sq(12, 12, 5)]}
        self.assertFalse(polygons_overlap(donut, inside_hole))
        self.assertTrue(polygons_overlap(donut, {"type": "Polygon", "coords": [sq(5, 5, 4)]}))
        mp = {"type": "MultiPolygon", "coords": [[sq(100, 100)], [sq(1, 1, 3)]]}
        self.assertTrue(polygons_overlap(donut, mp))

    def test_multi_validity(self):
        self.assertEqual(validity_errors({"type": "MultiPolygon", "coords": [[sq(0, 0)]]}), [])
        bow = [[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]
        e = validity_errors({"type": "MultiPolygon", "coords": [[sq(5, 5)], [bow]]})
        self.assertTrue(any("polygon 1" in x for x in e))
        self.assertTrue(validity_errors({"type": "MultiLineString", "coords": [[[0, 0]]]}))
        self.assertTrue(validity_errors({"type": "MultiPoint", "coords": []}))

    def test_hausdorff_ignores_extra_vertices_on_edges(self):
        a = {"type": "Polygon", "coords": [sq(0, 0)]}
        b = {"type": "Polygon", "coords": [[[0, 0], [5, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]}
        self.assertAlmostEqual(hausdorff(a, b), 0.0)
        c = {"type": "Polygon", "coords": [sq(0.3, 0)]}
        self.assertAlmostEqual(hausdorff(a, c, step=0.1), 0.3)


class TestWKB(unittest.TestCase):
    def test_roundtrip_all_types_2d_and_3d(self):
        geoms = [{"type": "Point", "coords": [1.5, 2.5]},
                 {"type": "LineString", "coords": [[0, 0], [1, 2], [3, 1]]},
                 {"type": "Polygon", "coords": [sq(0, 0), list(reversed(sq(2, 2, 3)))]},
                 {"type": "MultiPoint", "coords": [[0, 0], [5, 5]]},
                 {"type": "MultiLineString", "coords": [[[0, 0], [1, 1]], [[2, 2], [3, 3]]]},
                 {"type": "MultiPolygon", "coords": [[sq(0, 0)], [sq(20, 0), sq(22, 2, 2)]]},
                 {"type": "LineString", "coords": [[0, 0, 5], [1, 2, 6]]}]
        def floats(x):
            return [floats(v) for v in x] if isinstance(x, list) else float(x)
        for g in geoms:
            blob = _write_wkb(g)
            back, end = _read_wkb(blob)
            self.assertEqual(end, len(blob))
            self.assertEqual(back, {"type": g["type"], "coords": floats(g["coords"])})

    def test_ewkb_flags(self):
        import struct
        ewkb = struct.pack("<BIi3d", 1, 0x80000001 | 0x20000000, 4326, 1.0, 2.0, 3.0)
        self.assertEqual(_read_wkb(ewkb)[0], {"type": "Point", "coords": [1.0, 2.0, 3.0]})

    def test_empty_gpkg_blob(self):
        self.assertIsNone(gpkg_to_geom(b"GP\x00\x11" + b"\x00" * 4 + b"\x01"))


class TestGeoJSON(Tmp):
    def test_roundtrip_keeps_crs_order_extras_and_int_ids(self):
        crs = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::25832"}}
        doc = fc([feat(7, {"a": 1}, "Point", [1, 2], bbox=[1, 2, 1, 2]),
                  feat("x", {"a": None}, "LineString", [[0, 0], [1, 1]])], name="t", crs=crs)
        ds, meta = load(self.write("in.geojson", doc))
        self.assertEqual(list(ds), ["7", "x"])
        self.assertEqual(meta.crs_name, "urn:ogc:def:crs:EPSG::25832")
        save(ds, self.p("out.geojson"), meta)
        with open(self.p("out.geojson")) as fh:
            back = json.load(fh)
        self.assertEqual(back, doc)

    def test_geojsonseq_and_id_field(self):
        with open(self.p("in.geojsonl"), "w") as fh:
            for i in range(3):
                fh.write("\x1e" + json.dumps(feat(None, {"uid": f"u{i}"}, "Point", [i, i])) + "\n")
        ds, meta = load(self.p("in.geojsonl"), id_field="uid")
        self.assertEqual(sorted(ds), ["u0", "u1", "u2"])
        save(ds, self.p("out.geojsonl"), meta)
        self.assertEqual(load(self.p("out.geojsonl"), id_field="uid")[0], ds)

    def test_clear_errors(self):
        cases = {"noid": fc([feat(None, {}, "Point", [0, 0])]),
                 "dup": fc([feat(1, {}, "Point", [0, 0]), feat(1, {}, "Point", [1, 1])]),
                 "null": fc([{"type": "Feature", "id": 1, "properties": {}, "geometry": None}]),
                 "gc": fc([feat(1, {}, "GeometryCollection", [])]),
                 "notfc": {"type": "Feature"}}
        for name, doc in cases.items():
            with self.assertRaises(FormatError, msg=name):
                load(self.write(name + ".geojson", doc))
        with self.assertRaises(FormatError):
            load(self.p("x.shp"))


class TestGeoPackage(Tmp):
    def rows(self):
        return [(1, {"name": "a", "height": 9.0}, {"type": "Polygon", "coords": [sq(0, 0)]}),
                (2, {"name": "b", "height": 6.0}, {"type": "Polygon", "coords": [sq(20, 0)]}),
                (3, {"name": "c", "height": 3.0},
                 {"type": "MultiPolygon", "coords": [[sq(40, 0)], [sq(60, 0)]]})]

    def test_load_merge_save_gpkg(self):
        rows = self.rows()
        make_gpkg(self.p("base.gpkg"), rows)
        o = copy.deepcopy(rows)
        o[0][2]["coords"][0][2] = [11.0, 10.0]
        o[1][1]["height"] = 7.0
        t = copy.deepcopy(rows)
        t[0][2]["coords"][0][0] = t[0][2]["coords"][0][4] = [-1.0, 0.0]
        t = [r for r in t if r[0] != 3]                          # theirs deletes fid 3
        make_gpkg(self.p("ours.gpkg"), o)
        make_gpkg(self.p("theirs.gpkg"), t)
        ds, meta = load(self.p("base.gpkg"))
        self.assertEqual((meta.layer, meta.pk, meta.crs_name), ("buildings", "fid", "EPSG:25832"))
        self.assertEqual(ds["3"]["geom"]["type"], "MultiPolygon")
        code, _, err = run("merge", self.p("base.gpkg"), self.p("ours.gpkg"),
                           self.p("theirs.gpkg"), "-o", self.p("m.gpkg"))
        self.assertEqual(code, 0, err)
        m, _ = load(self.p("m.gpkg"))
        self.assertEqual(sorted(m), ["1", "2"])
        self.assertEqual(m["1"]["geom"]["coords"][0][0], [-1.0, 0.0])
        self.assertEqual(m["1"]["geom"]["coords"][0][2], [11.0, 10.0])
        self.assertEqual(m["2"]["props"]["height"], 7.0)
        con = sqlite3.connect(self.p("m.gpkg"))                  # spatial index kept in sync
        self.assertEqual(sorted(r[0] for r in con.execute(
            "SELECT id FROM rtree_buildings_geom")), [1, 2])
        self.assertEqual(con.execute("SELECT minx FROM rtree_buildings_geom WHERE id=1")
                         .fetchone()[0], -1.0)
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        con.close()

    def test_new_attribute_column_and_add_ids(self):
        rows = self.rows()
        make_gpkg(self.p("b.gpkg"), rows)
        ds, meta = load(self.p("b.gpkg"))
        ds["1"]["props"]["roof"] = "flat"
        save(ds, self.p("o.gpkg"), meta)
        self.assertEqual(load(self.p("o.gpkg"))[0]["1"]["props"]["roof"], "flat")
        code, _, _ = run("add-ids", self.p("o.gpkg"))
        self.assertEqual(code, 0)
        u, _ = load(self.p("o.gpkg"), id_field="uuid")
        self.assertEqual(len(u), 3)
        self.assertTrue(all(len(k) == 36 for k in u))

    def test_layer_errors(self):
        make_gpkg(self.p("b.gpkg"), self.rows())
        with self.assertRaises(FormatError):
            load(self.p("b.gpkg"), layer="nope")
        with self.assertRaises(FormatError):
            load(self.p("b.gpkg"), id_field="nope")


class TestCLI(Tmp):
    def ex(self, n):
        return os.path.join(EXAMPLES, n)

    def test_example_merge_and_reports(self):
        for rep in ("c.geojson", "c.csv", "c.json"):
            code, _, err = run("merge", self.ex("base.geojson"), self.ex("alice.geojson"),
                               self.ex("bob.geojson"), "-o", self.p("m.geojson"),
                               "-r", self.p(rep))
            self.assertEqual(code, 1, err)
        with open(self.p("c.json")) as fh:
            kinds = sorted({r["kind"] for r in json.load(fh)["conflicts"]})
        self.assertEqual(kinds, ["ATTRIBUTE", "DUPLICATE", "OVERLAP"])
        with open(self.p("c.geojson")) as fh:
            rep = json.load(fh)
        self.assertEqual(rep["crs"]["properties"]["name"], "urn:ogc:def:crs:EPSG::25832")
        self.assertTrue(all(f["geometry"] for f in rep["features"]))
        m, _ = load(self.p("m.geojson"))
        self.assertNotIn("b4", m)                                     # bob's delete applied
        self.assertEqual(m["b1"]["props"]["floors"], 4)               # bob's attribute
        self.assertEqual(m["b1"]["geom"]["coords"][0][2][0], 500021.5)  # alice's vertex
        self.assertEqual(m["b2"]["props"]["height"], 7.5)             # conflict -> ours
        self.assertEqual(m["r1"]["props"]["name"], "Mill Lane North")
        self.assertEqual(len(m["r1"]["geom"]["coords"]), 4)
        run("merge", self.ex("base.geojson"), self.ex("alice.geojson"), self.ex("bob.geojson"),
            "-o", self.p("t.geojson"), "--prefer", "theirs", "-q")
        self.assertEqual(load(self.p("t.geojson"))[0]["b2"]["props"]["height"], 6.5)

    def test_diff_validate_add_ids(self):
        code, out, _ = run("diff", self.ex("base.geojson"), self.ex("bob.geojson"))
        self.assertEqual(code, 1)
        self.assertIn("- b4", out)
        self.assertIn("+ shed-b", out)
        self.assertEqual(run("diff", self.ex("base.geojson"), self.ex("base.geojson"))[0], 0)
        code, out, _ = run("diff", self.ex("base.geojson"), self.ex("bob.geojson"), "--json")
        self.assertEqual(len(json.loads(out)), 6)
        bad = self.write("bad.geojson", fc([feat("z", {}, "Polygon",
                                                 [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]])]))
        code, out, _ = run("validate", bad)
        self.assertEqual(code, 1)
        self.assertIn("self-intersects", out)
        p = self.write("noid.geojson", fc([feat(None, {}, "Point", [0, 0])] * 2))
        self.assertEqual(run("validate", p)[0], 2)                   # unreadable: no ids
        self.assertEqual(run("add-ids", p)[0], 0)
        self.assertEqual(run("validate", p)[0], 0)

    def test_geographic_tolerances(self):
        b = fc([feat("a", {}, "Point", [8.5, 47.3])])
        o = fc([feat("a", {}, "Point", [8.5000001, 47.3])])           # ~1 cm move
        t = fc([feat("a", {}, "Point", [8.5, 47.3000002])])
        code, _, err = run("merge", self.write("b.geojson", b), self.write("o.geojson", o),
                           self.write("t.geojson", t), "-o", self.p("m.geojson"))
        self.assertIn("degrees", err)
        self.assertEqual(code, 1)                                     # both moved the point

    def test_errors_exit_2(self):
        self.assertEqual(run("diff", self.p("missing.geojson"), self.p("x.geojson"))[0], 2)


@unittest.skipUnless(shutil.which("git"), "git not installed")
class TestGitIntegration(Tmp):
    def git(self, *a, check=True):
        env = {**os.environ, "PYTHONPATH": os.path.abspath(SRC), "GIT_AUTHOR_NAME": "t",
               "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@example.org"}
        return subprocess.run(["git", *a], cwd=self.d, env=env, capture_output=True, text=True,
                              check=check)

    def test_git_merge_uses_geo3way(self):
        self.git("init", "-q", "-b", "main")
        env = {**os.environ, "PYTHONPATH": os.path.abspath(SRC)}
        subprocess.run([sys.executable, "-m", "geo3way", "install-git", "*.geojson"],
                       cwd=self.d, env=env, check=True, capture_output=True)
        with open(self.p(".gitattributes")) as fh:
            self.assertIn("*.geojson merge=geo3way diff=geo3way", fh.read())
        shutil.copy(os.path.join(EXAMPLES, "base.geojson"), self.p("city.geojson"))
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.git("checkout", "-qb", "bob")
        ds, meta = load(self.p("city.geojson"))
        ds["b1"]["geom"]["coords"][0][5] = [500000, 5700013]       # bob: one corner of b1
        ds["b1"]["props"]["floors"] = 4
        save(ds, self.p("city.geojson"), meta)
        self.git("commit", "-qam", "bob")
        self.git("checkout", "-q", "main")
        ds, meta = load(self.p("city.geojson"))
        ds["b1"]["geom"]["coords"][0][2] = [500021.5, 5700000]     # alice: another corner
        ds["b1"]["props"]["use"] = "mixed"
        save(ds, self.p("city.geojson"), meta)
        self.git("commit", "-qam", "alice")
        diff = self.git("diff", "HEAD~1", "--", "city.geojson").stdout
        self.assertIn("~ b1", diff)
        r = self.git("merge", "-q", "bob", "-m", "merge", check=False)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)     # plain git would conflict
        m, _ = load(self.p("city.geojson"))
        self.assertEqual(m["b1"]["props"], {"use": "mixed", "height": 9.0, "floors": 4})
        self.assertEqual(m["b1"]["geom"]["coords"][0][2], [500021.5, 5700000])
        self.assertEqual(m["b1"]["geom"]["coords"][0][5], [500000, 5700013])

        self.git("checkout", "-qb", "carol", "HEAD~1")             # true conflict
        ds, meta = load(self.p("city.geojson"))
        ds["b2"]["props"]["height"] = 1.0
        save(ds, self.p("city.geojson"), meta)
        self.git("commit", "-qam", "carol")
        self.git("checkout", "-q", "main")
        ds, meta = load(self.p("city.geojson"))
        ds["b2"]["props"]["height"] = 2.0
        save(ds, self.p("city.geojson"), meta)
        self.git("commit", "-qam", "dave")
        r = self.git("merge", "carol", "-m", "merge2", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("geo3way: 1 conflict", r.stderr + r.stdout)
        self.assertTrue(os.path.exists(self.p("city.geojson.conflicts.geojson")))
        load(self.p("city.geojson"))                                # file is still valid GeoJSON


if __name__ == "__main__":
    unittest.main()
