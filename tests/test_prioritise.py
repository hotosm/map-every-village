import json

import duckdb
import pytest

from mev import config, prioritise, split

CANDIDATES = [
    # id, buildings, status
    ("a", 22, "missing"),
    ("b", 20, "missing"),
    ("c", 55, "in_osm_unnamed"),
    ("d", 8, "missing"),
    ("e", 60, "missing"),
    ("f", 1200, "in_osm_named"),
]


def make_out(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    out = tmp_path / "out" / "TST"
    (out / "candidates").mkdir(parents=True)

    def osm(s, value):
        return "NULL" if s == "missing" else repr(value)

    rows = ", ".join(
        f"('{c}', {n}, '{s}', {osm(s, 'n' + c)}, {osm(s, 'village')}, {osm(s, 'n' + c)},"
        " 1000.0, ST_Point(30, 0))"
        for c, n, s in CANDIDATES
    )
    con = duckdb.connect()
    con.execute("LOAD spatial")
    con.execute(
        f"COPY (SELECT * FROM (VALUES {rows}) t(candidate_id, building_count, osm_status,"
        f" osm_id, osm_place, osm_name, area_m2, geometry))"
        f" TO '{out / 'candidates.parquet'}' (FORMAT parquet)"
    )
    features = [
        {"type": "Feature", "properties": {"candidate_id": c, "building_count": n}}
        for c, n, _ in CANDIDATES
    ]
    (out / "candidates" / "U1.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features})
    )
    (out / "index.json").write_text(json.dumps([{"pcode": "U1"}, {"pcode": "U2"}]))
    return out


def classes(out):
    return duckdb.execute(
        f"SELECT candidate_id, size_class, priority FROM '{out / 'candidates.parquet'}' ORDER BY 1"
    ).fetchall()


def groups(out):
    return dict(
        duckdb.execute(
            f"SELECT candidate_id, group_id FROM '{out / 'candidates.parquet'}'"
        ).fetchall()
    )


def test_prioritise(tmp_path, monkeypatch):
    out = make_out(tmp_path, monkeypatch)
    prioritise.run("TST", config.Params(village_buildings=50, town_buildings=1000))
    assert classes(out) == [
        ("a", "hamlet", 3),
        ("b", "hamlet", 3),
        ("c", "village", 2),
        ("d", "hamlet", 3),
        ("e", "village", 1),
        ("f", "town", 4),
    ]

    # All at one point: unmatched candidates join the biggest (f); c is matched to its own
    # OSM place, so it stays apart.
    assert groups(out) == {"a": "f", "b": "f", "c": "c", "d": "f", "e": "f", "f": "f"}

    fc = json.loads((out / "candidates" / "U1.geojson").read_text())
    assert [f["properties"]["candidate_id"] for f in fc["features"]] == list("ecabdf")
    # Field-TM labels entities by feature id and needs strings, not nulls.
    assert [f["id"] for f in fc["features"]] == list("ecabdf")
    assert all(v is not None for f in fc["features"] for v in f["properties"].values())
    assert fc["features"][0]["properties"]["hint_name"] == ""
    villages = json.loads((out / "villages" / "U1.geojson").read_text())
    assert [f["properties"]["candidate_id"] for f in villages["features"]] == list("ec")
    index = json.loads((out / "index.json").read_text())
    assert index[0]["priority_counts"] == {"1": 1, "2": 1, "3": 3, "4": 1}
    assert index[0]["villages"] == "villages/U1.geojson"
    assert index[1]["villages"] is None

    # A re-run with another threshold replaces the columns.
    prioritise.run("TST", config.Params(village_buildings=20))
    assert dict((c, p) for c, _, p in classes(out))["b"] == 1
    cols = duckdb.execute(f"DESCRIBE SELECT * FROM '{out / 'candidates.parquet'}'").fetchall()
    assert [c[0] for c in cols].count("priority") == 1


def test_group_climbs_to_local_peaks():
    # A row of candidates 200 m apart (x in metres along the equator), sizes 10 30 20 25 40:
    # two peaks (30, 40). Distance grouping would chain all five into one.
    rows = [("p", 10, 0), ("q", 30, 200), ("r", 20, 400), ("s", 25, 600), ("t", 40, 800)]
    con = duckdb.connect()
    con.execute("LOAD spatial")
    values = ", ".join(f"('{c}', {n}, NULL, 0.0, ST_Point({x / 111320}, 0))" for c, n, x in rows)
    con.execute(
        f"CREATE TABLE ranked AS SELECT * FROM (VALUES {values})"
        " t(candidate_id, building_count, osm_id, area_m2, geometry)"
    )
    prioritise.group(con, config.Params(group_m=250))
    got = dict(con.execute("SELECT candidate_id, group_id FROM ranked").fetchall())
    # r's bigger neighbours are q (30) and s (25): it climbs to q. s climbs to t.
    assert got == {"p": "q", "q": "q", "r": "q", "s": "t", "t": "t"}

    con.execute("ALTER TABLE ranked DROP COLUMN group_id")
    prioritise.group(con, config.Params(group_m=0))
    n = con.execute("SELECT count(*) FROM ranked WHERE group_id <> candidate_id").fetchone()[0]
    assert n == 0


def test_hints(tmp_path, monkeypatch):
    out = make_out(tmp_path, monkeypatch)
    prioritise.run("TST", config.Params())
    hint = f"SELECT count(hint_name) FROM '{out / 'candidates.parquet'}'"
    assert duckdb.execute(hint).fetchone()[0] == 0  # no hints.parquet: empty columns

    # One place on the candidates (all at 30, 0) and one ~1.1 km away, beyond 500 m.
    duckdb.execute(
        "COPY (SELECT * FROM (VALUES ('Nf', 'GeoNames', 30.0, 0.0),"
        " ('Far', 'GeoNames', 30.01, 0.0)) t(name, source, lon, lat))"
        f" TO '{out / 'hints.parquet'}' (FORMAT parquet)"
    )
    prioritise.run("TST", config.Params())
    got = duckdb.execute(
        f"SELECT DISTINCT hint_name, hint_distance_m FROM '{out / 'candidates.parquet'}'"
    ).fetchall()
    assert got == [("Nf", 0)]
    # f is named "nf" in OSM: the hint agrees with it.
    assert "same 100%" in (out / "priority.txt").read_text()
    fc = json.loads((out / "candidates" / "U1.geojson").read_text())
    assert fc["features"][0]["properties"]["hint_name"] == "Nf"


def test_split_halves_big_units(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUT_DIR", tmp_path)
    monkeypatch.setattr(split, "MAX_CANDIDATES", 2)
    out = tmp_path / "TST"
    (out / "aoi").mkdir(parents=True)
    (out / "candidates").mkdir()
    # Five candidates in a row along a 0..4 x 0..1 unit: parts of 2, 1 and 2 (halved twice).
    features = [
        {"type": "Feature", "properties": {"lon": x + 0.5, "lat": 0.5, "osm_status": "missing"}}
        for x in range(5)
    ]
    square = {"type": "Polygon", "coordinates": [[[0, 0], [5, 0], [5, 1], [0, 1], [0, 0]]]}
    (out / "aoi" / "U1.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": [{"geometry": square}]})
    )
    (out / "candidates" / "U1.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features})
    )
    unit = {
        "pcode": "U1", "name": "One", "level": 1, "project_name": "One",
        "bbox": [0, 0, 5, 1], "counts": {"missing": 5},
        "aoi": "aoi/U1.geojson", "candidates": "candidates/U1.geojson",
    }  # fmt: skip
    (out / "index.json").write_text(json.dumps([unit]))

    split.run("TST", config.Params())

    index = json.loads((out / "index.json").read_text())
    assert [(u["pcode"], u["counts"]["missing"]) for u in index] == [
        ("U1-1", 2), ("U1-2", 1), ("U1-3", 2)
    ]  # fmt: skip
    assert index[0]["project_name"] == "One 1/3"
    assert sum(u["bbox"][2] - u["bbox"][0] for u in index) == pytest.approx(5)  # parts tile it
    assert sorted(p.name for p in (out / "candidates").iterdir()) == [
        f"U1-{i}.geojson" for i in range(1, 4)
    ]
