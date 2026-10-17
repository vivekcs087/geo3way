import random
import shutil
import unittest

from geo3way import datasets_equal
from geo3way.baselines import FORMATS, line_merge, parse, serialise
from test_diff_merge import random_dataset, random_edit


class TestSerialisation(unittest.TestCase):
    def test_roundtrip_all_formats(self):
        ds = random_dataset(random.Random(3))
        for fmt in FORMATS:
            lines, owner = serialise(ds, fmt)
            self.assertEqual(len(lines), len(owner))
            self.assertTrue(datasets_equal(parse(lines, fmt), ds), fmt)

    def test_owner_map_covers_every_feature(self):
        ds = random_dataset(random.Random(4))
        for fmt in FORMATS:
            _, owner = serialise(ds, fmt)
            self.assertEqual({o for o in owner if o}, set(ds))

    def test_parse_rejects_garbage(self):
        for fmt in FORMATS:
            with self.assertRaises(ValueError):
                parse(["<<<<<<< ours", "{"], fmt)


@unittest.skipUnless(shutil.which("git"), "git not installed")
class TestGitBaseline(unittest.TestCase):
    def test_identical_branches_merge_cleanly(self):
        ds = random_dataset(random.Random(5))
        for fmt in FORMATS:
            r = line_merge(ds, ds, ds, fmt)
            self.assertEqual(r["n_conflicts"], 0)
            self.assertTrue(datasets_equal(r["merged"], ds))

    def test_hunk_count_matches_git_exit_code(self):
        for seed in range(10):
            rng = random.Random(seed)
            b = random_dataset(rng)
            o, t = random_edit(rng, b, "o"), random_edit(rng, b, "t")
            for fmt in FORMATS:
                r = line_merge(b, o, t, fmt)
                self.assertEqual(r["n_conflicts"], r["git_exit"], (seed, fmt))


if __name__ == "__main__":
    unittest.main()
