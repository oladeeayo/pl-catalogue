"""Enrich with Opta season stats via The Analyst's public sdapi endpoint.

Covers the metric families the FBref scrape cannot currently serve (their Opta
feed lapsed): goalkeeping (xGOT, goals prevented), set pieces, duels,
progressive carries, discipline, plus full team-level tables.

Source: wp-json/sdapi/v1/soccerdata/tournamentstats?tmcl=<token>
The token is parsed fresh from the stats page each run (fallback constant).
Season totals (not per-fixture), so they refresh weekly with the site.

  player sections -> season_stats   (source='opta', metric = raw metric name)
  team sections   -> club_stats     (source='opta', metric = section[_sub]_metric)

Player resolution: fpl_code (= Opta person id) -> reep key_opta_numeric ->
(name + DOB) -> unique name. Idempotent upserts; club_stats fully replaced.
"""
import json
import re
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import requests

DB = Path(__file__).parent / "catalogue.db"
SEASON = "2026-27"
STATS_PAGE = "https://theanalyst.com/competition/premier-league/stats"
API = "https://theanalyst.com/wp-json/sdapi/v1/soccerdata/tournamentstats"
FALLBACK_TMCL = "6pdwluctev9iebv00r4qqukno"
UA = {"User-Agent": "Mozilla/5.0"}
TIMEOUT = 60

PLAYER_IDENT = {
    "player_id", "player", "first_name", "last_name", "date_of_birth", "age",
    "team_id", "shirt_number", "squad_position", "squad_position_detailed",
    "player_uuid", "team_uuid", "contestantName", "contestantClubName",
    "contestantShortName", "contestantCode",
}
TEAM_IDENT = {
    "team_id", "team", "played", "team_uuid", "contestantName",
    "contestantClubName", "contestantShortName", "contestantCode",
}
TEAM_ALIASES = {
    "manchester utd": "manchester united", "man utd": "manchester united",
    "man city": "manchester city",
    "wolves": "wolverhampton wanderers", "tottenham": "tottenham hotspur",
    "spurs": "tottenham hotspur", "nottm forest": "nottingham forest",
    "west ham": "west ham united", "brighton": "brighton and hove albion",
    "newcastle": "newcastle united", "leeds": "leeds united",
}


def norm(s):
    s = re.sub(r"[^a-z0-9 ]", "", str(s or "").strip().lower())
    s = re.sub(r"\s+", " ", s).strip()
    return TEAM_ALIASES.get(s, s)


def num(v):
    if v is None or v == "":
        return None
    try:
        f = float(str(v).replace("%", ""))
        return f
    except ValueError:
        return None


def get_tmcl():
    try:
        html = requests.get(STATS_PAGE, headers=UA, timeout=30).text
        for blob in re.findall(
                r'<script class="attributes" type="application/json">(\{[^<]*\})</script>',
                html):
            tok = json.loads(blob).get("tmcl")
            if tok:
                return tok, "page"
    except (requests.RequestException, ValueError) as e:
        print(f"WARN enrich_opta: token fetch failed ({type(e).__name__}) - using fallback")
    return FALLBACK_TMCL, "fallback"


def run(verbose=True):
    def log(m):
        if verbose:
            print(m, flush=True)

    tmcl, origin = get_tmcl()
    log(f"tmcl via {origin}")
    resp = requests.get(API, headers=UA, params={"tmcl": tmcl}, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    updated = data.get("lastUpdated", "?")
    con = sqlite3.connect(DB)

    # ---------- player resolution maps ----------
    by_code, by_reep_opta, by_namedob, by_name = {}, {}, {}, {}
    players = pd.read_sql(
        "SELECT player_id, fpl_code, reep_id, first_name, second_name, web_name,"
        " date_of_birth FROM players WHERE removed=0", con)
    for _, p in players.iterrows():
        pid = int(p["player_id"])
        if pd.notna(p["fpl_code"]):
            by_code.setdefault(int(p["fpl_code"]), pid)
        first = p["first_name"] if isinstance(p["first_name"], str) else ""
        second = p["second_name"] if isinstance(p["second_name"], str) else ""
        names = [p["web_name"], first, second,
                 (first + " " + second).strip()]
        dob = p["date_of_birth"][:10] if isinstance(p["date_of_birth"], str) else ""
        for nm in names:
            if not isinstance(nm, str) or not nm:
                continue
            n = norm(nm)
            if not n:
                continue
            by_name.setdefault(n, pid)
            if dob:
                by_namedob.setdefault((n, dob), pid)
    rp_path = Path(__file__).parent.parent / "sources" / "reep_people.csv"
    if rp_path.exists():
        rp = pd.read_csv(rp_path, dtype=str,
                         usecols=["reep_id", "key_opta_numeric"], low_memory=False)
        rp = rp[rp["key_opta_numeric"].notna() & (rp["key_opta_numeric"].str.strip() != "")]
        sub = players[players["reep_id"].notna()]
        pid_of_reep = dict(zip(sub["reep_id"], sub["player_id"].astype(int)))
        for k, rid in zip(rp["key_opta_numeric"].str.strip(), rp["reep_id"]):
            if k.isdigit():
                by_reep_opta.setdefault(int(k), pid_of_reep.get(rid))

    def resolve(row):
        oid = row.get("player_id")
        if oid is not None and int(oid) in by_code:
            return by_code[int(oid)], "code"
        if oid is not None and int(oid) in by_reep_opta and by_reep_opta[int(oid)]:
            return by_reep_opta[int(oid)], "reep"
        dob = (row.get("date_of_birth") or "")[:10]
        full = norm(f"{row.get('first_name', '')} {row.get('last_name', '')}")
        for key in ((full, dob), (norm(row.get("player")), dob)):
            if dob and key in by_namedob:
                return by_namedob[key], "namedob"
        if full and full in by_name:
            return by_name[full], "name"
        if norm(row.get("player")) in by_name:
            return by_name[norm(row.get("player"))], "name"
        return None, None

    # ---------- players -> season_stats ----------
    fetched = date.today().isoformat()
    upserts, matched_rows, total_rows, seen_ids = [], 0, 0, set()
    unresolved = []
    pdata = data.get("player", {})
    for section, subs in pdata.items():
        if not isinstance(subs, dict):
            continue
        for sub, rows in subs.items():
            if not isinstance(rows, list):
                continue
            for row in rows:
                total_rows += 1
                pid, how = resolve(row)
                if pid is None:
                    unresolved.append(row.get("player"))
                    continue
                matched_rows += 1
                seen_ids.add(pid)
                for k, v in row.items():
                    if k in PLAYER_IDENT:
                        continue
                    val = num(v)
                    if val is None:
                        continue
                    upserts.append((pid, SEASON, "opta", k, val, fetched))
    if upserts:
        con.executemany(
            "INSERT INTO season_stats (player_id, season, source, metric, value, fetched_at)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(player_id, season, source, metric)"
            " DO UPDATE SET value=excluded.value, fetched_at=excluded.fetched_at",
            upserts)

    # ---------- teams -> club_stats ----------
    con.execute("DELETE FROM club_stats WHERE season=? AND source='opta'", (SEASON,))
    clubs = pd.read_sql("SELECT club_id, name, short_name FROM clubs", con)
    club_of = {}
    for _, c in clubs.iterrows():
        club_of.setdefault(norm(c["name"]), int(c["club_id"]))
        if c["short_name"]:
            club_of.setdefault(norm(c["short_name"]), int(c["club_id"]))
    club_rows, team_unresolved = [], []
    tdata = data.get("team", {})
    for section, subs in tdata.items():
        if not isinstance(subs, dict):
            continue
        for sub, rows in subs.items():
            if not isinstance(rows, list):
                continue
            prefix = section if sub == "overall" else f"{section}_{sub}"
            for row in rows:
                nm = row.get("team") or row.get("contestantClubName")
                cid = club_of.get(norm(nm)) or club_of.get(
                    norm(row.get("contestantClubName")))
                if cid is None:
                    team_unresolved.append(nm)
                    continue
                for k, v in row.items():
                    if k in TEAM_IDENT:
                        continue
                    val = num(v)
                    if val is None:
                        continue
                    club_rows.append((cid, SEASON, "opta", f"{prefix}_{k}", val,
                                      int(row["team_id"]) if row.get("team_id") else None,
                                      fetched))
    con.executemany(
        "INSERT INTO club_stats (club_id, season, source, metric, value,"
        " opta_team_id, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        club_rows)
    con.commit()
    con.close()

    n_players = len(seen_ids)
    n_clubs = len({r[0] for r in club_rows})
    log(f"opta updated {updated}: players {matched_rows}/{total_rows} rows"
        f" ({n_players} players, {len(upserts)} values),"
        f" teams {len(club_rows)} values across {n_clubs} clubs")
    if unresolved:
        uniq = sorted({u for u in unresolved if u})
        log(f"unresolved players ({len(uniq)}): {uniq[:8]}")
    if team_unresolved:
        log(f"unresolved teams: {sorted(set(team_unresolved))}")
    return {"player_values": len(upserts), "players": n_players,
            "team_values": len(club_rows), "clubs": n_clubs,
            "updated": updated, "matched_rows": matched_rows,
            "total_rows": total_rows}


if __name__ == "__main__":
    run()
