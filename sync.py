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
# Also derives and updates season start/end dates from GW1 / GW38.
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
                    value_season, value_form, points_per_game, selected_by_percent, form,
                    transfers_in, transfers_out, total_points, goals_scored, assists,
                    clean_sheets, goals_conceded, own_goals, penalties_saved, penalties_missed,
                    yellow_cards, red_cards, saves, bonus, bps,
                    influence, creativity, threat, ict_index, starts,
                    expected_goals, expected_assists, expected_goal_involvements,
                    expected_goals_conceded, clearances_blocks_interceptions,
                    recoveries, tackles, defensive_contribution, in_dreamteam
                ) VALUES %s
                ON CONFLICT (id) DO UPDATE SET
                    season_id      = EXCLUDED.season_id,
                    first_name     = EXCLUDED.first_name,
                    second_name    = EXCLUDED.second_name,
                    web_name       = EXCLUDED.web_name,
                    position       = EXCLUDED.position,
                    team           = EXCLUDED.team,
                    code           = EXCLUDED.code,
                    status         = EXCLUDED.status,
                    news           = EXCLUDED.news,
                    news_added     = EXCLUDED.news_added,
                    chance_of_playing_next_round = EXCLUDED.chance_of_playing_next_round,
                    chance_of_playing_this_round = EXCLUDED.chance_of_playing_this_round,
                    value_season   = EXCLUDED.value_season,
                    value_form     = EXCLUDED.value_form,
                    points_per_game       = EXCLUDED.points_per_game,
                    selected_by_percent   = EXCLUDED.selected_by_percent,
                    form                  = EXCLUDED.form,
                    transfers_in          = EXCLUDED.transfers_in,
                    transfers_out         = EXCLUDED.transfers_out,
                    total_points          = EXCLUDED.total_points,
                    goals_scored          = EXCLUDED.goals_scored,
                    assists               = EXCLUDED.assists,
                    clean_sheets          = EXCLUDED.clean_sheets,
                    goals_conceded        = EXCLUDED.goals_conceded,
                    own_goals             = EXCLUDED.own_goals,
                    penalties_saved       = EXCLUDED.penalties_saved,
                    penalties_missed      = EXCLUDED.penalties_missed,
                    yellow_cards          = EXCLUDED.yellow_cards,
                    red_cards             = EXCLUDED.red_cards,
                    saves                 = EXCLUDED.saves,
                    bonus                 = EXCLUDED.bonus,
                    bps                   = EXCLUDED.bps,
                    influence             = EXCLUDED.influence,
                    creativity            = EXCLUDED.creativity,
                    threat                = EXCLUDED.threat,
                    ict_index             = EXCLUDED.ict_index,
                    starts                = EXCLUDED.starts,
                    expected_goals        = EXCLUDED.expected_goals,
                    expected_assists      = EXCLUDED.expected_assists,
                    expected_goal_involvements  = EXCLUDED.expected_goal_involvements,
                    expected_goals_conceded     = EXCLUDED.expected_goals_conceded,
                    clearances_blocks_interceptions = EXCLUDED.clearances_blocks_interceptions,
                    recoveries            = EXCLUDED.recoveries,
                    tackles               = EXCLUDED.tackles,
                    defensive_contribution = EXCLUDED.defensive_contribution,
                    in_dreamteam          = EXCLUDED.in_dreamteam;
            """, p_records)
            counts["players"] = len(p_records)

            # 4. gameweeks
            events = data.get("events", {}).get("data", [])
            gw_records = []
            gw1_deadline  = None
            gw38_deadline = None

            for gw in events:
                deadline_str = gw.get("deadline_time")
                deadline_epoch = None
                deadline_dt    = None
                if deadline_str:
                    deadline_dt    = datetime.fromisoformat(deadline_str.replace("Z", "+00:00"))
                    deadline_epoch = int(deadline_dt.timestamp())

                waivers_str = gw.get("waivers_time")
                trades_str  = gw.get("trades_time")

                gw_num = gw["id"]  # GW number == event id in FPL Draft
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

    # API uses league_entry_1/2, no id field — PK is (season_id, gw, entry_1, entry_2)
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
# ---------------------------------------------------------------------------

def sync_player_status(season_id: int) -> dict:
    league_id = get_season_league_id(season_id)
    data      = fetch_element_status(league_id)
    statuses  = data.get("element_status", [])

    records = [
        (season_id, s["element"], s.get("entry"), s.get("status"))
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
        ))

    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO player_gameweek_stats (
                    player_id, season_id, gw,
                    goals, assists, bonus, clean_sheets, saves,
                    yellow_cards, red_cards, total_points, minutes, goals_conceded,
                    penalties_saved, penalties_missed, own_goals,
                    expected_goals, expected_assists, expected_goal_involvement,
                    expected_goals_conceded, influence, creativity, threat, ict_index
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
                    expected_goal_involvement  = EXCLUDED.expected_goal_involvement,
                    expected_goals_conceded    = EXCLUDED.expected_goals_conceded,
                    influence          = EXCLUDED.influence,
                    creativity         = EXCLUDED.creativity,
                    threat             = EXCLUDED.threat,
                    ict_index          = EXCLUDED.ict_index;
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

            # Pull all finished matches for the season
            cur.execute("""
                SELECT gw, entry_1, entry_2, entry_1_points, entry_2_points
                FROM fantasy_matches
                WHERE season_id = %s AND finished = TRUE
                ORDER BY gw ASC;
            """, (season_id,))
            matches = cur.fetchall()

        if not matches:
            return {"standings_rows": 0}

        # Build per-GW rows for each team
        gw_rows = []  # (season_id, gw, team_id, opponent_id, pts_for, pts_against, result, league_pts)
        for gw, e1, e2, p1, p2 in matches:
            if p1 > p2:
                r1, r2, lp1, lp2 = 'w', 'l', 3, 0
            elif p2 > p1:
                r1, r2, lp1, lp2 = 'l', 'w', 0, 3
            else:
                r1, r2, lp1, lp2 = 'd', 'd', 1, 1

            gw_rows.append((season_id, gw, e1, e2, p1, p2, r1, lp1))
            gw_rows.append((season_id, gw, e2, e1, p2, p1, r2, lp2))

        # Calculate cumulative points per team up to each GW
        # Accumulate in order since matches are already sorted by gw
        cumulative: dict[int, int] = {}  # team_id -> running total
        records = []
        for season_id_, gw, team_id, opponent_id, pts_for, pts_against, result, league_pts in gw_rows:
            cumulative[team_id] = cumulative.get(team_id, 0) + league_pts
            records.append((
                season_id_, gw, team_id, opponent_id,
                pts_for, pts_against, result, league_pts,
                cumulative[team_id],
            ))

        # Upsert — full rebuild so cumulative totals are always correct
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
