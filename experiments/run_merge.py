"""Experiment 1: conflict detection and merge correctness against ground truth.
Usage: python run_merge.py N_SEEDS N_BUILDINGS PER_TYPE"""
import copy
import os
import random
import sys
import time

import pandas as pd

from geo3way import merge
from geo3way.baselines import FORMATS, line_merge, serialise
from geo3way.diff import geom_equal
from simulator import make_scenario

N_SEEDS, N_BUILD, PER_TYPE = (int(a) for a in sys.argv[1:4])
CONDITIONS = ["clean", "reexport_order", "reexport_noise"]


def perturb(ds, rng, scale=1e-7):
    """Float noise of a re-projection round trip: every coordinate moves by < scale."""
    out = copy.deepcopy(ds)
    for f in out.values():
        g = f["geom"]
        if g["type"] == "Point":
            g["coords"] = [v + rng.uniform(-scale, scale) for v in g["coords"]]
        elif g["type"] == "LineString":
            g["coords"] = [[v + rng.uniform(-scale, scale) for v in p] for p in g["coords"]]
        else:
            rings = []
            for r in g["coords"]:
                rr = [[v + rng.uniform(-scale, scale) for v in p] for p in r[:-1]]
                rings.append(rr + [list(rr[0])])
            g["coords"] = rings
    return out
OUT = os.path.join(os.path.dirname(__file__), "..", "results")


def same_feature(a, b):
    return a is not None and b is not None and a["props"] == b["props"] and \
        geom_equal(a["geom"], b["geom"], 1e-3)


rows, val, feat = [], [], []
for seed in range(1, N_SEEDS + 1):
  sc = make_scenario(N_BUILD, PER_TYPE, seed)
  for cond in CONDITIONS:
    theirs, order = sc.theirs, None
    if cond == "reexport_noise":
        theirs = perturb(sc.theirs, random.Random(seed))
    if cond == "reexport_order":
        order = list(theirs)
        random.Random(seed).shuffle(order)
    methods = {}
    t = time.perf_counter()
    r = merge(sc.base, sc.ours, theirs)
    methods["geo3way"] = (r.conflicted_ids(), r.merged, time.perf_counter() - t)
    t = time.perf_counter()
    r0 = merge(sc.base, sc.ours, theirs, check_validity=False, duplicate_tol=None,
               check_overlaps=False)
    methods["geo3way-structural"] = (r0.conflicted_ids(), r0.merged, time.perf_counter() - t)
    for fmt in FORMATS:
        t = time.perf_counter()
        lm = line_merge(sc.base, sc.ours, theirs, fmt, theirs_order=order)
        methods[f"line:{fmt}"] = (lm["conflicted_ids"], lm["merged"], time.perf_counter() - t)
        val.append({"seed": seed, "condition": cond, "format": fmt,
                    "hunks_parsed": lm["n_conflicts"], "git_exit_code": lm["git_exit"],
                    "parsed": lm["merged"] is not None})
    truth_ids = {i for e in sc.episodes if e["conflict"] for i in e["ids"]}
    for m, (flagged, merged, secs) in methods.items():
        tp = len(flagged & truth_ids)
        feat.append({"seed": seed, "condition": cond, "method": m, "flagged": len(flagged),
                     "truth": len(truth_ids), "tp": tp, "seconds": secs,
                     "parsed": merged is not None})
        for e in sc.episodes:
            detected = bool(set(e["ids"]) & flagged)
            correct = None
            if not e["conflict"]:
                correct = merged is not None and all(
                    same_feature(merged.get(i), sc.expected[i]) for i in e["ids"])
            rows.append({"seed": seed, "condition": cond, "method": m, "label": e["label"],
                         "conflict": e["conflict"], "detected": detected, "correct": correct})
  print("seed", seed, "done", flush=True)

pd.DataFrame(rows).to_csv(f"{OUT}/merge_episodes.csv", index=False)
pd.DataFrame(val).to_csv(f"{OUT}/git_validation.csv", index=False)
pd.DataFrame(feat).to_csv(f"{OUT}/merge_features.csv", index=False)
