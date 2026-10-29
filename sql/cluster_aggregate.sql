-- One row per settlement, for this worker's share of cluster ids.
INSERT INTO work_clusters
    (n_buildings, n_cells, n_google, n_microsoft, n_osm, mean_conf, max_conf, centroid_m, hull_m)
SELECT
    n_buildings, n_cells, n_google, n_microsoft, n_osm, mean_conf, max_conf, centroid_m,
    -- Outline: eps/2 discs around cells. Towns use a 100 m grid to stay fast.
    CASE
        WHEN n_cells > 20000 THEN ST_SimplifyPreserveTopology(
            ST_Buffer(ST_RemoveRepeatedPoints(ST_SnapToGrid(pts, 100)), 100, 'quad_segs=2'), 20)
        ELSE ST_SimplifyPreserveTopology(ST_Buffer(pts, %(eps_m)s / 2.0, 'quad_segs=2'), 10)
    END
FROM (
    SELECT
        sum(c.n) AS n_buildings,
        count(*) AS n_cells,
        sum(c.n_google) AS n_google,
        sum(c.n_microsoft) AS n_microsoft,
        sum(c.n_osm) AS n_osm,
        sum(c.mean_conf * c.n_google)
            / nullif(sum(c.n_google) FILTER (WHERE c.mean_conf IS NOT NULL), 0) AS mean_conf,
        max(c.max_conf) AS max_conf,
        ST_SetSRID(ST_MakePoint(
            sum(ST_X(c.geom_m) * c.n) / sum(c.n), sum(ST_Y(c.geom_m) * c.n) / sum(c.n)
        ), %(srid)s) AS centroid_m,
        ST_Collect(c.geom_m) AS pts
    FROM work_cell_gid g
    JOIN work_cells c ON c.id = g.cell_id
    WHERE mod(g.gid, %(parts)s) = %(part)s
    GROUP BY g.gid
    HAVING sum(c.n) >= %(min_buildings)s
) x;
