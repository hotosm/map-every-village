"""Size classes and field priority, from out/ISO3/ only.

Runs after database cleanup: `mev prioritise ISO3`. See docs/prioritising.md.
"""

import csv
import json
import logging
from pathlib import Path

from mev import config, db

log = logging.getLogger(__name__)

NEW_COLUMNS = (
    "size_class", "priority", "group_id", "hint_name", "hint_source", "hint_distance_m"
)  # fmt: skip
OFFICIAL_CSV = config.ROOT / "reference" / "official_settlements.csv"
MIN_OSM_SAMPLE = 100  # matched OSM villages needed to trust their size distribution
GROUP_CELL_M = 250  # grid for finding neighbours; any size works, this one is fast
GROUP_RADIUS_CAP_M = 500  # outline radius from area overstates the reach of sprawling ones
HINT_MAX_GAP_M = 500  # name agreement with OSM falls off beyond this (docs/prioritising.md)
HINT_RADIUS_CAP_M = 1000


def classes_sql(params: config.Params) -> str:
    v, t = int(params.village_buildings), int(params.town_buildings)
    return f"""
        CASE WHEN building_count >= {t} THEN 'town'
             WHEN building_count >= {v} THEN 'village'
             ELSE 'hamlet' END AS size_class,
        CASE WHEN osm_status = 'in_osm_named' THEN 4
             WHEN building_count < {v} THEN 3
             WHEN osm_status = 'missing' THEN 1
             ELSE 2 END AS priority
    """


def rank(con, parquet: Path, params: config.Params) -> None:
    """Table `ranked`: the candidates with NEW_COLUMNS (replacing any from an earlier run)."""
    src = f"read_parquet({db.quote(parquet)})"
    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()]
    keep = ", ".join(f"c.{c}" for c in cols if c not in NEW_COLUMNS)
    con.execute(
        f"CREATE OR REPLACE TABLE ranked AS SELECT {keep}, {classes_sql(params)} FROM {src} c"
    )


def group(con, params: config.Params) -> None:
    """Add group_id to `ranked`: candidates that are probably parts of one village.

    Follow larger neighbours to a local maximum, using centroid/area estimates of gaps.
    OSM-matched candidates can only link to the same OSM place. See docs/prioritising.md.
    """
    gap, cell = params.group_m, GROUP_CELL_M
    if gap <= 0:  # off: every candidate is its own group
        con.execute(
            "CREATE OR REPLACE TABLE ranked AS SELECT *, candidate_id AS group_id FROM ranked"
        )
        return
    # reach: outline radius + half the gap. Two candidates are close enough when their
    # reaches overlap, and then they share a grid cell, so join on cells.
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE pts AS
        SELECT *, r + {gap / 2} AS reach FROM (
            SELECT candidate_id AS id, building_count AS b, osm_id,
                ST_X(geometry::GEOMETRY) * 111320 * cos(radians(ST_Y(geometry::GEOMETRY))) AS x,
                ST_Y(geometry::GEOMETRY) * 110540 AS y,
                least(sqrt(coalesce(area_m2, 0) / pi()), {GROUP_RADIUS_CAP_M}) AS r
            FROM ranked)
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE cells AS
        SELECT id, gx, gy FROM pts,
            unnest(range(floor((x - reach) / {cell})::BIGINT,
                         floor((x + reach) / {cell})::BIGINT + 1)) AS t1(gx),
            unnest(range(floor((y - reach) / {cell})::BIGINT,
                         floor((y + reach) / {cell})::BIGINT + 1)) AS t2(gy)
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE grp AS
        SELECT p.id, coalesce(l.parent, p.id) AS gid FROM pts p LEFT JOIN (
            SELECT a.id, arg_max(b.id, (b.b, b.id)) AS parent
            FROM cells ca
            JOIN cells cb ON cb.gx = ca.gx AND cb.gy = ca.gy AND cb.id <> ca.id
            JOIN pts a ON a.id = ca.id
            JOIN pts b ON b.id = cb.id
            WHERE (b.b, b.id) > (a.b, a.id)
              AND sqrt((a.x - b.x) ^ 2 + (a.y - b.y) ^ 2) - a.r - b.r <= {gap}
              AND (a.osm_id IS NULL OR a.osm_id = b.osm_id)
            GROUP BY a.id
        ) l USING (id)
    """)
    while con.execute("""
        UPDATE grp SET gid = p.gid FROM grp p WHERE grp.gid = p.id AND p.gid <> p.id
    """).fetchone()[0]:
        pass  # pointer jumping: each pass halves the remaining path to the peak
    con.execute("""
        CREATE OR REPLACE TABLE ranked AS
        SELECT r.*, g.gid AS group_id FROM ranked r JOIN grp g ON g.id = r.candidate_id
    """)


def hint(con, out: Path) -> None:
    """Add the nearest gazetteer name (out/ISO3/hints.parquet, from `mev hints`) to `ranked`.

    Nearest by gap to the outline, estimated from centroid and area; 0 means inside.
    """
    path = out / "hints.parquet"
    if not path.exists():
        con.execute("""
            CREATE OR REPLACE TABLE ranked AS SELECT *, NULL::VARCHAR AS hint_name,
                NULL::VARCHAR AS hint_source, NULL::INTEGER AS hint_distance_m FROM ranked
        """)
        return
    deg = 0.02  # grid for the join: > (gap + radius cap) / 111 km, so 3x3 cells suffice
    con.execute(f"""
        CREATE OR REPLACE TABLE ranked AS
        WITH c AS (
            SELECT candidate_id, ST_X(geometry::GEOMETRY) AS lon, ST_Y(geometry::GEOMETRY) AS lat,
                least(sqrt(coalesce(area_m2, 0) / pi()), {HINT_RADIUS_CAP_M}) AS r
            FROM ranked
        ), h AS (
            SELECT name, source, lon, lat, floor(lon / {deg})::INT AS gx,
                floor(lat / {deg})::INT AS gy
            FROM read_parquet({db.quote(path)})
        ), near AS (
            SELECT c.candidate_id, arg_min((h.name, h.source), d) AS best, min(d) AS gap
            FROM c JOIN h
              ON h.gx BETWEEN floor(c.lon / {deg})::INT - 1 AND floor(c.lon / {deg})::INT + 1
             AND h.gy BETWEEN floor(c.lat / {deg})::INT - 1 AND floor(c.lat / {deg})::INT + 1,
            LATERAL (SELECT sqrt(((h.lon - c.lon) * 111320 * cos(radians(c.lat))) ^ 2
                + ((h.lat - c.lat) * 110540) ^ 2) - c.r AS d)
            WHERE d <= {HINT_MAX_GAP_M}
            GROUP BY 1
        )
        SELECT r.*, n.best[1] AS hint_name, n.best[2] AS hint_source,
            round(greatest(n.gap, 0))::INTEGER AS hint_distance_m
        FROM ranked r LEFT JOIN near n USING (candidate_id)
    """)


def official(iso3: str) -> dict | None:
    with OFFICIAL_CSV.open() as f:
        return next((r for r in csv.DictReader(f) if r["iso3"] == iso3), None)


PRIORITY_LABELS = {
    1: "village-size, not in OSM",
    2: "village-size, OSM unnamed",
    3: "hamlet",
    4: "named in OSM (check)",
}
CLASS_ORDER = "['town', 'village', 'hamlet']"


def _class_table(rows: list[tuple]) -> list[str]:
    """(class, missing, unnamed, named) rows as the table in priority.txt."""
    lines = [f"  {'class':10} {'missing':>9} {'unnamed':>9} {'named':>9}"]
    lines += [f"  {cls:10} {m:>9} {u:>9} {n:>9}" for cls, m, u, n in rows]
    return lines


def _candidate_classes(con) -> list[str]:
    return _class_table(
        con.execute(f"""
            SELECT size_class,
                count(*) FILTER (osm_status = 'missing'),
                count(*) FILTER (osm_status = 'in_osm_unnamed'),
                count(*) FILTER (osm_status = 'in_osm_named')
            FROM ranked GROUP BY 1
            ORDER BY list_position({CLASS_ORDER}, size_class)
        """).fetchall()
    )


def _group_classes(con, params: config.Params) -> list[str]:
    """Groups classed by total buildings; a group's status is its best member's."""
    town, village = int(params.town_buildings), int(params.village_buildings)
    return _class_table(
        con.execute(f"""
            SELECT CASE WHEN b >= {town} THEN 'town'
                        WHEN b >= {village} THEN 'village' ELSE 'hamlet' END AS cls,
                count(*) FILTER (status = 3), count(*) FILTER (status = 2),
                count(*) FILTER (status = 1)
            FROM (SELECT group_id, sum(building_count) AS b,
                    min(CASE osm_status WHEN 'in_osm_named' THEN 1
                        WHEN 'in_osm_unnamed' THEN 2 ELSE 3 END) AS status
                  FROM ranked GROUP BY 1)
            GROUP BY 1 ORDER BY list_position({CLASS_ORDER}, cls)
        """).fetchall()
    )


def _priorities(con) -> list[str]:
    rows = con.execute("SELECT priority, count(*) FROM ranked GROUP BY 1 ORDER BY 1").fetchall()
    return [f"  {p} {PRIORITY_LABELS[p]:28} {n:>9}" for p, n in rows]


def _hints(con) -> list[str]:
    rows = con.execute(
        "SELECT priority, count(*), count(hint_name) FROM ranked GROUP BY 1 ORDER BY 1"
    ).fetchall()
    lines = [f"  {p} {PRIORITY_LABELS[p]:28} {h:>9} of {n:>9}" for p, n, h in rows]
    norm = "regexp_replace(lower(strip_accents({})), '[^a-z]', '', 'g')"
    hint, osm = norm.format("hint_name"), norm.format("osm_name")
    n, same, close = con.execute(f"""
        SELECT count(*), count(*) FILTER ({hint} = {osm}),
            count(*) FILTER (jaro_winkler_similarity({hint}, {osm}) >= 0.9)
        FROM ranked WHERE osm_status = 'in_osm_named' AND hint_name IS NOT NULL
    """).fetchone()
    if n:
        lines.append(
            f"  Hint vs OSM name, {n} named candidates: same {same / n:.0%},"
            f" close (Jaro-Winkler >= 0.9) {close / n:.0%}"
        )
    return lines


def _osm_place_sizes(con, village: int) -> list[str]:
    """OSM place sizes, using the largest candidate matched to each place."""
    lines = []
    for place, n, p20, p50, share in con.execute(f"""
        SELECT osm_place, count(*), quantile_disc(b, 0.2), quantile_disc(b, 0.5),
            avg((b >= {village})::INT)
        FROM (SELECT osm_id, osm_place, max(building_count) AS b FROM ranked
              WHERE osm_place IN ('village', 'hamlet') GROUP BY 1, 2)
        GROUP BY 1 ORDER BY 1 DESC
    """).fetchall():
        note = "" if n >= MIN_OSM_SAMPLE else "  (small sample)"
        lines.append(f"  {place:8} {n:>7}  {p20}/{p50}  {share:.0%}{note}")
    return lines


def _official(iso3: str, n_village: int) -> list[str]:
    ref = official(iso3)
    if not ref:
        return []
    count = int(ref["count"])
    return [
        f"Official: {count} {ref['unit']} ({ref['unit_kind']}, {ref['year']},"
        f" {ref['confidence']} confidence); village-size / official = {n_village / count:.2f}",
        f"  {ref['source']}: {ref['url']}",
    ]


def report(con, iso3: str, params: config.Params) -> str:
    village = int(params.village_buildings)
    n_village = con.execute(
        f"SELECT count(*) FROM ranked WHERE building_count >= {village}"
    ).fetchone()[0]
    lines = [
        f"{iso3}: classes (village >= {village} buildings, town >= {int(params.town_buildings)})",
        *_candidate_classes(con),
        f"Groups (biggest neighbour within {int(params.group_m)} m), by total buildings",
        *_group_classes(con, params),
        "Priority",
        *_priorities(con),
        f"Name hints within {HINT_MAX_GAP_M} m (`mev hints`)",
        *_hints(con),
        f"Matched OSM places, building count p20/p50, share >= {village}",
        *_osm_place_sizes(con, village),
        f"Village-size candidates (incl. towns): {n_village}",
        *_official(iso3, n_village),
    ]
    return "\n".join(lines)


def _patch_geojson(con, out: Path) -> dict[str, dict]:
    """Add the new properties, sort by priority, and write villages/ (priority 1-2).

    Returns per-unit counts by priority for index.json.
    """
    cols = ("size_class", "priority", "group_id", "hint_name", "hint_distance_m")
    props = {
        cid: dict(zip(cols, rest, strict=True))
        for cid, *rest in con.execute(
            f"SELECT candidate_id, {', '.join(cols)} FROM ranked"
        ).fetchall()
    }
    villages = out / "villages"
    villages.mkdir(exist_ok=True)
    for old in villages.glob("*.geojson"):
        old.unlink()
    counts = {}
    for path in sorted((out / "candidates").glob("*.geojson")):
        fc = json.loads(path.read_text())
        for f in fc["features"]:
            f["properties"].update(props[f["properties"]["candidate_id"]])
            f["id"] = f["properties"]["candidate_id"]
            # Field-TM takes entity properties from the first feature and stringifies
            # values, so keep every key and write "" rather than "None".
            f["properties"] = {k: "" if v is None else v for k, v in f["properties"].items()}
        fc["features"].sort(
            key=lambda f: (f["properties"]["priority"], -f["properties"]["building_count"])
        )
        path.write_text(json.dumps(fc))
        top = [f for f in fc["features"] if f["properties"]["priority"] <= 2]
        if top:
            (villages / path.name).write_text(json.dumps({**fc, "features": top}))
        n = {}
        for f in fc["features"]:
            key = str(f["properties"]["priority"])
            n[key] = n.get(key, 0) + 1
        counts[path.stem] = n
    return counts


def _update_index(out: Path, counts: dict[str, dict]) -> None:
    path = out / "index.json"
    index = json.loads(path.read_text())
    for entry in index:
        n = counts.get(entry["pcode"], {})
        entry["priority_counts"] = dict(sorted(n.items()))
        has = n.get("1", 0) + n.get("2", 0)
        entry["villages"] = f"villages/{entry['pcode']}.geojson" if has else None
    path.write_text(json.dumps(index, indent=1))


def run(iso3: str, params: config.Params) -> None:
    out = config.OUT_DIR / iso3
    parquet = out / "candidates.parquet"
    if not parquet.exists():
        raise RuntimeError(f"{parquet} missing: run `mev export {iso3}` first")
    con = db.duck(attach_pg=False, extensions=("spatial",))
    rank(con, parquet, params)
    group(con, params)
    hint(con, out)
    tmp = parquet.with_suffix(".tmp")
    con.execute(f"COPY ranked TO {db.quote(tmp)} (FORMAT parquet, COMPRESSION zstd)")
    tmp.replace(parquet)
    _update_index(out, _patch_geojson(con, out))
    text = report(con, iso3, params)
    print(text)
    (out / "priority.txt").write_text(text + "\n")
