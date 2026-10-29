"""Cluster grid cells into settlements with ST_ClusterDBSCAN.

Tiles keep memory bounded. Each tile also sees cells just past its edge; a
cell seen by two tiles joins their clusters, so the result is the same as one
DBSCAN over the whole country.
"""

import logging
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor

from mev import buildings, config, db

log = logging.getLogger(__name__)

# Metres per degree of longitude at 37° (Africa's extreme), so margins err large.
MIN_METRES_PER_DEG_LON = 89_000


def utm_srid(lon: float, lat: float) -> int:
    zone = min(int((lon + 180) // 6) + 1, 60)
    return (32600 if lat >= 0 else 32700) + zone


def merge_labels(groups: Iterable[list[int]]) -> dict[int, int]:
    """Union-find: labels sharing a cell join. Returns non-root label -> root."""
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for group in groups:
        root = find(group[0])
        for label in group[1:]:
            other = find(label)
            if other != root:
                parent[other] = root
    return {label: find(label) for label in parent if find(label) != label}


def _parallel(sql: str, jobs: list[dict]) -> None:
    def run_job(job: dict) -> None:
        with db.pg() as conn:
            db.run_sql(conn, sql, **job)

    with ThreadPoolExecutor(config.WORKERS) as pool:
        list(pool.map(run_job, jobs))


def run(iso3: str, params: config.Params) -> None:
    cells = buildings.cells_path(iso3, params)
    if not cells.exists():
        raise RuntimeError(f"{cells} missing: run `mev buildings {iso3}` first")

    con = db.duck()
    src = f"read_parquet({db.quote(cells)})"
    cell = float(params.cell_deg)
    lon, lat = con.execute(
        f"SELECT sum((gx + 0.5) * {cell} * n) / sum(n), sum((gy + 0.5) * {cell} * n) / sum(n)"
        f" FROM {src}"
    ).fetchone()
    srid = utm_srid(lon, lat)
    db.stage(
        con,
        "stage_cells",
        f"SELECT n, n_google, n_microsoft, n_osm, mean_conf, max_conf,"
        f" (gx + 0.5) * {cell} AS lon, (gy + 0.5) * {cell} AS lat FROM {src}",
    )

    with db.pg() as conn:
        conn.execute("DELETE FROM work_meta; INSERT INTO work_meta VALUES (%s, %s)", [iso3, srid])
        db.run_sql(
            conn,
            "cluster_prepare.sql",
            srid=srid,
            tile_deg=params.tile_deg,
            margin_deg=2 * params.eps_m / MIN_METRES_PER_DEG_LON,
        )
        tiles = conn.execute("SELECT DISTINCT tx, ty FROM work_tile_cells").fetchall()

    log.info("%s: clustering %d tiles (EPSG:%d)", iso3, len(tiles), srid)
    _parallel(
        "cluster_tile.sql",
        [{"tx": tx, "ty": ty, "eps_m": params.eps_m} for tx, ty in tiles],
    )

    with db.pg() as conn:
        groups = conn.execute(
            "SELECT array_agg(label) FROM work_labels GROUP BY cell_id HAVING count(*) > 1"
        ).fetchall()
        roots = merge_labels(g[0] for g in groups)
        with conn.cursor().copy("COPY work_roots (label, root) FROM STDIN") as copy:
            for label, root in roots.items():
                copy.write_row((label, root))
        db.run_sql(conn, "cluster_merge.sql", tile_deg=params.tile_deg)

    _parallel(
        "cluster_aggregate.sql",
        [
            {
                "part": part,
                "parts": config.WORKERS,
                "eps_m": params.eps_m,
                "srid": srid,
                "min_buildings": params.min_buildings,
            }
            for part in range(config.WORKERS)
        ],
    )

    with db.pg() as conn:
        n = conn.execute("SELECT count(*) FROM work_clusters").fetchone()[0]
    log.info("%s: %d settlements (%d tile-edge merges)", iso3, n, len(roots))
