-- Clusters to candidates: admin units, OSM status, nearest health facilities.
DROP TABLE IF EXISTS work_places, work_health;

CREATE TEMP TABLE work_places AS
SELECT
    p.osm_id, p.name, p.place,
    (%(buffers)s::jsonb ->> coalesce(p.place, p.landuse))::float8 AS buffer_m,
    ST_Transform(p.geom, %(srid)s) AS geom_m
FROM osm_places p
WHERE p.iso3 = %(iso3)s
    AND %(buffers)s::jsonb ? coalesce(p.place, p.landuse)
    AND (p.place IS NOT NULL OR p.name IS NOT NULL);
CREATE INDEX ON work_places USING gist (geom_m);
ANALYZE work_places;

CREATE TEMP TABLE work_health AS
SELECT osm_id, name, kind, ST_Transform(geom, %(srid)s) AS geom_m
FROM osm_health
WHERE iso3 = %(iso3)s;
CREATE INDEX ON work_health USING gist (geom_m);
ANALYZE work_health;

CREATE INDEX IF NOT EXISTS work_clusters_hull_idx ON work_clusters USING gist (hull_m);
ANALYZE work_clusters;

DELETE FROM candidates WHERE iso3 = %(iso3)s;

INSERT INTO candidates (
    candidate_id, iso3, building_count, cell_count, n_google, n_microsoft, n_osm,
    mean_confidence, max_confidence, area_m2,
    adm1_pcode, adm1_name, adm2_pcode, adm2_name, adm3_pcode, adm3_name, adm4_pcode, adm4_name,
    osm_status, osm_id, osm_name, osm_place, osm_distance_m, osm_nearby,
    health_facilities, geom, hull
)
SELECT
    %(iso3)s || '-' || ST_GeoHash(k.geom, 10),
    %(iso3)s, k.n_buildings, k.n_cells, k.n_google, k.n_microsoft, k.n_osm,
    k.mean_conf, k.max_conf, ST_Area(k.hull_m),
    a1.pcode, a1.name, a2.pcode, a2.name, a3.pcode, a3.name, a4.pcode, a4.name,
    CASE
        WHEN p.osm_id IS NULL THEN 'missing'
        WHEN p.name IS NULL THEN 'in_osm_unnamed'
        ELSE 'in_osm_named'
    END,
    p.osm_id, p.name, p.place, round(p.d), coalesce(p.n, 0),
    h.facilities, k.geom, ST_Transform(k.hull_m, 4326)
FROM (
    SELECT *, ST_Transform(centroid_m, 4326) AS geom FROM work_clusters
) k
LEFT JOIN LATERAL (
    SELECT pcode, name FROM admin_units a
    WHERE a.iso3 = %(iso3)s AND a.level = 1 AND ST_Intersects(a.geom, k.geom) LIMIT 1
) a1 ON true
LEFT JOIN LATERAL (
    SELECT pcode, name FROM admin_units a
    WHERE a.iso3 = %(iso3)s AND a.level = 2 AND ST_Intersects(a.geom, k.geom) LIMIT 1
) a2 ON true
LEFT JOIN LATERAL (
    SELECT pcode, name FROM admin_units a
    WHERE a.iso3 = %(iso3)s AND a.level = 3 AND ST_Intersects(a.geom, k.geom) LIMIT 1
) a3 ON true
LEFT JOIN LATERAL (
    SELECT pcode, name FROM admin_units a
    WHERE a.iso3 = %(iso3)s AND a.level = 4 AND ST_Intersects(a.geom, k.geom) LIMIT 1
) a4 ON true
LEFT JOIN LATERAL (
    -- Inside the outline first, biggest place type winning (a city over its hamlets).
    -- Else the closest, so a nearby hamlet beats a town 1 km away.
    SELECT *, count(*) OVER () AS n FROM (
        SELECT wp.osm_id, wp.name, wp.place, wp.buffer_m, ST_Distance(wp.geom_m, k.hull_m) AS d
        FROM work_places wp
        WHERE ST_DWithin(wp.geom_m, k.hull_m, %(max_buffer)s)
            AND ST_DWithin(wp.geom_m, k.hull_m, wp.buffer_m)
    ) x
    ORDER BY d > 0, CASE WHEN d = 0 THEN -buffer_m ELSE d END, name IS NULL
    LIMIT 1
) p ON true
LEFT JOIN LATERAL (
    SELECT jsonb_agg(jsonb_build_object(
        'osm_id', x.osm_id, 'name', x.name, 'kind', x.kind, 'distance_m', round(x.d)
    ) ORDER BY x.d) AS facilities
    FROM (
        SELECT wh.osm_id, wh.name, wh.kind, ST_Distance(wh.geom_m, k.centroid_m) AS d
        FROM work_health wh
        ORDER BY wh.geom_m <-> k.centroid_m
        LIMIT 3
    ) x
) h ON true
ON CONFLICT (candidate_id) DO NOTHING;

-- How many candidates got the same OSM place (> 1: parts of one village, or a borrowed name).
UPDATE candidates c SET osm_shared = s.n
FROM (
    SELECT osm_id, count(*) AS n FROM candidates
    WHERE iso3 = %(iso3)s AND osm_id IS NOT NULL GROUP BY 1
) s
WHERE c.iso3 = %(iso3)s AND c.osm_id = s.osm_id;
