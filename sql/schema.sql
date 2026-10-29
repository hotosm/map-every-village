CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS admin_units (
    iso3 text NOT NULL,
    level smallint NOT NULL,
    pcode text NOT NULL,
    name text,
    source text NOT NULL,
    geom geometry(MultiPolygon, 4326) NOT NULL,
    PRIMARY KEY (iso3, level, pcode)
);
CREATE INDEX IF NOT EXISTS admin_units_geom_idx ON admin_units USING gist (geom);

CREATE TABLE IF NOT EXISTS osm_places (
    iso3 text NOT NULL,
    osm_id text NOT NULL,
    name text,
    alt_name text,
    place text,
    landuse text,
    geom geometry(Geometry, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS osm_places_iso3_idx ON osm_places (iso3);

CREATE TABLE IF NOT EXISTS osm_health (
    iso3 text NOT NULL,
    osm_id text NOT NULL,
    name text,
    kind text,
    geom geometry(Point, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS osm_health_iso3_idx ON osm_health (iso3);

-- One row per settlement candidate. osm_status: missing | in_osm_unnamed | in_osm_named.
CREATE TABLE IF NOT EXISTS candidates (
    candidate_id text PRIMARY KEY,
    iso3 text NOT NULL,
    building_count integer NOT NULL,
    cell_count integer NOT NULL,
    n_google integer NOT NULL,
    n_microsoft integer NOT NULL,
    n_osm integer NOT NULL,
    mean_confidence real,
    max_confidence real,
    area_m2 real,
    adm1_pcode text,
    adm1_name text,
    adm2_pcode text,
    adm2_name text,
    adm3_pcode text,
    adm3_name text,
    adm4_pcode text,
    adm4_name text,
    osm_status text NOT NULL,
    osm_id text,
    osm_name text,
    osm_place text,
    osm_distance_m real,  -- matched place to outline, 0 = inside
    osm_nearby integer NOT NULL DEFAULT 0,  -- OSM places nearby (> 1: maybe merged villages)
    osm_shared integer NOT NULL DEFAULT 0,  -- candidates matched to the same place
    health_facilities jsonb,
    geom geometry(Point, 4326) NOT NULL,
    hull geometry(Geometry, 4326),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS candidates_iso3_idx ON candidates (iso3);
CREATE INDEX IF NOT EXISTS candidates_geom_idx ON candidates USING gist (geom);
CREATE INDEX IF NOT EXISTS candidates_hull_idx ON candidates USING gist (hull);

-- Per-country work state for the country being clustered.
CREATE TABLE IF NOT EXISTS work_meta (
    iso3 text PRIMARY KEY,
    srid integer NOT NULL
);
