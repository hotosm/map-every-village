"""OSM places and health facilities from oex GeoParquet: HOT's exports, or a local oex run."""

import logging
import subprocess

from mev import config, db

log = logging.getLogger(__name__)

OEX_VERSION = "0.4.18"
DUCKDB_PIN = "duckdb<2"  # quackosm 0.19 breaks on duckdb 2
LAYERS = ("populated_places", "health_facilities")
HEALTH_KINDS = ("hospital", "clinic", "doctors", "doctor", "centre", "health_post")


def published(iso3: str) -> dict[str, str]:
    return {layer: f"{config.OEX_BASE_URL}/{iso3}/_layers/osm/{layer}.parquet" for layer in LAYERS}


def run_oex(iso3: str) -> dict[str, str]:
    out = config.country_dir(config.DATA_DIR, iso3) / "oex"
    subprocess.run(
        [
            "uvx", "--from", f"oex=={OEX_VERSION}", "--with", DUCKDB_PIN, "oex-cli", "osm",
            "--config", str(config.CONFIGS_DIR / "oex-villages.yaml"),
            "--iso3", iso3, "--output-dir", str(out), "--no-hdx-push",
        ],
        check=True,
        cwd=out.parent,  # oex caches in ./data, so `clean` removes it
    )  # fmt: skip
    paths = {}
    for layer in LAYERS:
        found = sorted(out.rglob(f"_layers/{layer}.parquet"))
        if not found:
            raise RuntimeError(f"oex wrote no {layer}.parquet under {out}")
        paths[layer] = str(found[-1])
    return paths


def _columns(con, path: str) -> set[str]:
    rows = con.execute(f"DESCRIBE SELECT * FROM read_parquet({db.quote(path)})").fetchall()
    return {r[0] for r in rows}


def run(iso3: str, params: config.Params, osm_source: str = "published") -> None:
    paths = published(iso3) if osm_source == "published" else run_oex(iso3)
    con = db.duck()

    places = db.quote(paths["populated_places"])
    cols = _columns(con, paths["populated_places"])
    alt_name = "alt_name" if "alt_name" in cols else "NULL"
    db.stage(
        con,
        "stage_places",
        f"SELECT id::VARCHAR AS osm_id, name, {alt_name}::VARCHAR AS alt_name, place, landuse,"
        f" ST_AsWKB(geom)::BLOB AS wkb FROM read_parquet({places})",
    )

    health = db.quote(paths["health_facilities"])
    kinds = ", ".join(db.quote(k) for k in HEALTH_KINDS)
    db.stage(
        con,
        "stage_health",
        f"SELECT id::VARCHAR AS osm_id, name, coalesce(healthcare, amenity) AS kind,"
        f" ST_AsWKB(ST_PointOnSurface(geom))::BLOB AS wkb FROM read_parquet({health})"
        f" WHERE coalesce(healthcare, amenity) IN ({kinds})",
    )

    with db.pg() as conn:
        conn.execute(
            """
            DELETE FROM osm_places WHERE iso3 = %(iso3)s;
            INSERT INTO osm_places (iso3, osm_id, name, alt_name, place, landuse, geom)
            SELECT %(iso3)s, osm_id, name, alt_name, place, landuse,
                ST_SetSRID(ST_GeomFromWKB(wkb), 4326)
            FROM stage_places;
            DROP TABLE stage_places;

            DELETE FROM osm_health WHERE iso3 = %(iso3)s;
            INSERT INTO osm_health (iso3, osm_id, name, kind, geom)
            SELECT %(iso3)s, osm_id, name, kind, ST_SetSRID(ST_GeomFromWKB(wkb), 4326)
            FROM stage_health;
            DROP TABLE stage_health;
            """,
            {"iso3": iso3},
        )
        n_places = conn.execute("SELECT count(*) FROM osm_places WHERE iso3 = %s", [iso3])
        n_health = conn.execute("SELECT count(*) FROM osm_health WHERE iso3 = %s", [iso3])
        log.info(
            "%s: %d OSM places, %d health facilities",
            iso3, n_places.fetchone()[0], n_health.fetchone()[0],
        )  # fmt: skip
