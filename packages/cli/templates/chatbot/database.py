"""Database connection management for Chatbot service."""

import os
from contextlib import contextmanager
from typing import Generator

import psycopg2
from psycopg2.extras import RealDictCursor


def get_connection():
    """Get a database connection from environment."""
    db_url = os.environ.get("CHATBOT_DB_URL")
    if not db_url:
        raise ValueError("CHATBOT_DB_URL not set")

    # Parse connection URL
    from urllib.parse import urlparse
    parsed = urlparse(db_url)

    return psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port or 5432,
        user=parsed.username,
        password=parsed.password,
        database=parsed.path[1:],  # Remove leading slash
    )


@contextmanager
def get_db() -> Generator:
    """Context manager for database connections."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_cursor(cursor_factory=RealDictCursor) -> Generator:
    """Context manager for database cursor."""
    with get_db() as conn:
        cursor = conn.cursor(cursor_factory=cursor_factory)
        try:
            yield cursor
        finally:
            cursor.close()
