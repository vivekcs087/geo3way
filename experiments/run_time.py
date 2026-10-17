"""Experiment 3: merge runtime versus dataset size (run alone, on an idle machine)."""
import os
import time

import numpy as np
import pandas as pd

from geo3way import merge
from geo3way.baselines import line_merge
from simulator import make_scenario

OUT = os.path.join(os.path.dirname(__file__), "..", "results")
rows = []
for n in [500, 1000, 2000, 5000, 10000, 20000]:
    sc = make_scenario(n, 10, 7)
    feats = len(sc.base)
    runs = {
        "geo3way": lambda: merge(sc.base, sc.ours, sc.theirs),
        "geo3way-structural": lambda: merge(sc.base, sc.ours, sc.theirs, check_validity=False,
                                             duplicate_tol=None, check_overlaps=False),
        "git:geojsonseq": lambda: line_merge(sc.base, sc.ours, sc.theirs, "geojsonseq"),
        "git:geojson": lambda: line_merge(sc.base, sc.ours, sc.theirs, "geojson"),
    }
    for name, fn in runs.items():
        ts = []
        for _ in range(3):
            t = time.perf_counter()
            fn()
            ts.append(time.perf_counter() - t)
        rows.append({"n_buildings": n, "features": feats, "method": name,
                     "seconds": float(np.median(ts))})
        print(n, feats, name, round(float(np.median(ts)), 3), flush=True)
pd.DataFrame(rows).to_csv(f"{OUT}/runtime.csv", index=False)
