FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /uvx /bin/

# Venv outside /app, so mounting the repo over /app keeps it.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_CACHE_DIR=/tmp/uv \
    HOME=/home/mev \
    PATH=/opt/venv/bin:$PATH
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project
COPY . .
RUN mkdir -p /home/mev && uv sync --frozen \
    && python -c "import duckdb; c = duckdb.connect(); [c.install_extension(e) for e in ('httpfs', 'spatial', 'postgres')]" \
    && chmod -R a+rwX /home/mev
