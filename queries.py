"""
queries.py — read-only endpoints for the frontend.

Season ID convention:
  19/20 = 0  |  20/21 = 1  |  21/22 = 2  |  22/23 = 3
  23/24 = 4  |  24/25 = 5  |  25/26 = 6  |  26/27 = 7  (next)

FDR is calculated via _fdr() / _load_team_strengths() / _compute_fixture_fdrs()
which read from team_strength_history (snapshotted each GW by sync_gw_stats).
No live FPL API calls are made from query paths.
"""

import statistics
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
    Lower = easier fixture. Called with opponent's relevant strength axis
    vs the league-wide distribution for that axis.
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
    Load most recent team strength snapshot at or before `gw` from
    team_strength_history. Falls back to any snapshot in the season.
    Returns dict keyed by team_id.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
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
      home team attacking FDR  = how hard is it to score vs away team's AWAY defence
      home team defending FDR  = how hard is it to keep CS vs away team's AWAY attack
      away team attacking FDR  = how hard is it to score vs home team's HOME defence
      away team defending FDR  = how hard is it to keep CS vs home team's HOME attack
    """
    if not strength_map:
        for f in fixtures:
            f.update({"team_h_attack_fdr": 3, "team_h_defence_fdr": 3,
                       "team_a_attack_fdr": 3, "team_a_defence_fdr": 3})
        return fixtures

    all_atk_away = [t["strength_attack_away"]  for t in strength_map.values() if t["strength_attack_away"]]
    all_def_away = [t["strength_defence_away"] for t in strength_map.values() if t["strength_defence_away"]]
    all_atk_home = [t["strength_attack_home"]  for t in strength_map.values() if t["strength_attack_home"]]
    all_def_home = [t["strength_defence_home"] for t in strength_map.values() if t["strength_defence_home"]]

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
                FROM seasons ORDER BY start_date DESC;
            """)
            rows = cur.fetchall()
    return [
        {"id": r[0], "name": r[1], "start_date": str(r[2]), "end_date": str(r[3])}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Season summary (standings table)
# ---------------------------------------------------------------------------

@router.get("/season/{season_id}/summary")
def get_season_summary(season_id: int):
    """Final standings table with W/D/L, points for/against, rank."""
    _season_or_404(season_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    ft.player_first_name,
                    ft.player_last_name,
                    ft.team_name,
                    ft.internal_team_id,
                    COUNT(*) FILTER (WHERE s.result = 'w') AS wins,
                    COUNT(*) FILTER (WHERE s.result = 'd') AS draws,
                    COUNT(*) FILTER (WHERE s.result = 'l') AS losses,
                    SUM(s.points_for)                       AS points_for,
                    SUM(s.points_against)                   AS points_against,
                    MAX(s.cumulative_points)                AS league_points,
                    RANK() OVER (ORDER BY MAX(s.cumulative_points) DESC,
                                          SUM(s.points_for) DESC) AS rank
                FROM standings s
                JOIN fantasy_teams ft
                    ON ft.internal_team_id = s.team_id
                    AND ft.season_id = s.season_id
                WHERE s.season_id = %s
                GROUP BY ft.player_first_name, ft.player_last_name,
                         ft.team_name, ft.internal_team_id
                ORDER BY league_points DESC, points_for DESC;
            """, (season_id,))
            rows = cur.fetchall()
    return [
        {
            "rank":           r[10],
            "first_name":     r[0],
            "last_name":      r[1],
            "team_name":      r[2],
            "team_id":        r[3],
            "wins":           r[4],
            "draws":          r[5],
            "losses":         r[6],
            "points_for":     r[7],
            "points_against": r[8],
            "league_points":  r[9],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Standings chart (cumulative points over time)
# ---------------------------------------------------------------------------

@router.get("/season/{season_id}/standings-chart")
def get_standings_chart(season_id: int):
    """Cumulative points by team by GW — feeds the animated chart."""
    _season_or_404(season_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    s.gw,
                    ft.player_first_name,
                    ft.internal_team_id,
                    s.cumulative_points,
                    s.points_for,
                    s.result,
                    RANK() OVER (PARTITION BY s.gw
                                 ORDER BY s.cumulative_points DESC,
                                          s.points_for DESC) AS gw_rank
                FROM standings s
                JOIN fantasy_teams ft
                    ON ft.internal_team_id = s.team_id
                    AND ft.season_id = s.season_id
                WHERE s.season_id = %s
                ORDER BY s.gw, s.cumulative_points DESC;
            """, (season_id,))
            rows = cur.fetchall()
    return [
        {
            "gw":                r[0],
            "manager":           r[1],
            "team_id":           r[2],
            "cumulative_points": r[3],
            "points_for":        r[4],
            "result":            r[5],
            "gw_rank":           r[6],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Results grid (all H2H results this season)
# ---------------------------------------------------------------------------

@router.get("/season/{season_id}/results-grid")
def get_results_grid(season_id: int):
    """GW-by-GW results for every match — feeds the results grid."""
    _season_or_404(season_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    fm.gw,
                    t1.player_first_name AS entry_1_name,
                    fm.entry_1,
                    fm.entry_1_points,
                    fm.entry_2_points,
                    fm.entry_2,
                    t2.player_first_name AS entry_2_name,
                    t1.id AS entry_1_fpl_id,
                    t2.id AS entry_2_fpl_id
                FROM fantasy_matches fm
                JOIN fantasy_teams t1
                    ON t1.internal_team_id = fm.entry_1
                    AND t1.season_id = fm.season_id
                JOIN fantasy_teams t2
                    ON t2.internal_team_id = fm.entry_2
                    AND t2.season_id = fm.season_id
                WHERE fm.season_id = %s
                ORDER BY fm.gw;
            """, (season_id,))
            rows = cur.fetchall()
    return [
        {
            "gw":             r[0],
            "entry_1_name":   r[1],
            "entry_1_id":     r[2],
            "entry_1_points": r[3],
            "entry_2_points": r[4],
            "entry_2_id":     r[5],
            "entry_2_name":   r[6],
            "entry_1_fpl_id": r[7],
            "entry_2_fpl_id": r[8],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Season H2H matrix
# ---------------------------------------------------------------------------

@router.get("/season/{season_id}/h2h")
def get_h2h(season_id: int):
    """Full H2H record between every pair of managers this season."""
    _season_or_404(season_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    t1.player_first_name AS manager,
                    t2.player_first_name AS opponent,
                    s.team_id,
                    s.opponent_id,
                    COUNT(*) FILTER (WHERE s.result = 'w') AS wins,
                    COUNT(*) FILTER (WHERE s.result = 'd') AS draws,
                    COUNT(*) FILTER (WHERE s.result = 'l') AS losses,
                    SUM(s.points_for)     AS points_for,
                    SUM(s.points_against) AS points_against
                FROM standings s
                JOIN fantasy_teams t1
                    ON t1.internal_team_id = s.team_id
                    AND t1.season_id = s.season_id
                JOIN fantasy_teams t2
                    ON t2.internal_team_id = s.opponent_id
                    AND t2.season_id = s.season_id
                WHERE s.season_id = %s
                GROUP BY t1.player_first_name, t2.player_first_name,
                         s.team_id, s.opponent_id
                ORDER BY t1.player_first_name, t2.player_first_name;
            """, (season_id,))
            rows = cur.fetchall()
    return [
        {
            "manager":        r[0],
            "opponent":       r[1],
            "team_id":        r[2],
            "opponent_id":    r[3],
            "wins":           r[4],
            "draws":          r[5],
            "losses":         r[6],
            "points_for":     r[7],
            "points_against": r[8],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Season records
# ---------------------------------------------------------------------------

@router.get("/season/{season_id}/records")
def get_season_records(season_id: int):
    """Highest/lowest week, biggest margin, streaks, bench points."""
    _season_or_404(season_id)
    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT ft.player_first_name, s.gw, s.points_for
                FROM standings s
                JOIN fantasy_teams ft ON ft.internal_team_id = s.team_id
                    AND ft.season_id = s.season_id
                WHERE s.season_id = %s
                ORDER BY s.points_for DESC LIMIT 1;
            """, (season_id,))
            high = cur.fetchone()

            cur.execute("""
                SELECT ft.player_first_name, s.gw, s.points_for
                FROM standings s
                JOIN fantasy_teams ft ON ft.internal_team_id = s.team_id
                    AND ft.season_id = s.season_id
                WHERE s.season_id = %s
                ORDER BY s.points_for ASC LIMIT 1;
            """, (season_id,))
            low = cur.fetchone()

            cur.execute("""
                SELECT
                    t1.player_first_name AS winner,
                    t2.player_first_name AS loser,
                    s.gw,
                    s.points_for, s.points_against,
                    (s.points_for - s.points_against) AS margin
                FROM standings s
                JOIN fantasy_teams t1 ON t1.internal_team_id = s.team_id
                    AND t1.season_id = s.season_id
                JOIN fantasy_teams t2 ON t2.internal_team_id = s.opponent_id
                    AND t2.season_id = s.season_id
                WHERE s.season_id = %s AND s.result = 'w'
                ORDER BY margin DESC LIMIT 1;
            """, (season_id,))
            margin = cur.fetchone()

            cur.execute("""
                SELECT ft.player_first_name, s.gw, s.points_for, s.points_against
                FROM standings s
                JOIN fantasy_teams ft ON ft.internal_team_id = s.team_id
                    AND ft.season_id = s.season_id
                WHERE s.season_id = %s AND s.result = 'l'
                ORDER BY s.points_for DESC LIMIT 1;
            """, (season_id,))
            unlucky = cur.fetchone()

            cur.execute("""
                SELECT
                    t1.player_first_name AS winner,
                    t2.player_first_name AS loser,
                    s.gw, s.points_for, s.points_against,
                    ABS(s.points_for - s.points_against) AS margin
                FROM standings s
                JOIN fantasy_teams t1 ON t1.internal_team_id = s.team_id
                    AND t1.season_id = s.season_id
                JOIN fantasy_teams t2 ON t2.internal_team_id = s.opponent_id
                    AND t2.season_id = s.season_id
                WHERE s.season_id = %s AND s.result != 'd'
                ORDER BY margin ASC, s.gw ASC LIMIT 1;
            """, (season_id,))
            closest = cur.fetchone()

            cur.execute("""
                WITH ordered AS (
                    SELECT team_id, gw, result,
                           ROW_NUMBER() OVER (PARTITION BY team_id ORDER BY gw) -
                           ROW_NUMBER() OVER (PARTITION BY team_id, result ORDER BY gw) AS grp
                    FROM standings WHERE season_id = %s
                ),
                streaks AS (
                    SELECT team_id, result, COUNT(*) AS streak_len,
                           MIN(gw) AS start_gw, MAX(gw) AS end_gw
                    FROM ordered WHERE result = 'w'
                    GROUP BY team_id, result, grp
                )
                SELECT ft.player_first_name, s.streak_len, s.start_gw, s.end_gw
                FROM streaks s
                JOIN fantasy_teams ft ON ft.internal_team_id = s.team_id
                    AND ft.season_id = %s
                ORDER BY s.streak_len DESC LIMIT 1;
            """, (season_id, season_id))
            win_streak = cur.fetchone()

            cur.execute("""
                WITH ordered AS (
                    SELECT team_id, gw, result,
                           ROW_NUMBER() OVER (PARTITION BY team_id ORDER BY gw) -
                           ROW_NUMBER() OVER (PARTITION BY team_id, result ORDER BY gw) AS grp
                    FROM standings WHERE season_id = %s
                ),
                streaks AS (
                    SELECT team_id, result, COUNT(*) AS streak_len,
                           MIN(gw) AS start_gw, MAX(gw) AS end_gw
                    FROM ordered WHERE result = 'l'
                    GROUP BY team_id, result, grp
                )
                SELECT ft.player_first_name, s.streak_len, s.start_gw, s.end_gw
                FROM streaks s
                JOIN fantasy_teams ft ON ft.internal_team_id = s.team_id
                    AND ft.season_id = %s
                ORDER BY s.streak_len DESC LIMIT 1;
            """, (season_id, season_id))
            loss_streak = cur.fetchone()

            cur.execute("""
                SELECT
                    ft.player_first_name,
                    SUM(pgs.total_points) AS bench_points
                FROM gameweek_lineups gl
                JOIN player_gameweek_stats pgs
                    ON pgs.player_id = gl.player_id
                    AND pgs.gw = gl.gw
                    AND pgs.season_id = gl.season_id
                JOIN fantasy_teams ft
                    ON ft.id = gl.team_id
                    AND ft.season_id = gl.season_id
                WHERE gl.season_id = %s
                    AND gl.position > 11
                    AND gl.multiplier = 0
                GROUP BY ft.player_first_name
                ORDER BY bench_points DESC;
            """, (season_id,))
            bench = cur.fetchall()

    return {
        "highest_score": {
            "manager": high[0], "gw": high[1], "points": high[2]
        } if high else None,
        "lowest_score": {
            "manager": low[0], "gw": low[1], "points": low[2]
        } if low else None,
        "biggest_margin": {
            "winner": margin[0], "loser": margin[1], "gw": margin[2],
            "winner_points": margin[3], "loser_points": margin[4], "margin": margin[5]
        } if margin else None,
        "most_points_in_loss": {
            "manager": unlucky[0], "gw": unlucky[1],
            "points_for": unlucky[2], "points_against": unlucky[3]
        } if unlucky else None,
        "closest_match": {
            "winner": closest[0], "loser": closest[1], "gw": closest[2],
            "winner_points": closest[3], "loser_points": closest[4], "margin": closest[5]
        } if closest else None,
        "longest_win_streak": {
            "manager": win_streak[0], "length": win_streak[1],
            "start_gw": win_streak[2], "end_gw": win_streak[3]
        } if win_streak else None,
        "longest_loss_streak": {
            "manager": loss_streak[0], "length": loss_streak[1],
            "start_gw": loss_streak[2], "end_gw": loss_streak[3]
        } if loss_streak else None,
        "bench_points": [
            {"manager": r[0], "bench_points": r[1]} for r in bench
        ],
    }


# ---------------------------------------------------------------------------
# Manager profile
# ---------------------------------------------------------------------------

@router.get("/season/{season_id}/manager/{team_id}")
def get_manager_profile(season_id: int, team_id: int):
    """Full per-manager breakdown for a season."""
    _season_or_404(season_id)
    with get_conn() as conn:
        with conn.cursor() as cur:

            # Weekly scores + rank
            cur.execute("""
                SELECT
                    s.gw,
                    s.points_for,
                    s.points_against,
                    s.result,
                    s.league_points,
                    s.cumulative_points,
                    s.opponent_id,
                    opp.player_first_name AS opponent_name,
                    RANK() OVER (PARTITION BY s.gw
                                 ORDER BY s.cumulative_points DESC) AS gw_rank
                FROM standings s
                JOIN fantasy_teams opp
                    ON opp.internal_team_id = s.opponent_id
                    AND opp.season_id = s.season_id
                WHERE s.season_id = %s AND s.team_id = %s
                ORDER BY s.gw;
            """, (season_id, team_id))
            weekly = cur.fetchall()

            # Points breakdown by position (starters only)
            cur.execute("""
                SELECT
                    et.singular_name_short AS position,
                    SUM(pgs.total_points)  AS points,
                    SUM(pgs.goals_scored)  AS goals,
                    SUM(pgs.assists)       AS assists,
                    SUM(pgs.clean_sheets)  AS clean_sheets,
                    SUM(pgs.bonus)         AS bonus,
                    SUM(pgs.saves)         AS saves,
                    SUM(pgs.yellow_cards)  AS yellow_cards,
                    SUM(pgs.red_cards)     AS red_cards,
                    SUM(pgs.minutes)       AS minutes,
                    SUM(pgs.goals_conceded) AS goals_conceded,
                    SUM(pgs.own_goals)     AS own_goals,
                    SUM(pgs.penalties_saved)  AS penalties_saved,
                    SUM(pgs.penalties_missed) AS penalties_missed,
                    SUM(pgs.tackles)       AS tackles,
                    SUM(pgs.recoveries)    AS recoveries,
                    SUM(pgs.clearances_blocks_interceptions) AS cbi,
                    SUM(pgs.defensive_contribution) AS defensive_contribution,
                    SUM(pgs.bps)           AS bps
                FROM gameweek_lineups gl
                JOIN player_gameweek_stats pgs
                    ON pgs.player_id = gl.player_id
                    AND pgs.gw = gl.gw
                    AND pgs.season_id = gl.season_id
                JOIN players p ON p.id = gl.player_id AND p.season_id = gl.season_id
                JOIN element_type et ON et.id = p.position
                WHERE gl.season_id = %s
                    AND gl.team_id = %s
                    AND gl.position <= 11
                GROUP BY et.singular_name_short, et.id
                ORDER BY et.id;
            """, (season_id, team_id))
            by_position = cur.fetchall()

            # H2H record vs every other manager
            cur.execute("""
                SELECT
                    opp.player_first_name,
                    s.opponent_id,
                    COUNT(*) FILTER (WHERE s.result = 'w') AS wins,
                    COUNT(*) FILTER (WHERE s.result = 'd') AS draws,
                    COUNT(*) FILTER (WHERE s.result = 'l') AS losses,
                    SUM(s.points_for)     AS pf,
                    SUM(s.points_against) AS pa
                FROM standings s
                JOIN fantasy_teams opp
                    ON opp.internal_team_id = s.opponent_id
                    AND opp.season_id = s.season_id
                WHERE s.season_id = %s AND s.team_id = %s
                GROUP BY opp.player_first_name, s.opponent_id
                ORDER BY wins DESC;
            """, (season_id, team_id))
            h2h = cur.fetchall()

            # Transfer activity — join players with season_id for compound PK
            cur.execute("""
                SELECT
                    tx.gw,
                    tx.kind,
                    tx.result,
                    p_in.first_name  || ' ' || p_in.second_name  AS player_in,
                    p_out.first_name || ' ' || p_out.second_name AS player_out,
                    p_in.id  AS player_in_id,
                    p_out.id AS player_out_id
                FROM transactions tx
                JOIN players p_in
                    ON p_in.id = tx.player_in_id
                    AND p_in.season_id = tx.season_id
                JOIN players p_out
                    ON p_out.id = tx.player_out_id
                    AND p_out.season_id = tx.season_id
                JOIN fantasy_teams ft
                    ON ft.id = tx.team_id
                    AND ft.season_id = tx.season_id
                WHERE tx.season_id = %s
                    AND ft.internal_team_id = %s
                ORDER BY tx.gw, tx.timestamp;
            """, (season_id, team_id))
            transfers = cur.fetchall()

    return {
        "weekly": [
            {
                "gw":                r[0],
                "points_for":        r[1],
                "points_against":    r[2],
                "result":            r[3],
                "league_points":     r[4],
                "cumulative_points": r[5],
                "opponent_id":       r[6],
                "opponent_name":     r[7],
                "gw_rank":           r[8],
            }
            for r in weekly
        ],
        "by_position": [
            {
                "position":               r[0],
                "points":                 r[1],
                "goals":                  r[2],
                "assists":                r[3],
                "clean_sheets":           r[4],
                "bonus":                  r[5],
                "saves":                  r[6],
                "yellow_cards":           r[7],
                "red_cards":              r[8],
                "minutes":                r[9],
                "goals_conceded":         r[10],
                "own_goals":              r[11],
                "penalties_saved":        r[12],
                "penalties_missed":       r[13],
                "tackles":                r[14],
                "recoveries":             r[15],
                "cbi":                    r[16],
                "defensive_contribution": r[17],
                "bps":                    r[18],
            }
            for r in by_position
        ],
        "h2h": [
            {
                "opponent":       r[0],
                "opponent_id":    r[1],
                "wins":           r[2],
                "draws":          r[3],
                "losses":         r[4],
                "points_for":     r[5],
                "points_against": r[6],
            }
            for r in h2h
        ],
        "transfers": [
            {
                "gw":            r[0],
                "kind":          r[1],
                "result":        r[2],
                "player_in":     r[3],
                "player_out":    r[4],
                "player_in_id":  r[5],
                "player_out_id": r[6],
            }
            for r in transfers
        ],
    }


# ---------------------------------------------------------------------------
# GW lineup — URL pattern matches frontend calls exactly
# Frontend calls: /query/season/${seasonId}/lineup/${teamId}/${gw}
# ---------------------------------------------------------------------------

@router.get("/season/{season_id}/lineup/{team_id}/{gw}")
def get_gw_lineup(season_id: int, team_id: int, gw: int):
    """Returns starters and bench for a team in a specific GW with points."""
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
# Draft scorecard
# ---------------------------------------------------------------------------

@router.get("/season/{season_id}/draft")
def get_draft_scorecard(season_id: int):
    """Full draft board with each pick's season total points."""
    _season_or_404(season_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    dp.round,
                    dp.overall_pick,
                    ft.player_first_name AS manager,
                    ft.internal_team_id,
                    p.first_name || ' ' || p.second_name AS player_name,
                    p.id AS player_id,
                    et.singular_name_short AS position,
                    dp.was_auto,
                    COALESCE(SUM(pgs.total_points), 0) AS season_points,
                    RANK() OVER (ORDER BY COALESCE(SUM(pgs.total_points), 0) DESC) AS points_rank,
                    plt.short_name AS pl_team
                FROM draft_picks dp
                JOIN fantasy_teams ft
                    ON ft.id = dp.entry_id
                    AND ft.season_id = dp.season_id
                JOIN players p ON p.id = dp.player_id AND p.season_id = dp.season_id
                JOIN element_type et ON et.id = p.position
                JOIN premier_league_teams plt
                    ON plt.id = p.team AND plt.season_id = dp.season_id
                LEFT JOIN player_gameweek_stats pgs
                    ON pgs.player_id = dp.player_id
                    AND pgs.season_id = dp.season_id
                WHERE dp.season_id = %s
                GROUP BY dp.round, dp.overall_pick, ft.player_first_name,
                         ft.internal_team_id, p.first_name, p.second_name,
                         p.id, et.singular_name_short, dp.was_auto, plt.short_name
                ORDER BY dp.overall_pick;
            """, (season_id,))
            rows = cur.fetchall()

    picks = [
        {
            "round":         r[0],
            "overall_pick":  r[1],
            "manager":       r[2],
            "team_id":       r[3],
            "player_name":   r[4],
            "player_id":     r[5],
            "position":      r[6],
            "was_auto":      r[7],
            "season_points": r[8],
            "points_rank":   r[9],
            "pl_team":       r[10],
        }
        for r in rows
    ]

    value_by_score = sorted(picks, key=lambda x: x["season_points"], reverse=True)[:5]
    busts_by_score = sorted(
        [p for p in picks if p["overall_pick"] <= 36],
        key=lambda x: x["season_points"]
    )[:5]

    round_stats: dict = {}
    for p in picks:
        r = p["round"]
        if r not in round_stats:
            round_stats[r] = []
        round_stats[r].append(p["season_points"])

    round_medians = [
        {
            "round":  r,
            "median": round(statistics.median(pts), 1),
            "mean":   round(statistics.mean(pts), 1),
            "min":    min(pts),
            "max":    max(pts),
        }
        for r, pts in sorted(round_stats.items())
    ]

    median_by_round = {r["round"]: r["median"] for r in round_medians}
    for p in picks:
        expected = median_by_round.get(p["round"], 0)
        p["value_score"] = round(p["season_points"] - expected, 1)

    composition: dict = {}
    for p in picks:
        tid = p["team_id"]
        if tid not in composition:
            composition[tid] = {
                "manager": p["manager"], "team_id": tid,
                "GKP": 0, "DEF": 0, "MID": 0, "FWD": 0, "total_points": 0
            }
        composition[tid][p["position"]] = composition[tid].get(p["position"], 0) + 1
        composition[tid]["total_points"] += p["season_points"]

    return {
        "picks":         picks,
        "value_picks":   sorted(picks, key=lambda x: x["value_score"], reverse=True)[:5],
        "busts":         sorted(
            [p for p in picks if p["overall_pick"] <= 36],
            key=lambda x: x["value_score"]
        )[:5],
        "round_medians": round_medians,
        "composition":   list(composition.values()),
    }


# ---------------------------------------------------------------------------
# Player stats (season aggregate)
# ---------------------------------------------------------------------------

@router.get("/season/{season_id}/players")
def get_player_stats(season_id: int):
    """All players with season stats + fantasy ownership info."""
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
            "player_id":      r[0],
            "name":           r[1],
            "web_name":       r[2],
            "position":       r[3],
            "pl_team":        r[4],
            "total_points":   r[5],
            "goals":          r[6],
            "assists":        r[7],
            "clean_sheets":   r[8],
            "bonus":          r[9],
            "minutes":        r[10],
            "saves":          r[11],
            "yellow_cards":   r[12],
            "red_cards":      r[13],
            "goals_conceded": r[14],
            "blank_gws":      r[15],
            "owner":          r[16],
            "owner_team_id":  r[17],
        }
        for r in rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Transfer endpoints — drop these into queries.py replacing
# get_transfer_analytics() and adding get_transfer_stats().
#
# get_transfer_analytics()  → GET /season/{id}/transfers
# get_transfer_stats()      → GET /season/{id}/transfer-stats
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/season/{season_id}/transfers")
def get_transfer_analytics(season_id: int):
    """
    Enriched transfer list + manager summary with hit rate.

    Each transfer row contains:
      web_name_in/out, pos_in/out, team_in/out, delta (full-season after GW),
      delta_window (points during actual ownership window), delta_per_gw,
      gws_held, pts_in_window, pts_out_window, ownership_from/to,
      window_is_full_season, smart_score (FDR fixture quality of swap),
      fdr_in/out (avg FDR next 3 GWs at time of transfer),
      flag_in/out (injury flag at transfer time from player_status_history),
      chart_gw_points (dict gw → {in, out} for the mini line chart).

    manager_summary adds hit_rate (% of transfers with delta > 0).
    best/worst_transfer_pgw are ranked by delta_per_gw, not raw delta.
    """
    _season_or_404(season_id)

    with get_conn() as conn:
        with conn.cursor() as cur:

            # ── 1. Core transfer rows with enriched player metadata ──────────
            cur.execute("""
                SELECT
                    tx.id,
                    ft.id                              AS fpl_team_id,
                    ft.player_first_name               AS manager,
                    tx.gw,
                    tx.kind,
                    -- player IN
                    p_in.id                            AS player_in_id,
                    p_in.web_name                      AS web_name_in,
                    et_in.singular_name_short          AS pos_in,
                    plt_in.short_name                  AS team_in,
                    p_in.team                          AS team_in_id,
                    -- player OUT
                    p_out.id                           AS player_out_id,
                    p_out.web_name                     AS web_name_out,
                    et_out.singular_name_short         AS pos_out,
                    plt_out.short_name                 AS team_out,
                    p_out.team                         AS team_out_id
                FROM transactions tx
                JOIN fantasy_teams ft
                    ON ft.id = tx.team_id AND ft.season_id = tx.season_id
                JOIN players p_in
                    ON p_in.id = tx.player_in_id AND p_in.season_id = tx.season_id
                JOIN players p_out
                    ON p_out.id = tx.player_out_id AND p_out.season_id = tx.season_id
                JOIN element_type et_in  ON et_in.id  = p_in.position
                JOIN element_type et_out ON et_out.id = p_out.position
                LEFT JOIN premier_league_teams plt_in
                    ON plt_in.id = p_in.team AND plt_in.season_id = tx.season_id
                LEFT JOIN premier_league_teams plt_out
                    ON plt_out.id = p_out.team AND plt_out.season_id = tx.season_id
                WHERE tx.season_id = %s AND tx.result = 'a'
                ORDER BY tx.gw, tx.id;
            """, (season_id,))
            raw_rows = cur.fetchall()

            # ── 2. Per-player GW stats (for delta and chart) ─────────────────
            cur.execute("""
                SELECT player_id, gw, total_points
                FROM player_gameweek_stats
                WHERE season_id = %s
                ORDER BY player_id, gw;
            """, (season_id,))
            gw_stats_rows = cur.fetchall()

            # ── 3. Ownership windows (to compute ownership-window delta) ─────
            cur.execute("""
                SELECT player_id, team_id, from_gw, to_gw
                FROM player_ownership_history
                WHERE season_id = %s;
            """, (season_id,))
            ownership_rows = cur.fetchall()

            # ── 4. Team strength history for FDR at transfer time ────────────
            cur.execute("""
                SELECT DISTINCT ON (team_id)
                    team_id, gw,
                    strength_attack_home,  strength_attack_away,
                    strength_defence_home, strength_defence_away
                FROM team_strength_history
                WHERE season_id = %s
                ORDER BY team_id, gw DESC;
            """, (season_id,))
            strength_rows = cur.fetchall()

            # ── 5. Fixtures for FDR (next 3 GWs after each transfer) ─────────
            cur.execute("""
                SELECT gw, team_h, team_a
                FROM fixtures
                WHERE season_id = %s
                ORDER BY gw;
            """, (season_id,))
            fixture_rows = cur.fetchall()

            # ── 6. Player status history for archived flags ──────────────────
            cur.execute("""
                SELECT player_id, gw, status, chance_of_playing_next_round, news
                FROM player_status_history
                WHERE season_id = %s;
            """, (season_id,))
            flag_rows = cur.fetchall()

    # ── Build lookup structures ──────────────────────────────────────────────

    # gw points: {player_id: {gw: pts}}
    gw_pts: dict = {}
    for player_id, gw, pts in gw_stats_rows:
        if player_id not in gw_pts:
            gw_pts[player_id] = {}
        gw_pts[player_id][gw] = int(pts)

    # ownership windows: {player_id: [(team_id, from_gw, to_gw)]}
    ownership_map: dict = {}
    for player_id, team_id, from_gw, to_gw in ownership_rows:
        if player_id not in ownership_map:
            ownership_map[player_id] = []
        ownership_map[player_id].append((team_id, from_gw, to_gw or 38))

    # strength map: {team_id: {atk_h, atk_a, def_h, def_a}}
    strength_map = {
        row[0]: {
            "atk_h": row[2], "atk_a": row[3],
            "def_h": row[4], "def_a": row[5],
        }
        for row in strength_rows
    }

    # fixtures by GW: {gw: [(team_h, team_a)]}
    fixtures_by_gw: dict = {}
    for gw, team_h, team_a in fixture_rows:
        if gw not in fixtures_by_gw:
            fixtures_by_gw[gw] = []
        fixtures_by_gw[gw].append((team_h, team_a))

    # flags: {player_id: [(gw, status, chance, news)]} sorted by gw desc
    flags_map: dict = {}
    for player_id, gw, status, chance, news in flag_rows:
        if player_id not in flags_map:
            flags_map[player_id] = []
        flags_map[player_id].append((gw, status, chance, news))
    for pid in flags_map:
        flags_map[pid].sort(key=lambda x: x[0], reverse=True)

    # league-wide strength averages for FDR scale
    all_atk_a = [v["atk_a"] for v in strength_map.values() if v["atk_a"]]
    all_def_a = [v["def_a"] for v in strength_map.values() if v["def_a"]]
    all_atk_h = [v["atk_h"] for v in strength_map.values() if v["atk_h"]]
    all_def_h = [v["def_h"] for v in strength_map.values() if v["def_h"]]
    avg_atk_a = sum(all_atk_a) / len(all_atk_a) if all_atk_a else 1200
    avg_def_a = sum(all_def_a) / len(all_def_a) if all_def_a else 1200
    avg_atk_h = sum(all_atk_h) / len(all_atk_h) if all_atk_h else 1200
    avg_def_h = sum(all_def_h) / len(all_def_h) if all_def_h else 1200

    def _fdr_scale(raw, avg):
        if not raw or not avg:
            return 3
        r = raw / avg
        if r < 0.87: return 2
        if r < 1.07: return 3
        if r < 1.17: return 4
        return 5

    def _avg_fdr_next_n(team_id, from_gw, n, position):
        """Average FDR for team over next n GWs from from_gw. Position-aware."""
        fdrs = []
        checked = 0
        gw = from_gw + 1
        while checked < n and gw <= 38:
            for t_h, t_a in fixtures_by_gw.get(gw, []):
                if t_h == team_id or t_a == team_id:
                    is_home = t_h == team_id
                    opp_id  = t_a if is_home else t_h
                    opp     = strength_map.get(opp_id, {})
                    if position in ("GKP", "DEF"):
                        # difficulty keeping clean sheet = opp attack
                        raw = opp.get("atk_a") if is_home else opp.get("atk_h")
                        avg = avg_atk_a if is_home else avg_atk_h
                    else:
                        # difficulty scoring = opp defence
                        raw = opp.get("def_a") if is_home else opp.get("def_h")
                        avg = avg_def_a if is_home else avg_def_h
                    fdrs.append(_fdr_scale(raw, avg))
                    checked += 1
                    break
            gw += 1
        return round(sum(fdrs) / len(fdrs), 1) if fdrs else None

    def _get_flag(player_id, at_gw):
        """Return the most recent flag snapshot at or before at_gw."""
        for snap_gw, status, chance, news in flags_map.get(player_id, []):
            if snap_gw <= at_gw and status not in ("a", None):
                level = None
                if chance == 0:       level = "0%"
                elif chance is not None and chance <= 25: level = "25%"
                elif chance is not None and chance <= 50: level = "50%"
                elif chance is not None and chance <= 75: level = "75%"
                elif status == "i":   level = "INJ"
                elif status == "s":   level = "SUS"
                elif status == "d":   level = "DTB"
                if level:
                    return {"level": level, "news": news or ""}
        return None

    def _ownership_window(player_id, team_id, transfer_gw):
        """
        Find the ownership window for player_id at team_id starting around transfer_gw.
        Returns (from_gw, to_gw, is_full_season).
        """
        windows = ownership_map.get(player_id, [])
        # find the window for this team closest to the transfer GW
        best = None
        for tid, from_gw, to_gw in windows:
            if tid == team_id and from_gw >= transfer_gw - 2:
                if best is None or abs(from_gw - transfer_gw) < abs(best[0] - transfer_gw):
                    best = (from_gw, to_gw)
        if best:
            return best[0], best[1], False
        # no ownership history found — use full season from transfer_gw
        last_gw = max(gw_pts.get(player_id, {transfer_gw: transfer_gw}).keys(), default=transfer_gw)
        return transfer_gw + 1, last_gw, True

    # ── Build enriched transfer list ─────────────────────────────────────────
    transfers = []
    for row in raw_rows:
        (tx_id, fpl_team_id, manager, gw, kind,
         pid_in,  wn_in,  pos_in,  team_in,  tid_in,
         pid_out, wn_out, pos_out, team_out, tid_out) = row

        # Full-season delta (points in minus points out, after transfer GW)
        pts_in_full  = sum(v for g, v in gw_pts.get(pid_in,  {}).items() if g > gw)
        pts_out_full = sum(v for g, v in gw_pts.get(pid_out, {}).items() if g > gw)
        delta_full   = int(pts_in_full - pts_out_full)

        # Ownership-window delta
        own_from, own_to, is_full = _ownership_window(pid_in, fpl_team_id, gw)
        pts_in_win  = sum(v for g, v in gw_pts.get(pid_in,  {}).items() if own_from <= g <= own_to)
        pts_out_win = sum(v for g, v in gw_pts.get(pid_out, {}).items() if own_from <= g <= own_to)
        delta_win   = int(pts_in_win - pts_out_win)
        gws_held    = max(own_to - own_from + 1, 1)
        delta_pgw   = round(delta_win / gws_held, 1)

        # FDR at transfer time (avg next 3 GWs)
        fdr_in  = _avg_fdr_next_n(tid_in,  gw, 3, pos_in)
        fdr_out = _avg_fdr_next_n(tid_out, gw, 3, pos_out)

        # Smart score: improvement in fixture quality (negative = better for GKP/DEF)
        smart_score = None
        if fdr_in is not None and fdr_out is not None:
            # For attacking positions: lower FDR out = harder to score, lower FDR in = easier = good
            # For defensive: lower FDR in = harder CS, so we want fdr_in < fdr_out
            # Smart score = fdr_out - fdr_in (positive = brought in better fixtures)
            smart_score = round(fdr_out - fdr_in, 1)

        # Archived flags
        flag_in  = _get_flag(pid_in,  gw)
        flag_out = _get_flag(pid_out, gw)

        # GW-by-GW points chart data
        all_gws = sorted(set(
            list(gw_pts.get(pid_in,  {}).keys()) +
            list(gw_pts.get(pid_out, {}).keys())
        ))
        chart_gw_points = {
            g: {
                "in":  gw_pts.get(pid_in,  {}).get(g, 0),
                "out": gw_pts.get(pid_out, {}).get(g, 0),
            }
            for g in all_gws if g > gw
        }

        transfers.append({
            "id":                  tx_id,
            "team_id":             fpl_team_id,
            "manager":             manager,
            "gw":                  gw,
            "kind":                kind,
            # player fields
            "player_in_id":        pid_in,
            "web_name_in":         wn_in,
            "pos_in":              pos_in,
            "team_in":             team_in,
            "player_out_id":       pid_out,
            "web_name_out":        wn_out,
            "pos_out":             pos_out,
            "team_out":            team_out,
            # deltas
            "delta":               delta_full,
            "points_in_after":     int(pts_in_full),
            "points_out_after":    int(pts_out_full),
            # ownership-window metrics
            "delta_window":        delta_win,
            "delta_per_gw":        delta_pgw,
            "gws_held":            gws_held,
            "pts_in_window":       int(pts_in_win),
            "pts_out_window":      int(pts_out_win),
            "ownership_from":      own_from,
            "ownership_to":        own_to,
            "window_is_full_season": is_full,
            # FDR + smart score
            "fdr_in":              fdr_in,
            "fdr_out":             fdr_out,
            "smart_score":         smart_score,
            # flags
            "flag_in":             flag_in,
            "flag_in_archived":    flag_in is not None,
            "flag_out":            flag_out,
            "flag_out_archived":   flag_out is not None,
            # chart
            "chart_gw_points":     chart_gw_points,
        })

    # ── Manager summary with hit_rate ────────────────────────────────────────
    managers_dict: dict = {}
    for t in transfers:
        m = t["manager"]
        if m not in managers_dict:
            managers_dict[m] = {
                "manager":        m,
                "team_id":        t["team_id"],
                "total_moves":    0,
                "positive_moves": 0,
                "net_delta":      0,
                "hit_rate":       0,
                "best_transfer":  None,
                "worst_transfer": None,
            }
        managers_dict[m]["total_moves"]    += 1
        managers_dict[m]["net_delta"]      += t["delta"]
        if t["delta"] > 0:
            managers_dict[m]["positive_moves"] += 1
        if managers_dict[m]["best_transfer"]  is None or \
                t["delta"] > managers_dict[m]["best_transfer"]["delta"]:
            managers_dict[m]["best_transfer"] = t
        if managers_dict[m]["worst_transfer"] is None or \
                t["delta"] < managers_dict[m]["worst_transfer"]["delta"]:
            managers_dict[m]["worst_transfer"] = t

    for mgr in managers_dict.values():
        mgr["hit_rate"] = round(
            (mgr["positive_moves"] / mgr["total_moves"]) * 100
        ) if mgr["total_moves"] else 0

    manager_summary = list(managers_dict.values())

    # ── Best / worst by pts/GW (the featured callout cards) ─────────────────
    best_pgw  = max(transfers, key=lambda t: t["delta_per_gw"]) if transfers else None
    worst_pgw = min(transfers, key=lambda t: t["delta_per_gw"]) if transfers else None

    return {
        "all_transfers":       transfers,
        "best_transfer":       max(transfers, key=lambda t: t["delta"]) if transfers else None,
        "worst_transfer":      min(transfers, key=lambda t: t["delta"]) if transfers else None,
        "best_transfer_pgw":   best_pgw,
        "worst_transfer_pgw":  worst_pgw,
        "manager_summary":     manager_summary,
    }


@router.get("/season/{season_id}/transfer-stats")
def get_transfer_stats(season_id: int):
    """
    Aggregated transfer statistics for the Analytics tab.
    Returns: by_position, by_manager, by_gw, busiest_gw, regret_board,
             by_gw_position, by_team, by_gw_team, team_strengths, all_fixtures.
    """
    _season_or_404(season_id)

    with get_conn() as conn:
        with conn.cursor() as cur:

            # ── by_position ─────────────────────────────────────────────────
            # count, hit_rate, avg_delta by position (player IN position)
            cur.execute("""
                WITH tx_data AS (
                    SELECT
                        et_in.singular_name_short AS position,
                        COALESCE((
                            SELECT SUM(pgs.total_points) FROM player_gameweek_stats pgs
                            WHERE pgs.player_id = tx.player_in_id AND pgs.season_id = tx.season_id AND pgs.gw > tx.gw
                        ), 0) -
                        COALESCE((
                            SELECT SUM(pgs.total_points) FROM player_gameweek_stats pgs
                            WHERE pgs.player_id = tx.player_out_id AND pgs.season_id = tx.season_id AND pgs.gw > tx.gw
                        ), 0) AS delta
                    FROM transactions tx
                    JOIN players p_in  ON p_in.id  = tx.player_in_id  AND p_in.season_id  = tx.season_id
                    JOIN element_type et_in ON et_in.id = p_in.position
                    WHERE tx.season_id = %s AND tx.result = 'a'
                )
                SELECT
                    position,
                    COUNT(*)                                            AS count,
                    ROUND(AVG(delta)::numeric, 1)                      AS avg_delta,
                    ROUND(100.0 * COUNT(*) FILTER (WHERE delta > 0)
                          / NULLIF(COUNT(*), 0), 0)                    AS hit_rate,
                    -- normalise by squad slots: GKP=2, DEF=5, MID=5, FWD=3
                    ROUND(COUNT(*)::numeric / CASE position
                        WHEN 'GKP' THEN 2 WHEN 'DEF' THEN 5
                        WHEN 'MID' THEN 5 WHEN 'FWD' THEN 3 ELSE 1
                    END, 2)                                             AS count_per_slot
                FROM tx_data
                GROUP BY position
                ORDER BY count DESC;
            """, (season_id,))
            by_pos_rows = cur.fetchall()

            # ── by_manager ──────────────────────────────────────────────────
            cur.execute("""
                WITH tx_data AS (
                    SELECT
                        ft.player_first_name AS manager,
                        ft.id AS fpl_team_id,
                        COALESCE((
                            SELECT SUM(pgs.total_points) FROM player_gameweek_stats pgs
                            WHERE pgs.player_id = tx.player_in_id AND pgs.season_id = tx.season_id AND pgs.gw > tx.gw
                        ), 0) -
                        COALESCE((
                            SELECT SUM(pgs.total_points) FROM player_gameweek_stats pgs
                            WHERE pgs.player_id = tx.player_out_id AND pgs.season_id = tx.season_id AND pgs.gw > tx.gw
                        ), 0) AS delta
                    FROM transactions tx
                    JOIN fantasy_teams ft ON ft.id = tx.team_id AND ft.season_id = tx.season_id
                    WHERE tx.season_id = %s AND tx.result = 'a'
                )
                SELECT
                    manager,
                    fpl_team_id,
                    COUNT(*)                                             AS count,
                    SUM(delta)                                           AS net_delta,
                    COUNT(*) FILTER (WHERE delta > 0)                   AS positive_moves,
                    ROUND(100.0 * COUNT(*) FILTER (WHERE delta > 0)
                          / NULLIF(COUNT(*), 0), 0)                     AS hit_rate
                FROM tx_data
                GROUP BY manager, fpl_team_id
                ORDER BY net_delta DESC;
            """, (season_id,))
            by_mgr_rows = cur.fetchall()

            # ── by_gw ────────────────────────────────────────────────────────
            cur.execute("""
                SELECT tx.gw, COUNT(*) AS count
                FROM transactions tx
                WHERE tx.season_id = %s AND tx.result = 'a'
                GROUP BY tx.gw
                ORDER BY tx.gw;
            """, (season_id,))
            by_gw_rows = cur.fetchall()

            # ── by_gw_position ────────────────────────────────────────────────
            cur.execute("""
                SELECT tx.gw, et_in.singular_name_short AS position, COUNT(*) AS count
                FROM transactions tx
                JOIN players p_in ON p_in.id = tx.player_in_id AND p_in.season_id = tx.season_id
                JOIN element_type et_in ON et_in.id = p_in.position
                WHERE tx.season_id = %s AND tx.result = 'a'
                GROUP BY tx.gw, et_in.singular_name_short
                ORDER BY tx.gw, et_in.id;
            """, (season_id,))
            by_gw_pos_rows = cur.fetchall()

            # ── by_team ───────────────────────────────────────────────────────
            # transfers IN and OUT per PL team, split by attack/defence
            cur.execute("""
                SELECT
                    plt.short_name AS pl_team,
                    SUM(CASE WHEN et.singular_name_short IN ('MID','FWD') THEN 1 ELSE 0 END) AS attack_in,
                    SUM(CASE WHEN et.singular_name_short IN ('GKP','DEF') THEN 1 ELSE 0 END) AS defense_in
                FROM transactions tx
                JOIN players p ON p.id = tx.player_in_id AND p.season_id = tx.season_id
                JOIN element_type et ON et.id = p.position
                JOIN premier_league_teams plt ON plt.id = p.team AND plt.season_id = tx.season_id
                WHERE tx.season_id = %s AND tx.result = 'a'
                GROUP BY plt.short_name
                ORDER BY (attack_in + defense_in) DESC
                LIMIT 20;
            """, (season_id,))
            team_in_rows = cur.fetchall()

            cur.execute("""
                SELECT
                    plt.short_name AS pl_team,
                    SUM(CASE WHEN et.singular_name_short IN ('MID','FWD') THEN 1 ELSE 0 END) AS attack_out,
                    SUM(CASE WHEN et.singular_name_short IN ('GKP','DEF') THEN 1 ELSE 0 END) AS defense_out
                FROM transactions tx
                JOIN players p ON p.id = tx.player_out_id AND p.season_id = tx.season_id
                JOIN element_type et ON et.id = p.position
                JOIN premier_league_teams plt ON plt.id = p.team AND plt.season_id = tx.season_id
                WHERE tx.season_id = %s AND tx.result = 'a'
                GROUP BY plt.short_name
                ORDER BY (attack_out + defense_out) DESC
                LIMIT 20;
            """, (season_id,))
            team_out_rows = cur.fetchall()

            # ── by_gw_team ────────────────────────────────────────────────────
            cur.execute("""
                SELECT tx.gw, plt.short_name AS pl_team, 'in' AS direction, COUNT(*) AS count
                FROM transactions tx
                JOIN players p ON p.id = tx.player_in_id AND p.season_id = tx.season_id
                JOIN premier_league_teams plt ON plt.id = p.team AND plt.season_id = tx.season_id
                WHERE tx.season_id = %s AND tx.result = 'a'
                GROUP BY tx.gw, plt.short_name
                UNION ALL
                SELECT tx.gw, plt.short_name AS pl_team, 'out' AS direction, COUNT(*) AS count
                FROM transactions tx
                JOIN players p ON p.id = tx.player_out_id AND p.season_id = tx.season_id
                JOIN premier_league_teams plt ON plt.id = p.team AND plt.season_id = tx.season_id
                WHERE tx.season_id = %s AND tx.result = 'a'
                GROUP BY tx.gw, plt.short_name
                ORDER BY gw, pl_team;
            """, (season_id, season_id))
            by_gw_team_rows = cur.fetchall()

            # ── regret_board ──────────────────────────────────────────────────
            # transfers where dropped player outscored pickup — top 10
            cur.execute("""
                WITH regret AS (
                    SELECT
                        ft.player_first_name              AS manager,
                        tx.gw,
                        p_in.web_name                     AS player_in,
                        et_in.singular_name_short         AS pos_in,
                        p_out.web_name                    AS player_out,
                        et_out.singular_name_short        AS pos_out,
                        COALESCE((
                            SELECT SUM(pgs.total_points) FROM player_gameweek_stats pgs
                            WHERE pgs.player_id = tx.player_in_id AND pgs.season_id = tx.season_id AND pgs.gw > tx.gw
                        ), 0) AS pts_in,
                        COALESCE((
                            SELECT SUM(pgs.total_points) FROM player_gameweek_stats pgs
                            WHERE pgs.player_id = tx.player_out_id AND pgs.season_id = tx.season_id AND pgs.gw > tx.gw
                        ), 0) AS pts_out
                    FROM transactions tx
                    JOIN fantasy_teams ft ON ft.id = tx.team_id AND ft.season_id = tx.season_id
                    JOIN players p_in  ON p_in.id  = tx.player_in_id  AND p_in.season_id  = tx.season_id
                    JOIN players p_out ON p_out.id = tx.player_out_id AND p_out.season_id = tx.season_id
                    JOIN element_type et_in  ON et_in.id  = p_in.position
                    JOIN element_type et_out ON et_out.id = p_out.position
                    WHERE tx.season_id = %s AND tx.result = 'a'
                )
                SELECT manager, gw, player_in, pos_in, player_out, pos_out, pts_in, pts_out
                FROM regret
                WHERE pts_out > pts_in
                ORDER BY (pts_out - pts_in) DESC
                LIMIT 10;
            """, (season_id,))
            regret_rows = cur.fetchall()

            # ── team strengths + all fixtures (for FDR in club-activity chart) ─
            cur.execute("""
                SELECT DISTINCT ON (team_id)
                    team_id, name, short_name,
                    strength_attack_home  AS atk_h,
                    strength_attack_away  AS atk_a,
                    strength_defence_home AS def_h,
                    strength_defence_away AS def_a
                FROM premier_league_teams
                WHERE season_id = %s
                ORDER BY team_id, position DESC;
            """, (season_id,))
            team_strength_rows = cur.fetchall()

            cur.execute("""
                SELECT id, gw, team_h, team_a
                FROM fixtures
                WHERE season_id = %s
                ORDER BY gw;
            """, (season_id,))
            all_fixture_rows = cur.fetchall()

    # ── Assemble by_team ──────────────────────────────────────────────────────
    team_in_map  = {r[0]: {"attack": int(r[1]), "defense": int(r[2])} for r in team_in_rows}
    team_out_map = {r[0]: {"attack": int(r[1]), "defense": int(r[2])} for r in team_out_rows}
    all_teams = sorted(set(list(team_in_map.keys()) + list(team_out_map.keys())))
    by_team = [
        {
            "pl_team": t,
            "in":  team_in_map.get(t,  {"attack": 0, "defense": 0}),
            "out": team_out_map.get(t, {"attack": 0, "defense": 0}),
        }
        for t in all_teams
    ]
    by_team.sort(key=lambda x: x["in"]["attack"] + x["in"]["defense"], reverse=True)

    # ── Busiest GW ───────────────────────────────────────────────────────────
    busiest = max(by_gw_rows, key=lambda r: r[1]) if by_gw_rows else None

    return {
        "by_position": [
            {
                "position":      r[0],
                "count":         int(r[1]),
                "avg_delta":     float(r[2]) if r[2] else 0,
                "hit_rate":      int(r[3])   if r[3] else 0,
                "count_per_slot": float(r[4]) if r[4] else 0,
            }
            for r in by_pos_rows
        ],
        "by_manager": [
            {
                "manager":        r[0],
                "team_id":        r[1],
                "count":          int(r[2]),
                "net_delta":      int(r[3]),
                "positive_moves": int(r[4]),
                "hit_rate":       int(r[5]) if r[5] else 0,
            }
            for r in by_mgr_rows
        ],
        "by_gw": [
            {"gw": r[0], "count": int(r[1])}
            for r in by_gw_rows
        ],
        "by_gw_position": [
            {"gw": r[0], "position": r[1], "count": int(r[2])}
            for r in by_gw_pos_rows
        ],
        "by_team": by_team,
        "by_gw_team": [
            {"gw": r[0], "pl_team": r[1], "direction": r[2], "count": int(r[3])}
            for r in by_gw_team_rows
        ],
        "busiest_gw": {"gw": busiest[0], "count": int(busiest[1])} if busiest else None,
        "regret_board": [
            {
                "manager":    r[0],
                "gw":         r[1],
                "player_in":  r[2],
                "pos_in":     r[3],
                "player_out": r[4],
                "pos_out":    r[5],
                "pts_in":     int(r[6]),
                "pts_out":    int(r[7]),
            }
            for r in regret_rows
        ],
        "team_strengths": [
            {
                "id":         r[0],
                "name":       r[1],
                "short_name": r[2],
                "atk_h": r[3], "atk_a": r[4],
                "def_h": r[5], "def_a": r[6],
            }
            for r in team_strength_rows
        ],
        "all_fixtures": [
            {"id": r[0], "gw": r[1], "team_h": r[2], "team_a": r[3]}
            for r in all_fixture_rows
        ],
    }

# ---------------------------------------------------------------------------
# Fixtures upcoming — FDR from team_strength_history (no live API call)
# ---------------------------------------------------------------------------

@router.get("/season/{season_id}/fixtures-upcoming")
def get_fixtures_upcoming(season_id: int):
    """
    Returns upcoming fixtures with attack/defence FDR.
    FDR derived from team_strength_history — no live external API call.
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

    teams = [{"id": r[0], "name": r[1], "short_name": r[2]} for r in pl_team_rows]
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

            # Current season aggregate — join seasons for name
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

            # Player name — most recent season entry
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
        "player_id": player_id,
        "name":      f"{player[0]} {player[1]}" if player else "Unknown",
        "web_name":  player[2] if player else "Unknown",
        "history":   history,
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
                    SUM(s.points_for)     AS points_for,
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
    """All-time highs, lows, and historic finishes across every season."""
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
                WHERE s.result = 'w'
                ORDER BY margin DESC LIMIT 1;
            """)
            margin = cur.fetchone()

            cur.execute("""
                SELECT
                    ft.player_first_name,
                    se.name AS season,
                    se.id   AS season_id,
                    MAX(s.cumulative_points) AS league_points,
                    RANK() OVER (
                        PARTITION BY s.season_id
                        ORDER BY MAX(s.cumulative_points) DESC
                    ) AS finish
                FROM standings s
                JOIN fantasy_teams ft ON ft.internal_team_id = s.team_id
                    AND ft.season_id = s.season_id
                JOIN seasons se ON se.id = s.season_id
                GROUP BY ft.player_first_name, se.name, se.id, s.season_id
                ORDER BY se.id, finish;
            """)
            finishes = cur.fetchall()

    return {
        "highest_score_ever": {
            "manager": high[0], "gw": high[1],
            "season": high[2], "points": high[3]
        } if high else None,
        "lowest_score_ever": {
            "manager": low[0], "gw": low[1],
            "season": low[2], "points": low[3]
        } if low else None,
        "biggest_margin_ever": {
            "winner": margin[0], "loser": margin[1],
            "gw": margin[2], "season": margin[3],
            "winner_points": margin[4], "loser_points": margin[5],
            "margin": margin[6]
        } if margin else None,
        "historic_finishes": [
            {
                "manager":       r[0],
                "season":        r[1],
                "season_id":     r[2],
                "league_points": r[3],
                "finish":        r[4],
            }
            for r in finishes
        ],
    }
