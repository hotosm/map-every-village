-- Global cluster id per cell: its own tile's label, mapped to the merged root.
CREATE UNLOGGED TABLE work_cell_gid AS
SELECT l.cell_id, coalesce(r.root, l.label) AS gid
FROM work_labels l
JOIN work_cells c ON c.id = l.cell_id
LEFT JOIN work_roots r ON r.label = l.label
WHERE l.tx = floor(c.lon / %(tile_deg)s)::int AND l.ty = floor(c.lat / %(tile_deg)s)::int;
ANALYZE work_cell_gid;
