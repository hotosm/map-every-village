"""PostGIS and DuckDB connections."""

import duckdb
import psycopg

from mev import config


def pg() -> psycopg.Connection:
    # ClientCursor allows several statements per execute, as in sql/*.sql.
    return psycopg.connect(config.DB_URL, autocommit=True, cursor_factory=psycopg.ClientCursor)


def run_sql(conn: psycopg.Connection, name: str, **params) -> None:
    conn.execute((config.SQL_DIR / name).read_text(), params or None)


def init_schema() -> None:
    with pg() as conn:
        run_sql(conn, "schema.sql")


def quote(value: str) -> str:
    """Quote a string literal for DuckDB statements that cannot take parameters."""
    return "'" + str(value).replace("'", "''") + "'"


def duck(
    attach_pg: bool = True, extensions: tuple[str, ...] = ("httpfs", "spatial", "postgres")
) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    for ext in extensions:
        con.install_extension(ext)
        con.load_extension(ext)
    tmp = config.DATA_DIR / "duckdb_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    con.execute(f"SET memory_limit = {quote(config.DUCKDB_MEMORY)}")
    con.execute(f"SET temp_directory = {quote(tmp)}")
    con.execute("SET preserve_insertion_order = false")
    if attach_pg:
        con.execute(f"ATTACH {quote(config.DB_URL)} AS pg (TYPE postgres)")
    return con


def stage(con: duckdb.DuckDBPyConnection, table: str, select_sql: str) -> None:
    """Write a DuckDB query result to a PostGIS staging table."""
    con.execute("CALL pg_clear_cache()")
    con.execute(f"DROP TABLE IF EXISTS pg.{table}")
    con.execute(f"CREATE TABLE pg.{table} AS {select_sql}")
