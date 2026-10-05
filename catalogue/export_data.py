"""Export the catalogue into a data tree (vaastav-inspired, extended).

    data/2026-27/
      players/                      PER-PLAYER: everything known about one player
        <First_Last_id>/
          profile.csv               identity + live snapshot + season + market (1 row)
          gw.csv                    per-GW performance (with fixture context/result)
          season_stats.csv          FBref + Understat season metrics (long format)
          shots.csv                 Understat shot events taken
          history.csv               prior-season FPL career history
          market.csv                Transfermarkt value/contract snapshots (grows daily)
      gws/                          PER-GW: one slice per matchweek
        gw1.csv ... gwN.csv         player rows for that GW (only played GWs)
        merged_gw.csv               all GWs stacked
        xP1.csv                     expected points: xp forecast vs actual, all GWs
      players.csv                   merged master: one row per player, all sources
      transfers.csv                 Transfermarkt transfer history (5 seasons)
      club_stats.csv                Opta team season metrics (long format, The Analyst)
      shots.csv                     all shot events
      market.csv                    all market-value snapshots (time series)
      gw_calendar.csv               GW metadata: deadlines, averages, most-*
      fixtures.csv
      clubs.csv
      manifest.json                 export provenance: counts, gw range, timestamp

The catalogue DB is the store; this tree is a rebuildable view. Re-runs wipe and
rebuild, so stale folders never linger. Run weekly (run_weekly step) or standalone:
    python catalogue/export_data.py

Extension points for upcoming categories: ratings.csv / wages.csv (merged, root),
ratings.csv / wages.csv (per-player, inside each folder).
"""
import json
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "catalogue" / "catalogue.db"
SEASON = "2026-27"
OUT = ROOT / "data" / SEASON

POS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

GW_COLS = ["gw", "element", "code", "name", "position", "team", "opponent",
           "was_home", "result", "team_score", "opp_score", "fixture_id",
           "minutes", "starts", "total_points",
           "goals_scored", "assists", "clean_sheets", "goals_conceded", "saves",
           "own_goals", "penalties_saved", "penalties_missed", "yellow_cards",
           "red_cards", "bonus", "bps", "influence", "creativity", "threat",
           "ict_index", "clearances_blocks_interceptions", "recoveries", "tackles",
           "defensive_contribution", "expected_goals", "expected_assists",
           "expected_goal_involvements", "expected_goals_conceded",
           "value", "selected", "transfers_in", "transfers_out",
           "transfers_balance"]

INT_COLS = ["gw", "was_home", "team_score", "opp_score", "minutes", "starts",
            "total_points", "goals_scored", "assists", "clean_sheets",
            "goals_conceded", "saves", "own_goals", "penalties_saved",
            "penalties_missed", "yellow_cards", "red_cards", "bonus", "bps",
            "clearances_blocks_interceptions", "recoveries", "tackles",
            "defensive_contribution", "value", "selected", "transfers_in",
            "transfers_out", "transfers_balance"]

MASTER_COLS = [
    "player_id", "code", "web_name", "first_name", "second_name", "full_name",
    "known_name", "club_id", "team", "position", "position_detail", "status", "news",
    "date_of_birth", "nationality", "height_cm", "squad_number", "region",
    "reep_id", "key_fbref", "key_understat", "key_transfermarkt", "key_sofascore",
    "key_fotmob", "key_premier_league", "key_whoscored", "opta_code",
    "price", "form", "total_points", "points_per_game", "event_points",
    "selected_by_percent", "transfers_in_event", "transfers_out_event",
    "cost_change_event", "cost_change_start", "dreamteam_count", "ep_next",
    "ep_this", "chance_of_playing_next_round", "chance_of_playing_this_round",
    "gws_played", "fixtures_played", "season_minutes", "season_starts",
    "season_goals", "season_assists", "season_clean_sheets", "season_saves",
    "season_bonus", "season_bps", "season_points", "season_xg", "season_xa",
    "prior_seasons", "prior_total_points", "prior_minutes",
    "tm_id", "tm_name", "market_value_eur", "contract_until", "joined_date",
    "foot", "dob_tm", "age_at_fetch", "position_tm",
]
# season_stats pivot columns (fbref_*, understat_*, ...) are appended dynamically

HISTORY_COLS = None
SHOTS_COLS = None
MARKET_COLS = None


def _folder_name(first, second, player_id):
    return re.sub(r'[\\/:*?"<>|]', "_", f"{first}_{second}_{player_id}")


def _write(df, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8")


def run(verbose=True):
    def log(m):
        if verbose:
            print(m, flush=True)

    if not DB.exists():
        log(f"SKIP export: {DB} missing")
        return {"skipped": True}

    global HISTORY_COLS, SHOTS_COLS, MARKET_COLS
    con = sqlite3.connect(DB)
    clubs = pd.read_sql(
        "SELECT club_id, name AS team, short_name, position, played, win, draw,"
        " loss, points, form, us_team_id, reep_id FROM clubs ORDER BY club_id", con)
    players = pd.read_sql(
        "SELECT player_id, fpl_code AS code, reep_id, web_name, first_name,"
        " second_name, known_name, full_name, club_id, element_type,"
        " position_detail, date_of_birth, region, nationality, height_cm,"
        " squad_number, status, news, opta_code, key_wikidata, key_fbref,"
        " key_understat, key_transfermarkt, key_sofascore, key_fotmob,"
        " key_premier_league, key_whoscored FROM players", con)
    gwc = pd.read_sql("SELECT * FROM gws ORDER BY gw", con)
    snap = pd.read_sql(
        "SELECT * FROM player_snapshot WHERE fetch_date ="
        " (SELECT MAX(fetch_date) FROM player_snapshot)", con)
    market = pd.read_sql(
        "SELECT * FROM player_market ORDER BY fetch_date, player_id", con)
    market_latest = market[market["fetch_date"] == market["fetch_date"].max()]
    stats_long = pd.read_sql(
        "SELECT player_id, season, source, metric, value, fetched_at"
        " FROM season_stats ORDER BY source, metric", con)
    history = pd.read_sql(
        "SELECT * FROM player_season_prior ORDER BY player_id, season_name", con)
    fixtures = pd.read_sql(
        "SELECT f.fixture_id, f.gw, f.kickoff_time, f.home_club, f.away_club,"
        " hc.name AS home_team, ac.name AS away_team, f.home_score, f.away_score,"
        " f.finished, f.started, f.home_difficulty, f.away_difficulty, f.pulse_id,"
        " f.us_match_id FROM fixtures f"
        " LEFT JOIN clubs hc ON hc.club_id = f.home_club"
        " LEFT JOIN clubs ac ON ac.club_id = f.away_club ORDER BY f.gw, f.fixture_id", con)
    shots = pd.read_sql(
        "SELECT s.shot_id, s.us_match_id, s.fixture_id, fx.gw, s.date, s.minute,"
        " s.player_id, COALESCE(p.web_name, s.player_name) AS player,"
        " s.assisted_name, s.x, s.y, s.xg, s.result, s.situation, s.shot_type,"
        " s.last_action, s.h_a FROM us_shots s"
        " LEFT JOIN players p ON p.player_id = s.player_id"
        " LEFT JOIN fixtures fx ON fx.fixture_id = s.fixture_id"
        " ORDER BY s.date, s.minute", con)
    pf = pd.read_sql(
        "SELECT pf.*, p.element_type, p.web_name, p.first_name, p.second_name,"
        " p.fpl_code, c.name AS team, oc.name AS opponent,"
        " f.home_score AS fx_home, f.away_score AS fx_away, f.finished AS fx_finished"
        " FROM player_fixture pf"
        " JOIN players p ON p.player_id = pf.player_id"
        " LEFT JOIN clubs c ON c.club_id = p.club_id"
        " LEFT JOIN clubs oc ON oc.club_id = pf.opponent_team"
        " LEFT JOIN fixtures f ON f.fixture_id = pf.fixture_id"
        " ORDER BY pf.gw, c.name, p.web_name", con)
    fx_sched = pd.read_sql(
        "SELECT gw, home_club, away_club FROM fixtures", con)
    try:
        xp = pd.read_sql(
            "SELECT x.player_id, x.gw, x.xp, x.actual, p.club_id"
            " FROM xp_projection x JOIN players p ON p.player_id=x.player_id",
            con)
    except (sqlite3.Error, pd.errors.DatabaseError):
        xp = pd.DataFrame(columns=["player_id", "gw", "xp", "actual", "club_id"])
    try:
        transfers = pd.read_sql(
            "SELECT t.*, p.web_name AS fpl_name FROM transfer_history t"
            " LEFT JOIN players p ON p.player_id = t.player_id"
            " ORDER BY t.saison_id DESC, t.club_name, t.direction, t.player_name",
            con)
    except (sqlite3.Error, pd.errors.DatabaseError):
        transfers = pd.DataFrame()
    try:
        club_stats = pd.read_sql(
            "SELECT c.name AS team, cs.club_id, cs.source, cs.metric, cs.value,"
            " cs.opta_team_id, cs.fetched_at FROM club_stats cs"
            " JOIN clubs c ON c.club_id = cs.club_id"
            " ORDER BY cs.club_id, cs.metric", con)
    except (sqlite3.Error, pd.errors.DatabaseError):
        club_stats = pd.DataFrame()
    con.close()

    shots["player_id"] = shots["player_id"].astype("Int64")
    shots[["x", "y", "xg"]] = shots[["x", "y", "xg"]].round(4)
    HISTORY_COLS, SHOTS_COLS, MARKET_COLS = (
        list(history.columns), list(shots.columns), list(market.columns))

    # ---- per-GW frame with fixture context ----
    gw = pf.rename(columns={"fpl_code": "code"}).copy()
    gw.insert(1, "element", gw["player_id"])
    gw["name"] = gw["web_name"]
    gw["position"] = gw["element_type"].map(POS)
    home = gw["was_home"] == 1
    gw["team_score"] = gw["fx_home"].where(home, gw["fx_away"])
    gw["opp_score"] = gw["fx_away"].where(home, gw["fx_home"])
    gw["result"] = ""
    fin = gw["team_score"].notna() & gw["opp_score"].notna() & (gw["fx_finished"] == 1)
    gw.loc[fin & (gw["team_score"] > gw["opp_score"]), "result"] = "W"
    gw.loc[fin & (gw["team_score"] == gw["opp_score"]), "result"] = "D"
    gw.loc[fin & (gw["team_score"] < gw["opp_score"]), "result"] = "L"
    gw = gw[GW_COLS].copy()
    for col in INT_COLS:
        gw[col] = pd.to_numeric(gw[col], errors="coerce").astype("Int64")

    # ---- merged player master ----
    season = (pf.groupby("player_id")
              .agg(gws_played=("gw", "nunique"), fixtures_played=("fixture_id", "nunique"),
                   season_minutes=("minutes", "sum"), season_starts=("starts", "sum"),
                   season_goals=("goals_scored", "sum"), season_assists=("assists", "sum"),
                   season_clean_sheets=("clean_sheets", "sum"), season_saves=("saves", "sum"),
                   season_bonus=("bonus", "sum"), season_bps=("bps", "sum"),
                   season_points=("total_points", "sum"),
                   season_xg=("expected_goals", "sum"),
                   season_xa=("expected_assists", "sum")).reset_index())
    season[["season_xg", "season_xa"]] = season[["season_xg", "season_xa"]].round(2)
    if len(stats_long):
        pivot = (stats_long.pivot_table(index="player_id",
                                        columns=["source", "metric"], values="value")
                 .reset_index())
        pivot.columns = [f"{c[0]}_{c[1]}" if isinstance(c, tuple) and c[1] else
                         (c[0] if isinstance(c, tuple) else str(c))
                         for c in pivot.columns]
    else:
        pivot = pd.DataFrame({"player_id": []})
    prior = (history.groupby("player_id")
             .agg(prior_seasons=("season_name", "count"),
                  prior_total_points=("total_points", "sum"),
                  prior_minutes=("minutes", "sum")).reset_index())

    master = players.merge(clubs[["club_id", "team"]], on="club_id", how="left")
    master["position"] = master["element_type"].map(POS)
    master = (master.merge(snap, on="player_id", how="left", suffixes=("", "_snap"))
              .merge(market_latest.drop(columns=["source_club"]),
                     on="player_id", how="left", suffixes=("", "_tm"))
              .merge(season, on="player_id", how="left")
              .merge(pivot, on="player_id", how="left")
              .merge(prior, on="player_id", how="left"))
    master["price"] = master["now_cost"] / 10.0
    master = master.sort_values(["club_id", "element_type", "web_name"])
    metric_cols = sorted(c for c in pivot.columns if c != "player_id")
    master_out = master.reindex(
        columns=MASTER_COLS + [c for c in metric_cols if c not in MASTER_COLS])

    # ---- expected points: forecast vs actual, with fixture context ----
    xp_out = pd.DataFrame()
    if len(xp):
        home_s = fx_sched[["gw", "home_club", "away_club"]].rename(
            columns={"home_club": "club_id", "away_club": "opp_club"})
        home_s["was_home"] = 1
        away_s = fx_sched[["gw", "away_club", "home_club"]].rename(
            columns={"away_club": "club_id", "home_club": "opp_club"})
        away_s["was_home"] = 0
        sched = pd.concat([home_s, away_s], ignore_index=True)
        team_name = dict(zip(clubs["club_id"], clubs["team"]))
        xp_out = (xp.merge(sched, on=["club_id", "gw"], how="left")
                  .merge(players[["player_id", "web_name", "element_type"]],
                         on="player_id", how="left")
                  .merge(clubs[["club_id", "team"]], on="club_id", how="left"))
        xp_out["opponent"] = xp_out["opp_club"].map(team_name)
        xp_out["position"] = xp_out["element_type"].map(POS)
        xp_out["err"] = (xp_out["actual"] - xp_out["xp"]).round(3)
        xp_out["xp"] = xp_out["xp"].round(3)
        xp_out = xp_out.rename(columns={"player_id": "element",
                                        "web_name": "name"})[
            ["gw", "element", "name", "position", "team", "opponent",
             "was_home", "xp", "actual", "err"]]
        xp_out = xp_out.sort_values(["gw", "xp"], ascending=[True, False])

    # ---- rebuild the tree ----
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "gws").mkdir(parents=True)
    (OUT / "players").mkdir(parents=True)

    for gw_num, chunk in gw.groupby("gw"):
        _write(chunk, OUT / "gws" / f"gw{gw_num}.csv")
    _write(gw, OUT / "gws" / "merged_gw.csv")
    if len(xp_out):
        _write(xp_out, OUT / "gws" / "xP1.csv")
    _write(master_out, OUT / "players.csv")
    if len(transfers):
        _write(transfers, OUT / "transfers.csv")
    if len(club_stats):
        _write(club_stats, OUT / "club_stats.csv")

    name_map = dict(zip(players["player_id"], players["web_name"]))
    for el in ("most_selected", "most_transferred_in", "most_captained",
               "most_vice_captained"):
        if el in gwc.columns:
            gwc[f"{el}_name"] = gwc[el].map(name_map)
    _write(gwc, OUT / "gw_calendar.csv")
    _write(shots.drop(columns=["player_id"]), OUT / "shots.csv")
    _write(market, OUT / "market.csv")
    _write(fixtures.drop(columns=["home_club", "away_club", "pulse_id"]),
           OUT / "fixtures.csv")
    _write(clubs.rename(columns={"team": "name"}), OUT / "clubs.csv")

    gw_by_player = {pid: chunk for pid, chunk in gw.groupby("element")}
    stats_by_player = dict(tuple(stats_long.groupby("player_id")))
    hist_by_player = dict(tuple(history.groupby("player_id")))
    shots_resolved = shots[shots["player_id"].notna()]
    shots_by_player = dict(tuple(shots_resolved.groupby("player_id")))
    market_by_player = dict(tuple(market.groupby("player_id")))
    transfers_matched = (transfers[transfers["player_id"].notna()]
                         if len(transfers) and "player_id" in transfers.columns
                         else transfers.iloc[0:0])
    transfers_by_player = dict(tuple(transfers_matched.groupby("player_id")))
    transfers_cols = list(transfers.columns) if len(transfers) else ["row_key"]
    master_idx = master_out.set_index("player_id")

    n_folders = 0
    for _, row in master.iterrows():
        pid = int(row["player_id"])
        folder = OUT / "players" / _folder_name(row["first_name"], row["second_name"], pid)
        folder.mkdir(parents=True, exist_ok=True)
        _write(gw_by_player.get(pid, pd.DataFrame(columns=GW_COLS)), folder / "gw.csv")
        _write(master_idx.loc[[pid]].reset_index(), folder / "profile.csv")
        _write(stats_by_player.get(
            pid, pd.DataFrame(columns=["player_id", "season", "source", "metric",
                                       "value", "fetched_at"])), folder / "season_stats.csv")
        _write(hist_by_player.get(pid, pd.DataFrame(columns=HISTORY_COLS)),
               folder / "history.csv")
        _write(shots_by_player.get(pid, pd.DataFrame(columns=SHOTS_COLS)),
               folder / "shots.csv")
        _write(market_by_player.get(pid, pd.DataFrame(columns=MARKET_COLS)),
               folder / "market.csv")
        _write(transfers_by_player.get(pid, pd.DataFrame(columns=transfers_cols)),
               folder / "transfers.csv")
        n_folders += 1

    gw_files = sorted(int(p.stem[2:]) for p in (OUT / "gws").glob("gw*.csv"))
    manifest = {
        "season": SEASON,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "gw_range": [gw_files[0], gw_files[-1]] if gw_files else None,
        "counts": {
            "players": int(len(master_out)),
            "clubs": int(len(clubs)),
            "fixtures": int(len(fixtures)),
            "player_fixture_rows": int(len(gw)),
            "shots": int(len(shots)),
            "market_rows": int(len(market)),
            "season_stats_rows": int(len(stats_long)),
            "history_rows": int(len(history)),
            "xp_rows": int(len(xp_out)),
            "transfer_rows": int(len(transfers)),
            "club_stats_rows": int(len(club_stats)),
            "player_folders": n_folders,
            "csv_files": sum(1 for _ in OUT.rglob("*.csv")),
        },
        "sources": ["fpl", "reep", "fbref", "understat", "transfermarkt", "opta"],
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    log(f"exported data/{SEASON}: {n_folders} player folders, gw {gw_files}, "
        f"{manifest['counts']['csv_files']} csv files")
    return manifest["counts"]


if __name__ == "__main__":
    run()
