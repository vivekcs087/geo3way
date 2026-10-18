# geo3way

**Geometry-aware diff and three-way merge for vector GIS data.**
Merge two people's edits of the same GeoJSON or GeoPackage layer the way `git merge` merges
code, but feature by feature, attribute by attribute and vertex by vertex. Then check the
result for problems that only appear *after* merging: invalid geometries, duplicated features
and new overlaps.

```text
$ geo3way merge base.geojson alice.geojson bob.geojson -o merged.geojson -r conflicts.geojson
merged 6 features -> merged.geojson
3 conflict(s): ATTRIBUTE 1, DUPLICATE 1, OVERLAP 1
  ATTRIBUTE        b2  height
  DUPLICATE        shed-a, shed-b  Hausdorff <= 1.0
  OVERLAP          b2, b3  polygons overlap only after merging
report: conflicts.geojson
```

* No dependencies: pure Python 3.10+, standard library only.
* Reads and writes **GeoJSON**, **GeoJSON Text Sequences** (`.geojsonl`) and **GeoPackage**
  (`.gpkg`, the QGIS default). The CRS, feature order, schema and spatial index are kept.
* Works as a **git merge driver**, so `git merge` of map data simply works.
* Point, LineString, Polygon (with holes), MultiPoint, MultiLineString, MultiPolygon.

## Why

Line-based tools (git, diff) see GeoJSON as text. Two people who edit *different corners of
the same building* get a merge conflict. Re-saving a file in another program (new feature
order, last-digit float noise) turns into a diff of the whole file. And a "clean" text merge
can still produce a self-intersecting polygon, or two buildings on top of each other.

geo3way compares features by id, coordinates within a tolerance (default 1 mm), and checks
the merged result spatially. On the benchmark in the paper (3,600 edit scenarios), it found
100% of the true conflicts with no false conflicts. Line-based merging of GeoJSON missed or
over-reported conflicts and broke completely on re-exported files. See [Citing](#citing).

## Install

```bash
pip install geo3way                                  # once published on PyPI
pip install git+https://github.com/vivekcs087/geo3way     # latest from GitHub
```

## Quick start

```bash
cd examples
geo3way diff base.geojson bob.geojson          # what did Bob change?
geo3way merge base.geojson alice.geojson bob.geojson -o merged.geojson -r conflicts.geojson
geo3way validate merged.geojson                # any invalid geometries?
```

* `merge` exits with **0** when the merge is clean, **1** when there are conflicts, and
  **2** on errors, so it can be used in scripts and CI.
* Conflicted features keep *our* version (`--prefer theirs` for the other side). Every
  conflict is listed in the report.
* Open `conflicts.geojson` in QGIS next to the data to see where the problems are. Each
  feature has `kind`, `id`, `attribute` and the `base` / `ours` / `theirs` values. Reports can
  also be `.csv` or `.json`.

### Use it with git

```bash
cd my-map-repo
geo3way install-git                 # *.geojson, *.geojsonl, *.gpkg; or name your own patterns
git add .gitattributes && git commit -m "Use geo3way for map data"
```

From then on `git merge` and `git diff` use geo3way for those files. A clean merge needs no
intervention. With conflicts, git stops as usual, the file holds the merged data (our version
for conflicted features), and `<file>.conflicts.geojson` shows what to review. Fix, `git add`,
`git commit`. Every collaborator runs `geo3way install-git` once, because git does not run
merge drivers from a cloned repository by itself. See [docs/git.md](docs/git.md).

### Python

```python
from geo3way import load, save, merge, diff

base, meta = load("base.gpkg")
ours, _ = load("ours.gpkg")
theirs, _ = load("theirs.gpkg")
result = merge(base, ours, theirs, eps=1e-3, duplicate_tol=1.0)
for c in result.conflicts:
    print(c.kind, c.ids, c.detail)
save(result.merged, "merged.gpkg", meta)

# or in one call, with tolerances chosen from the data:
from geo3way import merge_files
merge_files("base.geojson", "ours.geojson", "theirs.geojson", "merged.geojson",
            report="conflicts.csv")
```

A dataset is a plain dict `{feature_id: {"props": {...}, "geom": {"type": ..., "coords": ...}}}`,
so data can come from anywhere, such as a database query or another library.

## Conflicts it detects

| Kind | Meaning |
|---|---|
| `ATTRIBUTE` | both sides set the same attribute of a feature to different values |
| `GEOMETRY` | both sides edited the same vertices of a feature differently |
| `GEOMETRY_TYPE` | both changed a geometry and one changed its type or number of parts/rings |
| `DELETE_MODIFY` | one side deleted a feature the other side modified |
| `INSERT_INSERT` | both sides added different features with the same id |
| `INVALID_GEOMETRY` | the combined geometry is invalid, although each side's version was valid |
| `DUPLICATE` | both sides added (with different ids) what is spatially the same object |
| `OVERLAP` | two polygons overlap in the merge but in none of the three inputs |

Edits to different attributes, or different vertex ranges, of the same feature merge
automatically.

## Requirements and limits

* **Stable feature ids.** Every feature needs an id that survives editing: the GeoJSON
  `id` member, a property (`--id-field`), or the GeoPackage primary key. Auto-numbered keys
  can collide when two people add features, so for team editing use a UUID column.
  `geo3way add-ids` creates one.
* **Coordinates.** Tolerances are in data units. The defaults (1 mm, duplicate distance 1 m)
  assume metres. When all coordinates look like longitude/latitude, geo3way switches to
  1e-8° / 1e-5° and says so. A projected CRS is recommended.
* **Formats.** Shapefile, FlatGeobuf, PostGIS and others are not read directly. Convert with
  `ogr2ogr` or QGIS (*Export → Save Features As… → GeoPackage*). GeoPackage output uses an
  input GeoPackage as its template.
* **Checks not done.** Holes are respected in overlap tests, but hole-in-shell containment and
  ring-to-ring crossings are not validated. Topology rules beyond overlap, such as gaps or
  network connectivity, are not checked.
* **Speed.** Pure Python: about 0.2 s for 1,000 features and about 4 s for 20,000 on a laptop.
* **Status.** Research software (alpha). The file formats and command-line options may still
  change before 1.0.

## How it relates to other tools

[Kart](https://kartproject.org) and [Mergin Maps / geodiff](https://github.com/MerginMaps/geodiff)
are complete versioning systems with conflicts at the feature or row level. geo3way is a small
merge engine that adds vertex-level merging and post-merge spatial checks. It plugs into plain
git, or into other tools as a library.

## Development

```bash
pip install -e ".[dev]"
pytest                      # or: PYTHONPATH=src:tests python -m unittest discover -s tests
ruff check src tests && mypy src
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports with a small example file are very welcome.

## Reproducing the paper

The paper's experiments use version 0.1.0 (git tag `v0.1.0`). Later versions give identical
detection results on the benchmark.

```bash
cd experiments
PYTHONPATH=../src:. python run_merge.py 30 1000 10    # Experiment 1 (merge)
PYTHONPATH=../src:. python run_size.py                # Experiment 2 (patch size)
PYTHONPATH=../src:. python run_time.py                # Experiment 3 (runtime)
python analyse.py                                     # tables and figures in results/
```

Requires `git`, `numpy`, `pandas` and `matplotlib` (`pip install ".[experiments]"`).

## Citing

If you use geo3way in research, please cite it. See [CITATION.cff](CITATION.cff); GitHub shows
a "Cite this repository" button.

## Licence

MIT. See [LICENSE](LICENSE).
