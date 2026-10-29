"""Suggest village_buildings and group_m from out/*/candidates.parquet.

`just calibrate` writes calibrate.tsv. Rules and evidence: docs/prioritising.md.
"""

import csv
import dataclasses
import sys

import duckdb

from mev import config, prioritise

DEFAULT, KEEP, FLOOR, MIN_SAMPLE = 50, 0.8, 20, 100
GROUP_GAPS, MAX_MERGED = (200, 150, 100), 0.10


def suggest(n_villages: int, kept: float, p20: int) -> int:
    if n_villages < MIN_SAMPLE or kept >= KEEP:
        return DEFAULT
    return max(FLOOR, p20 // 5 * 5)


def merged_share(con, path, params: config.Params, gap: float) -> float:
    """Share of named OSM villages/hamlets grouped with another, if none were in OSM."""
    prioritise.rank(con, path, params)
    con.execute(
        "CREATE OR REPLACE TABLE ranked AS"
        " SELECT * REPLACE (NULL::VARCHAR AS osm_id), osm_id AS true_osm FROM ranked"
    )
    prioritise.group(con, dataclasses.replace(params, group_m=gap))
    return con.execute("""
        WITH m AS (SELECT DISTINCT group_id, true_osm FROM ranked
                   WHERE osm_status = 'in_osm_named' AND osm_place IN ('village', 'hamlet')),
             k AS (SELECT group_id, count(*) AS n FROM m GROUP BY 1)
        SELECT coalesce(avg((n > 1)::INT), 0) FROM m JOIN k USING (group_id)
    """).fetchone()[0]


def suggest_group(con, path, params: config.Params) -> tuple[float, int]:
    """(merged share at the first gap, suggested group_m)."""
    first = None
    for gap in GROUP_GAPS:
        merged = merged_share(con, path, params, gap)
        first = merged if first is None else first
        if merged <= MAX_MERGED:
            return first, gap
    return first, 0


def main() -> None:
    grid3 = {
        r["iso3"]: r
        for r in csv.DictReader(
            line
            for line in open(config.ROOT / "reference" / "grid3_settlement_extents.csv")
            if not line.startswith("#")
        )
    }
    cols = [
        "iso3", "candidates", "ge20", "ge50", "osm_villages", "kept50", "p20", "suggested",
        "kept_suggested", "n_suggested", "official", "unit_kind", "ratio", "grid3_ge5",
        "grid3_ge50", f"merged{GROUP_GAPS[0]}", "group_m",
    ]  # fmt: skip
    print("\t".join(cols))
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial")
    for path in sorted(config.OUT_DIR.glob("*/candidates.parquet")):
        iso3 = path.parent.name
        con.execute(f"CREATE OR REPLACE VIEW c AS SELECT * FROM read_parquet('{path}')")
        n, ge20, ge50 = con.execute(
            "SELECT count(*), count(*) FILTER (building_count >= 20),"
            " count(*) FILTER (building_count >= 50) FROM c"
        ).fetchone()
        con.execute("""
            CREATE OR REPLACE TABLE v AS SELECT max(building_count) AS b FROM c
            WHERE osm_place = 'village' GROUP BY osm_id
        """)
        nv, kept50, p20 = con.execute(
            "SELECT count(*), coalesce(avg((b >= 50)::INT), 0), coalesce(quantile_disc(b, 0.2), 0)"
            " FROM v"
        ).fetchone()
        k = suggest(nv, kept50, p20)
        kept_k = con.execute(f"SELECT coalesce(avg((b >= {k})::INT), 0) FROM v").fetchone()[0]
        n_k = con.execute(f"SELECT count(*) FROM c WHERE building_count >= {k}").fetchone()[0]
        merged, group_m = suggest_group(con, path, config.load_params(iso3))
        ref = prioritise.official(iso3) or {}
        g = grid3.get(iso3)
        row = [
            iso3, n, ge20, ge50, nv, f"{kept50:.2f}", p20, k, f"{kept_k:.2f}", n_k,
            ref.get("count", ""), ref.get("unit_kind", ""),
            f"{n_k / int(ref['count']):.2f}" if ref else "",
            int(g["hamlet_ge5"]) + int(g["small_settlement"]) + int(g["built_up"]) if g else "",
            int(g["small_settlement"]) + int(g["built_up"]) if g else "",
            f"{merged:.2f}", group_m,
        ]  # fmt: skip
        print("\t".join(map(str, row)), flush=True)


if __name__ == "__main__":
    sys.exit(main())
