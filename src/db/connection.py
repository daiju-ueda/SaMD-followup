"""Database connection management."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

import psycopg2
import psycopg2.extras

from src.config import settings


def get_connection(autocommit: bool = True):
    """Get a psycopg2 connection using settings."""
    conn = psycopg2.connect(settings.db_dsn)
    conn.autocommit = autocommit
    return conn


@contextmanager
def transaction() -> Generator:
    """Context manager for transactional DB access with auto-rollback."""
    conn = get_connection(autocommit=False)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def dict_cursor(conn):
    """Create a RealDictCursor for a connection."""
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
