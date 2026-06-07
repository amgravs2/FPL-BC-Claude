import os
import psycopg2
from psycopg2.extras import execute_values
import logging

logger = logging.getLogger(__name__)


def get_conn():
    """Open and return a new DB connection."""
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return psycopg2.connect(url)


def get_active_season_id() -> int | None:
    """
    Return the active season (today falls within its dates).
    Falls back to the most recent season if none is currently active —
    handles the off-season gap between seasons.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM seasons
                WHERE CURRENT_DATE BETWEEN start_date AND end_date
                LIMIT 1;
            """)
            row = cur.fetchone()
            if row:
                return row[0]
            # Off-season: return the most recently ended season
            cur.execute("""
                SELECT id FROM seasons
                ORDER BY end_date DESC
                LIMIT 1;
            """)
            row = cur.fetchone()
            return row[0] if row else None


def get_season_league_id(season_id: int) -> int | None:
    """Return the FPL Draft league_id for a given season."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT league_id FROM seasons WHERE id = %s", (season_id,))
            row = cur.fetchone()
            return row[0] if row else None
