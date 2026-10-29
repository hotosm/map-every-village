"""HTTP downloads with retries, shared by the steps that fetch open data."""

import logging
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "map-every-village (HOT)"}  # Overpass refuses anonymous clients


def _retry(fn, tries: int = 4):
    """HDX and geoBoundaries drop connections, Overpass rate-limits: retry those, 429 and 5xx."""
    for attempt in range(tries):
        try:
            return fn()
        except (httpx.TransportError, httpx.HTTPStatusError) as e:
            status = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else None
            transient = status is None or status == 429 or status >= 500
            if attempt == tries - 1 or not transient:
                raise
            log.warning("%s, retrying", e)
            time.sleep(10 * 2**attempt)


def get(url: str, **params) -> httpx.Response:
    def get():
        r = httpx.get(
            url, params=params or None, headers=HEADERS, timeout=60, follow_redirects=True
        )
        if r.status_code >= 500:
            r.raise_for_status()
        return r

    return _retry(get)


def download(url: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")

    def download():
        with httpx.stream("GET", url, headers=HEADERS, follow_redirects=True, timeout=300) as r:
            r.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)

    _retry(download)
    tmp.rename(dest)
    return dest
