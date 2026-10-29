"""Command line: `mev <step> ISO3 [ISO3 ...]`, or `@file` for a list of codes."""

import argparse
import csv
import dataclasses
import json
import logging
import re
import shutil
import sys
import time
from pathlib import Path

from mev import (
    boundaries,
    buildings,
    classify,
    clean,
    cluster,
    config,
    db,
    evaluate,
    export,
    hints,
    licences,
    osm,
    prioritise,
    split,
)

log = logging.getLogger("mev")

# `mev run` order. `clean` is skipped with --keep.
STEPS = {
    "boundaries": boundaries.run,
    "osm": osm.run,
    "buildings": buildings.run,
    "cluster": cluster.run,
    "classify": classify.run,
    "export": export.run,
    "split": split.run,
    "hints": hints.run,
    "prioritise": prioritise.run,
    "evaluate": evaluate.run,
    "clean": clean.run,
}
DONE = ".done"  # written to out/ISO3/ when every `run` step succeeded
STATS = ("evaluate.txt", "priority.txt", "sources.json")
STATUSES = ("missing", "in_osm_unnamed", "in_osm_named")
PRIORITIES = ("1", "2", "3", "4")


def iso3_arg(value: str) -> str:
    value = value.upper()
    if not re.fullmatch(r"[A-Z]{3}", value):
        raise argparse.ArgumentTypeError(f"not an ISO3 code: {value}")
    return value


def read_codes(values: list[str]) -> list[str]:
    """Expand `@path` into the ISO3 codes in that file (`#` starts a comment)."""
    codes = []
    for value in values:
        if value.startswith("@"):
            for line in Path(value[1:]).read_text().splitlines():
                line = line.split("#")[0].strip()
                if line:
                    codes.append(iso3_arg(line))
        else:
            codes.append(iso3_arg(value))
    return codes


def check(iso3: str) -> None:
    """Print each input's licence, without downloading data."""
    for source in (licences.VIDA, licences.OSM):
        print(f"{iso3}  ok  {source.licence:10} {source.name}")
    found = boundaries.cod_ab_source(iso3)
    if found:
        print(f"{iso3}  ok  {found[0].licence:10} {found[0].name}")
    else:
        print(f"{iso3}  --  no usable COD-AB; geoBoundaries gbOpen, else OSM, will be used")


def run_country(iso3: str, steps: dict, args: argparse.Namespace) -> None:
    params = config.load_params(iso3)
    for name, step in steps.items():
        kwargs = {}
        if name == "osm":
            kwargs["osm_source"] = args.osm_source
        if name == "buildings":
            kwargs["force"] = args.force
        start = time.monotonic()
        step(iso3, params, **kwargs)
        log.info("%s: %s done in %.0fs", iso3, name, time.monotonic() - start)


def write_units_csv(index: list[dict], path: Path) -> None:
    """index.json as one row per admin unit, without bbox and file paths (diffs well)."""
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pcode", "name", "level", *(f"p{p}" for p in PRIORITIES), *STATUSES])
        for u in index:
            pc, sc = u.get("priority_counts", {}), u["counts"]
            w.writerow(
                [u["pcode"], u["name"], u["level"], *(pc.get(p, 0) for p in PRIORITIES)]
                + [sc.get(s, 0) for s in STATUSES]
            )


def save_stats(iso3: str, params: config.Params) -> None:
    """Copy the small outputs and params to stats/ISO3/, which is committed."""
    dest = config.country_dir(config.STATS_DIR, iso3)
    for name in STATS:
        shutil.copy(config.OUT_DIR / iso3 / name, dest / name)
    index = json.loads((config.OUT_DIR / iso3 / "index.json").read_text())
    write_units_csv(index, dest / "units.csv")
    (dest / "params.json").write_text(json.dumps(dataclasses.asdict(params), indent=1) + "\n")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="mev", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ["run", *STEPS, "check"]:
        p = sub.add_parser(name)
        p.add_argument("iso3", nargs="+", help="ISO3 codes, or @file with one per line")
        if name in ("run", "osm"):
            p.add_argument("--osm-source", choices=["published", "oex"], default="published")
        if name in ("run", "buildings"):
            p.add_argument("--force", action="store_true", help="re-download building cells")
        if name == "run":
            p.add_argument("--keep", action="store_true", help="skip clean: keep data and DB rows")
            p.add_argument("--keep-going", action="store_true", help="continue after a failure")
            p.add_argument("--skip-done", action="store_true", help="skip finished countries")
    return parser.parse_args(argv)


def log_failure(iso3: str, error: Exception, keep: bool) -> None:
    if isinstance(error, RuntimeError):  # our own, expected errors: no traceback
        log.error("%s: failed: %s", iso3, error)
    else:
        log.exception("%s: failed", iso3)
    if keep:
        return
    try:  # keep the machine small; a retry starts fresh
        clean.run(iso3, config.load_params(iso3))
    except Exception:
        log.exception("%s: clean after failure failed", iso3)


def process(iso3: str, steps: dict, args: argparse.Namespace) -> bool:
    """Run the steps for one country. False if it failed and `--keep-going` was given."""
    if args.cmd == "run" and args.skip_done and (config.OUT_DIR / iso3 / DONE).exists():
        log.info("%s: already done, skipping", iso3)
        return True
    try:
        run_country(iso3, steps, args)
        if args.cmd in ("run", "prioritise"):
            save_stats(iso3, config.load_params(iso3))
        if args.cmd == "run":
            (config.OUT_DIR / iso3 / DONE).touch()
    except Exception as e:
        if not (args.cmd == "run" and args.keep_going):
            raise
        log_failure(iso3, e, args.keep)
        return False
    return True


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    codes = read_codes(args.iso3)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.cmd == "check":
        for iso3 in codes:
            check(iso3)
        return

    if args.cmd not in ("split", "hints", "prioritise"):  # work on out/ alone
        db.init_schema()
    if args.cmd != "run":
        steps = {args.cmd: STEPS[args.cmd]}
    else:
        steps = {k: v for k, v in STEPS.items() if not (k == "clean" and args.keep)}

    failed = [iso3 for iso3 in codes if not process(iso3, steps, args)]
    if failed:
        log.error("Failed: %s", " ".join(failed))
        sys.exit(1)
