# Prioritising candidates

`prioritise` ranks candidates for field mapping. It keeps them all: a small
candidate may be a homestead or part of a village, and official village counts
often describe territories rather than separate groups of houses.

It reads only `out/ISO3/`, so it also works after the database has been cleaned:

```bash
just mev prioritise @countries/africa.txt
```

## Classes and priority

`size_class` is based on building count: `town` at 1,000 or more
(`town_buildings`), `village` at the country's `village_buildings` threshold,
and `hamlet` below it. These are working size classes for planning a survey.

| Priority | Candidates |
| -------: | ---------- |
| 1 | Village or town, missing from OSM |
| 2 | Village or town, unnamed in OSM |
| 3 | Hamlet, missing or unnamed in OSM |
| 4 | Any size, named in OSM; check in the field |

- `candidates/{pcode}.geojson` is sorted by priority, then largest first.
- `villages/{pcode}.geojson` contains priorities 1–2 for a first Field-TM campaign.
  `index.json` gives the file path and priority counts for each unit.
- `priority.txt` reports counts by class, priority and group, plus comparisons
  with OSM villages and official counts.

## Choosing the village threshold

The default is 50 buildings, [GRID3's](https://grid3.org/news/grid3-updates-settlement-extents-for-all-of-sub-saharan-africa)
boundary between hamlets and small settlement areas. Local building patterns
vary, so we check it against `place=village` in OSM, using the largest candidate
matched to each place.

`scripts/calibrate.py` suggests a threshold:

1. Keep 50 if there are fewer than 100 matched OSM villages, or at least 80%
   have at least 50 buildings.
2. Otherwise use their 20th percentile, rounded down to a multiple of 5, with
   a minimum of 20.

Run `just calibrate`, review the suggestions, then set `village_buildings` in
`params.toml` and rerun `prioritise`. The minimum of 20 limits how many small
homesteads count as villages. It also means some countries stay below the 80%
target. OSM imports and uneven mapping affect this check.

## Grouping candidates (`group_id`)

A village may have a core and several outlying candidates. They can share a
`group_id` to help plan visits and join field results later. Their outlines and
priorities stay separate; fieldwork must confirm whether they are one village.

Each candidate links to the largest bigger neighbour within `group_m`. Following
these links ends at a candidate whose ID becomes the `group_id`. The gap is
estimated from centroids and areas, with radii capped at 500 m. A candidate
matched to an OSM place can only link to one matched to that same place.

This avoids joining every nearby candidate in a chain across a hillside, though
it can still group distinct villages. Calibration tests it with OSM matches
hidden, then checks how often named villages and hamlets end up together. It
chooses the largest of 200, 150 or 100 m with at most 10% grouped with another
named place; otherwise grouping is off (`group_m = 0`).

Headline counts stay per candidate. Adding a group's buildings together can
turn several hamlets into a village-size group and increase the count.
`priority.txt` shows both counts.

## Name hints

`mev hints` downloads GeoNames populated places. `prioritise` adds the nearest
name within an estimated 500 m of each candidate's outline: `hint_name`,
`hint_source` and `hint_distance_m` (0 means inside the estimated outline).
The distance uses centroid and area, rather than the actual outline.

Use the hint to ask about a name in the field. GeoNames is CC BY 4.0; copying
it into OSM requires a waiver. Record the name confirmed in the field.

`priority.txt` reports agreement with nearby OSM names using Jaro-Winkler
similarity ≥ 0.9. Agreement can be inflated by shared source data or reduced
by spelling differences.

## Evidence and experiments (October 2026)

### Size thresholds and official counts

At 50 or more buildings, our count is a median 1.18 times GRID3's, compared
with 1.41 across all sizes ([comparison data](../reference/grid3_settlement_extents.csv)).
Country calibration lowers Uganda's threshold to 20, keeping 78% of matched
OSM villages instead of 59%; Sierra Leone goes from 52% to 84%.

[Official counts and sources](../reference/official_settlements.csv) provide
another check, but the units differ. For physical settlements, our village-size
counts range from 0.67 times the official count in Gambia to 1.58 in Niger.
Administrative villages and chiefdoms can contain several settlements, producing
much higher ratios (Burkina Faso 3.61, Eswatini 10.8). Old or incomplete lists
also limit the comparison. Don't tune towards a matching total alone.

### Grouping results

With OSM matches hidden, **merged** is the share of named OSM places grouped
with another named place. **Split** is the share of matched places still spread
across several groups; the value before grouping is in brackets.

| Country | `group_m` | Merged | Split |
| ------- | --------: | -----: | ----: |
| ETH | 200 | 3% | 23% (49%) |
| BFA | 200 | 4% | 25% (56%) |
| TZA | 200 | 5% | 36% (57%) |
| ZWE | 200 | 4% | 43% (65%) |
| SLE | 200 | 2% | 3% (9%) |
| RWA | 200 | 8% | 23% (34%) |
| MWI | 100 | 9% | 50% (60%) |
| UGA | 200 (not used) | 34% | 47% (65%) |
| KEN | 200 (not used) | 27% | 44% (64%) |

Grouping is off in Uganda, Kenya and Madagascar because even the smaller gaps
merge too many named places. Counting groups raises village-size totals in
Tanzania (3.8 → 5.0 times the official count) and Ethiopia (5.3 → 6.5).

Two alternatives were dropped:

- Joining all nearby candidates by distance merged 80% of OSM villages in
  Uganda and 92% in Rwanda at 100 m. Where safe, it found only 1–4% more villages.
- A threshold chosen to separate OSM villages from hamlets lost about a quarter
  of known villages. The tags overlap too much (Sierra Leone: Youden's J 0.13).

Splitting the building grid at density peaks was also tested. It was fast
(about 0.5 s per 0.5° tile), but increased counts, so it wasn't added.

### Name hint distance

The 500 m limit comes from declining agreement with OSM names at larger gaps.
This test used difflib similarity ≥ 0.8 on letters only, unlike the current
report's Jaro-Winkler measure.

| Country | Inside | 0–250 m | 250–500 m | 500–1000 m |
| ------- | -----: | ------: | --------: | ---------: |
| SLE | 82% | 71% | 65% | 45% |
| NGA | 73% | 78% | 71% | 55% |
| TZA | 58% | 53% | 43% | 38% |
| UGA | 50% | 56% | 48% | 21% |

OSM and GeoNames sometimes share GNS source data, so agreement isn't an
independent measure of accuracy.

### Other name sources considered

| Source | Licence | Status |
| ------ | ------- | ------ |
| [GRID3 settlement names](https://grid3.org/geospatial-data-nigeria) (NGA 2020, COD v8.0) | CC BY 4.0 | Compatible. Downloads sit behind bot protection, so it would be a manual download. Not added yet |
| [NGA GNS](https://geonames.nga.mil/) | No restrictions (US Government) | Mostly duplicates GeoNames in Africa |
| [Overture places](https://docs.overturemaps.org/guides/places) | CDLA Permissive 2.0 (some sources CC0, Apache) | Points of interest, not settlements |
| Overture divisions (localities) | ODbL | Derived from OSM, which we already use |
| OCHA COD populated places (HDX) | Varies per country | Only some countries; check each licence |
| Wikidata | CC0 | Not checked for village coverage |

### Satellite embeddings (OlmoEarth v1.3)

[OlmoEarth's](https://source.coop/olmoearth/olmoearth-v1-3-embeddings) annual
10 m embeddings were tested in 6 rural 10 km windows per country. A logistic
regression on 64 dimensions was trained on 4 windows (labels: candidate
outlines vs > 300 m away) and tested on the other 2:

| Country | AUC | Found at 1% false positives: 5–9 / 10–19 / 20–49 / 50+ buildings |
| ------- | --: | ---------------------------------------------------------------- |
| SLE | 0.97–0.99 | 83–100% / 100% / 100% / 100% |
| MWI | 0.92–0.95 | 77–98% / 91–97% / 99–100% / 100% |
| ZWE | 0.94–0.97 | 46–94% / 86–100% / 100% / 100% |
| BFA | 0.91–0.97 | 75–86% / 90–96% / 97–100% / 100% |

Training on a single window gave poor transfer to other windows (AUC 0.49–0.94).
The embeddings detect settlements well, but footprints already cover most of
these. Possible uses are finding settlements without footprints and ranking
small candidates once field results provide labels.

They haven't been added to the pipeline:

- Reading 64 dimensions takes ~65 MB per 100 km² (7 s). Uganda would need
  about 150 GB and Africa about 20 TB per year, compared with 85 GB of
  footprints for Africa.
- The OlmoEarth Artifact License carries use restrictions into derivatives,
  so the pilot treated embeddings as an internal aid, outside the ODbL outputs.

The pilot scripts aren't in the repo. The setup above records how it was run.
