set dotenv-load

export MEV_UID := `id -u`
export MEV_GID := `id -g`
mev := "docker compose run --rm mev"
# For steps that read only out/ or stats/: no database
mev-nodb := "docker compose run --rm --no-deps mev"

default:
    @just --list

# Build the pipeline image
setup:
    docker compose build mev

db-up:
    docker compose up -d --wait db

db-down:
    docker compose down

# Delete the database volume
db-reset:
    docker compose down -v

psql:
    docker compose exec db psql -U mev

# e.g. `just run SLE MWI`, or `just run @countries/africa.txt`
run *ARGS:
    {{mev}} mev run {{ARGS}}

# Any mev command, e.g. `just mev cluster SLE`
mev *ARGS:
    {{mev}} mev {{ARGS}}

# Every country in a list, skipping finished ones; logs to logs/
batch LIST="countries/africa.txt":
    mkdir -p logs
    docker compose run --rm -T mev mev run --keep-going --skip-done @{{LIST}} 2>&1 | tee -a logs/batch-$(date +%Y%m%d-%H%M).log

# Towns and villages missing/unnamed in OSM, all countries; writes stats/summary.csv
summary:
    {{mev-nodb}} python scripts/summary.py

# Evidence for village_buildings, from out/*/candidates.parquet; writes calibrate.tsv
calibrate:
    {{mev-nodb}} python scripts/calibrate.py > calibrate.tsv

test:
    {{mev}} pytest

lint:
    {{mev}} sh -c "ruff check . && ruff format --check ."
