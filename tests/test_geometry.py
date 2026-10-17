import unittest

from geo3way.geometry import (
    hausdorff,
    point_in_ring,
    polygons_overlap,
    ring_is_simple,
    segments_cross,
    validity_errors,
)


def sq(x, y, s=1.0):
    return {"type": "Polygon", "coords": [[[x, y], [x + s, y], [x + s, y + s], [x, y + s], [x, y]]]}


class TestPrimitives(unittest.TestCase):
    def test_segments(self):
        self.assertTrue(segments_cross([0, 0], [2, 2], [0, 2], [2, 0]))
        self.assertFalse(segments_cross([0, 0], [1, 0], [0, 1], [1, 1]))
        self.assertTrue(segments_cross([0, 0], [2, 0], [1, 0], [3, 0]))  # collinear overlap
        self.assertTrue(segments_cross([0, 0], [1, 0], [1, 0], [1, 1]))  # touching end

    def test_valid_square_and_bowtie(self):
        self.assertEqual(validity_errors(sq(0, 0)), [])
        bowtie = {"type": "Polygon", "coords": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]}
        self.assertTrue(any("self-intersects" in e for e in validity_errors(bowtie)))

    def test_collinear_subdivided_ring_is_simple(self):
        ring = [[0, 0], [1, 0], [2, 0], [2, 1], [1, 1], [0, 1], [0, 0]]
        self.assertTrue(ring_is_simple(ring))

    def test_invalid_structures(self):
        self.assertTrue(validity_errors({"type": "Polygon", "coords": [[[0, 0], [1, 0], [0, 0]]]}))
        self.assertTrue(validity_errors({"type": "Polygon", "coords": [[[0, 0], [1, 0], [1, 1], [0, 1]]]}))
        self.assertTrue(validity_errors({"type": "Polygon", "coords": [[[0, 0], [1, 0], [2, 0], [0, 0]]]}))
        self.assertTrue(validity_errors({"type": "LineString", "coords": [[0, 0]]}))
        self.assertTrue(validity_errors({"type": "Point", "coords": [0, float("nan")]}))
        self.assertTrue(validity_errors({"type": "Polygon", "coords": []}))
        self.assertTrue(validity_errors({"type": "Circle", "coords": [0, 0]}))
        self.assertEqual(validity_errors({"type": "Point", "coords": [1.0, 2.0]}), [])

    def test_overlap(self):
        self.assertTrue(polygons_overlap(sq(0, 0), sq(0.5, 0.5)))
        self.assertFalse(polygons_overlap(sq(0, 0), sq(1, 0)))        # shared edge only
        self.assertFalse(polygons_overlap(sq(0, 0), sq(3, 3)))
        self.assertTrue(polygons_overlap(sq(0, 0, 4), sq(1, 1, 1)))   # containment
        self.assertTrue(polygons_overlap(sq(1, 1, 1), sq(0, 0, 4)))

    def test_overlap_degenerate_contacts(self):
        """Regression: found by the evaluation (seed 3). A vertex of A lies exactly on B's
        edge, so no edge pair crosses properly, yet the interiors overlap."""
        a = {"type": "Polygon", "coords": [[[260.795, 957.156], [264.144, 957.156],
             [267.494, 957.156], [270.843, 957.156], [274.192, 957.156], [277.542, 957.156],
             [277.542, 967.627], [274.192, 967.627], [270.843, 967.627], [267.494, 967.627],
             [264.144, 967.627], [260.795, 967.627], [260.795, 957.156]]]}
        b = {"type": "Polygon", "coords": [[[274.192, 957.156], [287.886, 957.156],
             [301.581, 957.156], [301.581, 971.743], [287.886, 971.743], [274.192, 971.743],
             [274.192, 957.156]]]}
        self.assertTrue(polygons_overlap(a, b))
        self.assertTrue(polygons_overlap(sq(0, 0), sq(0, 0)))          # identical
        cw = {"type": "Polygon", "coords": [list(reversed(sq(0, 0)["coords"][0]))]}
        self.assertTrue(polygons_overlap(cw, sq(0.5, 0)))               # clockwise ring
        self.assertFalse(polygons_overlap(cw, sq(1, 0)))                # shared edge only

    def test_point_in_ring_and_hausdorff(self):
        ring = sq(0, 0, 2)["coords"][0]
        self.assertTrue(point_in_ring([1, 1], ring))
        self.assertFalse(point_in_ring([3, 1], ring))
        self.assertAlmostEqual(hausdorff(sq(0, 0), sq(0.5, 0)), 0.5)
        p = {"type": "Point", "coords": [0, 0]}
        q = {"type": "LineString", "coords": [[3, 4], [0, 0]]}
        self.assertAlmostEqual(hausdorff(p, q), 5.0)


if __name__ == "__main__":
    unittest.main()
