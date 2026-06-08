"""
queries.py — read-only endpoints for the frontend.
All heavy SQL lives here; the frontend just renders what these return.
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


# ---------------------------------------------------------------------------
# Seasons list — for the season picker in the UI
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
# Season overview
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
                                          SUM(s.points_for) DESC)  AS rank
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
            "gw":               r[0],
            "manager":          r[1],
            "team_id":          r[2],
            "cumulative_points": r[3],
            "points_for":       r[4],
            "result":           r[5],
            "gw_rank":          r[6],
        }
        for r in rows
    ]


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
                    t2.player_first_name AS entry_2_name
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
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# H2H matrix
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
                    SUM(s.points_for)                       AS points_for,
                    SUM(s.points_against)                   AS points_against
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
# Season records (fun stats)
# ---------------------------------------------------------------------------

@router.get("/season/{season_id}/records")
def get_season_records(season_id: int):
    """Highest/lowest week, biggest margin, most bench pts, etc."""
    _season_or_404(season_id)
    with get_conn() as conn:
        with conn.cursor() as cur:

            # Highest scoring week
            cur.execute("""
                SELECT ft.player_first_name, s.gw, s.points_for
                FROM standings s
                JOIN fantasy_teams ft ON ft.internal_team_id = s.team_id
                    AND ft.season_id = s.season_id
                WHERE s.season_id = %s
                ORDER BY s.points_for DESC LIMIT 1;
            """, (season_id,))
            high = cur.fetchone()

            # Lowest scoring week
            cur.execute("""
                SELECT ft.player_first_name, s.gw, s.points_for
                FROM standings s
                JOIN fantasy_teams ft ON ft.internal_team_id = s.team_id
                    AND ft.season_id = s.season_id
                WHERE s.season_id = %s
                ORDER BY s.points_for ASC LIMIT 1;
            """, (season_id,))
            low = cur.fetchone()

            # Biggest winning margin
            cur.execute("""
                SELECT
                    t1.player_first_name AS winner,
                    t2.player_first_name AS loser,
                    s.gw,
                    s.points_for,
                    s.points_against,
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

            # Most points in a losing effort
            cur.execute("""
                SELECT ft.player_first_name, s.gw, s.points_for, s.points_against
                FROM standings s
                JOIN fantasy_teams ft ON ft.internal_team_id = s.team_id
                    AND ft.season_id = s.season_id
                WHERE s.season_id = %s AND s.result = 'l'
                ORDER BY s.points_for DESC LIMIT 1;
            """, (season_id,))
            unlucky = cur.fetchone()

            # Closest match
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

            # Longest win streak
            cur.execute("""
                WITH ordered AS (
                    SELECT team_id, gw, result,
                           ROW_NUMBER() OVER (PARTITION BY team_id ORDER BY gw) -
                           ROW_NUMBER() OVER (PARTITION BY team_id, result ORDER BY gw) AS grp
                    FROM standings WHERE season_id = %s
                ),
                streaks AS (
                    SELECT team_id, result, COUNT(*) AS streak_len, MIN(gw) AS start_gw, MAX(gw) AS end_gw
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

            # Longest losing streak
            cur.execute("""
                WITH ordered AS (
                    SELECT team_id, gw, result,
                           ROW_NUMBER() OVER (PARTITION BY team_id ORDER BY gw) -
                           ROW_NUMBER() OVER (PARTITION BY team_id, result ORDER BY gw) AS grp
                    FROM standings WHERE season_id = %s
                ),
                streaks AS (
                    SELECT team_id, result, COUNT(*) AS streak_len, MIN(gw) AS start_gw, MAX(gw) AS end_gw
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

            # Bench points per manager (from gameweek_lineups + player_gameweek_stats)
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

            # Points breakdown by position
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
                    SUM(pgs.penalties_saved) AS penalties_saved,
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
                JOIN players p ON p.id = gl.player_id
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
                    SUM(s.points_for)      AS pf,
                    SUM(s.points_against)  AS pa
                FROM standings s
                JOIN fantasy_teams opp
                    ON opp.internal_team_id = s.opponent_id
                    AND opp.season_id = s.season_id
                WHERE s.season_id = %s AND s.team_id = %s
                GROUP BY opp.player_first_name, s.opponent_id
                ORDER BY wins DESC;
            """, (season_id, team_id))
            h2h = cur.fetchall()

            # Transfer activity
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
                JOIN players p_in  ON p_in.id  = tx.player_in_id
                JOIN players p_out ON p_out.id = tx.player_out_id
                JOIN fantasy_teams ft ON ft.id = tx.team_id
                    AND ft.season_id = tx.season_id
                WHERE tx.season_id = %s
                    AND ft.internal_team_id = %s
                ORDER BY tx.gw, tx.timestamp;
            """, (season_id, team_id))
            transfers = cur.fetchall()

    return {
        "weekly": [
            {
                "gw":               r[0],
                "points_for":       r[1],
                "points_against":   r[2],
                "result":           r[3],
                "league_points":    r[4],
                "cumulative_points": r[5],
                "opponent_id":      r[6],
                "opponent_name":    r[7],
                "gw_rank":          r[8],
            }
            for r in weekly
        ],
        "by_position": [
            {
                "position":         r[0],
                "points":           r[1],
                "goals":            r[2],
                "assists":          r[3],
                "clean_sheets":     r[4],
                "bonus":            r[5],
                "saves":            r[6],
                "yellow_cards":     r[7],
                "red_cards":        r[8],
                "minutes":          r[9],
                "goals_conceded":   r[10],
                "own_goals":        r[11],
                "penalties_saved":  r[12],
                "penalties_missed": r[13],
                "tackles":          r[14],
                "recoveries":       r[15],
                "cbi":              r[16],
                "defensive_contribution": r[17],
                "bps":              r[18],
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
                "gw":           r[0],
                "kind":         r[1],
                "result":       r[2],
                "player_in":    r[3],
                "player_out":   r[4],
                "player_in_id": r[5],
                "player_out_id": r[6],
            }
            for r in transfers
        ],
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
                    RANK() OVER (ORDER BY COALESCE(SUM(pgs.total_points), 0) DESC)
                        AS points_rank,
                    plt.short_name AS pl_team
                FROM draft_picks dp
                JOIN fantasy_teams ft
                    ON ft.id = dp.entry_id
                    AND ft.season_id = dp.season_id
                JOIN players p ON p.id = dp.player_id
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

    # Value picks: biggest overperformers vs draft position
    # Expected: earlier picks should score more. Compare actual vs peer picks.
    value = sorted(picks, key=lambda x: x["season_points"], reverse=True)[:5]
    busts = sorted(
        [p for p in picks if p["overall_pick"] <= 36],
        key=lambda x: x["season_points"]
    )[:5]

    # Median points per round
    import statistics
    round_stats = {}
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

    # Position composition per manager
    composition = {}
    for p in picks:
        tid = p["team_id"]
        mgr = p["manager"]
        if tid not in composition:
            composition[tid] = {"manager": mgr, "team_id": tid, "GKP": 0, "DEF": 0, "MID": 0, "FWD": 0, "total_points": 0}
        composition[tid][p["position"]] = composition[tid].get(p["position"], 0) + 1
        composition[tid]["total_points"] += p["season_points"]

    # Value score: season_points relative to pick position expectation
    # Simple: compare each pick to the median for its round
    median_by_round = {r["round"]: r["median"] for r in round_medians}
    for p in picks:
        expected = median_by_round.get(p["round"], 0)
        p["value_score"] = round(p["season_points"] - expected, 1)

    value_by_score = sorted(picks, key=lambda x: x["value_score"], reverse=True)[:5]
    busts_by_score = sorted(
        [p for p in picks if p["overall_pick"] <= 36],
        key=lambda x: x["value_score"]
    )[:5]

    return {
        "picks":           picks,
        "value_picks":     value_by_score,
        "busts":           busts_by_score,
        "round_medians":   round_medians,
        "composition":     list(composition.values()),
    }


# ---------------------------------------------------------------------------
# Player stats
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
                    COALESCE(SUM(pgs.goals), 0)           AS goals,
                    COALESCE(SUM(pgs.assists), 0)        AS assists,
                    COALESCE(SUM(pgs.clean_sheets), 0)   AS clean_sheets,
                    COALESCE(SUM(pgs.bonus), 0)          AS bonus,
                    COALESCE(SUM(pgs.minutes), 0)        AS minutes,
                    COALESCE(SUM(pgs.saves), 0)          AS saves,
                    COALESCE(SUM(pgs.yellow_cards), 0)   AS yellow_cards,
                    COALESCE(SUM(pgs.red_cards), 0)      AS red_cards,
                    COALESCE(SUM(pgs.goals_conceded), 0) AS goals_conceded,
                    COUNT(CASE WHEN pgs.minutes = 0 THEN 1 END) AS blank_gws,
                    ft.player_first_name                  AS owner,
                    ft.internal_team_id                   AS owner_team_id
                FROM players p
                JOIN element_type et ON et.id = p.position
                JOIN premier_league_teams plt
                    ON plt.id = p.team AND plt.season_id = %s
                LEFT JOIN player_gameweek_stats pgs
                    ON pgs.player_id = p.id AND pgs.season_id = %s
                LEFT JOIN player_fantasy_status pfs
                    ON pfs.player_id = p.id AND pfs.season_id = %s
                LEFT JOIN fantasy_teams ft
                    ON ft.id = pfs.team_id AND ft.season_id = %s
                WHERE p.season_id = %s
                GROUP BY p.id, p.first_name, p.second_name, p.web_name,
                         et.singular_name_short, plt.short_name,
                         ft.player_first_name, ft.internal_team_id
                HAVING COALESCE(SUM(pgs.total_points), 0) > 0
                ORDER BY total_points DESC;
            """, (season_id, season_id, season_id, season_id, season_id))
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
                    JOIN players p_in  ON p_in.id  = tx.player_in_id
                    JOIN players p_out ON p_out.id = tx.player_out_id
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

    # Per-manager summary
    managers: dict = {}
    for t in transfers:
        m = t["manager"]
        if m not in managers:
            managers[m] = {
                "manager":       m,
                "team_id":       t["team_id"],
                "total_moves":   0,
                "net_delta":     0,
                "best_transfer": None,
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
        "all_transfers":      transfers,
        "best_transfer":      transfers[0]  if transfers else None,
        "worst_transfer":     transfers[-1] if transfers else None,
        "manager_summary":    list(managers.values()),
    }


# ---------------------------------------------------------------------------
# All-time H2H (across all seasons in DB)
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
                WHERE s.result = 'w'
                ORDER BY margin DESC LIMIT 1;
            """)
            margin = cur.fetchone()

            # Historic league finishes per manager per season
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


# ---------------------------------------------------------------------------
# GW lineup for a specific team + gameweek (for expandable results)
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
                JOIN players p ON p.id = gl.player_id
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
# Fixtures upcoming — for fixture difficulty grid
# Uses FPL main bootstrap for attack/defence split FDR
# ---------------------------------------------------------------------------

@router.get("/season/{season_id}/fixtures-upcoming")
def get_fixtures_upcoming(season_id: int):
    """Returns upcoming fixtures with attack/defence FDR from FPL main bootstrap."""
    from fpl_client import fetch_fpl_bootstrap, fetch_fixtures
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

    # Pull live team strength data from FPL main bootstrap
    try:
        bootstrap  = fetch_fpl_bootstrap()
        fpl_teams  = bootstrap.get("teams", [])
    except Exception:
        fpl_teams  = []

    teams = [
        {
            "id":                    t["id"],
            "name":                  t["name"],
            "short_name":            t["short_name"],
            "strength_overall_home": t.get("strength_overall_home", 1200),
            "strength_overall_away": t.get("strength_overall_away", 1200),
            "strength_attack_home":  t.get("strength_attack_home", 1200),
            "strength_attack_away":  t.get("strength_attack_away", 1200),
            "strength_defence_home": t.get("strength_defence_home", 1200),
            "strength_defence_away": t.get("strength_defence_away", 1200),
        }
        for t in fpl_teams
    ]

    strength_map = {t["id"]: t for t in teams}

    def _fdr(strength_val, all_vals):
        """Convert raw strength to 1-5 FDR scale."""
        if not all_vals or not strength_val: return 3
        avg = sum(all_vals) / len(all_vals)
        ratio = strength_val / avg
        if ratio < 0.87: return 2
        if ratio < 0.97: return 3
        if ratio < 1.07: return 3
        if ratio < 1.17: return 4
        return 5

    all_atk  = [t["strength_attack_away"]  for t in teams if t["strength_attack_away"]]
    all_def  = [t["strength_defence_away"] for t in teams if t["strength_defence_away"]]
    all_atk_h = [t["strength_attack_home"]  for t in teams if t["strength_attack_home"]]
    all_def_h = [t["strength_defence_home"] for t in teams if t["strength_defence_home"]]

    fixtures = []
    for r in fixture_rows:
        gw, team_h_id, team_a_id, kickoff = r
        opp_h = strength_map.get(team_a_id, {})  # home team faces away opponent
        opp_a = strength_map.get(team_h_id, {})  # away team faces home opponent

        fixtures.append({
            "gw":           gw,
            "team_h":       team_h_id,
            "team_a":       team_a_id,
            "kickoff_time": str(kickoff) if kickoff else None,
            # Home team FDR — how hard is the away opponent's attack/defence?
            "team_h_attack_fdr":  _fdr(opp_h.get("strength_attack_away"), all_atk),
            "team_h_defence_fdr": _fdr(opp_h.get("strength_defence_away"), all_def),
            # Away team FDR
            "team_a_attack_fdr":  _fdr(opp_a.get("strength_attack_home"), all_atk_h),
            "team_a_defence_fdr": _fdr(opp_a.get("strength_defence_home"), all_def_h),
        })

    return {
        "current_gw": current_gw,
        "teams":      teams,
        "fixtures":   fixtures,
    }


# ---------------------------------------------------------------------------
# Player historical season stats (from element-summary)
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

            # Also get current season from player_gameweek_stats
            cur.execute("""
                SELECT SUM(total_points), SUM(minutes), SUM(goals), SUM(assists),
                       SUM(clean_sheets), SUM(bonus), SUM(saves)
                FROM player_gameweek_stats
                WHERE player_id = %s;
            """, (player_id,))
            current = cur.fetchone()

            cur.execute("SELECT first_name, second_name, web_name FROM players WHERE id = %s", (player_id,))
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
        } if current else None
    }
