"""Understat enrichment: fixture links, per-match shots, per-appearance xG/xA,
season xG/xA totals. Requests-only (XHR header), no browser. Idempotent.

Sources: https://understat.com/getLeagueData/EPL/2026 (season)
         https://understat.com/getMatchData/{id}     (per finished match)
"""
import json
import re
import sqlite3
import time
import unicodedata
from datetime import date
from pathlib import Path

import pandas as pd
import requests

DB = Path(__file__).parent / "catalogue.db"
ROOT = Path(__file__).parent.parent
SEASON = "2026-27"
LEAGUE_YEAR = "2026"
CACHE = Path(__file__).parent / "_understat_league.json"

HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": f"https://understat.com/league/EPL/{LEAGUE_YEAR}",
}

# Understat team title -> FPL club name (normalized)
US2FPL = {
    "tottenham": "spurs",
    "newcastle united": "newcastle",
    "manchester city": "man city",
    "manchester united": "man utd",
    "hull": "hull city",
    "nottingham forest": "nott m forest",
    "ipswich": "ipswich town",
    "coventry": "coventry city",
}

NICK_FIRST = {"kostas": "konstantinos"}

LETTERS = {"ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "ł": "l", "Ł": "L",
           "æ": "ae", "Æ": "AE", "ß": "ss", "œ": "oe", "Œ": "OE"}


def norm(s):
    s = unicodedata.normalize(
        "NFKD", str(s or "").strip()).encode("ascii", "ignore").decode()
    for k, v in LETTERS.items():
        s = s.replace(k, v)
    s = re.sub(r"[^a-z ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


US_SCHEMA = """
CREATE TABLE IF NOT EXISTS us_shots (
    shot_id            INTEGER PRIMARY KEY,   -- Understat shot id
    us_match_id        INTEGER NOT NULL,
    fixture_id         INTEGER REFERENCES fixtures(fixture_id),
    player_id          INTEGER REFERENCES players(player_id),
    us_player_id       INTEGER,
    player_name        TEXT,
    assisted_name      TEXT,
    assisted_player_id INTEGER REFERENCES players(player_id),
    minute             INTEGER,
    x                  REAL,                  -- Understat normalized 0-1
    y                  REAL,
    xg                 REAL,
    result             TEXT,                  -- Goal / SavedShot / MissedShots / ShotOnPost
    situation          TEXT,                  -- OpenPlay / FromCorner / SetPiece / Penalty
    shot_type          TEXT,                  -- RightFoot / LeftFoot / Header
    last_action         TEXT,
    h_a                TEXT,
    date               TEXT
);
CREATE INDEX IF NOT EXISTS idx_us_shots_fixture ON us_shots(fixture_id);
CREATE INDEX IF NOT EXISTS idx_us_shots_player ON us_shots(player_id);
CREATE INDEX IF NOT EXISTS idx_us_shots_match ON us_shots(us_match_id);

CREATE TABLE IF NOT EXISTS us_appearances (
    fixture_id   INTEGER NOT NULL REFERENCES fixtures(fixture_id),
    us_player_id INTEGER NOT NULL,
    player_id    INTEGER REFERENCES players(player_id),
    player_name  TEXT,
    h_a          TEXT,
    position     TEXT,
    minutes      INTEGER,
    goals        INTEGER,
    shots        INTEGER,
    xg           REAL,
    xa           REAL,
    key_passes   INTEGER,
    assists      INTEGER,
    yellow       INTEGER,
    red          INTEGER,
    roster_in    INTEGER,
    roster_out   INTEGER,
    PRIMARY KEY (fixture_id, us_player_id)
);
CREATE INDEX IF NOT EXISTS idx_us_app_player ON us_appearances(player_id);
"""


def _has_col(con, table, col):
    return any(r[1] == col for r in con.execute(f"PRAGMA table_info({table})"))


def ensure_schema(con):
    if not _has_col(con, "clubs", "us_team_id"):
        con.execute("ALTER TABLE clubs ADD COLUMN us_team_id INTEGER")
    if not _has_col(con, "fixtures", "us_match_id"):
        con.execute("ALTER TABLE fixtures ADD COLUMN us_match_id INTEGER")
    con.executescript(US_SCHEMA)
    con.commit()


def fetch(url, referer=None, tries=3):
    last = None
    for attempt in range(tries):
        try:
            h = dict(HEADERS)
            if referer:
                h["Referer"] = referer
            r = requests.get(url, headers=h, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1 + 2 * attempt)
    raise RuntimeError(f"understat fetch failed {url}: {last}")


def run(verbose=True):
    def log(m):
        if verbose:
            print(m, flush=True)

    con = sqlite3.connect(DB)
    ensure_schema(con)

    # ---- season league data ----
    league = fetch(f"https://understat.com/getLeagueData/EPL/{LEAGUE_YEAR}")
    CACHE.write_text(json.dumps(league), encoding="utf-8")

    # ---- team map ----
    fpl_norm = {norm(n): cid for cid, n in
                con.execute("SELECT club_id, name FROM clubs")}
    title2club = {}
    for t in league["teams"].values():
        key = US2FPL.get(norm(t["title"]), norm(t["title"]))
        cid = fpl_norm.get(key)
        if cid is None:
            raise RuntimeError(f"unknown understat team title: {t['title']!r}")
        title2club[t["title"]] = cid
        con.execute("UPDATE clubs SET us_team_id=? WHERE club_id=?",
                    (int(t["id"]), cid))

    # ---- fixture links (home-away pair is unique per season) ----
    fx_by_pair = {(h, a): fid for fid, h, a in
                  con.execute("SELECT fixture_id, home_club, away_club FROM fixtures")}
    fx_by_us = {us: fid for fid, us in
                con.execute("SELECT fixture_id, us_match_id FROM fixtures WHERE us_match_id IS NOT NULL")}
    linked = link_misses = 0
    for d in league["dates"]:
        pair = (title2club.get(d["h"]["title"]), title2club.get(d["a"]["title"]))
        fid = fx_by_pair.get(pair)
        if fid is None:
            link_misses += 1
            log(f"  LINK MISS us match {d['id']}: {d['h']['title']} v {d['a']['title']}")
            continue
        linked += 1
        fx_by_us[int(d["id"])] = fid
    if link_misses:
        raise RuntimeError(f"understat->FPL fixture link misses: {link_misses}")
    con.executemany(
        "UPDATE fixtures SET us_match_id=? WHERE fixture_id=?",
        [(us, fid) for us, fid in fx_by_us.items()])
    con.commit()
    log(f"fixtures linked: {linked}/{len(league['dates'])}")

    # ---- player resolution indexes ----
    players = pd.read_sql(
        "SELECT player_id, fpl_code, first_name, second_name, web_name FROM players", con)
    by_code, by_name, first_tok, by_last = {}, {}, {}, {}
    for _, p in players.iterrows():
        pid = int(p["player_id"])
        if pd.notna(p["fpl_code"]):
            by_code[str(int(p["fpl_code"]))] = pid
        for key in (norm(f"{p['first_name']} {p['second_name']}"),
                    norm(f"{p['first_name']} {p['web_name']}"),
                    norm(p["web_name"])):
            if key:
                by_name.setdefault(key, []).append(pid)
        f = norm(p["first_name"])
        if f:
            first_tok[pid] = f.split()[0]
        for field in (p["second_name"], p["web_name"]):
            v = norm(field)
            if v and len(v.split()[-1]) > 2:
                by_last.setdefault(v.split()[-1], set()).add(pid)

        rp_path = ROOT / "sources" / "reep_people.csv"
    by_us = {}
    if rp_path.exists():
        rp = pd.read_csv(rp_path, usecols=["type", "key_understat",
                        "key_opta_numeric"], dtype=str, low_memory=False)
        rp = rp[rp["type"] == "player"]
        for _, r in rp.iterrows():
            ku, ko = r["key_understat"], r["key_opta_numeric"]
            if pd.notna(ku) and ku.strip() and pd.notna(ko) and ko.strip():
                pid = by_code.get(str(int(float(ko))))
                if pid:
                    by_us.setdefault(ku.strip(), pid)
        del rp
    else:
        log("WARN enrich_understat: reep_people.csv missing - resolving by name only")
    log(f"resolution index: by_us={len(by_us)} by_name={len(by_name)}")

    def prefix_pair(a, b):
        if a == b:
            return True
        if min(len(a), len(b)) < 3:
            return False
        return a.startswith(b) or b.startswith(a)

    def loose(fbname):
        toks = fbname.split()
        if len(toks) < 2:
            return None
        t_first = NICK_FIRST.get(toks[0], toks[0])
        hits = {pid for pid in by_last.get(toks[-1], set())
                if prefix_pair(t_first, first_tok.get(pid, ""))}
        return next(iter(hits)) if len(hits) == 1 else None

    def resolve(us_id, name):
        if us_id is not None:
            pid = by_us.get(str(us_id).strip())
            if pid:
                return pid
        n = norm(name)
        if n:
            cands = set(by_name.get(n, []))
            if len(cands) == 1:
                return next(iter(cands))
            pid = loose(n)
            if pid is not None:
                return pid
        return None

    # ---- fetch per-match data for finished matches without shots ----
    have_shots = {m for (m,) in con.execute(
        "SELECT DISTINCT us_match_id FROM us_shots")}
    to_fetch = [d for d in league["dates"]
                if d["isResult"] and int(d["id"]) not in have_shots
                and int(d["id"]) in fx_by_us]
    n_shots = n_apps = 0
    shot_rows, app_rows = [], []
    for d in to_fetch:
        usm = int(d["id"])
        m = fetch(f"https://understat.com/getMatchData/{usm}",
                  referer=f"https://understat.com/match/{usm}")
        time.sleep(0.35)
        fid = fx_by_us[usm]
        for side in ("h", "a"):
            for s in m["shots"].get(side, []):
                pid = resolve(s.get("player_id"), s.get("player"))
                aid = resolve(None, s.get("player_assisted")) if s.get("player_assisted") else None
                shot_rows.append((
                    int(s["id"]), usm, fid, pid,
                    int(s["player_id"]) if s.get("player_id") else None,
                    s.get("player"), s.get("player_assisted"), aid,
                    int(s["minute"]), float(s["X"]), float(s["Y"]),
                    float(s["xG"]), s.get("result"), s.get("situation"),
                    s.get("shotType"), s.get("lastAction"),
                    s.get("h_a"), s.get("date")))
            for r in m["rosters"].get(side, {}).values():
                pid = resolve(r.get("player_id"), r.get("player"))
                app_rows.append((
                    fid, int(r["player_id"]), pid, r.get("player"),
                    r.get("h_a"), r.get("position"),
                    int(r.get("time") or 0), int(r.get("goals") or 0),
                    int(r.get("shots") or 0), float(r.get("xG") or 0),
                    float(r.get("xA") or 0), int(r.get("key_passes") or 0),
                    int(r.get("assists") or 0), int(r.get("yellow_card") or 0),
                    int(r.get("red_card") or 0), int(r.get("roster_in") or 0),
                    int(r.get("roster_out") or 0)))
        n_shots += len(m["shots"]["h"]) + len(m["shots"]["a"])
        n_apps += len(m["rosters"]["h"]) + len(m["rosters"]["a"])
    if shot_rows:
        con.executemany(
            "INSERT INTO us_shots (shot_id, us_match_id, fixture_id, player_id, us_player_id,"
            " player_name, assisted_name, assisted_player_id, minute, x, y, xg, result,"
            " situation, shot_type, last_action, h_a, date)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(shot_id) DO UPDATE SET fixture_id=excluded.fixture_id,"
            " player_id=excluded.player_id, assisted_player_id=excluded.assisted_player_id",
            shot_rows)
    if app_rows:
        con.executemany(
            "INSERT INTO us_appearances (fixture_id, us_player_id, player_id, player_name,"
            " h_a, position, minutes, goals, shots, xg, xa, key_passes, assists, yellow,"
            " red, roster_in, roster_out) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(fixture_id, us_player_id) DO UPDATE SET"
            " player_id=excluded.player_id, xg=excluded.xg, xa=excluded.xa,"
            " minutes=excluded.minutes, goals=excluded.goals, shots=excluded.shots",
            app_rows)
    con.commit()
    log(f"matches fetched: {len(to_fetch)} | shots: {n_shots} | appearances: {n_apps}")

    # ---- season totals ----
    METRICS = (("xg", "xG"), ("xa", "xA"), ("npg", "npg"),
               ("shots", "shots"), ("key_passes", "key_passes"))
    fetched = date.today().isoformat()
    season_rows, unres = 0, []
    for p in league["players"]:
        pid = resolve(p["id"], p["player_name"])
        if pid is None:
            unres.append(p["player_name"])
            continue
        for metric, key in METRICS:
            v = p.get(key)
            if v in (None, ""):
                continue
            con.execute(
                "INSERT INTO season_stats (player_id, season, source, metric, value,"
                " fetched_at) VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(player_id, season, source, metric)"
                " DO UPDATE SET value=excluded.value, fetched_at=excluded.fetched_at",
                (pid, SEASON, "understat", metric, float(v), fetched))
            season_rows += 1
    con.commit()

    # ---- report ----
    tot_shots = con.execute("SELECT COUNT(*) FROM us_shots").fetchone()[0]
    res_shots = con.execute(
        "SELECT COUNT(*) FROM us_shots WHERE player_id IS NOT NULL").fetchone()[0]
    tot_apps = con.execute("SELECT COUNT(*) FROM us_appearances").fetchone()[0]
    linked_fx = con.execute(
        "SELECT COUNT(*) FROM fixtures WHERE us_match_id IS NOT NULL").fetchone()[0]
    log(f"season stats rows: {season_rows} for {len(league['players']) - len(unres)}"
        f"/{len(league['players'])} understat players"
        + (f" | unresolved sample: {unres[:6]}" if unres else ""))
    log(f"DB totals: us_shots={tot_shots} ({round(100*res_shots/max(tot_shots,1),1)}%"
        f" resolved), us_appearances={tot_apps}, fixtures linked={linked_fx}")
    con.close()
    return {"season_rows": season_rows, "new_matches": len(to_fetch),
            "shots": tot_shots, "appearances": tot_apps,
            "linked": linked_fx, "unresolved_players": len(unres)}


if __name__ == "__main__":
    run()
