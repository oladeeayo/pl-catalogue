"""FPL spine fetcher: clubs, players, GWs, fixtures, per-match history, snapshots.

Idempotent (upserts) - safe to re-run any number of times, e.g. weekly.
HTTP only, no Cloudflare: runs anywhere (local or CI).
"""
import json
import sqlite3
import time
import urllib.request
from datetime import date
from pathlib import Path

DB = Path(__file__).parent / "catalogue.db"
SCHEMA = Path(__file__).parent / "schema.sql"
BASE = "https://fantasy.premierleague.com/api"
UA = {"User-Agent": "Mozilla/5.0 (personal catalogue; contact: local)"}


def get(path, retries=3):
    url = path if path.startswith("http") else f"{BASE}/{path}"
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001 - retry any transport error
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"GET {url} failed after {retries}: {last}")


def init_db():
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    return con


def _upsert(con, table, rows, conflict_col):
    if not rows:
        return
    cols = list(rows[0].keys())
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) "
        f"VALUES ({', '.join(':' + c for c in cols)}) "
        f"ON CONFLICT({conflict_col}) DO UPDATE SET "
        + ", ".join(f"{c}=excluded.{c}" for c in cols if c != conflict_col)
    )
    con.executemany(sql, rows)


def sync(con, verbose=True):
    def log(msg):
        if verbose:
            print(msg, flush=True)

    # --- clubs ---
    boot = get("bootstrap-static/")
    clubs = []
    for t in boot["teams"]:
        clubs.append({
            "club_id": t["id"], "fpl_code": t.get("code"), "reep_id": None,
            "name": t.get("name"), "short_name": t.get("short_name"),
            "position": t.get("position"), "played": t.get("played"),
            "win": t.get("win"), "draw": t.get("draw"), "loss": t.get("loss"),
            "points": t.get("points"), "form": t.get("form"),
            "strength_attack_home": t.get("strength_attack_home"),
            "strength_attack_away": t.get("strength_attack_away"),
            "strength_defence_home": t.get("strength_defence_home"),
            "strength_defence_away": t.get("strength_defence_away"),
            "pulse_id": t.get("pulse_id"),
        })
    # preserve reep_id already matched
    for c in clubs:
        row = con.execute("SELECT reep_id FROM clubs WHERE club_id=?", (c["club_id"],)).fetchone()
        c["reep_id"] = row[0] if row else None
    _upsert(con, "clubs", clubs, "club_id")
    log(f"clubs: {len(clubs)}")

    # --- GWs ---
    gws = [{
        "gw": e["id"], "name": e.get("name"), "deadline_time": e.get("deadline_time"),
        "finished": int(bool(e.get("finished"))), "is_current": int(bool(e.get("is_current"))),
        "is_next": int(bool(e.get("is_next"))),
        "average_entry_score": e.get("average_entry_score"),
        "highest_score": e.get("highest_score"), "transfers_made": e.get("transfers_made"),
        "most_selected": e.get("most_selected"), "most_transferred_in": e.get("most_transferred_in"),
        "most_captained": e.get("most_captained"), "most_vice_captained": e.get("most_vice_captained"),
    } for e in boot["events"]]
    _upsert(con, "gws", gws, "gw")
    log(f"gws: {len(gws)} (finished: {sum(g['finished'] for g in gws)})")

    # --- players (core cols only; identity step fills the rest) ---
    for p in boot["elements"]:
        bd = str(p.get("birth_date") or "")
        if bd.isdigit() and len(bd) == 8:  # defensive: YYYYMMDD
            bd = f"{bd[:4]}-{bd[4:6]}-{bd[6:]}"
        con.execute(
            """INSERT INTO players (player_id, fpl_code, web_name, first_name, second_name,
                   known_name, club_id, element_type, squad_number, status, news,
                   date_of_birth, region, opta_code, removed)
               VALUES (:id,:code,:web,:fn,:sn,:kn,:team,:et,:sq,:st,:news,:bd,:rg,:oc,:rm)
               ON CONFLICT(player_id) DO UPDATE SET
                 fpl_code=excluded.fpl_code, web_name=excluded.web_name,
                 first_name=excluded.first_name, second_name=excluded.second_name,
                 known_name=excluded.known_name, club_id=excluded.club_id,
                 element_type=excluded.element_type, squad_number=excluded.squad_number,
                 status=excluded.status, news=excluded.news,
                 date_of_birth=COALESCE(players.date_of_birth, excluded.date_of_birth),
                 region=excluded.region, opta_code=excluded.opta_code,
                 removed=excluded.removed""",
            {"id": p["id"], "code": p.get("code"), "web": p.get("web_name"),
             "fn": p.get("first_name"), "sn": p.get("second_name"),
             "kn": p.get("known_name"), "team": p.get("team"),
             "et": p.get("element_type"), "sq": p.get("squad_number"),
             "st": p.get("status"), "news": p.get("news"), "bd": bd or None,
             "rg": p.get("region"), "oc": p.get("opta_code"),
             "rm": int(bool(p.get("removed")))},
        )
    log(f"players: {len(boot['elements'])}")

    # --- fixtures ---
    fxs = [{
        "fixture_id": f["id"], "gw": f.get("event"), "home_club": f.get("team_h"),
        "away_club": f.get("team_a"), "kickoff_time": f.get("kickoff_time"),
        "finished": int(bool(f.get("finished"))), "started": int(bool(f.get("started"))),
        "home_score": f.get("team_h_score"), "away_score": f.get("team_a_score"),
        "home_difficulty": f.get("team_h_difficulty"), "away_difficulty": f.get("team_a_difficulty"),
        "pulse_id": f.get("pulse_id"),
    } for f in get("fixtures/")]
    _upsert(con, "fixtures", fxs, "fixture_id")
    log(f"fixtures: {len(fxs)} (finished: {sum(f['finished'] for f in fxs)})")

    # --- per player: history, prior seasons, snapshot ---
    ids = [p["id"] for p in boot["elements"]]
    fetch_date = date.today().isoformat()
    cur_gw = next((g["gw"] for g in gws if g["is_next"]), None)
    failed = []
    n_hist = n_past = 0
    by_id = {p["id"]: p for p in boot["elements"]}
    for i, pid in enumerate(ids, 1):
        try:
            d = get(f"element-summary/{pid}/")
        except RuntimeError:
            failed.append(pid)
            continue
        pf_rows, past_rows = [], []
        for h in d.get("history", []):
            pf_rows.append({
                "player_id": pid, "fixture_id": h.get("fixture"), "gw": h.get("round"),
                "opponent_team": h.get("opponent_team"), "was_home": int(bool(h.get("was_home"))),
                "minutes": h.get("minutes"), "starts": h.get("starts"),
                "goals_scored": h.get("goals_scored"), "assists": h.get("assists"),
                "clean_sheets": h.get("clean_sheets"), "goals_conceded": h.get("goals_conceded"),
                "own_goals": h.get("own_goals"), "penalties_saved": h.get("penalties_saved"),
                "penalties_missed": h.get("penalties_missed"), "yellow_cards": h.get("yellow_cards"),
                "red_cards": h.get("red_cards"), "saves": h.get("saves"),
                "bonus": h.get("bonus"), "bps": h.get("bps"),
                "influence": h.get("influence"), "creativity": h.get("creativity"),
                "threat": h.get("threat"), "ict_index": h.get("ict_index"),
                "clearances_blocks_interceptions": h.get("clearances_blocks_interceptions"),
                "recoveries": h.get("recoveries"), "tackles": h.get("tackles"),
                "defensive_contribution": h.get("defensive_contribution"),
                "expected_goals": h.get("expected_goals"),
                "expected_assists": h.get("expected_assists"),
                "expected_goal_involvements": h.get("expected_goal_involvements"),
                "expected_goals_conceded": h.get("expected_goals_conceded"),
                "total_points": h.get("total_points"), "value": h.get("value"),
                "selected": h.get("selected"), "transfers_in": h.get("transfers_in"),
                "transfers_out": h.get("transfers_out"),
                "transfers_balance": h.get("transfers_balance"),
            })
        for h in d.get("history_past", []):
            past_rows.append({
                "player_id": pid, "season_name": h.get("season_name"),
                "total_points": h.get("total_points"), "minutes": h.get("minutes"),
                "goals_scored": h.get("goals_scored"), "assists": h.get("assists"),
                "clean_sheets": h.get("clean_sheets"), "goals_conceded": h.get("goals_conceded"),
                "own_goals": h.get("own_goals"), "penalties_saved": h.get("penalties_saved"),
                "penalties_missed": h.get("penalties_missed"), "yellow_cards": h.get("yellow_cards"),
                "red_cards": h.get("red_cards"), "saves": h.get("saves"),
                "bonus": h.get("bonus"), "bps": h.get("bps"),
                "influence": h.get("influence"), "creativity": h.get("creativity"),
                "threat": h.get("threat"), "ict_index": h.get("ict_index"),
                "expected_goals": h.get("expected_goals"),
                "expected_assists": h.get("expected_assists"),
                "expected_goal_involvements": h.get("expected_goal_involvements"),
                "expected_goals_conceded": h.get("expected_goals_conceded"),
                "starts": h.get("starts"), "tackles": h.get("tackles"),
                "recoveries": h.get("recoveries"),
                "clearances_blocks_interceptions": h.get("clearances_blocks_interceptions"),
                "defensive_contribution": h.get("defensive_contribution"),
                "start_cost": h.get("start_cost"), "end_cost": h.get("end_cost"),
            })
        _upsert(con, "player_fixture", pf_rows, "player_id, fixture_id")
        _upsert(con, "player_season_prior", past_rows, "player_id, season_name")
        n_hist += len(pf_rows)
        n_past += len(past_rows)

        e = by_id[pid]
        con.execute(
            """INSERT INTO player_snapshot (player_id, fetch_date, gw, now_cost, form,
                   points_per_game, selected_by_percent, total_points, event_points,
                   transfers_in_event, transfers_out_event, cost_change_event,
                   cost_change_start, dreamteam_count, ep_next, ep_this, status, news,
                   chance_of_playing_next_round, chance_of_playing_this_round)
               VALUES (?,? ,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(player_id, fetch_date) DO UPDATE SET
                 now_cost=excluded.now_cost, form=excluded.form,
                 points_per_game=excluded.points_per_game,
                 selected_by_percent=excluded.selected_by_percent,
                 total_points=excluded.total_points, event_points=excluded.event_points,
                 transfers_in_event=excluded.transfers_in_event,
                 transfers_out_event=excluded.transfers_out_event,
                 cost_change_event=excluded.cost_change_event,
                 cost_change_start=excluded.cost_change_start,
                 dreamteam_count=excluded.dreamteam_count,
                 ep_next=excluded.ep_next, ep_this=excluded.ep_this,
                 status=excluded.status, news=excluded.news,
                 chance_of_playing_next_round=excluded.chance_of_playing_next_round,
                 chance_of_playing_this_round=excluded.chance_of_playing_this_round""",
            (pid, fetch_date, cur_gw, e.get("now_cost"),
             float(e.get("form") or 0), float(e.get("points_per_game") or 0),
             float(e.get("selected_by_percent") or 0), e.get("total_points"),
             e.get("event_points"), e.get("transfers_in_event"),
             e.get("transfers_out_event"), e.get("cost_change_event"),
             e.get("cost_change_start"), e.get("dreamteam_count"),
             e.get("ep_next"), e.get("ep_this"), e.get("status"), e.get("news"),
             e.get("chance_of_playing_next_round"), e.get("chance_of_playing_this_round")),
        )
        if i % 100 == 0:
            log(f"  {i}/{len(ids)} players...")
        time.sleep(0.05)

    # one retry pass for failures
    for pid in list(failed):
        try:
            d = get(f"element-summary/{pid}/", retries=2)
            failed.remove(pid)
        except RuntimeError:
            pass
    con.commit()
    log(f"player_fixture rows: {n_hist} | prior-season rows: {n_past} | snapshot date: {fetch_date}")
    if failed:
        log(f"WARNING: element-summary failed for {len(failed)} players: {failed[:10]}")
    return {"failed": failed, "snapshot_date": fetch_date}


if __name__ == "__main__":
    con = init_db()
    sync(con)
    con.close()
