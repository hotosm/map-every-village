"""Needs the dev database (`just db-up`); skipped without it. Clobbers work tables."""

import dataclasses

import duckdb
import psycopg
import pytest

from mev import buildings, cluster, config, db


@pytest.fixture
def pg_or_skip():
    try:
        psycopg.connect(config.DB_URL, connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("database not running")
    db.init_schema()
    with db.pg() as conn:
        busy = conn.execute("SELECT iso3 FROM work_meta WHERE iso3 <> 'TST'").fetchone()
    if busy:  # a batch is using the work tables; this test would wreck its country
        pytest.skip(f"work tables hold {busy[0]}")


def test_chain_across_tiles_is_one_settlement(pg_or_skip, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    # Tiles of ~1.1 km; a 121-cell chain ~6.6 km long crosses several of them.
    params = dataclasses.replace(config.load_params("TST"), tile_deg=0.01, min_buildings=5)
    chain = [(gx, 0) for gx in range(-40, 202, 2)]  # 0.0005° (~55 m) apart
    hamlet = [(4000 + i, 4000) for i in range(0, 10, 2)]  # 5 cells, far away
    rows = ", ".join(f"({gx}, {gy}, 1, 1, 0, 0, 0.8, 0.8)" for gx, gy in chain + hamlet)
    duckdb.execute(
        f"COPY (SELECT * FROM (VALUES {rows}) t(gx, gy, n, n_google, n_microsoft, n_osm,"
        f" mean_conf, max_conf)) TO '{buildings.cells_path('TST', params)}' (FORMAT parquet)"
    )
    cluster.run("TST", params)
    with db.pg() as conn:
        sizes = sorted(r[0] for r in conn.execute("SELECT n_buildings FROM work_clusters"))
    assert sizes == [5, 121]
