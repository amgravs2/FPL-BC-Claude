import requests
import logging
from typing import Any

logger = logging.getLogger(__name__)

DRAFT_BASE = "https://draft.premierleague.com/api"
FPL_BASE   = "https://fantasy.premierleague.com/api"


def _get(url: str) -> Any:
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_bootstrap() -> dict:
    """bootstrap-static: players, teams, element_types, gameweeks."""
    return _get(f"{DRAFT_BASE}/bootstrap-static")


def fetch_game_state() -> dict:
    """Current and next event IDs."""
    return _get(f"{DRAFT_BASE}/game")


def fetch_league_details(league_id: int) -> dict:
    """League entries (fantasy teams) and H2H matches."""
    return _get(f"{DRAFT_BASE}/league/{league_id}/details")


def fetch_element_status(league_id: int) -> dict:
    """Which fantasy team owns each player."""
    return _get(f"{DRAFT_BASE}/league/{league_id}/element-status")


def fetch_transactions(league_id: int) -> dict:
    """Waiver and trade transactions for the league."""
    return _get(f"{DRAFT_BASE}/draft/league/{league_id}/transactions")


def fetch_gw_live(gw: int) -> dict:
    """Live player stats for a given gameweek."""
    return _get(f"{DRAFT_BASE}/event/{gw}/live")


def fetch_entry_picks(entry_id: int, gw: int) -> dict:
    """A single team's picks for a given gameweek."""
    return _get(f"{DRAFT_BASE}/entry/{entry_id}/event/{gw}")


def fetch_fixtures() -> list:
    """All PL fixtures for the season (from main FPL API)."""
    return _get(f"{FPL_BASE}/fixtures")


def fetch_draft_choices(league_id: int) -> dict:
    """Draft pick order for the league."""
    return _get(f"{DRAFT_BASE}/draft/{league_id}/choices")


def fetch_element_summary(element_id: int) -> dict:
    """Per-fixture history and historical season stats for a player."""
    return _get(f"{FPL_BASE}/element-summary/{element_id}/")
