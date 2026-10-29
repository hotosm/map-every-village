"""Name hints: populated places from open gazetteers, joined to candidates by `prioritise`.

Writes out/ISO3/hints.parquet (kept after `clean`) and adds the sources to sources.json.
A hint is a name to confirm in the field, not a name to copy into OSM: GeoNames is
CC BY 4.0, which OSM accepts only with a waiver. Evidence: docs/prioritising.md.
"""

import logging
import zipfile

from mev import config, db, fetch, licences

log = logging.getLogger(__name__)

GEONAMES_URL = "https://download.geonames.org/export/dump/"
GEONAMES = licences.require(
    "GeoNames populated places",
    "CC BY 4.0",
    "https://www.geonames.org/",
    "GeoNames (geonames.org), CC BY 4.0",
)
# Populated places (class P), except historical, abandoned and destroyed ones.
SKIP_CODES = ("PPLH", "PPLQ", "PPLW", "PPLCH")
COLUMNS = {
    "geonameid": "BIGINT", "name": "VARCHAR", "asciiname": "VARCHAR",
    "alternatenames": "VARCHAR", "latitude": "DOUBLE", "longitude": "DOUBLE",
    "feature_class": "VARCHAR", "feature_code": "VARCHAR", "country_code": "VARCHAR",
    "cc2": "VARCHAR", "admin1": "VARCHAR", "admin2": "VARCHAR", "admin3": "VARCHAR",
    "admin4": "VARCHAR", "population": "BIGINT", "elevation": "VARCHAR", "dem": "VARCHAR",
    "timezone": "VARCHAR", "modified": "DATE",
}  # fmt: skip


def iso2(iso3: str) -> str:
    """GeoNames' own ISO3 -> ISO2 table."""
    path = fetch.download(
        GEONAMES_URL + "countryInfo.txt", config.DATA_DIR / "geonames_countries.txt"
    )
    for line in path.read_text(encoding="utf-8").splitlines():
        cols = line.split("\t")
        if not line.startswith("#") and len(cols) > 1 and cols[1] == iso3:
            return cols[0]
    raise RuntimeError(f"{iso3}: not in GeoNames countryInfo.txt")


def run(iso3: str, params: config.Params) -> None:
    code = iso2(iso3)
    data = config.country_dir(config.DATA_DIR, iso3)
    zipped = fetch.download(f"{GEONAMES_URL}{code}.zip", data / f"geonames_{code}.zip")
    with zipfile.ZipFile(zipped) as z:
        txt = z.extract(f"{code}.txt", data)

    out = config.OUT_DIR / iso3
    if not out.exists():
        raise RuntimeError(f"{out} missing: run `mev export {iso3}` first")
    con = db.duck(attach_pg=False, extensions=())
    skip = ", ".join(f"'{c}'" for c in SKIP_CODES)
    n = con.execute(f"""
        COPY (
            SELECT 'GeoNames' AS source, geonameid::VARCHAR AS source_id, name,
                alternatenames AS alt_names, feature_code, longitude AS lon, latitude AS lat
            FROM read_csv({db.quote(txt)}, delim = '\t', header = false, quote = '',
                escape = '', columns = {COLUMNS})
            WHERE feature_class = 'P' AND feature_code NOT IN ({skip})
        ) TO {db.quote(out / "hints.parquet")} (FORMAT parquet)
    """).fetchone()[0]

    sources_path = out / "sources.json"
    sources = [s for s in licences.load(sources_path) if s.name != GEONAMES.name]
    licences.save([*sources, GEONAMES], sources_path)
    log.info("%s: %d GeoNames populated places", iso3, n)
