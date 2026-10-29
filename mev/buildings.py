"""Count buildings per ~28 m grid cell. Reads only VIDA's bbox column, not footprints."""

import logging
from pathlib import Path

from mev import config, db

log = logging.getLogger(__name__)


def cells_path(iso3: str, params: config.Params) -> Path:
    name = f"cells_{params.cell_deg}_{params.min_confidence}.parquet"
    return config.country_dir(config.DATA_DIR, iso3) / name


def aggregate_sql(src: str, dest: str, params: config.Params) -> str:
    cell = float(params.cell_deg)
    return f"""
    COPY (
        SELECT
            gx, gy,
            count(*)::INTEGER AS n,
            count(*) FILTER (WHERE bf_source = 'google')::INTEGER AS n_google,
            count(*) FILTER (WHERE bf_source = 'microsoft')::INTEGER AS n_microsoft,
            count(*) FILTER (WHERE bf_source = 'osm')::INTEGER AS n_osm,
            avg(confidence)::FLOAT AS mean_conf,
            max(confidence)::FLOAT AS max_conf
        FROM (
            SELECT
                floor(((bbox.xmin + bbox.xmax) / 2) / {cell})::BIGINT AS gx,
                floor(((bbox.ymin + bbox.ymax) / 2) / {cell})::BIGINT AS gy,
                bf_source, confidence
            FROM read_parquet({db.quote(src)})
            WHERE bf_source <> 'google' OR confidence >= {float(params.min_confidence)}
        )
        GROUP BY gx, gy
    ) TO {db.quote(dest)} (FORMAT parquet, COMPRESSION zstd)
    """


def run(iso3: str, params: config.Params, force: bool = False) -> Path:
    path = cells_path(iso3, params)
    if path.exists() and not force:
        log.info("%s: using cached %s", iso3, path.name)
        return path
    tmp = path.with_suffix(".part")
    con = db.duck(attach_pg=False)
    con.execute(aggregate_sql(config.VIDA_URL.format(iso3=iso3), str(tmp), params))
    tmp.rename(path)
    n_cells, n = con.execute(
        f"SELECT count(*), sum(n) FROM read_parquet({db.quote(path)})"
    ).fetchone()
    log.info("%s: %d buildings in %d cells", iso3, n, n_cells)
    return path
