-- Label the cells of one tile (with its overlap) by DBSCAN cluster.
INSERT INTO work_labels (cell_id, tx, ty, label)
SELECT
    c.id, %(tx)s, %(ty)s,
    ((%(tx)s + 2000)::bigint * 4000 + (%(ty)s + 2000)) * 10000000
        + ST_ClusterDBSCAN(c.geom_m, %(eps_m)s, 1) OVER ()
FROM work_cells c
JOIN work_tile_cells tc ON tc.cell_id = c.id
WHERE tc.tx = %(tx)s AND tc.ty = %(ty)s;
