# Tuning clustering

Keep the default clustering settings unless local checks show a problem.
[Prioritising](prioritising.md) ranks smaller candidates for later visits, so
there is less reason to remove them just to reduce the total.

`eps_m` controls the gap between building cells; `min_buildings` removes small
clusters. Raising either can hide villages. Check `evaluate.txt` and a sample
on imagery before changing them. OSM recall alone is limited by what has
already been mapped.

For commands and rerunning a country, see [Running it](running.md#tuning-a-country).

## What the sweeps showed

- In sparse settlement such as Namibia, a larger gap can join homesteads
  without merging many known villages. At 200 m, candidates roughly halved
  and recall improved.
- In dense settlement such as Eswatini and Lesotho, a larger gap joins
  distinct villages. Raising `min_buildings` reduced candidates with fewer
  merges, but lost some hamlets.
- Sierra Leone gains little from changing the defaults. A higher minimum
  loses hamlets; a larger gap joins more known places.
- A 500 m gap caused extensive merging and slow outline generation, so it
  was dropped from the sweep.

These tests predate prioritising. The following proposals were **not applied**
to `params.toml`; they need imagery checks before adoption:

| Country | Proposed `eps_m` | Proposed `min_buildings` | Result |
| ------- | ---------------: | -----------------------: | ------ |
| NAM | 300 | 10 | 135.6 k → 27.1 k candidates; hamlet recall 84% → 81% |
| SWZ | 100 | 20 | 32.0 k → 11.2 k; hamlet recall 100% → 97% |
| LSO | 100 | 10 | 13.8 k → 10.6 k; hamlet recall 99% → 96% |

The original aim was to reduce candidates while losing at most 3–5 percentage
points of hamlet recall, keeping village recall ≥ 95% and merged places near
or below 15%. Lesotho already exceeds that merge target at the defaults.

## Sweep results

`scripts/tune.py` measures recall at the same distances as `classify`: 250 m
for hamlets and 500 m for villages. It uses a ~28 m grid up to `eps_m = 150`
and ~55 m above that, so gap and grid size both change in the larger-gap runs.

- **Merged:** share of OSM places inside outlines that share an outline with
  another place (`in_merged / inside`).
- **Split:** share of matched OSM places claimed by several candidates
  (`split_places / matched_places`).

| Country | `eps_m` | `min_buildings` | Candidates | Missing | Hamlets | Villages | Merged | Split |
| ------- | ------: | --------------: | ---------: | ------: | ------: | -------: | -----: | ----: |
| SWZ | 100 | 5 | 32.0 k | 30.6 k | 100% | 100% | 9% | 69% |
| SWZ | 100 | 10 | 21.1 k | 19.9 k | 100% | 100% | 9% | 63% |
| SWZ | 100 | 20 | 11.2 k | 10.4 k | 97% | 100% | 9% | 50% |
| SWZ | 200 | 10 | 5.1 k | 4.7 k | 100% | 100% | 52% | 14% |
| SWZ | 300 | 20 | 1.4 k | 1.2 k | 100% | 100% | 70% | 6% |
| SLE | 100 | 5 | 17.7 k | 8.6 k | 85% | 98% | 6% | 9% |
| SLE | 100 | 10 | 13.6 k | 5.5 k | 80% | 95% | 6% | 6% |
| SLE | 100 | 20 | 9.7 k | 3.1 k | 67% | 85% | 7% | 4% |
| SLE | 200 | 5 | 16.5 k | 8.1 k | 86% | 98% | 10% | 5% |
| SLE | 300 | 10 | 12.3 k | 4.9 k | 83% | 96% | 15% | 2% |
| LSO | 100 | 5 | 13.8 k | 6.4 k | 99% | 100% | 19% | 36% |
| LSO | 100 | 10 | 10.6 k | 4.2 k | 96% | 100% | 19% | 29% |
| LSO | 100 | 20 | 8.1 k | 2.7 k | 87% | 99% | 20% | 21% |
| LSO | 200 | 10 | 6.3 k | 2.1 k | 98% | 100% | 41% | 8% |
| NAM | 100 | 5 | 135.6 k | 131.4 k | 84% | 96% | 4% | 49% |
| NAM | 100 | 10 | 81.8 k | 79.1 k | 65% | 92% | 4% | 40% |
| NAM | 200 | 5 | 74.3 k | 72.2 k | 88% | 97% | 8% | 25% |
| NAM | 200 | 10 | 47.9 k | 46.2 k | 77% | 96% | 8% | 20% |
| NAM | 300 | 10 | 27.1 k | 25.8 k | 81% | 96% | 15% | 9% |
| NAM | 300 | 20 | 13.5 k | 12.4 k | 64% | 91% | 17% | 6% |

Eswatini has only 436 OSM hamlets, villages and towns, so its recall is weak
evidence.

## Further checks

The share of buildings in clusters under 50 buildings was 56% in Namibia,
20–30% in Eswatini, Guinea-Bissau, Morocco and Burundi, and under 20% in the
other countries checked. This may help identify countries to investigate;
it hasn't been adopted as an automatic tuning rule.

Togo and Burundi sweeps were interrupted. Botswana, Morocco, Rwanda, Malawi
and Ethiopia still need testing. Useful extra runs are Eswatini at 150 m and
Namibia at 300 m with a 5-building minimum. Check imagery too, especially in
Morocco where farm buildings may be counted as homes.

## Running a sweep

Use a scratch database: the script overwrites work tables and cannot share a
database with a running batch. It prints raw counts as TSV.

```bash
docker run -d --rm --name mev-tune -p 127.0.0.1:5440:5432 \
  -e POSTGRES_USER=mev -e POSTGRES_PASSWORD=mev -e POSTGRES_DB=mev postgis/postgis:18-3.6
MEV_DB_URL=postgresql://mev:mev@localhost:5440/mev MEV_DATA_DIR=/tmp/tune \
  uv run python scripts/tune.py TGO BDI > tune.tsv
```
