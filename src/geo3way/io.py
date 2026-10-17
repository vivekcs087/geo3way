"""Reading and writing datasets: GeoJSON, GeoJSON Text Sequences and GeoPackage.

Only the Python standard library is used (``json``, ``sqlite3``, ``struct``).

    ds, meta = load("parcels.geojson")                 # id from the feature "id" member
    ds, meta = load("parcels.gpkg", id_field="uuid")   # id from an attribute column
    save(ds, "out.geojson", like=meta)                 # keeps CRS, order and extra members

Every feature needs a stable, unique identifier that survives editing. For GeoJSON this is
the feature ``id`` member (or a property named with ``id_field``). For GeoPackage the default
is the table's primary key (``fid``). Auto-incremented keys can collide when two branches
both add features, so a UUID column (``id_field="uuid"``) is safer for collaborative editing;
``geo3way add-ids`` creates one.
"""
from __future__ import annotations

import copy
import json
import math
import os
import shutil
import sqlite3
import struct
import uuid
from dataclasses import dataclass, field
from typing import Any

from .diff import GEOMETRY_TYPES, Dataset


class FormatError(ValueError):
    """The input file cannot be read as a geo3way dataset."""


@dataclass
class Meta:
    """Everything about a source file that is not part of the dataset itself, kept so that a
    merged result can be written back in the same shape."""
    path: str
    fmt: str                                   # "geojson" | "geojsonseq" | "gpkg"
    id_field: str | None = None
    order: list[str] = field(default_factory=list)
    collection: dict[str, Any] = field(default_factory=dict)   # GeoJSON top-level members
    extras: dict[str, dict[str, Any]] = field(default_factory=dict)  # per-feature members
    id_types: dict[str, type] = field(default_factory=dict)    # original type of each id
    layer: str | None = None                   # GeoPackage table
    geom_column: str | None = None
    pk: str | None = None
    srs_id: int = 0
    crs_name: str | None = None                # e.g. "EPSG:25832", if known
    pks: dict[str, int] = field(default_factory=dict)          # id -> primary key value


def detect_format(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".gpkg",):
        return "gpkg"
    if ext in (".geojsonl", ".geojsons", ".geojsonseq", ".jsonl", ".ndjson"):
        return "geojsonseq"
    if ext in (".geojson", ".json"):
        return "geojson"
    raise FormatError(f"{path}: unknown extension {ext!r} (use .geojson, .geojsonl or .gpkg)")


# --------------------------------------------------------------------------- GeoJSON
def _feature_to_internal(f: dict[str, Any], where: str, id_field: str | None
                         ) -> tuple[str, dict[str, Any], dict[str, Any], type]:
    if not isinstance(f, dict) or f.get("type") != "Feature":
        raise FormatError(f"{where}: not a GeoJSON Feature")
    props = f.get("properties") or {}
    if id_field is not None:
        if id_field not in props or props[id_field] is None:
            raise FormatError(f"{where}: property {id_field!r} is missing")
        raw = props[id_field]
    else:
        if f.get("id") is None:
            raise FormatError(f"{where}: feature has no 'id'. Give each feature a stable id, "
                              "name an id property with --id-field, or run `geo3way add-ids`.")
        raw = f["id"]
    g = f.get("geometry")
    if g is None:
        raise FormatError(f"{where} (id {raw!r}): null geometry is not supported")
    if g.get("type") not in GEOMETRY_TYPES:
        raise FormatError(f"{where} (id {raw!r}): unsupported geometry type {g.get('type')!r}")
    extras = {k: v for k, v in f.items() if k not in ("type", "id", "properties", "geometry")}
    gextra = {k: v for k, v in g.items() if k not in ("type", "coordinates")}
    if gextra:
        extras["__geometry_members__"] = gextra
    feat = {"props": dict(props), "geom": {"type": g["type"], "coords": g["coordinates"]}}
    return str(raw), feat, extras, type(raw)


def _add(ds: Dataset, meta: Meta, fid: str, feat: dict[str, Any], extras: dict[str, Any],
         idt: type, where: str) -> None:
    if fid in ds:
        raise FormatError(f"{where}: duplicate feature id {fid!r}")
    ds[fid] = feat
    meta.order.append(fid)
    meta.id_types[fid] = idt
    if extras:
        meta.extras[fid] = extras


def _load_geojson(path: str, id_field: str | None) -> tuple[Dataset, Meta]:
    with open(path, encoding="utf-8") as fh:
        try:
            doc = json.load(fh)
        except json.JSONDecodeError as e:
            raise FormatError(f"{path}: invalid JSON ({e})") from None
    if not isinstance(doc, dict) or doc.get("type") != "FeatureCollection":
        raise FormatError(f"{path}: expected a GeoJSON FeatureCollection")
    meta = Meta(path, "geojson", id_field,
                collection={k: v for k, v in doc.items() if k not in ("type", "features")})
    crs = (meta.collection.get("crs") or {}).get("properties", {}).get("name")
    if isinstance(crs, str):
        meta.crs_name = crs
    ds: Dataset = {}
    for n, f in enumerate(doc.get("features", [])):
        fid, feat, extras, idt = _feature_to_internal(f, f"{path}: feature #{n}", id_field)
        _add(ds, meta, fid, feat, extras, idt, f"{path}: feature #{n}")
    return ds, meta


def _load_geojsonseq(path: str, id_field: str | None) -> tuple[Dataset, Meta]:
    meta = Meta(path, "geojsonseq", id_field)
    ds: Dataset = {}
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            try:
                f = json.loads(line)
            except json.JSONDecodeError as e:
                raise FormatError(f"{path}:{n}: invalid JSON ({e})") from None
            fid, feat, extras, idt = _feature_to_internal(f, f"{path}:{n}", id_field)
            _add(ds, meta, fid, feat, extras, idt, f"{path}:{n}")
    return ds, meta


def _feature_to_geojson(fid: str, f: dict[str, Any], meta: Meta | None) -> dict[str, Any]:
    extras = dict(meta.extras.get(fid, {})) if meta else {}
    gextra = extras.pop("__geometry_members__", {})
    out: dict[str, Any] = {"type": "Feature"}
    id_field = meta.id_field if meta else None
    if id_field is None:
        idt = meta.id_types.get(fid, str) if meta else str
        out["id"] = int(fid) if idt is int else fid
    out.update(extras)
    out["properties"] = f["props"]
    out["geometry"] = {"type": f["geom"]["type"], "coordinates": f["geom"]["coords"], **gextra}
    return out


def output_order(ds: Dataset, *metas: Meta | None) -> list[str]:
    """Order for writing: the first file's order, then features new in later files, then any
    remaining ids sorted. Keeps diffs of the output file small."""
    seen: set[str] = set()
    order: list[str] = []
    for m in metas:
        for fid in (m.order if m else []):
            if fid in ds and fid not in seen:
                seen.add(fid)
                order.append(fid)
    order += sorted(set(ds) - seen)
    return order


# --------------------------------------------------------------------------- WKB / GPKG
_WKB_CODES = {1: "Point", 2: "LineString", 3: "Polygon", 4: "MultiPoint", 5: "MultiLineString",
              6: "MultiPolygon"}
_WKB_TYPES = {v: k for k, v in _WKB_CODES.items()}


def _read_wkb(buf: bytes, off: int = 0) -> tuple[dict[str, Any], int]:
    order = "<" if buf[off] == 1 else ">"
    (code,) = struct.unpack_from(order + "I", buf, off + 1)
    off += 5
    has_z = has_m = False
    if code & 0xE0000000:                      # EWKB flags: Z, M, SRID present
        has_z, has_m = bool(code & 0x80000000), bool(code & 0x40000000)
        if code & 0x20000000:
            off += 4
        code &= 0x0FFFFFFF
    if code >= 1000:                           # ISO WKB: 1000 Z, 2000 M, 3000 ZM
        dim, code = divmod(code, 1000)
        has_z, has_m = dim in (1, 3), dim in (2, 3)
    if code not in _WKB_CODES:
        raise FormatError(f"unsupported WKB geometry type code {code}")
    nd = 2 + has_z + has_m
    keep = 2 + has_z                           # measures (M) are dropped

    def pts(n: int, o: int) -> tuple[list[list[float]], int]:
        vals = struct.unpack_from(order + "d" * (nd * n), buf, o)
        return [list(vals[i * nd:i * nd + keep]) for i in range(n)], o + 8 * nd * n

    def count(o: int) -> tuple[int, int]:
        return struct.unpack_from(order + "I", buf, o)[0], o + 4

    t = _WKB_CODES[code]
    if t == "Point":
        p, off = pts(1, off)
        return {"type": t, "coords": p[0]}, off
    if t == "LineString":
        n, off = count(off)
        c, off = pts(n, off)
        return {"type": t, "coords": c}, off
    if t == "Polygon":
        n, off = count(off)
        rings = []
        for _ in range(n):
            m, off = count(off)
            r, off = pts(m, off)
            rings.append(r)
        return {"type": t, "coords": rings}, off
    n, off = count(off)
    members = []
    for _ in range(n):
        g, off = _read_wkb(buf, off)
        members.append(g["coords"])
    return {"type": t, "coords": members}, off


def _write_wkb(g: dict[str, Any]) -> bytes:
    t, c = g["type"], g["coords"]
    pts_all = [c] if t == "Point" else _flat_points(g)
    has_z = bool(pts_all) and len(pts_all[0]) >= 3
    nd = 3 if has_z else 2
    code = _WKB_TYPES[t] + (1000 if has_z else 0)

    def p(q: list[float]) -> bytes:
        v = list(q[:nd]) + [0.0] * (nd - len(q))
        return struct.pack("<" + "d" * nd, *v)

    def seq(s: list[list[float]]) -> bytes:
        return struct.pack("<I", len(s)) + b"".join(p(q) for q in s)

    head = struct.pack("<BI", 1, code)
    if t == "Point":
        return head + p(c)
    if t == "LineString":
        return head + seq(c)
    if t == "Polygon":
        return head + struct.pack("<I", len(c)) + b"".join(seq(r) for r in c)
    sub = {"MultiPoint": "Point", "MultiLineString": "LineString", "MultiPolygon": "Polygon"}[t]
    return head + struct.pack("<I", len(c)) + b"".join(
        _write_wkb({"type": sub, "coords": m}) for m in c)


def _flat_points(g: dict[str, Any]) -> list[list[float]]:
    from .geometry import vertices
    return [list(v) for v in vertices(g)]


def gpkg_to_geom(blob: bytes) -> dict[str, Any] | None:
    """Decode a GeoPackage geometry blob (header + WKB). Returns None for empty geometries."""
    if blob is None:
        return None
    if blob[:2] != b"GP":
        raise FormatError("not a GeoPackage geometry blob")
    flags = blob[3]
    env = (flags >> 1) & 0x07
    if flags & 0x10:
        return None
    env_len = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get(env)
    if env_len is None:
        raise FormatError(f"invalid GeoPackage envelope indicator {env}")
    g, _ = _read_wkb(blob, 8 + env_len)
    return g


def geom_to_gpkg(g: dict[str, Any], srs_id: int) -> bytes:
    pts = _flat_points(g)
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    header = b"GP" + bytes([0, 0b00000011]) + struct.pack("<i", srs_id)
    env = struct.pack("<4d", min(xs), max(xs), min(ys), max(ys))
    return header + env + _write_wkb(g)


def _env(blob: bytes | None, k: int) -> float | None:
    g = gpkg_to_geom(blob) if blob else None
    if g is None:
        return None
    pts = _flat_points(g)
    vals = [q[k // 2] for q in pts]
    return min(vals) if k % 2 == 0 else max(vals)


def _connect(path: str) -> sqlite3.Connection:
    """Connection with the SQL functions GeoPackage R-tree triggers call (normally provided
    by SpatiaLite or GDAL), so edits keep the spatial index consistent."""
    con = sqlite3.connect(path)
    con.create_function("ST_MinX", 1, lambda b: _env(b, 0), deterministic=True)
    con.create_function("ST_MaxX", 1, lambda b: _env(b, 1), deterministic=True)
    con.create_function("ST_MinY", 1, lambda b: _env(b, 2), deterministic=True)
    con.create_function("ST_MaxY", 1, lambda b: _env(b, 3), deterministic=True)
    con.create_function("ST_IsEmpty", 1,
                        lambda b: 1 if b is None or gpkg_to_geom(b) is None else 0,
                        deterministic=True)
    return con


def gpkg_layers(path: str) -> list[str]:
    con = _connect(path)
    try:
        return [r[0] for r in con.execute(
            "SELECT table_name FROM gpkg_geometry_columns ORDER BY table_name")]
    except sqlite3.DatabaseError as e:
        raise FormatError(f"{path}: not a GeoPackage ({e})") from None
    finally:
        con.close()


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _plain(v: Any) -> Any:
    if isinstance(v, bytes):
        return {"__bytes__": v.hex()}
    return v


def _from_plain(v: Any) -> Any:
    if isinstance(v, dict) and set(v) == {"__bytes__"}:
        return bytes.fromhex(v["__bytes__"])
    return v


def _load_gpkg(path: str, id_field: str | None, layer: str | None) -> tuple[Dataset, Meta]:
    layers = gpkg_layers(path)
    if layer is None:
        if len(layers) != 1:
            raise FormatError(f"{path}: {len(layers)} feature layers {layers}; choose one with "
                              "--layer")
        layer = layers[0]
    elif layer not in layers:
        raise FormatError(f"{path}: no feature layer {layer!r} (layers: {layers})")
    con = _connect(path)
    try:
        gcol, srs = con.execute("SELECT column_name, srs_id FROM gpkg_geometry_columns "
                                "WHERE table_name = ?", (layer,)).fetchone()
        cols = con.execute(f"PRAGMA table_info({_q(layer)})").fetchall()
        pk = next((c[1] for c in cols if c[5]), None)
        if pk is None:
            raise FormatError(f"{path}: layer {layer!r} has no primary key")
        names = [c[1] for c in cols]
        if id_field is not None and id_field not in names:
            raise FormatError(f"{path}: layer {layer!r} has no column {id_field!r}")
        meta = Meta(path, "gpkg", id_field, layer=layer, geom_column=gcol, pk=pk, srs_id=srs)
        org = con.execute("SELECT organization, organization_coordsys_id FROM "
                          "gpkg_spatial_ref_sys WHERE srs_id = ?", (srs,)).fetchone()
        if org and org[0] and org[1] is not None and int(org[1]) > 0:
            meta.crs_name = f"{str(org[0]).upper()}:{org[1]}"
        ds: Dataset = {}
        sel = ", ".join(_q(n) for n in names)
        for row in con.execute(f"SELECT {sel} FROM {_q(layer)} ORDER BY {_q(pk)}"):
            rec = dict(zip(names, row, strict=True))
            raw = rec[id_field] if id_field else rec[pk]
            where = f"{path}: {layer} {pk}={rec[pk]}"
            if raw is None:
                raise FormatError(f"{where}: {id_field!r} is empty")
            g = gpkg_to_geom(rec[gcol])
            if g is None:
                raise FormatError(f"{where}: empty or null geometry is not supported")
            props = {k: _plain(v) for k, v in rec.items() if k not in (pk, gcol)}
            fid = str(raw)
            _add(ds, meta, fid, {"props": props, "geom": g}, {}, type(raw), where)
            meta.pks[fid] = rec[pk]
        return ds, meta
    finally:
        con.close()


def _sql_type(v: Any) -> str:
    if isinstance(v, bool) or isinstance(v, int):
        return "INTEGER"
    if isinstance(v, float):
        return "REAL"
    if isinstance(v, bytes):
        return "BLOB"
    return "TEXT"


def _save_gpkg(ds: Dataset, path: str, like: Meta, others: tuple[Meta | None, ...]) -> None:
    """Write ``ds`` into a copy of the GeoPackage ``like`` (schema, CRS, styles and other
    layers are kept); the feature layer's rows are replaced."""
    if os.path.abspath(path) != os.path.abspath(like.path):
        shutil.copyfile(like.path, path)
    assert like.layer and like.pk and like.geom_column
    con = _connect(path)
    try:
        with con:
            layer, pk, gcol = like.layer, like.pk, like.geom_column
            cols = [c[1] for c in con.execute(f"PRAGMA table_info({_q(layer)})")]
            for f in ds.values():
                for k, v in f["props"].items():
                    if k not in cols:
                        con.execute(f"ALTER TABLE {_q(layer)} ADD COLUMN {_q(k)} "
                                    f"{_sql_type(_from_plain(v))}")
                        cols.append(k)
            con.execute(f"DELETE FROM {_q(layer)}")
            used: set[int] = set()
            pk_of: dict[str, int | None] = {}
            for fid in output_order(ds, like, *others):
                cand: list[int | None] = [m.pks.get(fid) for m in (like, *others) if m is not None]
                if like.id_field is None and fid.lstrip("-").isdigit():
                    cand.insert(0, int(fid))
                pick = next((p for p in cand if p is not None and p not in used), None)
                pk_of[fid] = pick
                if pick is not None:
                    used.add(pick)
            for fid in output_order(ds, like, *others):
                f = ds[fid]
                rec = {k: _from_plain(v) for k, v in f["props"].items()}
                rec[gcol] = geom_to_gpkg(f["geom"], like.srs_id)
                if pk_of[fid] is not None:
                    rec[pk] = pk_of[fid]
                names = list(rec)
                con.execute(f"INSERT INTO {_q(layer)} ({', '.join(_q(n) for n in names)}) "
                            f"VALUES ({', '.join('?' * len(names))})", [rec[n] for n in names])
            if ds:
                pts = [p for f in ds.values() for p in _flat_points(f["geom"])]
                con.execute("UPDATE gpkg_contents SET min_x=?, min_y=?, max_x=?, max_y=?, "
                            "last_change=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                            "WHERE table_name=?",
                            (min(p[0] for p in pts), min(p[1] for p in pts),
                             max(p[0] for p in pts), max(p[1] for p in pts), layer))
    finally:
        con.close()


# --------------------------------------------------------------------------- public API
def load(path: str, id_field: str | None = None, layer: str | None = None,
         fmt: str | None = None) -> tuple[Dataset, Meta]:
    """Read a dataset. Returns (dataset, meta); pass ``meta`` to :func:`save` as ``like``.
    ``fmt`` overrides detection from the file extension."""
    fmt = fmt or detect_format(path)
    if fmt == "gpkg":
        return _load_gpkg(path, id_field, layer)
    if fmt == "geojsonseq":
        return _load_geojsonseq(path, id_field)
    return _load_geojson(path, id_field)


def save(ds: Dataset, path: str, like: Meta | None = None, *others: Meta | None) -> None:
    """Write a dataset. ``like`` supplies the CRS, feature order, extra members and, for
    GeoPackage output, the template file; ``others`` add order for features new in them."""
    fmt = detect_format(path)
    if fmt == "gpkg":
        if like is None or like.fmt != "gpkg":
            raise FormatError("GeoPackage output needs a GeoPackage input as template")
        _save_gpkg(ds, path, like, others)
        return
    metas = [m for m in (like, *others) if m is not None]
    order = output_order(ds, *metas)
    merged_meta = copy.copy(like) if like else None
    if merged_meta is not None:
        merged_meta.extras = {k: v for m in reversed(metas) for k, v in m.extras.items()}
        merged_meta.id_types = {k: v for m in reversed(metas) for k, v in m.id_types.items()}
    feats = [_feature_to_geojson(fid, ds[fid], merged_meta) for fid in order]
    tmp = path + ".tmp-geo3way"
    with open(tmp, "w", encoding="utf-8") as fh:
        if fmt == "geojsonseq":
            for f in feats:
                fh.write(json.dumps(f, ensure_ascii=False, default=str) + "\n")
        else:
            coll = (like.collection if like and like.fmt == "geojson" else {})
            doc = {"type": "FeatureCollection", **coll, "features": feats}
            fh.write(_dumps_one_feature_per_line(doc))
    os.replace(tmp, path)


def _dumps_one_feature_per_line(doc: dict[str, Any]) -> str:
    """FeatureCollection with one feature per line: valid GeoJSON, readable, and friendly
    to line-based tools (git diffs touch only the edited feature's line)."""
    head = {k: v for k, v in doc.items() if k != "features"}
    parts = [json.dumps(head, ensure_ascii=False, default=str)[:-1]]
    feats = ",\n".join(json.dumps(f, ensure_ascii=False, default=str) for f in doc["features"])
    sep = ", " if len(head) > 0 else ""
    return parts[0] + sep + '"features": [\n' + feats + "\n]}\n"


def looks_geographic(ds: Dataset) -> bool:
    """True if every coordinate lies within longitude/latitude bounds (likely degrees)."""
    from .geometry import vertices
    n = 0
    for f in ds.values():
        for p in vertices(f["geom"]):
            if not (math.isfinite(p[0]) and -180 <= p[0] <= 180 and -90 <= p[1] <= 90):
                return False
            n += 1
    return n > 0


def add_ids(path: str, out: str, id_field: str | None = None, layer: str | None = None) -> int:
    """Give every feature without an id a new UUID (GeoJSON: the ``id`` member, or the
    property ``id_field``; GeoPackage: column ``id_field``, default ``uuid``). Returns the
    number of ids added."""
    fmt = detect_format(path)
    if fmt == "gpkg":
        col = id_field or "uuid"
        if os.path.abspath(out) != os.path.abspath(path):
            shutil.copyfile(path, out)
        lay = layer or (gpkg_layers(out) + [None])[0]
        if lay is None:
            raise FormatError(f"{path}: no feature layer")
        con = _connect(out)
        try:
            with con:
                cols = [c[1] for c in con.execute(f"PRAGMA table_info({_q(lay)})")]
                if col not in cols:
                    con.execute(f"ALTER TABLE {_q(lay)} ADD COLUMN {_q(col)} TEXT")
                rows = con.execute(f"SELECT rowid FROM {_q(lay)} WHERE {_q(col)} IS NULL "
                                   f"OR {_q(col)} = ''").fetchall()
                for (rid,) in rows:
                    con.execute(f"UPDATE {_q(lay)} SET {_q(col)} = ? WHERE rowid = ?",
                                (str(uuid.uuid4()), rid))
            return len(rows)
        finally:
            con.close()
    if fmt == "geojsonseq":
        with open(path, encoding="utf-8") as fh:
            feats = [json.loads(s.strip().lstrip("\x1e")) for s in fh if s.strip()]
        doc = None
    else:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        feats = doc.get("features", [])
    n = 0
    for f in feats:
        if id_field:
            props = f.setdefault("properties", {}) or {}
            f["properties"] = props
            if props.get(id_field) in (None, ""):
                props[id_field] = str(uuid.uuid4())
                n += 1
        elif f.get("id") in (None, ""):
            f["id"] = str(uuid.uuid4())
            n += 1
    with open(out, "w", encoding="utf-8") as fh:
        if doc is None:
            fh.writelines(json.dumps(f, ensure_ascii=False) + "\n" for f in feats)
        else:
            fh.write(_dumps_one_feature_per_line(doc))
    return n
