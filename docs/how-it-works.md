# How it works

We group AI-detected buildings into settlement candidates, check them against
OSM, and rank them for field mapping in Field-TM. Candidates missing from OSM
or without a name come first. It runs one country at a time.

```text
VIDA buildings ──► buildings ──► cluster ──► classify ──► export ──► out/ISO3/
                   (DuckDB)      (PostGIS)   ▲
OSM via oex ───────────────────────────────┤
OCHA COD-AB / geoBoundaries ───────────────┘
```

## Steps

| Step | What it does |
| ---- | ------------ |
| `boundaries` | Admin levels 0–4 from OCHA COD-AB, else geoBoundaries, else OSM. Drops any level covering under 95% of the country |
| `osm` | OSM places and health facilities, from HOT's oex exports |
| `buildings` | Counts VIDA buildings per ~28 m grid cell. Reads bounding boxes only, so it's quick |
| `cluster` | Groups nearby cells into settlements (DBSCAN), each with an outline |
| `classify` | Adds admin P-codes, OSM status and the 3 nearest health facilities |
| `export` | Writes the files below |
| `split` | Splits any admin unit with over 10,000 candidates into balanced parts (`Province 1/6`), as Field-TM projects |
| `hints` | GeoNames populated places, as names to confirm in the field (`hints.parquet`) |
| `prioritise` | Size class, field priority, `group_id` and the nearest name hint. See [prioritising](prioritising.md) |
| `evaluate` | How many OSM hamlets and villages we found (`evaluate.txt`) |
| `clean` | Deletes downloads and database rows. Only `out/ISO3/` is kept |

Clustering runs in 0.5° tiles to keep memory down. Tiles overlap a little, and
clusters sharing a cell are joined, so the result is the same as clustering the
whole country in one go.

## Clustering defaults

Two buildings are in the same settlement if you can hop between them in steps
of at most `eps_m`. A settlement needs at least `min_buildings`.

| Setting | Default | Why |
| ------- | ------- | --- |
| `min_confidence` | 0.65 | Google's lowest value, so we keep every prediction. A false positive costs a field visit; a missed village is never visited |
| `cell_deg` | ~28 m | Halves the rows to cluster. Small next to the 100 m gap |
| `eps_m` | 100 m | Joins the houses of a village, but rarely joins two villages |
| `min_buildings` | 5 | Below this it's mostly single homesteads and false detections |

### Testing on Sierra Leone

Sierra Leone has 1.77 M buildings and 9.4 k OSM hamlets and villages. A hamlet
counts as found within 250 m, a village within 500 m (the same as `classify`).

| `eps_m` | `min_buildings` | Hamlets found | Villages found | Candidates | Not in OSM | Covering 2+ OSM places |
| ------: | --------------: | ------------: | -------------: | ---------: | ---------: | ---------------------: |
| 50 | 3 | 87% | 98% | 33.6 k | 18.4 k | 108 |
| 50 | 5 | 85% | 98% | 24.2 k | 12.0 k | 108 |
| 75 | 5 | 85% | 98% | 19.6 k | 9.6 k | 137 |
| **100** | **5** | **85%** | **98%** | **17.7 k** | **8.6 k** | **156** |
| 100 | 3 | 87% | 99% | 22.4 k | 12.5 k | 156 |
| 100 | 10 | 80% | 95% | 13.6 k | 5.5 k | 154 |
| 150 | 5 | 86% | 98% | 16.8 k | 8.2 k | 214 |

- **`eps_m` barely changes what we find.** Too small splits a village into
  several candidates, so it's visited twice. Too big merges neighbours into one.
  At 100 m, under 1% of candidates cover two known OSM places. Merges of two
  unmapped villages can't be counted this way.
- **`min_buildings` matters most.** 3 finds 2% more hamlets, but adds 4.7 k
  mostly tiny candidates. 10 loses 5% of hamlets.
- **The ~28 m grid is fine.** An ~11 m grid gives identical recall and 0.9%
  more candidates, but runs 4x slower.
- **13% of OSM hamlets have no buildings nearby.** Either the footprints are
  missing (thatch, tree cover) or the OSM node is in the wrong place. Villages
  like this get no candidate at all, so a pilot should also survey a few areas
  with no candidates. Settlement rasters should help (not built yet).
- **OSM recall has limits.** Mapped villages aren't a random sample, and VIDA
  includes OSM buildings. Recall doesn't show how many `missing` candidates
  are real villages. Check a random sample on imagery before a pilot, then
  use field results to measure accuracy.

A candidate is a place to look, not always one village. Towns form one big
cluster (all of Freetown is a single 474 k-building candidate), and roadside
building can join neighbouring villages. Mappers should record every village
they find within a candidate.

### Tuning a country

Start with the defaults. Check `evaluate.txt` and imagery before adding
country overrides to `params.toml`. See [tuning](tuning.md) for the trade-offs
and [running it](running.md#tuning-a-country) for commands.

## OSM status

A candidate is in OSM if an OSM place is close enough to its outline. How close
depends on the place type (`place_buffer_m`): 250 m for a hamlet, 500 m for a
village, 1.5 km for a town.

If several places qualify, a place inside the outline wins, biggest type first
(a city over its hamlets). Otherwise the closest wins, so a hamlet 100 m away
beats a town 1 km away.

| `osm_status` | Meaning | In the field |
| ------------ | ------- | ------------ |
| `missing` | No OSM place nearby | Full survey |
| `in_osm_unnamed` | OSM place, no name | Add the name |
| `in_osm_named` | Near a named OSM place | Check, see below |

`in_osm_named` only means a named place is nearby, not that it's the same
village. These columns show when to look closer:

| Column | Look closer if |
| ------ | -------------- |
| `osm_distance_m` | Over 0: the place is outside the outline |
| `osm_shared` | Over 1: other candidates got the same place (parts of one village, or a borrowed name) |
| `osm_nearby` | Over 1: several OSM places nearby, so maybe several villages |

In Sierra Leone, about 1 in 4 `in_osm_named` candidates are flagged this way.

Mapped buildings don't count. Only a place node or area does.

## Outputs (`out/ISO3/`)

One Field-TM project per admin unit, at the deepest level up to 3
(`project_admin_level`), so typically a few hundred candidates each. Field-TM's
task splitting divides a project further. Where the deepest open boundaries are
too coarse (Angola's provinces), `split` cuts units of over 10,000 candidates
into parts.

| File | Contents |
| ---- | -------- |
| `candidates.parquet` | All candidates (GeoParquet) |
| `candidates/{pcode}.geojson` | Candidate outlines per admin unit, ready to upload to Field-TM, highest priority first |
| `villages/{pcode}.geojson` | Priority 1–2 only: village-size candidates missing or unnamed in OSM |
| `aoi/{pcode}.geojson` | Admin unit outline, used as the Field-TM project area |
| `index.json` | Per unit: project name, bbox, counts by status and priority, file paths |
| `evaluate.txt` | Hamlets and villages found, and candidate sizes |
| `priority.txt` | Counts by class and priority, against OSM villages and the official count |
| `hints.parquet` | Gazetteer places used for name hints |
| `sources.json` | Inputs, licences, attribution |

`out/` isn't committed. It's several GB for Africa, and every re-run would add
another copy to git history. Instead:

- The small files (`evaluate.txt`, `priority.txt`, `sources.json`, plus
  `params.json`) are copied to `stats/ISO3/` and committed, with `units.csv`:
  `index.json` as one row per unit (counts by priority and status, no bbox or
  paths). That's under 40 KB per country, and a re-run shows up as a readable
  diff. `mev run` and `mev prioritise` both refresh it.
- `scripts/summary.py` writes `stats/summary.csv`: towns and villages missing,
  unnamed and named in OSM, per country (`just summary` prints the totals).
- Full outputs are published as dated releases.

## Licences

Every input must be openly licensed: CC0, PDDL, CC BY 4.0, CC BY-IGO or ODbL
(`mev/licences.py`). Boundary licences are checked on HDX and geoBoundaries at
run time, and `mev check ISO3` lists them. Outputs are ODbL.

**Why not GADM?** Its licence forbids redistribution and commercial use, and we
redistribute boundaries in the outputs and Field-TM project areas. OCHA COD-AB
is openly licensed, endorsed by governments, and has the P-codes partners
already use. If HDX says a COD-AB was sourced from GADM (e.g. Djibouti), we
skip it and use geoBoundaries. Where neither is open (Angola: COD-AB from GADM,
geoBoundaries CC BY-SA), we use OSM's admin boundaries via Overpass. They're
ODbL like our outputs, but often only down to provinces, so outputs are split
by province.

## Not built yet

- Settlements in rasters (Google 2.5D Temporal, WSF 2019) that the footprints
  miss.
- PMTiles of boundaries and candidates for maps.
- Pulling Field-TM submissions back to mark candidates as verified.
