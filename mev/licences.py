"""Open licence allowlist. Every input must pass before it is used."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

ALLOWED = {"CC0", "PDDL", "CC BY 4.0", "CC BY-IGO", "ODbL"}

# HDX licence ids.
_IDS = {
    "cc-by-igo": "CC BY-IGO",
    "cc-by": "CC BY 4.0",
    "cc-zero": "CC0",
    "hdx-pddl": "PDDL",
    "hdx-odc-odbl": "ODbL",
    "odbl": "ODbL",
}


class LicenceError(Exception):
    pass


@dataclass(frozen=True)
class Source:
    name: str
    licence: str
    url: str
    attribution: str


def normalise(licence: str) -> str | None:
    text = licence.strip().lower()
    if text in _IDS:
        return _IDS[text]
    if "noncommercial" in text or "non-commercial" in text or "-nc" in text or " nc " in text:
        return None
    if "igo" in text and ("attribution" in text or "cc by" in text):
        return "CC BY-IGO"
    if "open database license" in text or "odbl" in text:
        return "ODbL"
    if "cc0" in text or "public domain" in text:
        return "CC0"
    if "attribution 4.0" in text or "cc by 4.0" in text:
        return "CC BY 4.0"
    return None


def require(name: str, licence: str, url: str, attribution: str) -> Source:
    canonical = normalise(licence)
    if canonical not in ALLOWED:
        raise LicenceError(f"{name}: licence {licence!r} is not on the open allowlist")
    return Source(name, canonical, url, attribution)


VIDA = Source(
    "VIDA Google-Microsoft-OSM Open Buildings",
    "ODbL",
    "https://source.coop/vida/google-microsoft-osm-open-buildings",
    "VIDA; Google Open Buildings; Microsoft Global ML Building Footprints; "
    "© OpenStreetMap contributors",
)
OSM = Source(
    "OpenStreetMap (oex country exports)",
    "ODbL",
    "https://github.com/osgeonepal/oex",
    "© OpenStreetMap contributors",
)


def save(sources: list[Source], path: Path) -> None:
    path.write_text(json.dumps([asdict(s) for s in sources], indent=2))


def load(path: Path) -> list[Source]:
    return [Source(**s) for s in json.loads(path.read_text())]
