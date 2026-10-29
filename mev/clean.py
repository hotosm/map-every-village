"""Free disk once a country is exported. Only out/{ISO3}/ is kept."""

import logging
import shutil

from mev import config, db

log = logging.getLogger(__name__)


def run(iso3: str, params: config.Params) -> None:
    with db.pg() as conn:
        conn.execute(
            """
            DROP TABLE IF EXISTS work_cells, work_tile_cells, work_labels, work_roots,
                work_cell_gid, work_clusters;
            DELETE FROM work_meta;
            DELETE FROM candidates WHERE iso3 = %(iso3)s;
            DELETE FROM osm_places WHERE iso3 = %(iso3)s;
            DELETE FROM osm_health WHERE iso3 = %(iso3)s;
            DELETE FROM admin_units WHERE iso3 = %(iso3)s;
            """,
            {"iso3": iso3},
        )
        # Freed space is reused by the next country.
        conn.execute("VACUUM")
    shutil.rmtree(config.DATA_DIR / iso3, ignore_errors=True)
    shutil.rmtree(config.DATA_DIR / "duckdb_tmp", ignore_errors=True)
    log.info("%s: cleaned database and data/%s", iso3, iso3)
