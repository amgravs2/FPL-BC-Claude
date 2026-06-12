"""
queries.py — read-only endpoints for the frontend.

Season ID convention:
  19/20 = 0  |  20/21 = 1  |  21/22 = 2  |  22/23 = 3
  23/24 = 4  |  24/25 = 5  |  25/26 = 6  |  26/27 = 7  (next)

FDR is calculated inline via compute_fdr() which reads from team_strength_history
(written by sync_gw_stats and sync_bootstrap) rather than making live FPL API calls.
"""

from fastapi import APIRouter, HTTPException
from db import get_conn

router = APIRouter(prefix="/query", tags=["query"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _season_or_404(season_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM seasons WHERE id = %s", (season_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Season {season_id} not found")
            return {"id": row[0], "name": row[1]}


def _fdr(strength_val, all_vals) -> int:
    """
    Convert a raw FPL strength value to a 1–5 FDR rating.
    Lower rating = easier fixture.
    Called with opponent's relevant strength axis vs the league-wide distribution.
    """
    if not all_vals or not strength_val:
        return 3
    avg   = sum(all_vals) / len(all_vals)
    ratio = strength_val / avg
    if ratio < 0.87: return 2
    if ratio < 0.97: return 3
    if ratio < 1.07: return 3
    if ratio < 1.17: return 4
    return 5


def _load_team_strengths(season_id: int, gw: int) -> dict:
    """
    Load the most recent team strength snapshot at or before `gw` from
    team_strength_history.  Falls back to any available snapshot if none
    exists for the exact GW.  Returns a dict keyed by team_id.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Most recent snapshot per team at or before the requested GW
            cur.execute("""
                SELECT DISTINCT ON (team_id)
                    team_id,
                    strength_attack_home,  strength_attack_away,
                    strength_defence_home, strength_defence_away
                FROM team_strength_history
                WHERE season_id = %s AND gw <= %s
                ORDER BY team_id, gw DESC;
            """, (season_id, gw))
            rows = cur.fetchall()

            if not rows:
                # Fallback: any snapshot in the season
                cur.execute("""
                    SELECT DISTINCT ON (team_id)
                        team_id,
                        strength_attack_home,  strength_attack_away,
                        strength_defence_home, strength_defence_away
                    FROM team_strength_history
                    WHERE season_id = %s
                    ORDER BY team_id, gw DESC;
                """, (season_id,))
                rows = cur.fetchall()

    return {
        row[0]: {
            "strength_attack_home":  row[1],
            "strength_attack_away":  row[2],
            "strength_defence_home": row[3],
            "strength_defence_away": row[4],
        }
        for row in rows
    }


def _compute_fixture_fdrs(fixtures: list, strength_map: dict) -> list:
    """
    Attach attack/defence FDR columns to a list of fixture dicts.
    Each fixture must have team_h and team_a keys.

    FDR axis logic:
      home team attacking FDR  = difficulty of scoring vs away team's AWAY defence
      home team defending FDR  = difficulty of keeping CS vs away team's AWAY attack
      away team attacking FDR  = difficulty of scoring vs home team's HOME defence
      away team defending FDR  = difficulty of keeping CS vs home team's HOME attack
    """
    if not strength_map:
        for f in fixtures:
            f.update({"team_h_attack_fdr": 3, "team_h_defence_fdr": 3,
                       "team_a_attack_fdr": 3, "team_a_defence_fdr": 3})
        return fixtures

    all_atk_away  = [t["strength_attack_away"]  for t in strength_map.values() if t["strength_attack_away"]]
    all_def_away  = [t["strength_defence_away"] for t in strength_map.values() if t["strength_defence_away"]]
    all_atk_home  = [t["strength_attack_home"]  for t in strength_map.values() if t["strength_attack_home"]]
    all_def_home  = [t["strength_defence_home"] for t in strength_map.values() if t["strength_defence_home"]]

    enriched = []
    for f in fixtures:
        away_team = strength_map.get(f["team_a"], {})
        home_team = strength_map.get(f["team_h"], {})
        f["team_h_attack_fdr"]  = _fdr(away_team.get("strength_defence_away"), all_def_away)
        f["team_h_defence_fdr"] = _fdr(away_team.get("strength_attack_away"),  all_atk_away)
        f["team_a_attack_fdr"]  = _fdr(home_team.get("strength_defence_home"), all_def_home)
        f["team_a_defence_fdr"] = _fdr(home_team.get("strength_attack_home"),  all_atk_home)
        enriched.append(f)

    return enriched


# ---------------------------------------------------------------------------
# Seasons list
# ---------------------------------------------------------------------------

@router.get("/seasons")
def get_seasons():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, start_date, end_date
                FROM seasons
                ORDER BY start_date DESC;
            """)
            rows = cur.fetchall()
    return [
        {"id": r[0], "name": r[1], "start_date": str(r[2]), "end_date": str(r[3])}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Season overview / standings table
# ---------------------------------------------------------------------------

@router.get("/season/{season_id}/summary")
def get_season_summary(season_id: int):
    """Final standings table with W/D/L, points for/against, rank."""
    _season_or_404(season_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    ft.player_first_name AS manager,
                    ft.id                AS team_id,
                    ft.internal_team_id,
                    COUNT(*) FILTER (WHERE s.result = 'w') AS wins,
                    COUNT(*) FILTER (WHERE s.result = 'd') AS draws,
                    COUNT(*) FILTER (WHERE s.result = 'l') AS losses,
                    SUM(s.points_for)     AS points_for,
                    SUM(s.points_against) AS points_against,
                    MAX(s.cumulative_points) AS league_points
                FROM standings s
                JOIN fantasy_teams ft
                    ON ft.internal_team_id = s.team_id
                    AND ft.season_id       = s.season_id
                WHERE s.season_id = %s
                GROUP BY ft.player_first_name, ft.id, ft.internal_team_id
                ORDER BY league_points DESC, points_for DESC;
            """, (season_id,))
            rows = cur.fetchall()

    return [
        {
            "manager":        r[0],
            "team_id":        r[1],
            "internal_team_id": r[2],
            "wins":           r[3],
            "draws":          r[4],
            "losses":         r[5],
            "points_for":     r[6],
            "points_against": r[7],
            "league_points":  r[8],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Standings chart (cumulative points over time)
# ---------------------------------------------------------------------------

@router.get("/season/{season_id}/standings-chart")
def get_standings_chart(season_id: int):
    _season_or_404(season_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s.gw, ft.player_first_name, s.cumulative_points
                FROM standings s
                JOIN fantasy_teams ft
                    ON ft.internal_team_id = s.team_id
                    AND ft.season_id       = s.season_id
                WHERE s.season_id = %s
                ORDER BY s.gw, ft.player_first_name;
            """, (season_id,))
            rows = cur.fetchall()

    return [{"gw": r[0], "manager": r[1], "cumulative_points": r[2]} for r in rows]


# ---------------------------------------------------------------------------
# Results grid (all H2H results this season)
# ---------------------------------------------------------------------------

@router.get("/season/{season_id}/results-grid")
def get_results_grid(season_id: int):
    _season_or_404(season_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    s.gw,
                    t1.player_first_name AS manager,
                    t1.id                AS entry_1_id,
                    t2.player_first_name AS opponent,
                    t2.id                AS entry_2_id,
                    s.points_for,
                    s.points_against,
                    s.result
                FROM standings s
                JOIN fantasy_teams t1
                    ON t1.internal_team_id = s.team_id
                    AND t1.season_id       = s.season_id
                JOIN fantasy_teams t2
                    ON t2.internal_team_id = s.opponent_id
                    AND t2.season_id       = s.season_id
                WHERE s.season_id = %s
                ORDER BY s.gw, s.points_for DESC;
            """, (season_id,))
            rows = cur.fetchall()

    return [
        {
            "gw":             r[0],
            "manager":        r[1],
            "entry_1_id":     r[2],
            "opponent":       r[3],
            "entry_2_id":     r[4],
            "points_for":     r[5],
            "points_against": r[6],
            "result":         r[7],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Season player stats (aggregated across all GWs)
# ---------------------------------------------------------------------------

@router.get("/season/{season_id}/players")
def get_season_players(season_id: int):
    _season_or_404(season_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    p.id,
                    p.first_name || ' ' || p.second_name AS name,
                    p.web_name,
                    et.singular_name_short AS position,
                    plt.short_name         AS pl_team,
                    COALESCE(SUM(pgs.total_points), 0)   AS total_points,
                    COALESCE(SUM(pgs.goals), 0)          AS goals,
                    COALESCE(SUM(pgs.assists), 0)        AS assists,
                    COALESCE(SUM(pgs.clean_sheets), 0)   AS clean_sheets,
                    COALESCE(SUM(pgs.bonus), 0)          AS bonus,
                    COALESCE(SUM(pgs.minutes), 0)        AS minutes,
                    COALESCE(SUM(pgs.saves), 0)          AS saves,
                    COALESCE(SUM(pgs.yellow_cards), 0)   AS yellow_cards,
                    COALESCE(SUM(pgs.red_cards), 0)      AS red_cards,
                    COALESCE(SUM(pgs.goals_conceded), 0) AS goals_conceded,
                    COUNT(*) FILTER (WHERE pgs.total_points = 2 AND pgs.minutes > 0
                                       AND pgs.goals = 0 AND pgs.assists = 0
                                       AND pgs.clean_sheets = 0) AS blank_gws,
                    pfs.team_id  AS owner_internal,
                    ft.id        AS owner_team_id
                FROM players p
                JOIN element_type et ON et.id = p.position
                LEFT JOIN premier_league_teams plt
                    ON plt.id = p.team AND plt.season_id = p.season_id
                LEFT JOIN player_gameweek_stats pgs
                    ON pgs.player_id = p.id AND pgs.season_id = p.season_id
                LEFT JOIN player_fantasy_status pfs
                    ON pfs.player_id = p.id AND pfs.season_id = p.season_id
                LEFT JOIN fantasy_teams ft
                    ON ft.internal_team_id = pfs.team_id AND ft.season_id = p.season_id
                WHERE p.season_id = %s
                GROUP BY p.id, p.first_name, p.second_name, p.web_name,
                         et.singular_name_short, plt.short_name,
                         pfs.team_id, ft.id
                ORDER BY total_points DESC;
            """, (season_id,))
            rows = cur.fetchall()

    return [
        {
            "player_id":     r[0],
            "name":          r[1],
            "web_name":      r[2],
            "position":      r[3],
            "pl_team":       r[4],
            "total_points":  r[5],
            "goals":         r[6],
            "assists":       r[7],
            "clean_sheets":  r[8],
            "bonus":         r[9],
            "minutes":       r[10],
            "saves":         r[11],
            "yellow_cards":  r[12],
            "red_cards":     r[13],
            "goals_conceded": r[14],
            "blank_gws":     r[15],
            "owner":         r[16],
            "owner_team_id": r[17],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Transfer analytics
# ---------------------------------------------------------------------------

@router.get("/season/{season_id}/transfers")
def get_transfer_analytics(season_id: int):
    """
    Transfer activity per manager with post-transfer point delta.
    Delta = points scored by player_in after transfer GW
            minus points scored by player_out after transfer GW.
    """
    _season_or_404(season_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                WITH transfer_deltas AS (
                    SELECT
                        tx.id,
                        ft.player_first_name AS manager,
                        ft.internal_team_id,
                        tx.gw,
                        tx.kind,
                        tx.result,
                        p_in.first_name  || ' ' || p_in.second_name  AS player_in,
                        p_out.first_name || ' ' || p_out.second_name AS player_out,
                        tx.player_in_id,
                        tx.player_out_id,
                        COALESCE((
                            SELECT SUM(pgs.total_points)
                            FROM player_gameweek_stats pgs
                            WHERE pgs.player_id = tx.player_in_id
                              AND pgs.season_id = tx.season_id
                              AND pgs.gw > tx.gw
                        ), 0) AS points_in_after,
                        COALESCE((
                            SELECT SUM(pgs.total_points)
                            FROM player_gameweek_stats pgs
                            WHERE pgs.player_id = tx.player_out_id
                              AND pgs.season_id = tx.season_id
                              AND pgs.gw > tx.gw
                        ), 0) AS points_out_after
                    FROM transactions tx
                    JOIN fantasy_teams ft
                        ON ft.id = tx.team_id
                        AND ft.season_id = tx.season_id
                    JOIN players p_in
                        ON p_in.id = tx.player_in_id
                        AND p_in.season_id = tx.season_id
                    JOIN players p_out
                        ON p_out.id = tx.player_out_id
                        AND p_out.season_id = tx.season_id
                    WHERE tx.season_id = %s AND tx.result = 'a'
                )
                SELECT *,
                    (points_in_after - points_out_after) AS delta
                FROM transfer_deltas
                ORDER BY delta DESC;
            """, (season_id,))
            rows = cur.fetchall()

    transfers = [
        {
            "id":               r[0],
            "manager":          r[1],
            "team_id":          r[2],
            "gw":               r[3],
            "kind":             r[4],
            "result":           r[5],
            "player_in":        r[6],
            "player_out":       r[7],
            "player_in_id":     r[8],
            "player_out_id":    r[9],
            "points_in_after":  r[10],
            "points_out_after": r[11],
            "delta":            r[12],
        }
        for r in rows
    ]

    managers: dict = {}
    for t in transfers:
        m = t["manager"]
        if m not in managers:
            managers[m] = {
                "manager":        m,
                "team_id":        t["team_id"],
                "total_moves":    0,
                "net_delta":      0,
                "best_transfer":  None,
                "worst_transfer": None,
            }
        managers[m]["total_moves"] += 1
        managers[m]["net_delta"]   += t["delta"]
        if managers[m]["best_transfer"] is None or \
                t["delta"] > managers[m]["best_transfer"]["delta"]:
            managers[m]["best_transfer"] = t
        if managers[m]["worst_transfer"] is None or \
                t["delta"] < managers[m]["worst_transfer"]["delta"]:
            managers[m]["worst_transfer"] = t

    return {
        "all_transfers":   transfers,
        "best_transfer":   transfers[0]  if transfers else None,
        "worst_transfer":  transfers[-1] if transfers else None,
        "manager_summary": list(managers.values()),
    }


# ---------------------------------------------------------------------------
# All-time H2H
# ---------------------------------------------------------------------------

@router.get("/alltime/h2h")
def get_alltime_h2h():
    """All-time H2H records across every season in the database."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    t1.player_first_name AS manager,
                    t2.player_first_name AS opponent,
                    COUNT(*) FILTER (WHERE s.result = 'w') AS wins,
                    COUNT(*) FILTER (WHERE s.result = 'd') AS draws,
                    COUNT(*) FILTER (WHERE s.result = 'l') AS losses,
                    SUM(s.points_for)    AS points_for,
                    SUM(s.points_against) AS points_against
                FROM standings s
                JOIN fantasy_teams t1
                    ON t1.internal_team_id = s.team_id
                    AND t1.season_id = s.season_id
                JOIN fantasy_teams t2
                    ON t2.internal_team_id = s.opponent_id
                    AND t2.season_id = s.season_id
                GROUP BY t1.player_first_name, t2.player_first_name
                ORDER BY t1.player_first_name, wins DESC;
            """)
            rows = cur.fetchall()
    return [
        {
            "manager":        r[0],
            "opponent":       r[1],
            "wins":           r[2],
            "draws":          r[3],
            "losses":         r[4],
            "points_for":     r[5],
            "points_against": r[6],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# All-time records
# ---------------------------------------------------------------------------

@router.get("/alltime/records")
def get_alltime_records():
    """All-time highs and lows across all seasons."""
    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT ft.player_first_name, s.gw, se.name, s.points_for
                FROM standings s
                JOIN fantasy_teams ft ON ft.internal_team_id = s.team_id
                    AND ft.season_id = s.season_id
                JOIN seasons se ON se.id = s.season_id
                ORDER BY s.points_for DESC LIMIT 1;
            """)
            high = cur.fetchone()

            cur.execute("""
                SELECT ft.player_first_name, s.gw, se.name, s.points_for
                FROM standings s
                JOIN fantasy_teams ft ON ft.internal_team_id = s.team_id
                    AND ft.season_id = s.season_id
                JOIN seasons se ON se.id = s.season_id
                ORDER BY s.points_for ASC LIMIT 1;
            """)
            low = cur.fetchone()

            cur.execute("""
                SELECT
                    t1.player_first_name, t2.player_first_name,
                    s.gw, se.name,
                    s.points_for, s.points_against,
                    (s.points_for - s.points_against) AS margin
                FROM standings s
                JOIN fantasy_teams t1 ON t1.internal_team_id = s.team_id
                    AND t1.season_id = s.season_id
                JOIN fantasy_teams t2 ON t2.internal_team_id = s.opponent_id
                    AND t2.season_id = s.season_id
                JOIN seasons se ON se.id = s.season_id
                ORDER BY margin DESC LIMIT 1;
            """)
            biggest_win = cur.fetchone()

    def _fmt(r):
        return {"manager": r[0], "gw": r[1], "season": r[2], "score": r[3]} if r else None

    return {
        "highest_score": _fmt(high),
        "lowest_score":  _fmt(low),
        "biggest_win": {
            "winner":   biggest_win[0],
            "loser":    biggest_win[1],
            "gw":       biggest_win[2],
            "season":   biggest_win[3],
            "score_for":     biggest_win[4],
            "score_against": biggest_win[5],
            "margin":        biggest_win[6],
        } if biggest_win else None,
    }


# ---------------------------------------------------------------------------
# GW lineup for a specific team
# ---------------------------------------------------------------------------

@router.get("/season/{season_id}/team/{team_id}/gw/{gw}/lineup")
def get_gw_lineup(season_id: int, team_id: int, gw: int):
    _season_or_404(season_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    gl.position,
                    gl.is_captain,
                    gl.is_vice_captain,
                    gl.multiplier,
                    p.first_name || ' ' || p.second_name AS player_name,
                    p.web_name,
                    p.id AS player_id,
                    et.singular_name_short AS pos,
                    COALESCE(pgs.total_points, 0) AS points,
                    COALESCE(pgs.minutes, 0)       AS minutes,
                    COALESCE(pgs.goals, 0)         AS goals,
                    COALESCE(pgs.assists, 0)       AS assists,
                    COALESCE(pgs.bonus, 0)         AS bonus
                FROM gameweek_lineups gl
                JOIN players p ON p.id = gl.player_id AND p.season_id = gl.season_id
                JOIN element_type et ON et.id = p.position
                LEFT JOIN player_gameweek_stats pgs
                    ON pgs.player_id = gl.player_id
                    AND pgs.season_id = gl.season_id
                    AND pgs.gw = gl.gw
                WHERE gl.season_id = %s
                    AND gl.team_id = %s
                    AND gl.gw = %s
                ORDER BY gl.position;
            """, (season_id, team_id, gw))
            rows = cur.fetchall()

    picks = [
        {
            "position":        r[0],
            "is_captain":      r[1],
            "is_vice_captain": r[2],
            "multiplier":      r[3],
            "player_name":     r[4],
            "web_name":        r[5],
            "player_id":       r[6],
            "pos":             r[7],
            "points":          r[8],
            "minutes":         r[9],
            "goals":           r[10],
            "assists":         r[11],
            "bonus":           r[12],
        }
        for r in rows
    ]

    return {
        "starters": [p for p in picks if p["position"] <= 11],
        "bench":    [p for p in picks if p["position"] > 11],
    }


# ---------------------------------------------------------------------------
# Fixtures upcoming — fixture difficulty grid
# FDR read from team_strength_history (no live API call)
# ---------------------------------------------------------------------------

@router.get("/season/{season_id}/fixtures-upcoming")
def get_fixtures_upcoming(season_id: int):
    """
    Returns upcoming fixtures with attack/defence FDR.
    FDR is derived from team_strength_history (snapshotted each GW by sync_gw_stats)
    so this endpoint never makes a live external API call.
    """
    _season_or_404(season_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(MAX(gw), 1) FROM player_gameweek_stats
                WHERE season_id = %s;
            """, (season_id,))
            current_gw = cur.fetchone()[0]

            cur.execute("""
                SELECT f.gw, f.team_h, f.team_a, f.kickoff_time
                FROM fixtures f
                WHERE f.season_id = %s AND f.gw >= %s AND f.gw <= %s
                ORDER BY f.gw, f.kickoff_time;
            """, (season_id, current_gw, current_gw + 6))
            fixture_rows = cur.fetchall()

            cur.execute("""
                SELECT id, name, short_name
                FROM premier_league_teams
                WHERE season_id = %s;
            """, (season_id,))
            pl_team_rows = cur.fetchall()

    teams = [
        {"id": r[0], "name": r[1], "short_name": r[2]}
        for r in pl_team_rows
    ]

    # Load stored team strengths — no live API call
    strength_map = _load_team_strengths(season_id, current_gw)

    raw_fixtures = [
        {
            "gw":           r[0],
            "team_h":       r[1],
            "team_a":       r[2],
            "kickoff_time": str(r[3]) if r[3] else None,
        }
        for r in fixture_rows
    ]

    fixtures = _compute_fixture_fdrs(raw_fixtures, strength_map)

    return {
        "current_gw": current_gw,
        "teams":      teams,
        "fixtures":   fixtures,
    }


# ---------------------------------------------------------------------------
# Player historical season stats
# ---------------------------------------------------------------------------

@router.get("/player/{player_id}/history")
def get_player_history(player_id: int):
    """Historical season totals for a player across all FPL seasons."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT season_name, total_points, minutes, goals_scored, assists,
                       clean_sheets, bonus, saves, yellow_cards, red_cards,
                       start_cost, end_cost
                FROM player_season_history
                WHERE player_id = %s
                ORDER BY season_name DESC;
            """, (player_id,))
            rows = cur.fetchall()

            # Current season aggregate from player_gameweek_stats
            # Join to seasons to get the most recent season this player appears in
            cur.execute("""
                SELECT SUM(pgs.total_points), SUM(pgs.minutes), SUM(pgs.goals),
                       SUM(pgs.assists), SUM(pgs.clean_sheets), SUM(pgs.bonus),
                       SUM(pgs.saves), se.name
                FROM player_gameweek_stats pgs
                JOIN seasons se ON se.id = pgs.season_id
                WHERE pgs.player_id = %s
                GROUP BY se.name
                ORDER BY MAX(pgs.gw) DESC
                LIMIT 1;
            """, (player_id,))
            current = cur.fetchone()

            # Player name — get from their most recent season entry
            cur.execute("""
                SELECT first_name, second_name, web_name
                FROM players
                WHERE id = %s
                ORDER BY season_id DESC
                LIMIT 1;
            """, (player_id,))
            player = cur.fetchone()

    history = [
        {
            "season":       r[0],
            "total_points": r[1],
            "minutes":      r[2],
            "goals":        r[3],
            "assists":      r[4],
            "clean_sheets": r[5],
            "bonus":        r[6],
            "saves":        r[7],
            "yellow_cards": r[8],
            "red_cards":    r[9],
            "start_cost":   r[10] / 10 if r[10] else None,
            "end_cost":     r[11] / 10 if r[11] else None,
        }
        for r in rows
    ]

    return {
        "player_id":   player_id,
        "name":        f"{player[0]} {player[1]}" if player else "Unknown",
        "web_name":    player[2] if player else "Unknown",
        "history":     history,
        "current_season": {
            "total_points": current[0] or 0,
            "minutes":      current[1] or 0,
            "goals":        current[2] or 0,
            "assists":      current[3] or 0,
            "clean_sheets": current[4] or 0,
            "bonus":        current[5] or 0,
            "saves":        current[6] or 0,
            "season_name":  current[7],
        } if current else None,
    }


# ---------------------------------------------------------------------------
# Draft picks for a season
# ---------------------------------------------------------------------------

@router.get("/season/{season_id}/draft")
def get_draft(season_id: int):
    _season_or_404(season_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    dp.round, dp.pick, dp.overall_pick,
                    dp.was_auto, dp.choice_time,
                    p.first_name || ' ' || p.second_name AS player_name,
                    p.web_name, p.id AS player_id,
                    et.singular_name_short AS position,
                    plt.short_name AS pl_team,
                    ft.player_first_name AS manager,
                    ft.id AS team_id
                FROM draft_picks dp
                JOIN players p ON p.id = dp.player_id AND p.season_id = dp.season_id
                JOIN element_type et ON et.id = p.position
                LEFT JOIN premier_league_teams plt
                    ON plt.id = p.team AND plt.season_id = p.season_id
                JOIN fantasy_teams ft
                    ON ft.id = dp.entry_id AND ft.season_id = dp.season_id
                WHERE dp.season_id = %s
                ORDER BY dp.overall_pick;
            """, (season_id,))
            rows = cur.fetchall()

    return [
        {
            "round":        r[0],
            "pick":         r[1],
            "overall_pick": r[2],
            "was_auto":     r[3],
            "choice_time":  str(r[4]) if r[4] else None,
            "player_name":  r[5],
            "web_name":     r[6],
            "player_id":    r[7],
            "position":     r[8],
            "pl_team":      r[9],
            "manager":      r[10],
            "team_id":      r[11],
        }
        for r in rows
    ]
