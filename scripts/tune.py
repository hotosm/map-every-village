"""Sweep eps_m / min_buildings per country. Use a scratch DB: it clobbers work tables.

MEV_DB_URL=... uv run python scripts/tune.py NAM SWZ > tune.tsv
"""

import dataclasses
import logging
import sys
import time

from mev import boundaries, buildings, classify, cluster, config, db, evaluate, osm

logging.basicConfig(level=logging.WARNING)

GRID = [(100, 5), (100, 10), (100, 20), (200, 5), (200, 10), (200, 20), (300, 10), (300, 20)]

PLACES_INSIDE = """
WITH hits AS (
    SELECT c.candidate_id, count(*) AS n FROM candidates c
    JOIN osm_places p ON p.iso3 = c.iso3 AND p.place IN ('hamlet', 'village', 'town')
        AND GeometryType(p.geom) = 'POINT' AND ST_Intersects(c.hull, p.geom)
    WHERE c.iso3 = %(iso3)s GROUP BY 1
)
SELECT coalesce(sum(n), 0), coalesce(sum(n) FILTER (WHERE n > 1), 0),
    count(*) FILTER (WHERE n > 1) FROM hits
"""
OSM_TOTAL = """
SELECT count(*) FROM osm_places WHERE iso3 = %(iso3)s
    AND place IN ('hamlet', 'village', 'town') AND GeometryType(geom) = 'POINT'
"""
CANDS = """
SELECT count(*), count(*) FILTER (WHERE osm_status = 'missing'),
    percentile_disc(0.5) WITHIN GROUP (ORDER BY building_count),
    count(DISTINCT osm_id) FILTER (WHERE osm_shared > 1), count(DISTINCT osm_id)
FROM candidates WHERE iso3 = %(iso3)s
"""


def cell_for(eps: int) -> float:
    return 0.00025 if eps <= 150 else 0.0005 if eps <= 300 else 0.001


print(
    "iso3\teps\tmin\tcell\tcands\tmissing\tmed_bldg\thamlet_rec\tvillage_rec"
    "\tosm_places\tinside\tin_merged\tmerged_cands\tsplit_places\tmatched_places\tsecs",
    flush=True,
)
for iso3 in sys.argv[1:]:
    base = config.load_params(iso3)
    db.init_schema()
    boundaries.run(iso3, base)
    osm.run(iso3, base)
    args = {"iso3": iso3, "hamlet_m": 250, "village_m": 500}
    for eps, mb in GRID:
        p = dataclasses.replace(base, eps_m=eps, min_buildings=mb, cell_deg=cell_for(eps))
        t = time.monotonic()
        buildings.run(iso3, p)
        cluster.run(iso3, p)
        classify.run(iso3, p)
        with db.pg() as conn:
            conn.execute(evaluate.HULLS_SQL, args)
            rec = {pl: f / n for pl, n, f in conn.execute(evaluate.RECALL_SQL, args).fetchall()}
            inside, in_merged, merged = conn.execute(PLACES_INSIDE, args).fetchone()
            total = conn.execute(OSM_TOTAL, args).fetchone()[0]
            n, missing, med, split, matched = conn.execute(CANDS, args).fetchone()
        print(
            f"{iso3}\t{eps}\t{mb}\t{p.cell_deg}\t{n}\t{missing}\t{med}"
            f"\t{rec.get('hamlet', 0):.3f}\t{rec.get('village', 0):.3f}\t{total}\t{inside}"
            f"\t{in_merged}\t{merged}\t{split}\t{matched}\t{time.monotonic() - t:.0f}",
            flush=True,
        )
    from mev import clean

    clean.run(iso3, base)
