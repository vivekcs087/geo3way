# Changelog

## 0.2.0

* Command-line tool `geo3way` with the subcommands `merge`, `diff`, `validate`, `add-ids` and
  `install-git`.
* File input/output: GeoJSON, GeoJSON Text Sequences and GeoPackage, keeping CRS, feature
  order, schema, foreign members and the GeoPackage spatial index.
* git merge driver and diff driver (`geo3way install-git`).
* Conflict reports as GeoJSON (viewable in QGIS), CSV or JSON, with base/ours/theirs values.
* `--prefer theirs`, and automatic tolerances for longitude/latitude data.
* Multi-part geometries (MultiPoint, MultiLineString, MultiPolygon); holes respected in overlap
  tests.
* Duplicate detection uses the distance from points to segments, so extra vertices along an
  edge no longer hide duplicates.
* Packaging (`pyproject.toml`), CI and release workflows, documentation and examples.
* Detection results on the paper's benchmark are unchanged from 0.1.0 (all 108,000 episode
  outcomes identical).

## 0.1.0

* Version evaluated in the paper: dataset diff, three-way merge with structural and semantic
  spatial conflicts, line-based git baselines and experiments.
