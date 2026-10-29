"""Write out/{ISO3}/: GeoParquet, per-unit GeoJSON for Field-TM, index and sources."""

import json
import logging
import shutil

from mev import boundaries, config, db, licences

log = logging.getLogger(__name__)

# Settlement outlines (Field-TM uploads accept polygons only); flat properties for ODK.
FEATURE_SQL = """
SELECT {unit_col} AS pcode, jsonb_build_object(
    'type', 'FeatureCollection',
    'features', jsonb_agg(ST_AsGeoJSON(f.*, 'outline', 7)::jsonb)
)
FROM (
    SELECT
        candidate_id, osm_status, building_count,
        round(mean_confidence::numeric, 2) AS mean_confidence,
        osm_id, osm_name, osm_place, osm_distance_m, osm_nearby, osm_shared,
        adm1_name, adm2_name, adm3_name, adm4_name,
        health_facilities -> 0 ->> 'name' AS health_1_name,
        health_facilities -> 0 ->> 'distance_m' AS health_1_distance_m,
        health_facilities -> 1 ->> 'name' AS health_2_name,
        health_facilities -> 1 ->> 'distance_m' AS health_2_distance_m,
        health_facilities -> 2 ->> 'name' AS health_3_name,
        health_facilities -> 2 ->> 'distance_m' AS health_3_distance_m,
        round(ST_X(geom)::numeric, 6) AS lon,
        round(ST_Y(geom)::numeric, 6) AS lat,
        {unit_col},
        CASE WHEN ST_NumGeometries(hull) = 1 THEN ST_GeometryN(hull, 1) ELSE ST_ConvexHull(hull) END
            AS outline
    FROM candidates
    WHERE iso3 = %(iso3)s
) f
GROUP BY {unit_col}
"""

UNITS_SQL = """
SELECT
    u.pcode, u.name,
    jsonb_build_object('type', 'FeatureCollection', 'features', jsonb_build_array(
        jsonb_build_object('type', 'Feature', 'properties',
            jsonb_build_object('pcode', u.pcode, 'name', u.name),
            'geometry', ST_AsGeoJSON(u.geom, 7)::jsonb))) AS aoi,
    ARRAY[ST_XMin(u.geom), ST_YMin(u.geom), ST_XMax(u.geom), ST_YMax(u.geom)] AS bbox,
    (
        SELECT array_agg(p.name ORDER BY p.level)
        FROM admin_units p
        WHERE p.iso3 = u.iso3 AND p.level BETWEEN 1 AND u.level - 1
            AND ST_Intersects(p.geom, ST_PointOnSurface(u.geom))
    ) AS parents
FROM admin_units u
WHERE u.iso3 = %(iso3)s AND u.level = %(level)s
"""

COUNTS_SQL = """
SELECT {unit_col}, osm_status, count(*) FROM candidates WHERE iso3 = %(iso3)s GROUP BY 1, 2
"""


def _parquet(iso3: str, out) -> None:
    query = (
        "SELECT candidate_id, iso3, building_count, cell_count, n_google, n_microsoft, n_osm,"
        " mean_confidence, max_confidence, area_m2,"
        " adm1_pcode, adm1_name, adm2_pcode, adm2_name, adm3_pcode, adm3_name,"
        " adm4_pcode, adm4_name, osm_status, osm_id, osm_name, osm_place,"
        " osm_distance_m, osm_nearby, osm_shared,"
        " health_facilities::text AS health_facilities, ST_AsBinary(geom) AS wkb"
        f" FROM candidates WHERE iso3 = '{iso3}'"
    )
    con = db.duck()
    con.execute(
        f"COPY (SELECT * EXCLUDE (wkb), ST_GeomFromWKB(wkb) AS geometry"
        f" FROM postgres_query('pg', {db.quote(query)}))"
        f" TO {db.quote(out / 'candidates.parquet')} (FORMAT parquet, COMPRESSION zstd)"
    )


def run(iso3: str, params: config.Params) -> None:
    # Built aside and swapped in, so a failed export never mixes with an old one.
    final = config.OUT_DIR / iso3
    out = config.OUT_DIR / f".{iso3}.partial"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    _parquet(iso3, out)

    with db.pg() as conn:
        max_level = conn.execute(
            "SELECT max(level) FROM admin_units WHERE iso3 = %s", [iso3]
        ).fetchone()[0]
        level = min(params.project_admin_level, max_level or 0)
        if level < 1:
            raise RuntimeError(f"{iso3}: no admin level >= 1 to split outputs by")
        unit_col = f"adm{int(level)}_pcode"
        args = {"iso3": iso3, "level": level}
        features = dict(conn.execute(FEATURE_SQL.format(unit_col=unit_col), args).fetchall())
        units = conn.execute(UNITS_SQL, args).fetchall()
        counts: dict[str, dict[str, int]] = {}
        for pcode, status, n in conn.execute(COUNTS_SQL.format(unit_col=unit_col), args):
            counts.setdefault(pcode, {})[status] = n

    cand_dir, aoi_dir = out / "candidates", out / "aoi"
    cand_dir.mkdir(exist_ok=True)
    aoi_dir.mkdir(exist_ok=True)
    index = []
    for pcode, name, aoi, bbox, parents in units:
        (aoi_dir / f"{pcode}.geojson").write_text(json.dumps(aoi))
        entry = {
            "pcode": pcode,
            "name": name,
            "level": level,
            "project_name": " | ".join([*(parents or []), name or pcode]),
            "bbox": bbox,
            "counts": counts.get(pcode, {}),
            "aoi": f"aoi/{pcode}.geojson",
            "candidates": None,
        }
        if pcode in features:
            (cand_dir / f"{pcode}.geojson").write_text(json.dumps(features[pcode]))
            entry["candidates"] = f"candidates/{pcode}.geojson"
        index.append(entry)
    if None in features:
        (cand_dir / "_unassigned.geojson").write_text(json.dumps(features[None]))
        log.warning(
            "%s: %d candidates outside admin units", iso3, sum(counts.get(None, {}).values())
        )

    (out / "index.json").write_text(json.dumps(index, indent=1))
    sources = [licences.VIDA, licences.OSM, *licences.load(boundaries.source_path(iso3))]
    licences.save(sources, out / "sources.json")
    shutil.rmtree(final, ignore_errors=True)
    out.rename(final)
    log.info("%s: wrote %d units (admin level %d) to %s", iso3, len(index), level, final)
