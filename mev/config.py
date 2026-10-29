"""Paths, settings and per-country parameters."""

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"
CONFIGS_DIR = ROOT / "configs"
PARAMS_FILE = ROOT / "params.toml"

DATA_DIR = Path(os.environ.get("MEV_DATA_DIR", ROOT / "data"))
OUT_DIR = Path(os.environ.get("MEV_OUT_DIR", ROOT / "out"))
STATS_DIR = ROOT / "stats"  # small per-country summaries, committed
DB_URL = os.environ.get(
    "MEV_DB_URL", f"postgresql://mev:mev@localhost:{os.environ.get('MEV_DB_PORT', '5439')}/mev"
)
DUCKDB_MEMORY = os.environ.get("MEV_DUCKDB_MEMORY", "4GB")
WORKERS = int(os.environ.get("MEV_WORKERS", "4"))

# HOT's country exports, produced by oex (hotosm/osm-country-exports).
OEX_BASE_URL = os.environ.get(
    "MEV_OEX_BASE_URL", "https://production-raw-data-api.s3.amazonaws.com/ISO3"
)
VIDA_URL = (
    "https://data.source.coop/vida/google-microsoft-osm-open-buildings/geoparquet/"
    "by_country/country_iso={iso3}/{iso3}.parquet"
)


@dataclass(frozen=True)
class Params:
    cell_deg: float = 0.00025
    min_confidence: float = 0.65
    eps_m: float = 100
    min_buildings: int = 5
    tile_deg: float = 0.5
    project_admin_level: int = 3
    village_buildings: int = 50  # GRID3's small settlement threshold
    town_buildings: int = 1000
    group_m: float = 200
    place_buffer_m: dict[str, float] = field(default_factory=dict)


def load_params(iso3: str, path: Path = PARAMS_FILE) -> Params:
    """Merge [default] with the country's [ISO3] table (nested tables merge by key)."""
    raw = tomllib.loads(path.read_text())
    merged = dict(raw.get("default", {}))
    for key, value in raw.get(iso3, {}).items():
        merged[key] = {**merged.get(key, {}), **value} if isinstance(value, dict) else value
    unknown = set(merged) - {f.name for f in fields(Params)}
    if unknown:
        raise ValueError(f"Unknown keys in {path.name}: {sorted(unknown)}")
    return Params(**merged)


def country_dir(base: Path, iso3: str) -> Path:
    path = base / iso3
    path.mkdir(parents=True, exist_ok=True)
    return path
