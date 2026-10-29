"""Towns and villages missing or unnamed in OSM, per country, from stats/*/priority.txt.

just summary   # writes stats/summary.csv, prints Africa totals

Hamlets are left out. "Unnamed" means in OSM but without a name. group_* columns
count groups of candidates (group_id) instead, by their total buildings.
"""

import csv
import re

from mev import config, prioritise

ROW = re.compile(r"^\s+(town|village)\s+(\d+)\s+(\d+)\s+(\d+)$")
HINT = re.compile(r"^\s+([12]) .*?(\d+) of\s+(\d+)$")
COLS = [
    "iso3", "town_missing", "town_unnamed", "town_named", "village_missing",
    "village_unnamed", "village_named", "group_town_missing", "group_town_unnamed",
    "group_town_named", "group_village_missing", "group_village_unnamed",
    "group_village_named", "p1_hinted", "p2_hinted", "village_buildings", "official",
    "unit_kind",
]  # fmt: skip
SUMS = COLS[1:15]


def parse(iso3: str) -> dict:
    text = (config.STATS_DIR / iso3 / "priority.txt").read_text()
    row, section = {"iso3": iso3}, None
    for line in text.splitlines():
        if not line.startswith(" "):  # a heading: "SLE: classes ...", "Groups ...", ...
            section = line.split()[0]
        elif section in (f"{iso3}:", "Groups") and (m := ROW.match(line)):
            prefix = "group_" if section == "Groups" else ""
            for status, n in zip(("missing", "unnamed", "named"), m.groups()[1:], strict=True):
                row[f"{prefix}{m[1]}_{status}"] = int(n)
        elif section == "Name" and (m := HINT.match(line)):
            row[f"p{m[1]}_hinted"] = int(m[2])
    row["village_buildings"] = re.search(r"village >= (\d+)", text)[1]
    ref = prioritise.official(iso3) or {}
    row |= {"official": ref.get("count", ""), "unit_kind": ref.get("unit_kind", "")}
    return row


def main() -> None:
    rows = [parse(p.parent.name) for p in sorted(config.STATS_DIR.glob("*/priority.txt"))]
    with (config.STATS_DIR / "summary.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, COLS)
        w.writeheader()
        w.writerows(rows)
    total = {k: sum(r.get(k, 0) for r in rows) for k in SUMS}
    print(f"{len(rows)} countries (hamlets excluded)")
    print(f"  {'class':14} {'missing':>9} {'unnamed':>9} {'named':>9}")
    for cls in ("town", "village", "group_town", "group_village"):
        n = [total[f"{cls}_{s}"] for s in ("missing", "unnamed", "named")]
        print(f"  {cls:14} {n[0]:>9,} {n[1]:>9,} {n[2]:>9,}")
    top = sorted(rows, key=lambda r: -(r["village_missing"] + r["town_missing"]))[:10]
    p1, hinted = total["town_missing"] + total["village_missing"], total["p1_hinted"]
    print(f"Priority 1 with a name hint: {hinted:,} of {p1:,} ({hinted / p1:.0%})")
    print("Most missing (town + village):")
    for r in top:
        print(f"  {r['iso3']}  {r['village_missing'] + r['town_missing']:>8,}")


if __name__ == "__main__":
    main()
