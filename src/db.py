import psycopg
from psycopg.rows import dict_row
from contextlib import contextmanager
from src import config


def get_connection(**kwargs):
    return psycopg.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        dbname=config.DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        autocommit=kwargs.get("autocommit", False),
    )


@contextmanager
def transaction():
    """Context manager that yields a cursor within a transaction.
    Commits on success, rolls back on exception."""
    conn = get_connection()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def connection():
    """Context manager that yields a raw connection (for bulk operations)."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()
