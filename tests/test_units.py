import argparse
import json

import duckdb
import pytest

from mev import boundaries, buildings, cli, config, fetch, licences
from mev.boundaries import pick_layer
from mev.cluster import utm_srid


@pytest.mark.parametrize(
    ("licence", "expected"),
    [
        ("cc-by-igo", "CC BY-IGO"),
        ("hdx-odc-odbl", "ODbL"),
        ("Creative Commons Attribution 4.0 International (CC BY 4.0)", "CC BY 4.0"),
        ("Open Data Commons Open Database License 1.0", "ODbL"),
        ("Public Domain", "CC0"),
        ("hdx-other", None),
        ("Creative Commons Attribution-NonCommercial 4.0 (CC BY-NC 4.0)", None),
        ("GADM: free for academic and non-commercial use, redistribution not allowed", None),
    ],
)
def test_normalise(licence, expected):
    assert licences.normalise(licence) == expected


def test_require_rejects_closed_licence():
    with pytest.raises(licences.LicenceError):
        licences.require("x", "hdx-other", "", "")


def test_params_merge(tmp_path):
    path = tmp_path / "params.toml"
    path.write_text(
        "[default]\neps_m = 100\n[default.place_buffer_m]\nvillage = 500\nhamlet = 250\n"
        "[SLE]\neps_m = 80\n[SLE.place_buffer_m]\nhamlet = 300\n"
    )
    params = config.load_params("SLE", path)
    assert params.eps_m == 80
    assert params.place_buffer_m == {"village": 500, "hamlet": 300}
    assert config.load_params("MWI", path).eps_m == 100


def test_params_unknown_key(tmp_path):
    path = tmp_path / "params.toml"
    path.write_text("[default]\nepsilon = 1\n")
    with pytest.raises(ValueError, match="epsilon"):
        config.load_params("SLE", path)


@pytest.mark.parametrize(
    ("lon", "lat", "srid"),
    [(-13.2, 8.48, 32628), (28.0, -26.2, 32735), (36.8, -1.3, 32737), (179.9, 0, 32660)],
)
def test_utm_srid(lon, lat, srid):
    assert utm_srid(lon, lat) == srid


def test_pick_layer_prefers_edge_matched():
    names = ["sle_admin2.geojson", "sle_admin2_em.geojson", "sle_admin3.geojson"]
    assert pick_layer(names, 2) == "sle_admin2_em.geojson"
    assert pick_layer(names, 3) == "sle_admin3.geojson"
    assert pick_layer(names, 4) is None


def test_pick_layer_shapefiles():
    names = ["rwa_adm1_2006.shp", "x/tza_admbnda_adm3_2018.shp", "bwa_admbnda_adm0.shp"]
    assert pick_layer(names, 1) == "rwa_adm1_2006.shp"
    assert pick_layer(names, 3) == "x/tza_admbnda_adm3_2018.shp"
    assert pick_layer(names, 0) == "bwa_admbnda_adm0.shp"
    assert pick_layer(["bwa_admbndl_ALL_2011.shp"], 1) is None


def test_aggregate_cells(tmp_path):
    src, dest = tmp_path / "b.parquet", tmp_path / "cells.parquet"
    con = duckdb.connect()
    # Two buildings share a cell; one low-confidence Google building is dropped.
    con.execute(f"""
        COPY (SELECT * FROM (VALUES
            ({{'xmin': 1e-5, 'ymin': 1e-5, 'xmax': 3e-5, 'ymax': 3e-5}}, 'google', 0.9),
            ({{'xmin': 2e-5, 'ymin': 2e-5, 'xmax': 4e-5, 'ymax': 4e-5}}, 'microsoft', NULL),
            ({{'xmin': 1e-3, 'ymin': 1e-3, 'xmax': 1.02e-3, 'ymax': 1.02e-3}}, 'google', 0.5)
        ) t(bbox, bf_source, confidence)) TO '{src}' (FORMAT parquet)
    """)
    params = config.Params(min_confidence=0.65)
    con.execute(buildings.aggregate_sql(str(src), str(dest), params))
    rows = con.execute(f"SELECT gx, gy, n, n_google, n_microsoft FROM '{dest}'").fetchall()
    assert rows == [(0, 0, 2, 1, 1)]


def test_read_codes(tmp_path):
    from mev.cli import read_codes

    path = tmp_path / "list.txt"
    path.write_text("# header\nsle\nMWI  # comment\n\n")
    assert read_codes(["NGA", f"@{path}"]) == ["NGA", "SLE", "MWI"]


def test_merge_labels():
    from mev.cluster import merge_labels

    roots = merge_labels([[1, 2], [3, 4], [2, 3], [5, 6]])

    def root(x):
        return roots.get(x, x)

    assert len({root(x) for x in (1, 2, 3, 4)}) == 1
    assert root(5) == root(6) != root(1)


def test_write_units_csv(tmp_path):
    index = [
        {"pcode": "U1", "name": "One", "level": 2, "counts": {"missing": 3, "in_osm_named": 1},
         "priority_counts": {"1": 2, "3": 1, "4": 1}, "bbox": [0, 0, 1, 1]},
        {"pcode": "U2", "name": "Two", "level": 2, "counts": {}},
    ]  # fmt: skip
    path = tmp_path / "units.csv"
    cli.write_units_csv(index, path)
    assert path.read_text().splitlines() == [
        "pcode,name,level,p1,p2,p3,p4,missing,in_osm_unnamed,in_osm_named",
        "U1,One,2,2,0,1,1,3,0,1",
        "U2,Two,2,0,0,0,0,0,0,0",
    ]


def _relation(rel_id: int, level: str, ring: list[tuple[float, float]], **tags) -> dict:
    """An Overpass `out geom` relation with one closed outer way."""
    geometry = [{"lon": x, "lat": y} for x, y in [*ring, ring[0]]]
    return {
        "type": "relation",
        "id": rel_id,
        "members": [{"type": "way", "ref": rel_id, "role": "outer", "geometry": geometry}],
        "tags": {"boundary": "administrative", "admin_level": level, **tags},
    }


def test_osm_admin_keeps_units_inside_the_country(tmp_path, monkeypatch):
    # Country 0..2 x 0..2; a province inside it; a neighbour's province (touching) at 2..3.
    elements = [
        _relation(1, "2", [(0, 0), (2, 0), (2, 2), (0, 2)], **{"ISO3166-1:alpha3": "TST"}),
        _relation(2, "4", [(0.5, 0.5), (1.5, 0.5), (1.5, 1.5), (0.5, 1.5)], name="Inside"),
        _relation(3, "4", [(2, 0), (3, 0), (3, 2), (2, 2)], name="Neighbour"),
    ]

    def fake_download(url, dest):
        dest.write_text(json.dumps({"elements": elements}))
        return dest

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(fetch, "download", fake_download)
    source, layers = boundaries.osm_admin("TST")
    assert source.licence == "ODbL"
    assert [layer.level for layer in layers] == [0, 1]
    con = duckdb.connect()
    con.execute("LOAD spatial")
    names = con.execute(f"SELECT name FROM ST_Read('{layers[1].path}')").fetchall()
    assert names == [("Inside",)]


def _args(**kw):
    defaults = {"cmd": "run", "skip_done": False, "keep_going": False, "keep": True}
    return argparse.Namespace(**{**defaults, **kw})


def _failing_step(iso3, params):
    raise RuntimeError("boom")


def test_process_skips_done_and_stops_or_continues_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUT_DIR", tmp_path)
    (tmp_path / "TST").mkdir()
    (tmp_path / "TST" / cli.DONE).touch()
    steps = {"fail": _failing_step}
    assert cli.process("TST", steps, _args(skip_done=True))  # done: never runs the step
    with pytest.raises(RuntimeError):
        cli.process("TST", steps, _args())
    assert not cli.process("TST", steps, _args(keep_going=True))
