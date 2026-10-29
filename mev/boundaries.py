"""Admin boundaries: OCHA COD-AB, else geoBoundaries gbOpen, else OSM. Not GADM.

GADM forbids redistribution, and gbOpen is sometimes CC BY-SA (Angola). OSM is ODbL,
like our outputs, but its lower levels are often partial; `drop_partial_levels` drops them.
"""

import json
import logging
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from mev import config, db, fetch, licences
from mev.licences import LicenceError, Source

log = logging.getLogger(__name__)

HDX_API = "https://data.humdata.org/api/3/action/package_show"
GB_API = "https://www.geoboundaries.org/api/current/gbOpen/{iso3}/ADM{level}/"
OVERPASS_API = "https://overpass-api.de/api/interpreter"
MAX_LEVEL = 4
# OSM admin_level of our levels 0-3 (the usual African scheme: country, region, district).
OSM_ADMIN_LEVELS = {0: "2", 1: "4", 2: "6", 3: "8"}
OSM_QUERY = """[out:json][timeout:900];
area["ISO3166-1:alpha3"="{iso3}"][admin_level=2]->.country;
rel(area.country)[boundary=administrative][admin_level~"^({levels})$"];
out geom;"""


@dataclass(frozen=True)
class Layer:
    level: int
    path: str  # GDAL path, may be /vsizip/
    pcode_col: str
    name_col: str


def pick_layer(names: list[str], level: int) -> str | None:
    """GeoJSON, edge-matched (_em) first; else an older shapefile, e.g. rwa_adm1_2006.shp."""
    for suffix in (f"_admin{level}_em.geojson", f"_admin{level}.geojson"):
        for name in names:
            if name.endswith(suffix):
                return name
    shp = re.compile(rf"_adm{level}(_.*)?\.shp$", re.IGNORECASE)
    for name in names:
        if shp.search(Path(name).name):
            return name
    return None


def cod_ab_source(iso3: str) -> tuple[Source, list[str]] | None:
    """Licence-checked COD-AB source and its zip URLs (GeoJSON, else shapefiles), or None."""
    dataset = f"cod-ab-{iso3.lower()}"
    r = fetch.get(HDX_API, id=dataset)
    if r.status_code != 200:
        log.info("%s: no COD-AB on HDX", iso3)
        return None
    pkg = r.json()["result"]
    try:
        source = licences.require(
            f"OCHA COD-AB {iso3}",
            pkg["license_id"],
            f"https://data.humdata.org/dataset/{dataset}",
            "OCHA Field Information Services Section (FISS), COD-AB",
        )
    except LicenceError as e:
        log.warning("%s", e)
        return None
    if "gadm" in (pkg.get("dataset_source") or "").lower():
        log.warning("%s: COD-AB is derived from GADM, not used", iso3)
        return None
    zips = {fmt: [] for fmt in ("geojson", "shp")}
    for res in pkg["resources"]:
        if res["format"].lower() in zips and res["url"].endswith(".zip"):
            zips[res["format"].lower()].append(res["url"])
    urls = zips["geojson"][:1] or zips["shp"]
    if not urls:
        log.warning("%s: COD-AB has no zipped GeoJSON or shapefile", iso3)
        return None
    return source, urls


def cod_ab(iso3: str) -> tuple[Source, list[Layer]] | None:
    found = cod_ab_source(iso3)
    if found is None:
        return None
    source, urls = found
    members = {}  # name in zip -> zip path
    for i, url in enumerate(urls):
        path = fetch.download(url, config.country_dir(config.DATA_DIR, iso3) / f"cod-ab-{i}.zip")
        members |= dict.fromkeys(zipfile.ZipFile(path).namelist(), path)
    layers = []
    for level in range(MAX_LEVEL + 1):
        name = pick_layer(list(members), level)
        if name:
            name_col = f"adm{level}_name" if name.endswith(".geojson") else f"adm{level}_en"
            path = f"/vsizip/{members[name]}/{name}"
            layers.append(Layer(level, path, f"adm{level}_pcode", name_col))
    return (source, layers) if layers else None


def gb_open(iso3: str) -> tuple[Source, list[Layer]] | None:
    source, layers = None, []
    for level in range(MAX_LEVEL + 1):
        r = fetch.get(GB_API.format(iso3=iso3, level=level))
        try:
            meta = r.json() if r.status_code == 200 else None
        except ValueError:
            meta = None
        if not meta:
            break
        try:
            source = licences.require(
                f"geoBoundaries gbOpen {iso3}",
                meta["boundaryLicense"],
                "https://www.geoboundaries.org",
                "geoBoundaries (Runfola et al. 2020), gbOpen",
            )
        except LicenceError as e:
            log.warning("%s", e)
            return None
        dest = config.country_dir(config.DATA_DIR, iso3) / f"gb-adm{level}.geojson"
        path = fetch.download(meta["gjDownloadURL"], dest)
        layers.append(Layer(level, str(path), "shapeID", "shapeName"))
    return (source, layers) if layers else None


def osm_admin(iso3: str) -> tuple[Source, list[Layer]] | None:
    """OSM admin relations from Overpass, one GeoJSON per level, inside the country only.

    The area query also returns neighbours' relations that touch the border, so each
    level keeps only units whose interior point is inside the country's own outline.
    """
    data = config.country_dir(config.DATA_DIR, iso3)
    query = OSM_QUERY.format(iso3=iso3, levels="|".join(OSM_ADMIN_LEVELS.values()))
    path = fetch.download(
        f"{OVERPASS_API}?{httpx.QueryParams(data=query)}", data / "osm-admin.json"
    )
    rows = []
    for rel in json.loads(path.read_text())["elements"]:
        tags, lines = rel.get("tags", {}), _member_lines(rel)
        if lines:
            iso = tags.get("ISO3166-1:alpha3")
            rows.append((rel["id"], tags.get("name"), tags.get("admin_level"), iso, lines))
    if not rows:
        log.warning("%s: no OSM admin boundaries", iso3)
        return None
    con = db.duck(attach_pg=False, extensions=("spatial",))
    con.execute(
        "CREATE TABLE rels (osm_id BIGINT, name VARCHAR, admin_level VARCHAR,"
        " iso3 VARCHAR, wkt VARCHAR)"
    )
    con.executemany("INSERT INTO rels VALUES (?, ?, ?, ?, ?)", rows)
    con.execute("""
        CREATE TABLE units AS
        SELECT osm_id, name, admin_level, iso3, ST_BuildArea(ST_Node(ST_GeomFromText(wkt))) AS geom
        FROM rels
    """)
    country = f"(SELECT geom FROM units WHERE admin_level = '2' AND iso3 = '{iso3}')"
    if con.execute(f"SELECT count(*) FROM {country}").fetchone()[0] != 1:
        log.warning("%s: no OSM country boundary", iso3)
        return None
    layers = []
    for level, admin_level in OSM_ADMIN_LEVELS.items():
        inside = f"""
            SELECT '{iso3}-' || osm_id AS pcode, name, geom FROM units
            WHERE admin_level = '{admin_level}' AND NOT ST_IsEmpty(geom)
                AND ST_Within(ST_PointOnSurface(geom), {country})
        """
        if not con.execute(f"SELECT count(*) FROM ({inside})").fetchone()[0]:
            continue
        dest = data / f"osm-adm{level}.geojson"
        con.execute(f"COPY ({inside}) TO {db.quote(dest)} (FORMAT gdal, DRIVER 'GeoJSON')")
        layers.append(Layer(level, str(dest), "pcode", "name"))
    source = licences.require(
        f"OpenStreetMap admin boundaries {iso3}",
        "ODbL",
        "https://www.openstreetmap.org",
        "© OpenStreetMap contributors",
    )
    return source, layers


def _member_lines(rel: dict) -> str | None:
    """A relation's way members as one WKT MULTILINESTRING (outer and inner rings alike)."""
    lines = [
        "(" + ", ".join(f"{p['lon']} {p['lat']}" for p in m["geometry"]) + ")"
        for m in rel["members"]
        if m["type"] == "way" and m.get("geometry")
    ]
    return f"MULTILINESTRING ({', '.join(lines)})" if lines else None


def load(iso3: str, source: Source, layers: list[Layer]) -> None:
    with db.pg() as conn:
        conn.execute("DELETE FROM admin_units WHERE iso3 = %s", [iso3])
    con = db.duck()
    for layer in layers:
        src = f"ST_Read({db.quote(layer.path)})"
        cols = {r[0].lower() for r in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()}
        name = f'"{layer.name_col}"' if layer.name_col.lower() in cols else "NULL"  # e.g. LSO adm0
        db.stage(
            con,
            "stage_admin",
            f'SELECT "{layer.pcode_col}"::VARCHAR AS pcode, {name}::VARCHAR AS name,'
            f" ST_AsWKB(geom)::BLOB AS wkb FROM {src}",
        )
        with db.pg() as conn:
            conn.execute(
                """
                INSERT INTO admin_units (iso3, level, pcode, name, source, geom)
                SELECT %s, %s, pcode, name, %s,
                    ST_Multi(ST_CollectionExtract(
                        ST_MakeValid(ST_SetSRID(ST_GeomFromWKB(wkb), 4326)), 3))
                FROM stage_admin WHERE pcode IS NOT NULL
                ON CONFLICT DO NOTHING;
                DROP TABLE stage_admin;
                """,
                [iso3, layer.level, source.name],
            )
        log.info("%s: admin level %d loaded from %s", iso3, layer.level, source.name)
    drop_partial_levels(iso3)


def drop_partial_levels(iso3: str, min_cover: float = 0.95) -> None:
    """Drop incomplete levels, e.g. RWA level 4 only covers 37% of the country."""
    with db.pg() as conn:
        areas = conn.execute(
            "SELECT level, sum(ST_Area(geom)) FROM admin_units WHERE iso3 = %s GROUP BY 1",
            [iso3],
        ).fetchall()
        full = max(area for _, area in areas)
        for level, area in areas:
            if area < min_cover * full:
                conn.execute(
                    "DELETE FROM admin_units WHERE iso3 = %s AND level = %s", [iso3, level]
                )
                log.warning(
                    "%s: admin level %d covers %.0f%%, dropped", iso3, level, 100 * area / full
                )


def source_path(iso3: str) -> Path:
    return config.country_dir(config.DATA_DIR, iso3) / "boundaries_source.json"


def run(iso3: str, params: config.Params) -> None:
    found = cod_ab(iso3) or gb_open(iso3) or osm_admin(iso3)
    if found is None:
        raise RuntimeError(f"{iso3}: no openly licensed admin boundaries found")
    source, layers = found
    load(iso3, source, layers)
    licences.save([source], source_path(iso3))
