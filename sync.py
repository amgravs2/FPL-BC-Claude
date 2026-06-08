"""
sync.py — all data ingestion from the FPL Draft and FPL main APIs.
Each function pulls from an API and upserts into Neon.
"""

import json
import logging
from datetime import datetime, timezone

from psycopg2.extras import execute_values

from db import get_conn, get_season_league_id
from fpl_client import (
    fetch_bootstrap,
    fetch_game_state,
    fetch_league_details,
    fetch_element_status,
    fetch_transactions,
    fetch_gw_live,
    fetch_entry_picks,
    fetch_fixtures,
    fetch_draft_choices,
    fetch_element_summary,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _int(val, default=0):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# bootstrap — element_types, premier_league_teams, players, gameweeks
# ---------------------------------------------------------------------------

def sync_bootstrap(season_id: int) -> dict:
    data        = fetch_bootstrap()
    game_state  = fetch_game_state()
    current_id  = game_state.get("current_event")
    next_id     = game_state.get("next_event")

    counts = {}

    with get_conn() as conn:
        with conn.cursor() as cur:

            # 1. element_types
            et_records = [
                (
                    et["id"],
                    et.get("singular_name"),
                    et.get("singular_name_short"),
                    et.get("plural_name"),
                    et.get("plural_name_short"),
                    et.get("squad_select_limit"),
                    et.get("squad_min_play"),
                    et.get("squad_max_play"),
                    et.get("ui_shirt_specific", False),
                    et.get("sub_positions_locked", False),
                )
                for et in data.get("element_types", [])
            ]
            execute_values(cur, """
                INSERT INTO element_type (
                    id, singular_name, singular_name_short, plural_name, plural_name_short,
                    squad_select_limit, squad_min_play, squad_max_play,
                    ui_shirt_specific, sub_positions_locked
                ) VALUES %s
                ON CONFLICT (id) DO UPDATE SET
                    singular_name        = EXCLUDED.singular_name,
                    singular_name_short  = EXCLUDED.singular_name_short,
                    plural_name          = EXCLUDED.plural_name,
                    plural_name_short    = EXCLUDED.plural_name_short,
                    squad_select_limit   = EXCLUDED.squad_select_limit,
                    squad_min_play       = EXCLUDED.squad_min_play,
                    squad_max_play       = EXCLUDED.squad_max_play,
                    ui_shirt_specific    = EXCLUDED.ui_shirt_specific,
                    sub_positions_locked = EXCLUDED.sub_positions_locked;
            """, et_records)
            counts["element_types"] = len(et_records)

            # 2. premier_league_teams
            pl_records = [
                (
                    t["id"], season_id,
                    t.get("name"), t.get("short_name"),
                    t.get("strength_overall_home"), t.get("strength_overall_away"),
                    t.get("strength_attack_home"),  t.get("strength_attack_away"),
                    t.get("strength_defence_home"), t.get("strength_defence_away"),
                    t.get("played", 0), t.get("win", 0), t.get("loss", 0),
                    t.get("draw", 0),   t.get("points", 0), t.get("position", 0),
                )
                for t in data.get("teams", [])
            ]
            execute_values(cur, """
                INSERT INTO premier_league_teams (
                    id, season_id, name, short_name,
                    strength_overall_home, strength_overall_away,
                    strength_attack_home,  strength_attack_away,
                    strength_defence_home, strength_defence_away,
                    played, win, loss, draw, points, position
                ) VALUES %s
                ON CONFLICT (id, season_id) DO UPDATE SET
                    name                  = EXCLUDED.name,
                    short_name            = EXCLUDED.short_name,
                    strength_overall_home = EXCLUDED.strength_overall_home,
                    strength_overall_away = EXCLUDED.strength_overall_away,
                    strength_attack_home  = EXCLUDED.strength_attack_home,
                    strength_attack_away  = EXCLUDED.strength_attack_away,
                    strength_defence_home = EXCLUDED.strength_defence_home,
                    strength_defence_away = EXCLUDED.strength_defence_away,
                    played   = EXCLUDED.played,
                    win      = EXCLUDED.win,
                    loss     = EXCLUDED.loss,
                    draw     = EXCLUDED.draw,
                    points   = EXCLUDED.points,
                    position = EXCLUDED.position;
            """, pl_records)
            counts["pl_teams"] = len(pl_records)

            # 3. players
            p_records = [
                (
                    p["id"], season_id,
                    p.get("first_name"), p.get("second_name"), p.get("web_name"),
                    p.get("element_type"), p.get("team"), p.get("code"),
                    p.get("status"), p.get("news"), p.get("news_added"),
                    p.get("chance_of_playing_next_round"),
                    p.get("chance_of_playing_this_round"),
                    p.get("value_season"), p.get("value_form"),
                    _float(p.get("points_per_game")),
                    _float(p.get("selected_by_percent")),
                    _float(p.get("form")),
                    p.get("transfers_in"),    p.get("transfers_out"),
                    p.get("total_points"),    p.get("goals_scored"),
                    p.get("assists"),         p.get("clean_sheets"),
                    p.get("goals_conceded"),  p.get("own_goals"),
                    p.get("penalties_saved"), p.get("penalties_missed"),
                    p.get("yellow_cards"),    p.get("red_cards"),
                    p.get("saves"),           p.get("bonus"), p.get("bps"),
                    _float(p.get("influence")),
                    _float(p.get("creativity")),
                    _float(p.get("threat")),
                    _float(p.get("ict_index")),
                    p.get("starts"),
                    _float(p.get("expected_goals")),
                    _float(p.get("expected_assists")),
                    _float(p.get("expected_goal_involvements")),
                    _float(p.get("expected_goals_conceded")),
                    p.get("clearances_blocks_interceptions"),
                    p.get("recoveries"), p.get("tackles"),
                    p.get("defensive_contribution"),
                    p.get("in_dreamteam", False),
                )
                for p in data.get("elements", [])
            ]
            execute_values(cur, """
                INSERT INTO players (
                    id, season_id, first_name, second_name, web_name,
                    position, team, code, status, news, news_added,
                    chance_of_playing_next_round, chance_of_playing_this_round,
                    value_season, value_form, points_per_game, selected_by_percent,
                    form, transfers_in, transfers_out,
                    total_points, goals_scored, assists, clean_sheets,
                    goals_conceded, own_goals, penalties_saved, penalties_missed,
                    yellow_cards, red_cards, saves, bonus, bps,
                    influence, creativity, threat, ict_index, starts,
                    expected_goals, expected_assists, expected_goal_involvements,
                    expected_goals_conceded,
                    clearances_blocks_interceptions, recoveries, tackles,
                    defensive_contribution, in_dreamteam
                ) VALUES %s
                ON CONFLICT (id) DO UPDATE SET
                    season_id                       = EXCLUDED.season_id,
                    first_name                      = EXCLUDED.first_name,
                    second_name                     = EXCLUDED.second_name,
                    web_name                        = EXCLUDED.web_name,
                    position                        = EXCLUDED.position,
                    team                            = EXCLUDED.team,
                    code                            = EXCLUDED.code,
                    status                          = EXCLUDED.status,
                    news                            = EXCLUDED.news,
                    news_added                      = EXCLUDED.news_added,
                    chance_of_playing_next_round    = EXCLUDED.chance_of_playing_next_round,
                    chance_of_playing_this_round    = EXCLUDED.chance_of_playing_this_round,
                    value_season                    = EXCLUDED.value_season,
                    value_form                      = EXCLUDED.value_form,
                    points_per_game                 = EXCLUDED.points_per_game,
                    selected_by_percent             = EXCLUDED.selected_by_percent,
                    form                            = EXCLUDED.form,
                    transfers_in                    = EXCLUDED.transfers_in,
                    transfers_out                   = EXCLUDED.transfers_out,
                    total_points                    = EXCLUDED.total_points,
                    goals_scored                    = EXCLUDED.goals_scored,
                    assists                         = EXCLUDED.assists,
                    clean_sheets                    = EXCLUDED.clean_sheets,
                    goals_conceded                  = EXCLUDED.goals_conceded,
                    own_goals                       = EXCLUDED.own_goals,
                    penalties_saved                 = EXCLUDED.penalties_saved,
                    penalties_missed                = EXCLUDED.penalties_missed,
                    yellow_cards                    = EXCLUDED.yellow_cards,
                    red_cards                       = EXCLUDED.red_cards,
                    saves                           = EXCLUDED.saves,
                    bonus                           = EXCLUDED.bonus,
                    bps                             = EXCLUDED.bps,
                    influence                       = EXCLUDED.influence,
                    creativity                      = EXCLUDED.creativity,
                    threat                          = EXCLUDED.threat,
                    ict_index                       = EXCLUDED.ict_index,
                    starts                          = EXCLUDED.starts,
                    expected_goals                  = EXCLUDED.expected_goals,
                    expected_assists                = EXCLUDED.expected_assists,
                    expected_goal_involvements      = EXCLUDED.expected_goal_involvements,
                    expected_goals_conceded         = EXCLUDED.expected_goals_conceded,
                    clearances_blocks_interceptions = EXCLUDED.clearances_blocks_interceptions,
                    recoveries                      = EXCLUDED.recoveries,
                    tackles                         = EXCLUDED.tackles,
                    defensive_contribution          = EXCLUDED.defensive_contribution,
                    in_dreamteam                    = EXCLUDED.in_dreamteam;
            """, p_records)
            counts["players"] = len(p_records)

            # 4. gameweeks
            events = data.get("events", {}).get("data", [])
            gw_records    = []
            gw1_deadline  = None
            gw38_deadline = None

            for gw in events:
                deadline_str   = gw.get("deadline_time")
                deadline_epoch = None
                deadline_dt    = None
                if deadline_str:
                    deadline_dt    = datetime.fromisoformat(deadline_str.replace("Z", "+00:00"))
                    deadline_epoch = int(deadline_dt.timestamp())

                waivers_str = gw.get("waivers_time")
                trades_str  = gw.get("trades_time")

                gw_num = gw["id"]
                if gw_num == 1:
                    gw1_deadline = deadline_dt
                if gw_num == 38:
                    gw38_deadline = deadline_dt

                gw_records.append((
                    gw["id"], season_id, gw_num,
                    gw.get("name"), deadline_str, deadline_epoch,
                    gw["id"] == current_id,
                    gw["id"] == next_id,
                    gw.get("finished", False),
                    gw.get("average_entry_score"),
                    gw.get("highest_score"),
                    gw.get("highest_scoring_entry"),
                    waivers_str, trades_str,
                ))

            execute_values(cur, """
                INSERT INTO gameweeks (
                    id, season_id, gw_number, name, deadline_time, deadline_time_epoch,
                    is_current, is_next, finished,
                    average_entry_score, highest_score, highest_scoring_entry,
                    waivers_time, trades_time
                ) VALUES %s
                ON CONFLICT (id, season_id) DO UPDATE SET
                    gw_number             = EXCLUDED.gw_number,
                    name                  = EXCLUDED.name,
                    deadline_time         = EXCLUDED.deadline_time,
                    deadline_time_epoch   = EXCLUDED.deadline_time_epoch,
                    is_current            = EXCLUDED.is_current,
                    is_next               = EXCLUDED.is_next,
                    finished              = EXCLUDED.finished,
                    average_entry_score   = EXCLUDED.average_entry_score,
                    highest_score         = EXCLUDED.highest_score,
                    highest_scoring_entry = EXCLUDED.highest_scoring_entry,
                    waivers_time          = EXCLUDED.waivers_time,
                    trades_time           = EXCLUDED.trades_time;
            """, gw_records)
            counts["gameweeks"] = len(gw_records)

            # 5. Derive and update season start/end dates from GW1 / GW38
            if gw1_deadline and gw38_deadline:
                cur.execute("""
                    UPDATE seasons
                    SET start_date = %s, end_date = %s
                    WHERE id = %s AND (start_date IS NULL OR end_date IS NULL);
                """, (gw1_deadline.date(), gw38_deadline.date(), season_id))
                counts["season_dates_updated"] = cur.rowcount

        conn.commit()

    return counts


# ---------------------------------------------------------------------------
# fantasy_teams
# ---------------------------------------------------------------------------

def sync_fantasy_teams(season_id: int) -> dict:
    league_id = get_season_league_id(season_id)
    data      = fetch_league_details(league_id)
    entries   = data.get("league_entries", [])

    records = [
        (
            e["entry_id"], season_id,
            e.get("player_first_name"), e.get("player_last_name"),
            e.get("short_name"), e.get("entry_name"),
            e.get("waiver_pick"), e.get("joined_time"),
            e["id"],  # internal_team_id
        )
        for e in entries
    ]

    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO fantasy_teams (
                    id, season_id, player_first_name, player_last_name,
                    short_name, team_name, waiver_pick, joined_time, internal_team_id
                ) VALUES %s
                ON CONFLICT (id, season_id) DO UPDATE SET
                    player_first_name = EXCLUDED.player_first_name,
                    player_last_name  = EXCLUDED.player_last_name,
                    short_name        = EXCLUDED.short_name,
                    team_name         = EXCLUDED.team_name,
                    waiver_pick       = EXCLUDED.waiver_pick,
                    joined_time       = EXCLUDED.joined_time,
                    internal_team_id  = EXCLUDED.internal_team_id;
            """, records)
        conn.commit()

    return {"fantasy_teams": len(records)}


# ---------------------------------------------------------------------------
# fantasy_matches
# ---------------------------------------------------------------------------

def sync_fantasy_matches(season_id: int) -> dict:
    league_id = get_season_league_id(season_id)
    data      = fetch_league_details(league_id)
    matches   = data.get("matches", [])

    records = [
        (
            season_id,
            m.get("event"),
            m.get("league_entry_1"), m.get("league_entry_2"),
            m.get("league_entry_1_points"), m.get("league_entry_2_points"),
            m.get("finished", False), m.get("started", False),
        )
        for m in matches
        if m.get("event") is not None
    ]

    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO fantasy_matches (
                    season_id, gw, entry_1, entry_2,
                    entry_1_points, entry_2_points, finished, started
                ) VALUES %s
                ON CONFLICT (season_id, gw, entry_1, entry_2) DO UPDATE SET
                    entry_1_points = EXCLUDED.entry_1_points,
                    entry_2_points = EXCLUDED.entry_2_points,
                    finished       = EXCLUDED.finished,
                    started        = EXCLUDED.started;
            """, records)
        conn.commit()

    return {"fantasy_matches": len(records)}


# ---------------------------------------------------------------------------
# player_fantasy_status
#
# FIX: element-status API returns "owner" (not "entry") for the owning team.
# "owner" = fantasy_teams.id (the large entry_id, e.g. 115464).
# ---------------------------------------------------------------------------

def sync_player_status(season_id: int) -> dict:
    league_id = get_season_league_id(season_id)
    data      = fetch_element_status(league_id)
    statuses  = data.get("element_status", [])

    # API shape: {"element": 728, "in_accepted_trade": false, "owner": 115464, "status": "o"}
    # "owner" maps to fantasy_teams.id (entry_id). Status "o"=owned, "a"=available.
    records = [
        (season_id, s["element"], s.get("owner"), s.get("status"))
        for s in statuses
    ]

    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO player_fantasy_status (season_id, player_id, team_id, status)
                VALUES %s
                ON CONFLICT (season_id, player_id) DO UPDATE SET
                    team_id    = EXCLUDED.team_id,
                    status     = EXCLUDED.status,
                    updated_at = NOW();
            """, records)
        conn.commit()

    return {"player_statuses": len(records)}


# ---------------------------------------------------------------------------
# transactions
# ---------------------------------------------------------------------------

def sync_transactions(season_id: int) -> dict:
    league_id    = get_season_league_id(season_id)
    data         = fetch_transactions(league_id)
    transactions = data.get("transactions", [])

    records = [
        (
            tx["id"], tx.get("entry"), season_id,
            tx.get("element_in"), tx.get("element_out"),
            tx.get("event"), tx.get("kind"),
            tx.get("priority"), tx.get("result"),
            tx.get("index"), tx.get("added"),
        )
        for tx in transactions
    ]

    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO transactions (
                    id, team_id, season_id, player_in_id, player_out_id,
                    gw, kind, priority, result, index, timestamp
                ) VALUES %s
                ON CONFLICT (id) DO NOTHING;
            """, records)
        conn.commit()

    return {"transactions": len(records)}


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def sync_fixtures(season_id: int) -> dict:
    fixtures = fetch_fixtures()

    records = [
        (
            f["id"], season_id,
            f.get("event"), f.get("kickoff_time"),
            f.get("team_h"), f.get("team_a"),
            f.get("team_h_score"), f.get("team_a_score"),
            f.get("finished", False), f.get("started", False),
            f.get("minutes", 0),
            json.dumps(f.get("stats", [])),
        )
        for f in fixtures
    ]

    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO fixtures (
                    id, season_id, gw, kickoff_time,
                    team_h, team_a, team_h_score, team_a_score,
                    finished, started, minutes, stats
                ) VALUES %s
                ON CONFLICT (id, season_id) DO UPDATE SET
                    gw           = EXCLUDED.gw,
                    kickoff_time = EXCLUDED.kickoff_time,
                    team_h       = EXCLUDED.team_h,
                    team_a       = EXCLUDED.team_a,
                    team_h_score = EXCLUDED.team_h_score,
                    team_a_score = EXCLUDED.team_a_score,
                    finished     = EXCLUDED.finished,
                    started      = EXCLUDED.started,
                    minutes      = EXCLUDED.minutes,
                    stats        = EXCLUDED.stats::jsonb;
            """, records)
        conn.commit()

    return {"fixtures": len(records)}


# ---------------------------------------------------------------------------
# player_gameweek_stats
# ---------------------------------------------------------------------------

def sync_gw_stats(season_id: int, gw: int) -> dict:
    data     = fetch_gw_live(gw)
    elements = data.get("elements", {})

    records = []
    for player_id_str, player_data in elements.items():
        s = player_data.get("stats", {})
        records.append((
            int(player_id_str), season_id, gw,
            _int(s.get("goals_scored")),
            _int(s.get("assists")),
            _int(s.get("bonus")),
            _int(s.get("clean_sheets")),
            _int(s.get("saves")),
            _int(s.get("yellow_cards")),
            _int(s.get("red_cards")),
            _int(s.get("total_points")),
            _int(s.get("minutes")),
            _int(s.get("goals_conceded")),
            _int(s.get("penalties_saved")),
            _int(s.get("penalties_missed")),
            _int(s.get("own_goals")),
            _float(s.get("expected_goals")),
            _float(s.get("expected_assists")),
            _float(s.get("expected_goal_involvement")),
            _float(s.get("expected_goals_conceded")),
            _float(s.get("influence")),
            _float(s.get("creativity")),
            _float(s.get("threat")),
            _float(s.get("ict_index")),
            _int(s.get("bps")),
            _int(s.get("starts")),
            _int(s.get("clearances_blocks_interceptions")),
            _int(s.get("recoveries")),
            _int(s.get("tackles")),
            _int(s.get("defensive_contribution")),
        ))

    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO player_gameweek_stats (
                    player_id, season_id, gw,
                    goals, assists, bonus, clean_sheets, saves,
                    yellow_cards, red_cards, total_points, minutes,
                    goals_conceded, penalties_saved, penalties_missed, own_goals,
                    expected_goals, expected_assists, expected_goal_involvement,
                    expected_goals_conceded,
                    influence, creativity, threat, ict_index,
                    bps, starts,
                    clearances_blocks_interceptions, recoveries, tackles,
                    defensive_contribution
                ) VALUES %s
                ON CONFLICT (player_id, season_id, gw) DO UPDATE SET
                    goals              = EXCLUDED.goals,
                    assists            = EXCLUDED.assists,
                    bonus              = EXCLUDED.bonus,
                    clean_sheets       = EXCLUDED.clean_sheets,
                    saves              = EXCLUDED.saves,
                    yellow_cards       = EXCLUDED.yellow_cards,
                    red_cards          = EXCLUDED.red_cards,
                    total_points       = EXCLUDED.total_points,
                    minutes            = EXCLUDED.minutes,
                    goals_conceded     = EXCLUDED.goals_conceded,
                    penalties_saved    = EXCLUDED.penalties_saved,
                    penalties_missed   = EXCLUDED.penalties_missed,
                    own_goals          = EXCLUDED.own_goals,
                    expected_goals     = EXCLUDED.expected_goals,
                    expected_assists   = EXCLUDED.expected_assists,
                    expected_goal_involvement   = EXCLUDED.expected_goal_involvement,
                    expected_goals_conceded     = EXCLUDED.expected_goals_conceded,
                    influence          = EXCLUDED.influence,
                    creativity         = EXCLUDED.creativity,
                    threat             = EXCLUDED.threat,
                    ict_index          = EXCLUDED.ict_index,
                    bps                = EXCLUDED.bps,
                    starts             = EXCLUDED.starts,
                    clearances_blocks_interceptions = EXCLUDED.clearances_blocks_interceptions,
                    recoveries         = EXCLUDED.recoveries,
                    tackles            = EXCLUDED.tackles,
                    defensive_contribution = EXCLUDED.defensive_contribution;
            """, records)
        conn.commit()

    return {"gw_stats": len(records)}


# ---------------------------------------------------------------------------
# gameweek_lineups
# ---------------------------------------------------------------------------

def sync_lineups(season_id: int, gw: int) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM fantasy_teams WHERE season_id = %s",
                (season_id,)
            )
            team_ids = [row[0] for row in cur.fetchall()]

    records = []
    skipped = 0
    for team_id in team_ids:
        try:
            data  = fetch_entry_picks(team_id, gw)
            picks = data.get("picks", [])
            for pick in picks:
                records.append((
                    season_id, team_id, gw,
                    pick["element"],
                    pick.get("position"),
                    pick.get("multiplier"),
                    pick.get("is_captain", False),
                    pick.get("is_vice_captain", False),
                ))
        except Exception as e:
            logger.warning(f"Could not fetch picks for team {team_id} GW {gw}: {e}")
            skipped += 1

    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO gameweek_lineups (
                    season_id, team_id, gw, player_id,
                    position, multiplier, is_captain, is_vice_captain
                ) VALUES %s
                ON CONFLICT (season_id, team_id, gw, player_id) DO UPDATE SET
                    position        = EXCLUDED.position,
                    multiplier      = EXCLUDED.multiplier,
                    is_captain      = EXCLUDED.is_captain,
                    is_vice_captain = EXCLUDED.is_vice_captain;
            """, records)
        conn.commit()

    return {"lineup_picks": len(records), "teams_skipped": skipped}


# ---------------------------------------------------------------------------
# standings
# Rebuilt from fantasy_matches on each sync.
# One row per team per GW — stores opponent, result, and cumulative points.
# ---------------------------------------------------------------------------

def sync_standings(season_id: int) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT gw, entry_1, entry_2, entry_1_points, entry_2_points
                FROM fantasy_matches
                WHERE season_id = %s AND finished = TRUE
                ORDER BY gw ASC;
            """, (season_id,))
            matches = cur.fetchall()

        if not matches:
            return {"standings_rows": 0}

        gw_rows = []
        for gw, e1, e2, p1, p2 in matches:
            if p1 > p2:
                r1, r2, lp1, lp2 = 'w', 'l', 3, 0
            elif p2 > p1:
                r1, r2, lp1, lp2 = 'l', 'w', 0, 3
            else:
                r1, r2, lp1, lp2 = 'd', 'd', 1, 1

            gw_rows.append((season_id, gw, e1, e2, p1, p2, r1, lp1))
            gw_rows.append((season_id, gw, e2, e1, p2, p1, r2, lp2))

        cumulative: dict = {}
        records = []
        for season_id_, gw, team_id, opponent_id, pts_for, pts_against, result, league_pts in gw_rows:
            cumulative[team_id] = cumulative.get(team_id, 0) + league_pts
            records.append((
                season_id_, gw, team_id, opponent_id,
                pts_for, pts_against, result, league_pts,
                cumulative[team_id],
            ))

        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO standings (
                    season_id, gw, team_id, opponent_id,
                    points_for, points_against, result, league_points, cumulative_points
                ) VALUES %s
                ON CONFLICT (season_id, gw, team_id) DO UPDATE SET
                    opponent_id       = EXCLUDED.opponent_id,
                    points_for        = EXCLUDED.points_for,
                    points_against    = EXCLUDED.points_against,
                    result            = EXCLUDED.result,
                    league_points     = EXCLUDED.league_points,
                    cumulative_points = EXCLUDED.cumulative_points;
            """, records)
            conn.commit()

    return {"standings_rows": len(records)}


# ---------------------------------------------------------------------------
# draft_picks
# ---------------------------------------------------------------------------

def sync_draft_picks(season_id: int) -> dict:
    league_id = get_season_league_id(season_id)
    data      = fetch_draft_choices(league_id)
    choices   = data.get("choices", [])

    records = [
        (
            c["id"], season_id,
            c.get("draft"),
            c.get("entry"),
            c.get("element"),
            c.get("round"),
            c.get("pick"),
            c.get("index"),
            c.get("was_auto", False),
            c.get("choice_time"),
        )
        for c in choices
    ]

    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO draft_picks (
                    id, season_id, draft_id, entry_id, player_id,
                    round, pick, overall_pick, was_auto, choice_time
                ) VALUES %s
                ON CONFLICT (id) DO NOTHING;
            """, records)
        conn.commit()

    return {"draft_picks": len(records)}


# ---------------------------------------------------------------------------
# element_summary — per-fixture history + historical season totals
#
# FIX: Uses season name from DB (seasons.name) for current-season fixture rows,
# so it matches what queries.py expects when filtering by season_name.
# ---------------------------------------------------------------------------

def sync_element_summaries(season_id: int) -> dict:
    """Syncs per-fixture history for drafted players only (~90 players, ~10s)."""
    import time

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT player_id FROM draft_picks WHERE season_id = %s;
            """, (season_id,))
            player_ids = [row[0] for row in cur.fetchall()]

            cur.execute("SELECT name FROM seasons WHERE id = %s", (season_id,))
            season_row = cur.fetchone()
            current_season_name = season_row[0] if season_row else "2024/25"

    if not player_ids:
        return {"error": "No drafted players found for this season"}

    return _sync_element_summaries_for_players(player_ids, current_season_name)

    fixture_records = []
    season_records  = []
    skipped         = 0

    for player_id in player_ids:
        try:
            data = fetch_element_summary(player_id)

            # Current-season per-fixture history
            for fix in data.get("history", []):
                fixture_records.append((
                    player_id,
                    fix.get("fixture"),
                    fix.get("round", 0),
                    fix.get("opponent_team"),
                    fix.get("was_home", False),
                    fix.get("kickoff_time"),
                    _int(fix.get("minutes")),
                    _int(fix.get("goals_scored")),
                    _int(fix.get("assists")),
                    _int(fix.get("clean_sheets")),
                    _int(fix.get("goals_conceded")),
                    _int(fix.get("own_goals")),
                    _int(fix.get("penalties_saved")),
                    _int(fix.get("penalties_missed")),
                    _int(fix.get("yellow_cards")),
                    _int(fix.get("red_cards")),
                    _int(fix.get("bonus")),
                    _int(fix.get("bps")),
                    _int(fix.get("total_points")),
                    _int(fix.get("value")),
                    current_season_name,  # use DB name, not a hardcoded string
                    _int(fix.get("saves")),
                    _int(fix.get("defensive_contribution")),
                    _int(fix.get("clearances_blocks_interceptions")),
                    _int(fix.get("recoveries")),
                    _int(fix.get("tackles")),
                ))

            # Historical season totals (history_past has correct season_name e.g. "2023/24")
            for past in data.get("history_past", []):
                season_records.append((
                    player_id,
                    past.get("season_name"),
                    _int(past.get("start_cost")),
                    _int(past.get("end_cost")),
                    _int(past.get("total_points")),
                    _int(past.get("minutes")),
                    _int(past.get("goals_scored")),
                    _int(past.get("assists")),
                    _int(past.get("clean_sheets")),
                    _int(past.get("goals_conceded")),
                    _int(past.get("own_goals")),
                    _int(past.get("penalties_saved")),
                    _int(past.get("penalties_missed")),
                    _int(past.get("yellow_cards")),
                    _int(past.get("red_cards")),
                    _int(past.get("saves")),
                    _int(past.get("bonus")),
                    _int(past.get("bps")),
                    _float(past.get("influence")),
                    _float(past.get("creativity")),
                    _float(past.get("threat")),
                    _float(past.get("ict_index")),
                ))

            time.sleep(0.1)

        except Exception as e:
            logger.warning(f"Skipping player {player_id}: {e}")
            skipped += 1

    with get_conn() as conn:
        with conn.cursor() as cur:
            if fixture_records:
                execute_values(cur, """
                    INSERT INTO player_fixture_history (
                        player_id, fixture_id, gw, opponent_team, was_home,
                        kickoff_time, minutes, goals_scored, assists, clean_sheets,
                        goals_conceded, own_goals, penalties_saved, penalties_missed,
                        yellow_cards, red_cards, bonus, bps, total_points,
                        value, season_name,
                        saves, defensive_contribution,
                        clearances_blocks_interceptions, recoveries, tackles
                    ) VALUES %s
                    ON CONFLICT (player_id, fixture_id) DO UPDATE SET
                        total_points                    = EXCLUDED.total_points,
                        bonus                           = EXCLUDED.bonus,
                        bps                             = EXCLUDED.bps,
                        value                           = EXCLUDED.value,
                        season_name                     = EXCLUDED.season_name,
                        saves                           = EXCLUDED.saves,
                        defensive_contribution          = EXCLUDED.defensive_contribution,
                        clearances_blocks_interceptions = EXCLUDED.clearances_blocks_interceptions,
                        recoveries                      = EXCLUDED.recoveries,
                        tackles                         = EXCLUDED.tackles;
                """, fixture_records)

            if season_records:
                execute_values(cur, """
                    INSERT INTO player_season_history (
                        player_id, season_name, start_cost, end_cost, total_points,
                        minutes, goals_scored, assists, clean_sheets, goals_conceded,
                        own_goals, penalties_saved, penalties_missed, yellow_cards,
                        red_cards, saves, bonus, bps, influence, creativity, threat,
                        ict_index
                    ) VALUES %s
                    ON CONFLICT (player_id, season_name) DO UPDATE SET
                        total_points = EXCLUDED.total_points,
                        minutes      = EXCLUDED.minutes,
                        goals_scored = EXCLUDED.goals_scored,
                        assists      = EXCLUDED.assists;
                """, season_records)

        conn.commit()

    return {
        "players_synced":  len(player_ids) - skipped,
        "fixture_records": len(fixture_records),
        "season_records":  len(season_records),
        "skipped":         skipped,
    }
def _sync_element_summaries_for_players(player_ids: list, current_season_name: str) -> dict:
    import time

    fixture_records = []
    season_records  = []
    skipped         = 0

    for player_id in player_ids:
        try:
            data = fetch_element_summary(player_id)

            for fix in data.get("history", []):
                fixture_records.append((
                    player_id,
                    fix.get("fixture"),
                    fix.get("round", 0),
                    fix.get("opponent_team"),
                    fix.get("was_home", False),
                    fix.get("kickoff_time"),
                    _int(fix.get("minutes")),
                    _int(fix.get("goals_scored")),
                    _int(fix.get("assists")),
                    _int(fix.get("clean_sheets")),
                    _int(fix.get("goals_conceded")),
                    _int(fix.get("own_goals")),
                    _int(fix.get("penalties_saved")),
                    _int(fix.get("penalties_missed")),
                    _int(fix.get("yellow_cards")),
                    _int(fix.get("red_cards")),
                    _int(fix.get("bonus")),
                    _int(fix.get("bps")),
                    _int(fix.get("total_points")),
                    _int(fix.get("value")),
                    current_season_name,
                    _int(fix.get("saves")),
                    _int(fix.get("defensive_contribution")),
                    _int(fix.get("clearances_blocks_interceptions")),
                    _int(fix.get("recoveries")),
                    _int(fix.get("tackles")),
                ))

            for past in data.get("history_past", []):
                season_records.append((
                    player_id,
                    past.get("season_name"),
                    _int(past.get("start_cost")),
                    _int(past.get("end_cost")),
                    _int(past.get("total_points")),
                    _int(past.get("minutes")),
                    _int(past.get("goals_scored")),
                    _int(past.get("assists")),
                    _int(past.get("clean_sheets")),
                    _int(past.get("goals_conceded")),
                    _int(past.get("own_goals")),
                    _int(past.get("penalties_saved")),
                    _int(past.get("penalties_missed")),
                    _int(past.get("yellow_cards")),
                    _int(past.get("red_cards")),
                    _int(past.get("saves")),
                    _int(past.get("bonus")),
                    _int(past.get("bps")),
                    _float(past.get("influence")),
                    _float(past.get("creativity")),
                    _float(past.get("threat")),
                    _float(past.get("ict_index")),
                ))

            time.sleep(0.1)

        except Exception as e:
            logger.warning(f"Skipping player {player_id}: {e}")
            skipped += 1

    with get_conn() as conn:
        with conn.cursor() as cur:
            if fixture_records:
                execute_values(cur, """
                    INSERT INTO player_fixture_history (
                        player_id, fixture_id, gw, opponent_team, was_home,
                        kickoff_time, minutes, goals_scored, assists, clean_sheets,
                        goals_conceded, own_goals, penalties_saved, penalties_missed,
                        yellow_cards, red_cards, bonus, bps, total_points,
                        value, season_name,
                        saves, defensive_contribution,
                        clearances_blocks_interceptions, recoveries, tackles
                    ) VALUES %s
                    ON CONFLICT (player_id, fixture_id) DO UPDATE SET
                        total_points                    = EXCLUDED.total_points,
                        bonus                           = EXCLUDED.bonus,
                        bps                             = EXCLUDED.bps,
                        value                           = EXCLUDED.value,
                        season_name                     = EXCLUDED.season_name,
                        saves                           = EXCLUDED.saves,
                        defensive_contribution          = EXCLUDED.defensive_contribution,
                        clearances_blocks_interceptions = EXCLUDED.clearances_blocks_interceptions,
                        recoveries                      = EXCLUDED.recoveries,
                        tackles                         = EXCLUDED.tackles;
                """, fixture_records)

            if season_records:
                execute_values(cur, """
                    INSERT INTO player_season_history (
                        player_id, season_name, start_cost, end_cost, total_points,
                        minutes, goals_scored, assists, clean_sheets, goals_conceded,
                        own_goals, penalties_saved, penalties_missed, yellow_cards,
                        red_cards, saves, bonus, bps, influence, creativity, threat,
                        ict_index
                    ) VALUES %s
                    ON CONFLICT (player_id, season_name) DO UPDATE SET
                        total_points = EXCLUDED.total_points,
                        minutes      = EXCLUDED.minutes,
                        goals_scored = EXCLUDED.goals_scored,
                        assists      = EXCLUDED.assists;
                """, season_records)

        conn.commit()

    return {
        "players_synced":  len(player_ids) - skipped,
        "fixture_records": len(fixture_records),
        "season_records":  len(season_records),
        "skipped":         skipped,
    }

# ---------------------------------------------------------------------------
# NEW: sync_pl_team_records
# Derives W/D/L/points/position from the fixtures table.
# The FPL bootstrap returns all zeros during the off-season so we compute
# these ourselves from finished fixture scores.
# Call after sync_fixtures().
# ---------------------------------------------------------------------------

def sync_pl_team_records(season_id: int) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT team_h, team_a, team_h_score, team_a_score
                FROM fixtures
                WHERE season_id = %s AND finished = true
                  AND team_h_score IS NOT NULL AND team_a_score IS NOT NULL;
            """, (season_id,))
            fixture_rows = cur.fetchall()

            cur.execute(
                "SELECT id FROM premier_league_teams WHERE season_id = %s;",
                (season_id,)
            )
            team_ids = [r[0] for r in cur.fetchall()]

    records = {tid: {"played": 0, "win": 0, "draw": 0, "loss": 0,
                     "gf": 0, "ga": 0, "points": 0}
               for tid in team_ids}

    for team_h, team_a, h_score, a_score in fixture_rows:
        for tid in (team_h, team_a):
            if tid not in records:
                records[tid] = {"played": 0, "win": 0, "draw": 0, "loss": 0,
                                "gf": 0, "ga": 0, "points": 0}

        h, a = records[team_h], records[team_a]
        h["played"] += 1;  a["played"] += 1
        h["gf"] += h_score;  h["ga"] += a_score
        a["gf"] += a_score;  a["ga"] += h_score

        if h_score > a_score:
            h["win"] += 1;  h["points"] += 3;  a["loss"] += 1
        elif h_score < a_score:
            a["win"] += 1;  a["points"] += 3;  h["loss"] += 1
        else:
            h["draw"] += 1;  h["points"] += 1
            a["draw"] += 1;  a["points"] += 1

    sorted_teams = sorted(
        records.items(),
        key=lambda x: (x[1]["points"], x[1]["gf"] - x[1]["ga"]),
        reverse=True,
    )

    with get_conn() as conn:
        with conn.cursor() as cur:
            for pos, (tid, r) in enumerate(sorted_teams, 1):
                cur.execute("""
                    UPDATE premier_league_teams
                    SET played   = %s,
                        win      = %s,
                        loss     = %s,
                        draw     = %s,
                        points   = %s,
                        position = %s
                    WHERE id = %s AND season_id = %s;
                """, (r["played"], r["win"], r["loss"], r["draw"],
                      r["points"], pos, tid, season_id))
        conn.commit()

    return {"teams_updated": len(records)}


# ---------------------------------------------------------------------------
# NEW: sync_ownership_from_draft
# Builds a complete per-GW ownership timeline from draft picks + transactions.
# Creates/populates the player_ownership_history table.
# Call after sync_draft_picks() and sync_transactions().
# ---------------------------------------------------------------------------

def sync_ownership_from_draft(season_id: int) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT dp.player_id, dp.entry_id
                FROM draft_picks dp
                WHERE dp.season_id = %s;
            """, (season_id,))
            draft_rows = cur.fetchall()

            cur.execute("""
                SELECT tx.player_in_id, tx.player_out_id, tx.gw, tx.team_id
                FROM transactions tx
                WHERE tx.season_id = %s AND tx.result = 'a'
                ORDER BY tx.gw, tx.timestamp;
            """, (season_id,))
            tx_rows = cur.fetchall()

            cur.execute("""
                SELECT COALESCE(MAX(gw), 38) FROM fixtures
                WHERE season_id = %s AND finished = true;
            """, (season_id,))
            max_gw = cur.fetchone()[0]

            # Ensure table exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS player_ownership_history (
                    season_id  integer NOT NULL,
                    player_id  integer NOT NULL,
                    team_id    integer,
                    from_gw    integer NOT NULL,
                    to_gw      integer,
                    PRIMARY KEY (season_id, player_id, from_gw),
                    FOREIGN KEY (season_id) REFERENCES seasons(id),
                    FOREIGN KEY (player_id) REFERENCES players(id)
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_ownership_player
                    ON player_ownership_history (player_id, season_id);
            """)
        conn.commit()

    # Build ownership timeline: draft picks set initial owner at GW1
    ownership = {}  # player_id → (entry_id, from_gw)
    for player_id, entry_id in draft_rows:
        ownership[player_id] = (entry_id, 1)

    ownership_history = []  # (season_id, player_id, team_id, from_gw, to_gw)

    for player_in, player_out, gw, new_owner in tx_rows:
        # Close out player leaving a team
        if player_out and player_out in ownership:
            old_owner, old_from = ownership[player_out]
            ownership_history.append((season_id, player_out, old_owner, old_from, gw - 1))
            del ownership[player_out]

        # Open new ownership for incoming player
        if player_in:
            if player_in in ownership:
                old_owner, old_from = ownership[player_in]
                ownership_history.append((season_id, player_in, old_owner, old_from, gw - 1))
            ownership[player_in] = (new_owner, gw)

    # Close remaining open ownerships at end of season
    for player_id, (entry_id, from_gw) in ownership.items():
        ownership_history.append((season_id, player_id, entry_id, from_gw, max_gw))

    with get_conn() as conn:
        with conn.cursor() as cur:
            if ownership_history:
                execute_values(cur, """
                    INSERT INTO player_ownership_history
                        (season_id, player_id, team_id, from_gw, to_gw)
                    VALUES %s
                    ON CONFLICT (season_id, player_id, from_gw) DO UPDATE SET
                        team_id = EXCLUDED.team_id,
                        to_gw   = EXCLUDED.to_gw;
                """, ownership_history)
        conn.commit()

    return {"ownership_records": len(ownership_history)}
