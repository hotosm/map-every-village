-- Rebuilds the work tables from stage_cells (loaded by DuckDB).
DROP TABLE IF EXISTS work_cells, work_tile_cells, work_labels, work_roots, work_cell_gid,
    work_clusters;

CREATE UNLOGGED TABLE work_cells AS
SELECT
    row_number() OVER () AS id,
    n, n_google, n_microsoft, n_osm, mean_conf, max_conf, lon, lat,
    ST_Transform(ST_SetSRID(ST_MakePoint(lon, lat), 4326), %(srid)s) AS geom_m
FROM stage_cells;
ALTER TABLE work_cells ADD PRIMARY KEY (id);

-- Cells near a tile edge also go to the neighbour; shared cells join clusters.
CREATE UNLOGGED TABLE work_tile_cells AS
SELECT DISTINCT
    floor((c.lon + dx * %(margin_deg)s) / %(tile_deg)s)::int AS tx,
    floor((c.lat + dy * %(margin_deg)s) / %(tile_deg)s)::int AS ty,
    c.id AS cell_id
FROM work_cells c,
    (VALUES (-1), (0), (1)) AS a (dx),
    (VALUES (-1), (0), (1)) AS b (dy);
CREATE INDEX ON work_tile_cells (tx, ty);

-- label: per-tile cluster id, unique across tiles.
CREATE UNLOGGED TABLE work_labels (cell_id bigint, tx int, ty int, label bigint);
CREATE UNLOGGED TABLE work_roots (label bigint PRIMARY KEY, root bigint NOT NULL);

CREATE UNLOGGED TABLE work_clusters (
    id bigserial PRIMARY KEY,
    n_buildings integer,
    n_cells integer,
    n_google integer,
    n_microsoft integer,
    n_osm integer,
    mean_conf real,
    max_conf real,
    centroid_m geometry(Point),
    hull_m geometry
);

DROP TABLE stage_cells;
ANALYZE work_cells;
ANALYZE work_tile_cells;
