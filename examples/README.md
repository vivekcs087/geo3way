# Example: two people edit the same buildings layer

`base.geojson` holds four buildings and a road (EPSG:25832, metres). Alice and Bob each
edited a copy:

| Feature | Alice | Bob | Result |
|---|---|---|---|
| b1 | widened the east side | raised the north-west corner; floors 3 → 4 | **merged**: both edits kept (plain git: conflict) |
| b2 | height 7.5; extended east | height 6.5 | **ATTRIBUTE** conflict on height; **OVERLAP** with b3 |
| b3 | — | moved its west wall west | (part of the overlap) |
| b4 | — | deleted | **deleted** |
| r1 (road) | renamed | added a vertex | **merged** |
| new shed | digitised as `shed-a` | digitised as `shed-b` | **DUPLICATE** |

```bash
geo3way merge base.geojson alice.geojson bob.geojson -o merged.geojson -r conflicts.geojson
```

Open `base.geojson`, `merged.geojson` and `conflicts.geojson` in QGIS to compare them.
