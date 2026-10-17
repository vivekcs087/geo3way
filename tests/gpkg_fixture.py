"""Builds GeoPackage files from scratch following the OGC GeoPackage 1.3 encoding, including
the R-tree spatial index and the triggers GDAL/QGIS create, so the GeoPackage code is
tested against the same structure real files have."""
import sqlite3

from geo3way.io import _connect, geom_to_gpkg


def make_gpkg(path, rows, layer="buildings", srs_id=25832, extra_cols=(("name", "TEXT"),
              ("height", "REAL"))):
    con = sqlite3.connect(path)
    con.executescript(f"""
    PRAGMA application_id = 1196444487;  PRAGMA user_version = 10300;
    CREATE TABLE gpkg_spatial_ref_sys (srs_name TEXT NOT NULL, srs_id INTEGER PRIMARY KEY,
      organization TEXT NOT NULL, organization_coordsys_id INTEGER NOT NULL,
      definition TEXT NOT NULL, description TEXT);
    INSERT INTO gpkg_spatial_ref_sys VALUES ('Undefined cartesian', -1, 'NONE', -1, 'undefined', NULL);
    INSERT INTO gpkg_spatial_ref_sys VALUES ('Undefined geographic', 0, 'NONE', 0, 'undefined', NULL);
    INSERT INTO gpkg_spatial_ref_sys VALUES ('ETRS89 / UTM 32N', {srs_id}, 'EPSG', {srs_id}, 'PROJCS[...]', NULL);
    CREATE TABLE gpkg_contents (table_name TEXT NOT NULL PRIMARY KEY, data_type TEXT NOT NULL,
      identifier TEXT UNIQUE, description TEXT DEFAULT '', last_change DATETIME NOT NULL
      DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), min_x DOUBLE, min_y DOUBLE, max_x DOUBLE,
      max_y DOUBLE, srs_id INTEGER);
    CREATE TABLE gpkg_geometry_columns (table_name TEXT NOT NULL, column_name TEXT NOT NULL,
      geometry_type_name TEXT NOT NULL, srs_id INTEGER NOT NULL, z TINYINT NOT NULL,
      m TINYINT NOT NULL, CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name));
    CREATE TABLE gpkg_extensions (table_name TEXT, column_name TEXT, extension_name TEXT NOT NULL,
      definition TEXT NOT NULL, scope TEXT NOT NULL);
    CREATE TABLE "{layer}" (fid INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, geom GEOMETRY
      {''.join(f', "{c}" {t}' for c, t in extra_cols)});
    INSERT INTO gpkg_contents (table_name, data_type, identifier, srs_id)
      VALUES ('{layer}', 'features', '{layer}', {srs_id});
    INSERT INTO gpkg_geometry_columns VALUES ('{layer}', 'geom', 'GEOMETRY', {srs_id}, 0, 0);
    INSERT INTO gpkg_extensions VALUES ('{layer}', 'geom', 'gpkg_rtree_index',
      'http://www.geopackage.org/spec120/#extension_rtree', 'write-only');
    CREATE VIRTUAL TABLE "rtree_{layer}_geom" USING rtree(id, minx, maxx, miny, maxy);
    CREATE TRIGGER "rtree_{layer}_geom_insert" AFTER INSERT ON "{layer}"
      WHEN (new."geom" NOT NULL AND NOT ST_IsEmpty(NEW."geom")) BEGIN
      INSERT OR REPLACE INTO "rtree_{layer}_geom" VALUES (NEW."fid", ST_MinX(NEW."geom"),
      ST_MaxX(NEW."geom"), ST_MinY(NEW."geom"), ST_MaxY(NEW."geom")); END;
    CREATE TRIGGER "rtree_{layer}_geom_delete" AFTER DELETE ON "{layer}"
      WHEN old."geom" NOT NULL BEGIN DELETE FROM "rtree_{layer}_geom" WHERE id = OLD."fid"; END;
    """)
    con.close()
    con = _connect(path)
    with con:
        for fid, props, geom in rows:
            cols = ["fid", "geom", *props]
            con.execute(f'INSERT INTO "{layer}" ({", ".join(cols)}) VALUES '
                        f'({", ".join("?" * len(cols))})',
                        [fid, geom_to_gpkg(geom, srs_id), *props.values()])
    con.close()
