# A QGIS workflow

geo3way has no QGIS plugin yet. It works alongside QGIS through files, so no plugin is needed.

1. **Prepare the layer once.** *Layer → Export → Save Features As…* → GeoPackage (or
   GeoJSON). Then give it stable ids:

   ```bash
   geo3way add-ids parcels.gpkg          # adds a "uuid" column
   ```

   For team editing, use `--id-field uuid` in later commands (or
   `geo3way install-git --options "--id-field uuid"`).
2. **Share a base copy.** Each person edits their own copy (or branch) in QGIS as usual:
   moving vertices, editing attributes, adding and deleting features.
3. **Merge.**

   ```bash
   geo3way merge base.gpkg anna.gpkg ben.gpkg -o merged.gpkg -r conflicts.geojson --id-field uuid
   ```

4. **Review in QGIS.** Load `merged.gpkg` and `conflicts.geojson`. Style the report by the
   `kind` field. The attribute table shows the `base`, `ours` and `theirs` values of disputed
   attributes. Fix the features in `merged.gpkg`, then run `geo3way validate merged.gpkg`.

Tolerances are in layer units. For a layer in EPSG:4326 (degrees), geo3way detects this and
uses about 1 mm / 1 m equivalents. For best results, edit in a projected CRS.
