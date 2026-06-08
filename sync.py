# =============================================================================
# sync.py — ALL FIXES
# =============================================================================
# 1. sync_player_status()     — fix "entry" → "owner"  
# 2. sync_ownership_from_draft() — NEW: build per-GW ownership from draft+tx
# 3. sync_pl_team_records()   — NEW: derive W/D/L from fixtures, update premier_league_teams
# 4. sync_element_summaries() — fix season_name to match seasons.name exactly
# =============================================================================


# ─────────────────────────────────────────────────────────────────────────────
# FIX 1: sync_player_status — "entry" → "owner"
#
# The element-status API returns:
#   {"element": 728, "in_accepted_trade": false, "owner": 115464, "status": "o"}
# The field is "owner" (= fantasy_teams.id = entry_id), NOT "entry".
# ─────────────────────────────────────────────────────────────────────────────

def sync_player_status(season_id: int) -> dict:
    league_id = get_season_league_id(season_id)
    data      = fetch_element_status(league_id)
    statuses  = data.get("element_status", [])

    # "owner" = fantasy_teams.id (the large entry_id e.g. 115464)
    # status "o" = owned, "a" = available (free agent), "u" = unavailable
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


# ─────────────────────────────────────────────────────────────────────────────
# FIX 2: sync_ownership_from_draft — NEW
#
# Builds a complete per-GW ownership history by combining:
#   a) Draft picks  → initial ownership from GW1
#   b) Accepted transactions (waivers/trades) → ownership changes by GW
#
# This populates player_fantasy_status correctly for historical tracking.
# For the Players page "current owner" we still call sync_player_status()
# (element-status API) for the live state. This function is for history.
#
# Writes into a new table: player_ownership_history
# CREATE TABLE player_ownership_history (
#     season_id  integer,
#     player_id  integer,
#     team_id    integer,   -- fantasy_teams.id (entry_id)
#     from_gw    integer,
#     to_gw      integer,   -- NULL = still owned
#     PRIMARY KEY (season_id, player_id, from_gw)
# );
# ─────────────────────────────────────────────────────────────────────────────

def sync_ownership_from_draft(season_id: int) -> dict:
    """
    Builds ownership history: who owned each player in each GW.
    Draft → initial owner from GW1.
    Accepted transactions (waivers/trades) → ownership changes mid-season.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            # 1. Get all draft picks for this season
            #    entry_id = fantasy_teams.id (large number like 115464)
            cur.execute("""
                SELECT dp.player_id, dp.entry_id
                FROM draft_picks dp
                WHERE dp.season_id = %s;
            """, (season_id,))
            draft_rows = cur.fetchall()

            # 2. Get accepted transactions ordered by GW
            #    tx.team_id = fantasy_teams.id (entry_id)
            cur.execute("""
                SELECT tx.player_in_id, tx.player_out_id, tx.gw, tx.team_id
                FROM transactions tx
                WHERE tx.season_id = %s AND tx.result = 'a'
                ORDER BY tx.gw, tx.timestamp;
            """, (season_id,))
            tx_rows = cur.fetchall()

            # 3. Get max GW
            cur.execute("""
                SELECT COALESCE(MAX(gw), 38) FROM fixtures WHERE season_id = %s AND finished = true;
            """, (season_id,))
            max_gw = cur.fetchone()[0]

    # Build ownership map: player_id → (owner_team_id, from_gw)
    # Start with draft picks at GW1
    ownership = {}  # player_id → (entry_id, from_gw)
    for player_id, entry_id in draft_rows:
        ownership[player_id] = (entry_id, 1)

    ownership_history = []  # (season_id, player_id, team_id, from_gw, to_gw)

    # Apply transactions in order
    for player_in, player_out, gw, new_owner in tx_rows:
        # Close out the player going out (player_out leaves new_owner's team)
        # player_out is being dropped — find who owns them
        if player_out and player_out in ownership:
            old_owner, old_from = ownership[player_out]
            ownership_history.append((season_id, player_out, old_owner, old_from, gw - 1))
            del ownership[player_out]

        # Open new ownership for player_in
        if player_in:
            if player_in in ownership:
                # Player was owned before (trade scenario — close old)
                old_owner, old_from = ownership[player_in]
                ownership_history.append((season_id, player_in, old_owner, old_from, gw - 1))
            ownership[player_in] = (new_owner, gw)

    # Close out remaining (still-owned players at end of season)
    for player_id, (entry_id, from_gw) in ownership.items():
        ownership_history.append((season_id, player_id, entry_id, from_gw, max_gw))

    with get_conn() as conn:
        with conn.cursor() as cur:
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


# ─────────────────────────────────────────────────────────────────────────────
# FIX 3: sync_pl_team_records — NEW
#
# The FPL bootstrap returns 0 W/D/L for ALL teams during the off-season.
# We derive the full-season W/D/L record from the fixtures table which
# DOES have team_h_score / team_a_score for finished games.
# Updates premier_league_teams in-place.
# ─────────────────────────────────────────────────────────────────────────────

def sync_pl_team_records(season_id: int) -> dict:
    """
    Derives W/D/L, played, points, position from the fixtures table.
    Call after sync_fixtures() has run.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT team_h, team_a, team_h_score, team_a_score
                FROM fixtures
                WHERE season_id = %s AND finished = true
                  AND team_h_score IS NOT NULL AND team_a_score IS NOT NULL;
            """, (season_id,))
            fixture_rows = cur.fetchall()

            cur.execute("""
                SELECT id FROM premier_league_teams WHERE season_id = %s;
            """, (season_id,))
            team_ids = [r[0] for r in cur.fetchall()]

    # Build records
    records = {tid: {"played": 0, "win": 0, "draw": 0, "loss": 0,
                     "gf": 0, "ga": 0, "points": 0}
               for tid in team_ids}

    for team_h, team_a, h_score, a_score in fixture_rows:
        for tid in (team_h, team_a):
            if tid not in records:
                records[tid] = {"played": 0, "win": 0, "draw": 0, "loss": 0,
                                "gf": 0, "ga": 0, "points": 0}

        h, a = records[team_h], records[team_a]
        h["played"] += 1
        a["played"] += 1
        h["gf"] += h_score;  h["ga"] += a_score
        a["gf"] += a_score;  a["ga"] += h_score

        if h_score > a_score:
            h["win"] += 1;  h["points"] += 3
            a["loss"] += 1
        elif h_score < a_score:
            a["win"] += 1;  a["points"] += 3
            h["loss"] += 1
        else:
            h["draw"] += 1; h["points"] += 1
            a["draw"] += 1; a["points"] += 1

    # Compute league position by points desc, goal diff desc
    sorted_teams = sorted(
        records.items(),
        key=lambda x: (x[1]["points"], x[1]["gf"] - x[1]["ga"]),
        reverse=True
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


# ─────────────────────────────────────────────────────────────────────────────
# FIX 4: sync_element_summaries — fix season_name
#
# player_fixture_history.season_name should match seasons.name exactly.
# seasons.name for season_id=1 is likely "2024/25".
# The FPL API element-summary returns history_past with season_name like "2024/25".
# The CURRENT season history (in "history" array) has no season_name field —
# we must derive it from the season record.
# ─────────────────────────────────────────────────────────────────────────────

def sync_element_summaries(season_id: int) -> dict:
    import time

    # Get all player IDs that were drafted this season
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT player_id FROM draft_picks WHERE season_id = %s;
            """, (season_id,))
            player_ids = [row[0] for row in cur.fetchall()]

            # Get the correct season_name string for this season
            cur.execute("SELECT name FROM seasons WHERE id = %s", (season_id,))
            season_row = cur.fetchone()
            current_season_name = season_row[0] if season_row else "2024/25"

    if not player_ids:
        return {"error": "No drafted players found for this season"}

    fixture_records = []
    season_records  = []
    skipped         = 0

    for player_id in player_ids:
        try:
            data = fetch_element_summary(player_id)

            # Per-fixture history — current season only (history array has no season_name)
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
                    _int(fix.get("saves")),
                    _int(fix.get("bonus")),
                    _int(fix.get("bps")),
                    _int(fix.get("total_points")),
                    _int(fix.get("value")),
                    current_season_name,   # ← use DB season name, not API field
                ))

            # Historical season totals (history_past has correct season_name like "2024/25")
            for past in data.get("history_past", []):
                season_records.append((
                    player_id,
                    past.get("season_name"),   # e.g. "2023/24", "2022/23"
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
                        yellow_cards, red_cards, saves, bonus, bps, total_points,
                        value, season_name
                    ) VALUES %s
                    ON CONFLICT (player_id, fixture_id) DO UPDATE SET
                        total_points = EXCLUDED.total_points,
                        bonus        = EXCLUDED.bonus,
                        bps          = EXCLUDED.bps,
                        value        = EXCLUDED.value,
                        season_name  = EXCLUDED.season_name;
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
