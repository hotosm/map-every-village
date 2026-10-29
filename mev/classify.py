"""Assign admin units, OSM status and nearest health facilities to clusters."""

import json
import logging

from mev import config, db

log = logging.getLogger(__name__)


def run(iso3: str, params: config.Params) -> None:
    with db.pg() as conn:
        row = conn.execute("SELECT iso3, srid FROM work_meta").fetchone()
        if row is None or row[0] != iso3:
            raise RuntimeError(f"Work tables hold {row and row[0]}: run `mev cluster {iso3}` first")
        db.run_sql(
            conn,
            "classify.sql",
            iso3=iso3,
            srid=row[1],
            buffers=json.dumps(params.place_buffer_m),
            max_buffer=max(params.place_buffer_m.values()),
        )
        counts = conn.execute(
            "SELECT osm_status, count(*) FROM candidates WHERE iso3 = %s GROUP BY 1 ORDER BY 1",
            [iso3],
        ).fetchall()
    log.info("%s: %s", iso3, ", ".join(f"{status} {n}" for status, n in counts))
