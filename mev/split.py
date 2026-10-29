"""Split admin units with too many candidates into parts, from out/ISO3/ only.

Halve along the longer side at the median candidate until each part fits Field-TM.
Clip the unit outline to each part's box. Run before `prioritise` fills in counts.
"""

import json
import logging
import math
from pathlib import Path

from mev import config, db

log = logging.getLogger(__name__)

MAX_CANDIDATES = 10_000

Box = tuple[float, float, float, float]  # xmin, ymin, xmax, ymax


def halve(features: list[dict], box: Box) -> list[tuple[Box, list[dict]]]:
    """Balanced parts of at most MAX_CANDIDATES features, with boxes that tile `box`."""
    if len(features) <= MAX_CANDIDATES:
        return [(box, features)]
    xmin, ymin, xmax, ymax = box
    lon_scale = math.cos(math.radians((ymin + ymax) / 2))
    key = "lon" if (xmax - xmin) * lon_scale >= ymax - ymin else "lat"
    features = sorted(features, key=lambda f: f["properties"][key])
    mid = len(features) // 2
    cut = features[mid - 1]["properties"][key]
    low, high = features[:mid], features[mid:]
    if key == "lon":
        boxes = (xmin, ymin, cut, ymax), (cut, ymin, xmax, ymax)
    else:
        boxes = (xmin, ymin, xmax, cut), (xmin, cut, xmax, ymax)
    return halve(low, boxes[0]) + halve(high, boxes[1])


def _feature_collection(features: list[dict]) -> str:
    return json.dumps({"type": "FeatureCollection", "features": features})


def split_unit(con, out: Path, unit: dict) -> list[dict]:
    """Write the unit's parts (aoi/, candidates/) and return their index entries."""
    features = json.loads((out / unit["candidates"]).read_text())["features"]
    parts = halve(features, tuple(unit["bbox"]))
    aoi_path = out / unit["aoi"]
    aoi = json.loads(aoi_path.read_text())["features"][0]["geometry"]
    entries = []
    for i, (box, part) in enumerate(parts, start=1):
        pcode, name = f"{unit['pcode']}-{i}", f"{unit['name']} {i}/{len(parts)}"
        geometry, bbox = con.execute(
            """
            SELECT ST_AsGeoJSON(g), [ST_XMin(g), ST_YMin(g), ST_XMax(g), ST_YMax(g)]
            FROM (SELECT ST_Intersection(ST_GeomFromGeoJSON(?), ST_MakeEnvelope(?, ?, ?, ?)) g)
            """,
            [json.dumps(aoi), *box],
        ).fetchone()
        area = {"type": "Feature", "properties": {"pcode": pcode, "name": name}}
        area["geometry"] = json.loads(geometry)
        (out / "aoi" / f"{pcode}.geojson").write_text(_feature_collection([area]))
        (out / "candidates" / f"{pcode}.geojson").write_text(_feature_collection(part))
        counts: dict[str, int] = {}
        for f in part:
            status = f["properties"]["osm_status"]
            counts[status] = counts.get(status, 0) + 1
        entries.append(
            {
                **unit,
                "pcode": pcode,
                "name": name,
                "project_name": f"{unit['project_name']} {i}/{len(parts)}",
                "bbox": bbox,
                "counts": counts,
                "aoi": f"aoi/{pcode}.geojson",
                "candidates": f"candidates/{pcode}.geojson",
            }
        )
    aoi_path.unlink()
    (out / unit["candidates"]).unlink()
    log.info("%s: %d candidates in %d parts", unit["pcode"], len(features), len(parts))
    return entries


def run(iso3: str, params: config.Params) -> None:
    out = config.OUT_DIR / iso3
    path = out / "index.json"
    index = json.loads(path.read_text())
    if all(_size(unit) <= MAX_CANDIDATES for unit in index):
        return
    con = db.duck(attach_pg=False, extensions=("spatial",))
    entries = []
    for unit in index:
        entries += split_unit(con, out, unit) if _size(unit) > MAX_CANDIDATES else [unit]
    path.write_text(json.dumps(entries, indent=1))


def _size(unit: dict) -> int:
    return sum(unit["counts"].values())
