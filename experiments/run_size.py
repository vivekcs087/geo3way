"""Experiment 2: size of a change record (patch) per edit class.
geo3way patch (compact JSON, and gzip) vs `git diff -U3` of each text serialisation."""
import copy
import gzip
import os
import random

import pandas as pd

from geo3way import diff, patch_bytes
from geo3way.baselines import FORMATS, text_diff_bytes, text_diff_gzip_bytes
from simulator import City, parts_of, rnd

OUT = os.path.join(os.path.dirname(__file__), "..", "results")
SEEDS = range(1, 11)
N_EDITS = [1, 10]


def edited(ds, cls, k, rng):
    """Return (new dataset, theirs order or None) after k edits of class cls."""
    new = copy.deepcopy(ds)
    order = None
    ids = sorted(new)
    if cls == "reexport_order":
        order = ids[:]
        rng.shuffle(order)
        return new, order
    if cls == "reexport_noise":
        for f in new.values():
            g = f["geom"]
            if g["type"] == "Point":
                g["coords"] = [v + rng.uniform(-1e-7, 1e-7) for v in g["coords"]]
            elif g["type"] == "LineString":
                g["coords"] = [[v + rng.uniform(-1e-7, 1e-7) for v in p] for p in g["coords"]]
            else:
                r = [[v + rng.uniform(-1e-7, 1e-7) for v in p] for p in g["coords"][0][:-1]]
                g["coords"] = [r + [list(r[0])]]
        return new, None
    for _ in range(k):
        if cls == "feature_insert":
            fid = "%012x" % rng.getrandbits(48)
            x, y = rng.uniform(0, 500), rng.uniform(2000, 3000)
            ring = [[rnd(x), rnd(y)], [rnd(x + 20), rnd(y)], [rnd(x + 20), rnd(y + 15)],
                    [rnd(x), rnd(y + 15)]]
            new[fid] = {"props": {"kind": "building", "use": "residential", "height": 9.0,
                                  "floors": 3, "name": "new"},
                        "geom": {"type": "Polygon", "coords": [ring + [list(ring[0])]]}}
            continue
        poly = [k2 for k2 in sorted(new) if new[k2]["geom"]["type"] == "Polygon"]
        fid = rng.choice(poly)
        f = new[fid]
        if cls == "attribute":
            f["props"]["height"] = rnd(f["props"]["height"] + 1.5)
        elif cls == "feature_delete":
            del new[fid]
        else:
            ring = parts_of(f["geom"])[0]
            i = rng.randrange(len(ring))
            if cls == "vertex_move":
                ring[i] = [rnd(ring[i][0] + 0.4), rnd(ring[i][1] - 0.4)]
            elif cls == "vertex_insert":
                j = (i + 1) % len(ring)
                ring.insert(i + 1, [rnd((ring[i][0] + ring[j][0]) / 2 + 0.3),
                                    rnd((ring[i][1] + ring[j][1]) / 2)])
            f["geom"]["coords"][0] = ring + [list(ring[0])]
    return new, order


CLASSES = ["attribute", "vertex_move", "vertex_insert", "feature_insert", "feature_delete",
           "reexport_noise", "reexport_order"]
rows = []
for seed in SEEDS:
    base = City(1000, seed).ds
    for cls in CLASSES:
        for k in (N_EDITS if not cls.startswith("reexport") else [1]):
            rng = random.Random(seed * 100 + k)
            new, order = edited(base, cls, k, rng)
            p = patch_bytes(diff(base, new))
            rows.append({"seed": seed, "class": cls, "k": k, "method": "geo3way",
                         "bytes": len(p), "gzip": len(gzip.compress(p, 9)) if len(p) > 2 else 0})
            for fmt in FORMATS:
                b = text_diff_bytes(base, new, fmt, None, order)
                gz = text_diff_gzip_bytes(base, new, fmt, None, order)
                rows.append({"seed": seed, "class": cls, "k": k, "method": f"git:{fmt}",
                             "bytes": b, "gzip": gz})
    print("seed", seed, flush=True)
pd.DataFrame(rows).to_csv(f"{OUT}/patch_size.csv", index=False)
