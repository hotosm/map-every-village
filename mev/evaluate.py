"""Tuning report: share of OSM hamlets/villages found, with the distances classify uses."""

from mev import config, db

# In metres on the UTM zone from `cluster`, as classify does. Geography distances
# on big town outlines took hours (Morocco).
HULLS_SQL = """
DROP TABLE IF EXISTS eval_hulls;
CREATE TEMP TABLE eval_hulls AS
SELECT ST_Transform(c.hull, m.srid) AS hull_m
FROM candidates c, work_meta m
WHERE c.iso3 = %(iso3)s;
CREATE INDEX ON eval_hulls USING gist (hull_m);
ANALYZE eval_hulls;
"""

RECALL_SQL = """
SELECT p.place, count(*) AS total,
    count(*) FILTER (WHERE EXISTS (
        SELECT 1 FROM eval_hulls h
        WHERE ST_DWithin(
            h.hull_m, ST_Transform(p.geom, (SELECT srid FROM work_meta)),
            CASE p.place WHEN 'hamlet' THEN %(hamlet_m)s ELSE %(village_m)s END
        )
    )) AS found
FROM osm_places p
WHERE p.iso3 = %(iso3)s AND p.place IN ('hamlet', 'village')
    AND GeometryType(p.geom) = 'POINT'
GROUP BY p.place
"""

SIZE_SQL = """
SELECT osm_status, count(*),
    percentile_disc(ARRAY[0.1, 0.5, 0.9]) WITHIN GROUP (ORDER BY building_count)
FROM candidates WHERE iso3 = %(iso3)s GROUP BY 1 ORDER BY 1
"""


def report(iso3: str, params: config.Params) -> str:
    hamlet_m, village_m = params.place_buffer_m["hamlet"], params.place_buffer_m["village"]
    args = {"iso3": iso3, "hamlet_m": hamlet_m, "village_m": village_m}
    with db.pg() as conn:
        conn.execute(HULLS_SQL, args)
        recall = conn.execute(RECALL_SQL, args).fetchall()
        sizes = conn.execute(SIZE_SQL, args).fetchall()
    lines = [f"{iso3}: OSM hamlets/villages matched by a candidate (recall)"]
    for place, total, found in recall:
        lines.append(f"  {place:8} {found:>7}/{total:<7} {found / total:.0%}")
    lines.append("Candidates, building count p10/p50/p90")
    lines += [f"  {status:15} {n:>8}  {p10}/{p50}/{p90}" for status, n, (p10, p50, p90) in sizes]
    return "\n".join(lines)


def run(iso3: str, params: config.Params) -> None:
    text = report(iso3, params)
    print(text)
    (config.country_dir(config.OUT_DIR, iso3) / "evaluate.txt").write_text(text + "\n")
