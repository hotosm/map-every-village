# Running it

Run countries one at a time from a list, cleaning up after each one.

## Before you start

- **Software:** Docker and [just](https://github.com/casey/just). Everything
  else runs in containers.
- **Machine:** 16 GB RAM, 4+ cores, ~20 GB free disk and a steady connection.
  The building data is ~85 GB on VIDA's server, but we only stream the columns
  we need.
- **Time:** about 15 hours for all of Africa on 10 cores. Sierra Leone takes 2–3
  minutes, and Africa is ~380 times bigger. Set `MEV_WORKERS` to your core count
  to speed up clustering.

## Run

```bash
just setup
just db-up
just mev check @countries/africa.txt   # are all inputs openly licensed?

tmux new -s mev                        # survives a closed terminal
just batch                             # countries/africa.txt, smallest first
# detach: Ctrl-b d    reattach: tmux attach -t mev
```

For a different list: `just batch countries/my-list.txt`, with one ISO3 code
per line (`#` for comments).

## What happens per country

The [pipeline](how-it-works.md#steps) downloads inputs, clusters buildings,
classifies and exports candidates, then splits large units, adds name hints,
prioritises and evaluates the results, and cleans up.

- Only `out/ISO3/` is kept, so disk use peaks at the largest country (Nigeria,
  ~7 GB in PostGIS), not the total.
- If a country fails, it's logged, cleaned up, and the batch moves on.
- A finished country gets an `out/ISO3/.done` marker and is skipped next time.
  So `just batch` is safe to re-run: it retries only what failed or never ran.
- After changing `params.toml`, re-run a country with `just run XXX` (no
  skipping), or delete its `.done`.
- The database skips disk syncs for speed. A crash or power cut can corrupt
  it, so run `just db-reset` before the next batch. Ctrl-C is fine.

## After it completes

```bash
grep -E "Failed|ERROR" logs/batch-*.log    # anything to retry?
ls out/ | wc -l                            # countries done
cat out/*/evaluate.txt                     # what we found
```

To debug one country, keep its data:

```bash
just mev run NGA --keep
just mev clean NGA                     # when done
```

Commit `stats/` to record the run. Then `just db-down` (or `just db-reset` to
delete the database too).

## Tuning a country

Run `just mev run XXX --keep` and read `out/XXX/evaluate.txt`. Check the
[tuning notes](tuning.md) before changing clustering settings in an `[XXX]`
table in `params.toml`.

Building cells stay cached in `data/XXX/` until `clean`, so re-running just
the clustering is quick:

```bash
just mev cluster XXX && just mev classify XXX && just mev export XXX \
  && just mev split XXX && just mev prioritise XXX && just mev evaluate XXX
```

To change `village_buildings`, run `just calibrate` (it reads
`out/`), set the value, then `just mev prioritise XXX`. It needs only `out/XXX/`,
so it works after `clean`.
