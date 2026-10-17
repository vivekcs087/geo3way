# geo3way

Geometry-aware diff and three-way merge for vector spatial data (Point, LineString, Polygon),
with detection of semantic spatial conflicts (invalid combined geometry, duplicate digitisation,
overlaps created by merging). Pure Python, no dependencies; experiments call `git`.

```python
from geo3way import diff, merge
patch = diff(base, new, eps=1e-3)
result = merge(base, ours, theirs, eps=1e-3, duplicate_tol=1.0)
result.merged, result.conflicts
```

A dataset is `{feature_id: {"props": {...}, "geom": {"type": ..., "coords": ...}}}`.

## Reproduce the paper
```bash
PYTHONPATH=src:tests python -m unittest discover -s tests     # 30 tests
python tools/line_coverage.py                                 # statement coverage
cd experiments
PYTHONPATH=../src:. python run_merge.py 30 1000 10            # Experiment 1 (merge)
PYTHONPATH=../src:. python run_size.py                        # Experiment 2 (patch size)
PYTHONPATH=../src:. python run_time.py                        # Experiment 3 (runtime)
python analyse.py                                             # tables + figures in results/
```
Results from the paper are in `results/`. Licence: MIT.
