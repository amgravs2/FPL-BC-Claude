import logging
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
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
    sync_all_element_summaries,
    sync_pl_team_records,
    sync_ownership_from_draft,
    sync_team_strengths,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Job status tracker — in-memory, resets on restart
# ---------------------------------------------------------------------------
_job_status: dict = {}


def _run_background_sync(job_id: str, fn, *args):
    """Wrapper that updates job status around a sync function."""
    _job_status[job_id] = {"status": "running", "result": None, "error": None}
    try:
        result = fn(*args)
        _job_status[job_id] = {"status": "done", "result": result, "error": None}
        logger.info(f"Background job {job_id} complete: {result}")
    except Exception as e:
        _job_status[job_id] = {"status": "error", "result": None, "error": str(e)}
        logger.error(f"Background job {job_id} failed: {e}")


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


@app.get("/sync/job/{job_id}", tags=["sync"])
def get_job_status(job_id: str):
    """Poll the status of a background sync job."""
    if job_id not in _job_status:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return {"job_id": job_id, **_job_status[job_id]}


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
# ---------------------------------------------------------------------------

@app.post("/sync/bootstrap", tags=["sync"])
def route_sync_bootstrap(season_id: int | None = Query(default=None)):
    """Syncs element_types, premier_league_teams, players, gameweeks."""
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
    """Syncs current fantasy ownership from element-status API."""
    sid = _resolve_season(season_id)
    result = sync_player_status(sid)
    return {"season_id": sid, "synced": result}


@app.post("/sync/transactions", tags=["sync"])
def route_sync_transactions(season_id: int | None = Query(default=None)):
    """Syncs waiver and trade transactions."""
    sid = _resolve_season(season_id)
    result = sync_transactions(sid)
    return {"season_id": sid, "synced": result}


@app.post("/sync/fixtures", tags=["sync"])
def route_sync_fixtures(season_id: int | None = Query(default=None)):
    """Syncs PL fixture schedule and scores."""
    sid = _resolve_season(season_id)
    result = sync_fixtures(sid)
    return {"season_id": sid, "synced": result}


@app.post("/sync/stats/{gw}", tags=["sync"])
def route_sync_gw_stats(gw: int, season_id: int | None = Query(default=None)):
    """Syncs live player stats for a specific gameweek."""
    if gw < 1 or gw > 38:
        raise HTTPException(status_code=400, detail="GW must be between 1 and 38")
    sid = _resolve_season(season_id)
    result = sync_gw_stats(sid, gw)
    return {"season_id": sid, "gw": gw, "synced": result}


@app.post("/sync/lineups/{gw}", tags=["sync"])
def route_sync_lineups(gw: int, season_id: int | None = Query(default=None)):
    """Syncs team lineups for a specific gameweek."""
    if gw < 1 or gw > 38:
        raise HTTPException(status_code=400, detail="GW must be between 1 and 38")
    sid = _resolve_season(season_id)
    result = sync_lineups(sid, gw)
    return {"season_id": sid, "gw": gw, "synced": result}


@app.post("/sync/standings", tags=["sync"])
def route_sync_standings(season_id: int | None = Query(default=None)):
    """Rebuilds standings from fantasy_matches."""
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
    Syncs per-fixture history for drafted players only (~90 players, ~10s).
    Runs synchronously — completes within request timeout.
    """
    sid = _resolve_season(season_id)
    result = sync_element_summaries(sid)
    return {"season_id": sid, "synced": result}


@app.post("/sync/all-element-summaries", tags=["sync"])
def route_sync_all_element_summaries(
    background_tasks: BackgroundTasks,
    season_id: int | None = Query(default=None),
):
    """
    Syncs per-fixture history for ALL players in the season (~700 players, ~70s).
    Runs as a background task to avoid request timeout — returns a job_id immediately.
    Poll GET /sync/job/{job_id} to check progress.
    """
    import uuid
    sid = _resolve_season(season_id)
    job_id = f"all-elements-{sid}-{uuid.uuid4().hex[:8]}"
    background_tasks.add_task(_run_background_sync, job_id, sync_all_element_summaries, sid)
    return {
        "season_id": sid,
        "job_id": job_id,
        "status": "started",
        "message": f"Syncing all players in background. Poll GET /sync/job/{job_id} for status.",
        "poll_url": f"/sync/job/{job_id}",
    }


@app.post("/sync/pl-records", tags=["sync"])
def route_sync_pl_records(season_id: int | None = Query(default=None)):
    """Derives W/D/L/points/position from fixtures. Run after /sync/fixtures."""
    sid = _resolve_season(season_id)
    result = sync_pl_team_records(sid)
    return {"season_id": sid, "synced": result}


@app.post("/sync/ownership", tags=["sync"])
def route_sync_ownership(season_id: int | None = Query(default=None)):
    """Builds per-GW ownership history from draft picks + transactions."""
    sid = _resolve_season(season_id)
    result = sync_ownership_from_draft(sid)
    return {"season_id": sid, "synced": result}


@app.post("/sync/all", tags=["sync"])
def route_sync_all(season_id: int | None = Query(default=None)):
    """
    Runs all non-GW-specific syncs in order.
    Use for initial load or daily refresh. Does NOT sync GW stats/lineups.
    """
    sid = _resolve_season(season_id)
    results = {}
    results["bootstrap"]     = sync_bootstrap(sid)
    results["fantasy_teams"] = sync_fantasy_teams(sid)
    results["matches"]       = sync_fantasy_matches(sid)
    results["player_status"] = sync_player_status(sid)
    results["transactions"]  = sync_transactions(sid)
    results["fixtures"]      = sync_fixtures(sid)
    results["standings"]     = sync_standings(sid)
    results["draft_picks"]   = sync_draft_picks(sid)
    results["pl_records"]    = sync_pl_team_records(sid)
    return {"season_id": sid, "synced": results}


 @app.post("/sync/team-strengths", tags=["sync"])
 def route_sync_team_strengths(
     gw: int | None = Query(default=None),
     season_id: int | None = Query(default=None),
 ):
#     """
#     Snapshots current FPL team strength values for the given GW.
#     Pass ?gw= to record a specific GW (useful for backfilling).
#     Defaults to current_event from FPL game endpoint.
#     ON CONFLICT DO NOTHING — safe to call multiple times.
#     """
     sid = _resolve_season(season_id)
     result = sync_team_strengths(sid, gw)
     return {"season_id": sid, "synced": result}
