import logging
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException, Query
from contextlib import asynccontextmanager

from db import get_active_season_id
from queries import router as query_router
from sync import (
    sync_bootstrap,
    sync_fantasy_teams,
    sync_fantasy_matches,
    sync_player_status,
    sync_transactions,
    sync_fixtures,
    sync_gw_stats,
    sync_lineups,
    sync_standings,
    sync_draft_picks,
    sync_element_summaries,
    sync_pl_team_records,       # NEW
    sync_ownership_from_draft,  # NEW
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("FPL Draft backend starting up")
    yield
    logger.info("FPL Draft backend shutting down")


app = FastAPI(title="FPL Draft Backend", lifespan=lifespan)
app.include_router(query_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/", tags=["health"])
def root():
    return {"status": "ok"}


@app.get("/health", tags=["health"])
def health():
    season_id = get_active_season_id()
    return {"status": "ok", "active_season_id": season_id}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_season(season_id: int | None) -> int:
    sid = season_id or get_active_season_id()
    if not sid:
        raise HTTPException(
            status_code=400,
            detail="No active season found. Pass ?season_id= explicitly or set season dates."
        )
    return sid


# ---------------------------------------------------------------------------
# Sync routes
# Each returns a count summary so you can confirm what landed.
# ---------------------------------------------------------------------------

@app.post("/sync/bootstrap", tags=["sync"])
def route_sync_bootstrap(season_id: int | None = Query(default=None)):
    """
    Syncs: element_types, premier_league_teams, players, gameweeks.
    Also derives and saves season start/end dates from GW1 and GW38 deadlines.
    Run this first on a fresh DB, then daily.
    """
    sid = _resolve_season(season_id)
    result = sync_bootstrap(sid)
    return {"season_id": sid, "synced": result}


@app.post("/sync/fantasy-teams", tags=["sync"])
def route_sync_fantasy_teams(season_id: int | None = Query(default=None)):
    """Syncs fantasy team entries and manager names from the league."""
    sid = _resolve_season(season_id)
    result = sync_fantasy_teams(sid)
    return {"season_id": sid, "synced": result}


@app.post("/sync/matches", tags=["sync"])
def route_sync_matches(season_id: int | None = Query(default=None)):
    """Syncs H2H match results for all gameweeks."""
    sid = _resolve_season(season_id)
    result = sync_fantasy_matches(sid)
    return {"season_id": sid, "synced": result}


@app.post("/sync/player-status", tags=["sync"])
def route_sync_player_status(season_id: int | None = Query(default=None)):
    """Syncs which fantasy team owns each player (or free agent status)."""
    sid = _resolve_season(season_id)
    result = sync_player_status(sid)
    return {"season_id": sid, "synced": result}


@app.post("/sync/transactions", tags=["sync"])
def route_sync_transactions(season_id: int | None = Query(default=None)):
    """Syncs waiver and trade transactions. New records only (ON CONFLICT DO NOTHING)."""
    sid = _resolve_season(season_id)
    result = sync_transactions(sid)
    return {"season_id": sid, "synced": result}


@app.post("/sync/fixtures", tags=["sync"])
def route_sync_fixtures(season_id: int | None = Query(default=None)):
    """Syncs all PL fixtures including scores for completed matches."""
    sid = _resolve_season(season_id)
    result = sync_fixtures(sid)
    return {"season_id": sid, "synced": result}


@app.post("/sync/stats/{gw}", tags=["sync"])
def route_sync_gw_stats(gw: int, season_id: int | None = Query(default=None)):
    """Syncs per-player points and stats for a specific gameweek."""
    if gw < 1 or gw > 38:
        raise HTTPException(status_code=400, detail="GW must be between 1 and 38")
    sid = _resolve_season(season_id)
    result = sync_gw_stats(sid, gw)
    return {"season_id": sid, "gw": gw, "synced": result}


@app.post("/sync/lineups/{gw}", tags=["sync"])
def route_sync_lineups(gw: int, season_id: int | None = Query(default=None)):
    """Syncs all team lineups (picks) for a specific gameweek."""
    if gw < 1 or gw > 38:
        raise HTTPException(status_code=400, detail="GW must be between 1 and 38")
    sid = _resolve_season(season_id)
    result = sync_lineups(sid, gw)
    return {"season_id": sid, "gw": gw, "synced": result}


@app.post("/sync/all", tags=["sync"])
def route_sync_all(season_id: int | None = Query(default=None)):
    """
    Runs all non-GW-specific syncs in the correct order.
    Use for initial load or daily refresh. Does NOT sync per-GW stats/lineups.
    """
    sid = _resolve_season(season_id)
    results = {}
    results["bootstrap"]      = sync_bootstrap(sid)
    results["fantasy_teams"]  = sync_fantasy_teams(sid)
    results["matches"]        = sync_fantasy_matches(sid)
    results["player_status"]  = sync_player_status(sid)
    results["transactions"]   = sync_transactions(sid)
    results["fixtures"]       = sync_fixtures(sid)
    return {"season_id": sid, "synced": results}


@app.post("/sync/standings", tags=["sync"])
def route_sync_standings(season_id: int | None = Query(default=None)):
    """
    Rebuilds standings from fantasy_matches.
    Run after /sync/matches. Calculates W/D/L, league points, and cumulative totals.
    """
    sid = _resolve_season(season_id)
    result = sync_standings(sid)
    return {"season_id": sid, "synced": result}


@app.post("/sync/draft-picks", tags=["sync"])
def route_sync_draft_picks(season_id: int | None = Query(default=None)):
    """Syncs draft pick order for the season."""
    sid = _resolve_season(season_id)
    result = sync_draft_picks(sid)
    return {"season_id": sid, "synced": result}


@app.post("/sync/element-summaries", tags=["sync"])
def route_sync_element_summaries(season_id: int | None = Query(default=None)):
    """
    Syncs per-fixture history and historical season totals for all drafted players.
    Calls FPL element-summary API once per drafted player (~90 calls with 0.1s delay).
    Run once after draft sync — takes ~2 minutes.
    """
    sid = _resolve_season(season_id)
    result = sync_element_summaries(sid)
    return {"season_id": sid, "synced": result}


# =============================================================================
# main.py — ADD these two new sync routes
# =============================================================================
# Add these after the existing /sync/player-status route.
# Also import the new functions at the top of main.py:
#   from sync import (..., sync_ownership_from_draft, sync_pl_team_records)
# =============================================================================
 
 
@app.post("/sync/ownership", tags=["sync"])
def route_sync_ownership(season_id: int | None = Query(default=None)):
    """
    Builds per-GW ownership history from draft picks + accepted transactions.
    Creates/populates player_ownership_history table.
    Run after /sync/draft-picks and /sync/transactions.
    """
    sid = _resolve_season(season_id)
    result = sync_ownership_from_draft(sid)
    return {"season_id": sid, "synced": result}
 
 
@app.post("/sync/pl-records", tags=["sync"])
def route_sync_pl_records(season_id: int | None = Query(default=None)):
    """
    Derives W/D/L/points/position for each PL team from the fixtures table.
    The FPL bootstrap returns all zeros during the off-season, so we compute
    these ourselves from finished fixture scores.
    Run after /sync/fixtures.
    """
    sid = _resolve_season(season_id)
    result = sync_pl_team_records(sid)
    return {"season_id": sid, "synced": result}

@app.post("/sync/all-element-summaries", tags=["sync"])
def route_sync_all_element_summaries(season_id: int | None = Query(default=None)):
    """
    Syncs per-fixture history for ALL players in the season (~700 players, ~70s).
    Use this instead of /sync/element-summaries when you want undrafted players
    to have fixture history for drill-through and DC data.
    """
    sid = _resolve_season(season_id)
    result = sync_all_element_summaries(sid)
    return {"season_id": sid, "synced": result}
