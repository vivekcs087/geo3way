import os

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

R = os.path.join(os.path.dirname(__file__), "..", "results")
F = os.path.join(R, "fig")
os.makedirs(F, exist_ok=True)
INK, INK2, GRID, BASE = "#0b0b0b", "#52514e", "#e1e0d9", "#c3c2b7"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": BASE,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                     "axes.spines.top": False, "axes.spines.right": False, "savefig.dpi": 300})
M = ["geo3way", "geo3way-structural", "line:geojson", "line:geojsonseq", "line:csvwkt"]
LAB = {"geo3way": "geo3way", "geo3way-structural": "geo3way (structural only)",
       "line:geojson": "git · pretty GeoJSON", "line:geojsonseq": "git · GeoJSONSeq",
       "line:csvwkt": "git · CSV+WKT"}
COL = {"geo3way": "#2a78d6", "geo3way-structural": "#86b6ef", "line:geojson": "#eb6834",
       "line:geojsonseq": "#1baf7a", "line:csvwkt": "#eda100"}
MK = {"geo3way": "o", "geo3way-structural": "o", "line:geojson": "s", "line:geojsonseq": "^",
      "line:csvwkt": "D"}

d = pd.read_csv(f"{R}/merge_episodes.csv")
f = pd.read_csv(f"{R}/merge_features.csv")
v = pd.read_csv(f"{R}/git_validation.csv")
for x in (d, f):
    x["method"] = x["method"].str.replace("geomerge", "geo3way")
d["correct"] = d["correct"].astype("boolean")


def ci(s):
    s = s.dropna()
    return s.mean(), 1.96 * s.std(ddof=1) / np.sqrt(len(s))


# ---------------------------------------------------------------- summary table
rows = []
for (c, m), g in d.groupby(["condition", "method"]):
    per = g.groupby("seed").apply(lambda s: pd.Series({
        "det": s[s.conflict].detected.mean(), "fc": s[~s.conflict].detected.mean(),
        "ok": s[~s.conflict].correct.astype(float).mean()}), include_groups=False)
    ff = f[(f.condition == c) & (f.method == m)]
    prec = (ff.tp / ff.flagged.replace(0, np.nan)).fillna(1.0)
    rec = ff.tp / ff.truth
    row = {"condition": c, "method": m}
    for k, s in (("detection", per.det), ("false_conflict", per.fc), ("clean_correct", per.ok),
                 ("precision", prec), ("recall", rec)):
        mu, h = ci(s)
        row[k], row[k + "_ci"] = mu, h
    row["parse_fail"] = 1 - ff.parsed.mean()
    row["median_s"] = ff.seconds.median()
    rows.append(row)
S = pd.DataFrame(rows)
S.to_csv(f"{R}/summary.csv", index=False)

by_type = d.groupby(["condition", "label", "method"]).agg(
    detected=("detected", "mean"), correct=("correct", lambda s: s.astype(float).mean()))
by_type.to_csv(f"{R}/by_type.csv")

# ---------------------------------------------------------------- Fig 2: per type, clean
clean = d[d.condition == "clean"]
fig, ax = plt.subplots(1, 2, figsize=(7.2, 3.3))
for k, (labels, col, title, xl) in enumerate([
        (["C1", "C2", "C3", "C4", "C5", "C6"], "detected", "(a) Conflict episodes: detected",
         "Share detected"),
        (["NC1", "NC2", "NC3", "NC4", "NC5", "NC6"], "correct", "(b) Clean episodes: merged correctly",
         "Share correct")]):
    a = ax[k]
    a.grid(axis="x", color=GRID, lw=0.5)
    a.set_axisbelow(True)
    for i, m in enumerate(M):
        sub = clean[(clean.method == m) & (clean.label.isin(labels))]
        vals = sub.groupby("label")[col].apply(lambda s: s.astype(float).mean()).reindex(labels)
        y = np.arange(len(labels)) + (i - 2) * 0.14
        a.scatter(vals.values, y, color=COL[m], marker=MK[m], s=22, label=LAB[m], zorder=3,
                  edgecolors="white", linewidths=0.4)
    a.set_yticks(range(len(labels)))
    a.set_yticklabels(labels)
    a.set_xlim(-0.05, 1.05)
    a.set_title(title, fontsize=9, color=INK, loc="left")
    a.set_xlabel(xl)
    a.invert_yaxis()
h, lab = ax[0].get_legend_handles_labels()
fig.legend(h, lab, loc="lower center", ncol=3, frameon=False, fontsize=8)
fig.tight_layout(rect=(0, 0.12, 1, 1))
fig.savefig(f"{F}/fig2_by_type.png")
plt.close(fig)

# ---------------------------------------------------------------- Fig 3: conditions
conds = ["clean", "reexport_order", "reexport_noise"]
cl = {"clean": "Clean", "reexport_order": "Re-export (order)", "reexport_noise": "Re-export (noise)"}
fig, ax = plt.subplots(1, 3, figsize=(7.2, 2.9), sharey=True)
for k, (met, title) in enumerate([("precision", "(a) Precision"), ("recall", "(b) Recall"),
                                  ("clean_correct", "(c) Clean episodes correct")]):
    a = ax[k]
    a.grid(axis="y", color=GRID, lw=0.5)
    a.set_axisbelow(True)
    for i, m in enumerate(M):
        sub = S[S.method == m].set_index("condition").loc[conds]
        x = np.arange(len(conds)) + (i - 2) * 0.15
        a.bar(x, sub[met], width=0.14, color=COL[m], label=LAB[m], yerr=sub[met + "_ci"],
              error_kw={"lw": 0.6, "ecolor": INK2})
    a.set_xticks(range(len(conds)))
    a.set_xticklabels([cl[c] for c in conds], fontsize=7.5, rotation=15)
    a.set_ylim(0, 1.05)
    a.set_title(title, fontsize=9, color=INK, loc="left")
h, lab = ax[0].get_legend_handles_labels()
fig.legend(h, lab, loc="lower center", ncol=3, frameon=False, fontsize=8)
fig.tight_layout(rect=(0, 0.14, 1, 1))
fig.savefig(f"{F}/fig3_conditions.png")
plt.close(fig)

# ---------------------------------------------------------------- Fig 4: patch size
p = pd.read_csv(f"{R}/patch_size.csv")
p["method"] = p["method"].str.replace("geomerge", "geo3way")
classes = ["attribute", "vertex_move", "vertex_insert", "feature_insert", "feature_delete",
           "reexport_order", "reexport_noise"]
cn = {"attribute": "Attribute", "vertex_move": "Vertex move", "vertex_insert": "Vertex insert",
      "feature_insert": "Feature insert", "feature_delete": "Feature delete",
      "reexport_order": "Re-export (order)", "reexport_noise": "Re-export (noise)"}
pm = ["geo3way", "git:geojson", "git:geojsonseq", "git:csvwkt"]
pl = {"geo3way": "geo3way", "git:geojson": "git diff · pretty GeoJSON",
      "git:geojsonseq": "git diff · GeoJSONSeq", "git:csvwkt": "git diff · CSV+WKT"}
pc = {"geo3way": COL["geo3way"], "git:geojson": COL["line:geojson"],
      "git:geojsonseq": COL["line:geojsonseq"], "git:csvwkt": COL["line:csvwkt"]}
sel = p[(p.k == 1)]
med = sel.groupby(["class", "method"])[["bytes", "gzip"]].median().reset_index()
med.to_csv(f"{R}/patch_size_median.csv", index=False)
p.groupby(["class", "k", "method"])[["bytes", "gzip"]].median().to_csv(f"{R}/patch_size_median_all.csv")
fig, ax = plt.subplots(figsize=(7.2, 3.2))
ax.grid(axis="x", color=GRID, lw=0.5)
ax.set_axisbelow(True)
for i, m in enumerate(pm):
    vals = med[med.method == m].set_index("class").reindex(classes)["gzip"].clip(lower=1)
    y = np.arange(len(classes)) + (i - 1.5) * 0.18
    ax.barh(y, vals, height=0.17, color=pc[m], label=pl[m])
ax.set_xscale("log")
ax.set_yticks(range(len(classes)))
ax.set_yticklabels([cn[c] for c in classes])
ax.invert_yaxis()
ax.set_xlabel("Change record size, gzip-compressed (bytes, log scale; 1 = empty)")
ax.legend(loc="upper center", bbox_to_anchor=(0.45, -0.2), ncol=2, frameon=False, fontsize=8)
fig.set_size_inches(7.2, 3.8)
fig.tight_layout()
fig.savefig(f"{F}/fig4_patch_size.png")
plt.close(fig)

# ---------------------------------------------------------------- Fig 5: runtime
t = pd.read_csv(f"{R}/runtime.csv")
t["method"] = t["method"].str.replace("geomerge", "geo3way")
tm = {"geo3way": "geo3way", "geo3way-structural": "geo3way (structural only)",
      "git:geojsonseq": "git · GeoJSONSeq", "git:geojson": "git · pretty GeoJSON"}
tc = {"geo3way": COL["geo3way"], "geo3way-structural": COL["geo3way-structural"],
      "git:geojsonseq": COL["line:geojsonseq"], "git:geojson": COL["line:geojson"]}
fig, ax = plt.subplots(figsize=(4.6, 3.0))
ax.grid(color=GRID, lw=0.5)
ax.set_axisbelow(True)
for m in tm:
    s = t[t.method == m]
    ax.plot(s.features, s.seconds, marker=MK.get(m.replace("git:", "line:"), "o"), color=tc[m],
            lw=2, ms=5, label=tm[m])
ax.set_xscale("log")
ax.set_yscale("log")
ax.set_xlabel("Features in dataset")
ax.set_ylabel("Merge time (s)")
ax.legend(frameon=False, fontsize=7.5)
fig.tight_layout()
fig.savefig(f"{F}/fig5_runtime.png")
plt.close(fig)

pd.set_option("display.width", 250)
print(S.round(3).to_string())
print(v.groupby(["condition", "format"]).apply(
    lambda g: pd.Series({"hunks_eq_git": (g.hunks_parsed == g.git_exit_code).mean(),
                         "parsed": g.parsed.mean(), "median_hunks": g.git_exit_code.median()}),
    include_groups=False))
print(med.pivot(index="class", columns="method", values=["bytes", "gzip"]).round(0))
print(t.pivot(index="features", columns="method", values="seconds").round(3))
