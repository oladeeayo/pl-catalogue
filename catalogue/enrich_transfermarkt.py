"""Transfermarkt enrichment: market value, contract, physicals per player.

Requests-only (no browser). Source: 20 per-club squad pages linked from the
league startseite, saison_id taken from the links themselves. Idempotent,
point-in-time rows keyed (player_id, fetch_date).

Identity: TM player id -> Reep key_transfermarkt -> FPL code (primary),
name fallback via matching.FPLIndex; FPL/TM date-of-birth cross-check.
"""
import re
import sqlite3
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from matching import FPLIndex, norm

DB = Path(__file__).parent / "catalogue.db"
ROOT = Path(__file__).parent.parent
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}

TM_SCHEMA = """
CREATE TABLE IF NOT EXISTS player_market (
    player_id       INTEGER NOT NULL REFERENCES players(player_id),
    fetch_date      TEXT NOT NULL,               -- YYYY-MM-DD
    tm_id           INTEGER,
    tm_name         TEXT,                        -- as listed by Transfermarkt
    source_club     INTEGER,                     -- FPL club of the squad page
    market_value_eur REAL,
    contract_until  TEXT,                        -- ISO date
    joined_date     TEXT,                        -- ISO date
    height_cm       INTEGER,
    foot            TEXT,
    dob_tm          TEXT,                        -- ISO, cross-check vs FPL
    age_at_fetch    INTEGER,
    position_tm     TEXT,
    PRIMARY KEY (player_id, fetch_date)
);
CREATE INDEX IF NOT EXISTS idx_market_date ON player_market(fetch_date);
"""

# TM club title (normalized, fc/afc stripped) -> FPL club name (normalized)
TM_CLUB_ALIASES = {
    "tottenham hotspur": "spurs",
    "manchester city": "man city",
    "manchester united": "man utd",
    "newcastle united": "newcastle",
    "nottingham forest": "nott m forest",
    "brighton hove albion": "brighton",
    "leeds united": "leeds",
    "wolverhampton wanderers": "wolverhampton wanderers",
}

# verified full-name fixes (TM spelling vs FPL spelling)
NAME_FIXES = {"yegor yarmolyuk": "yehor yarmoliuk"}


def _fpl_club_id(title, fpl_norm):
    n = norm(title)
    words = n.split()
    while words and words[0] in ("afc", "fc"):
        words = words[1:]
    while words and words[-1] in ("fc", "afc"):
        words = words[:-1]
    n = " ".join(words)
    cid = fpl_norm.get(TM_CLUB_ALIASES.get(n, n))
    if cid is None:
        raise RuntimeError(f"unknown TM club title: {title!r} (norm {n!r})")
    return cid


def fetch(url, tries=3):
    last = None
    for attempt in range(tries):
        try:
            r = requests.get(url, headers=H, timeout=30)
            r.raise_for_status()
            return r.text
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1 + 2 * attempt)
    raise RuntimeError(f"transfermarkt fetch failed {url}: {last}")


def _iso(dmY):
    d, m, y = dmY.split("/")
    return f"{y}-{m}-{d}"


def parse_squad(t):
    """Parse a kader/plus/1 page into raw player dicts."""
    links = list(re.finditer(r'/profil/spieler/(\d+)"', t))
    out = []
    for i, m in enumerate(links):
        tid = int(m.group(1))
        seg = t[m.start(): links[i + 1].start() if i + 1 < len(links) else len(t)]
        nm = re.search(rf'/profil/spieler/{tid}"[^>]*>\s*([^<]+?)\s*</a>', seg)
        name = nm.group(1).strip() if nm else None
        if not name:
            sm = re.search(rf'/([a-z0-9-]+)/(?:profil|marktwertverlauf)/spieler/{tid}', seg)
            if sm:
                name = sm.group(1).replace("-", " ").strip().title()
        dob_m = re.search(r'(\d{2})/(\d{2})/(\d{4})\s*\((\d+)\)', seg)
        h_m = re.search(r'>?(\d),(\d{2})m<', seg)
        foot_m = re.search(r'<td class="zentriert">(right|left|both)</td>', seg)
        dates = re.findall(r'<td class="zentriert">(\d{2}/\d{2}/\d{4})</td>', seg)
        mv_m = re.search(
            rf'marktwertverlauf/spieler/{tid}"[^>]*>\s*[€$£]\s?([\d.,]+)\s?(m|k|bn)\s*</a>', seg)
        pos_m = re.search(
            r'</a>\s*</td>\s*</tr>\s*<tr>\s*<td>\s*([A-Za-z][A-Za-z -]*?)\s*</td>', seg)
        value = None
        if mv_m:
            num = float(mv_m.group(1).replace(",", ""))
            value = num * {"m": 1e6, "k": 1e3, "bn": 1e9}[mv_m.group(2)]
        out.append({
            "tm_id": tid,
            "name": name,
            "dob": f"{dob_m.group(3)}-{dob_m.group(2)}-{dob_m.group(1)}" if dob_m else None,
            "age": int(dob_m.group(4)) if dob_m else None,
            "height_cm": int(h_m.group(1)) * 100 + int(h_m.group(2)) if h_m else None,
            "foot": foot_m.group(1) if foot_m else None,
            "joined": _iso(dates[0]) if len(dates) > 0 else None,
            "contract_until": _iso(dates[1]) if len(dates) > 1 else None,
            "market_value_eur": value,
            "position": pos_m.group(1).strip() if pos_m else None,
        })
    return out


def run(verbose=True):
    def log(m):
        if verbose:
            print(m, flush=True)

    con = sqlite3.connect(DB)
    con.executescript(TM_SCHEMA)

    def has_col(table, col):
        return any(r[1] == col for r in con.execute(f"PRAGMA table_info({table})"))
    if not has_col("player_market", "tm_name"):
        con.execute("ALTER TABLE player_market ADD COLUMN tm_name TEXT")
    if not has_col("player_market", "source_club"):
        con.execute("ALTER TABLE player_market ADD COLUMN source_club INTEGER")
    con.commit()

    # ---- club squad pages (title attr carries the club name) ----
    start = fetch("https://www.transfermarkt.com/premier-league/startseite/wettbewerb/GB1")
    clubs = {}
    for title, slug, vid, saison in re.findall(
            r'title="([^"]+)"\s+href="/([^/]+)/kader/verein/(\d+)/saison_id/(\d+)', start):
        clubs[vid] = (slug, saison, title)
    if len(clubs) != 20:
        raise RuntimeError(f"expected 20 TM clubs, found {len(clubs)}")
    fpl_norm = {norm(n): cid for cid, n in con.execute("SELECT club_id, name FROM clubs")}
    title2club = {vid: _fpl_club_id(t, fpl_norm) for vid, (_, _, t) in clubs.items()}

    # ---- identity indexes ----
    idx = FPLIndex(con=con)
    by_tm = {}
    rp_path = ROOT / "reep_people.csv"
    if rp_path.exists():
        rp = pd.read_csv(rp_path, dtype=str, low_memory=False,
                         usecols=["type", "key_transfermarkt", "key_opta_numeric"])
        rp = rp[rp["type"] == "player"]
        for _, r in rp.iterrows():
            kt, ko = r["key_transfermarkt"], r["key_opta_numeric"]
            if pd.notna(kt) and kt.strip() and pd.notna(ko) and ko.strip():
                pid = idx.by_code.get(str(int(float(ko))))
                if pid:
                    by_tm.setdefault(kt.strip(), pid)
        del rp
    else:
        log("WARN enrich_transfermarkt: reep_people.csv missing - resolving by name only")
    fpl_dob = {int(pid): dob for pid, dob in con.execute(
        "SELECT player_id, date_of_birth FROM players WHERE date_of_birth IS NOT NULL")}
    fpl_club = {int(pid): cid for pid, cid in con.execute(
        "SELECT player_id, club_id FROM players WHERE club_id IS NOT NULL")}

    fetched = date.today().isoformat()
    n_tm = matched = n_id = n_name = 0
    dob_checks = dob_bad = 0
    no_match, rows = [], []
    club_rejected = []
    for vid, (slug, saison, title) in sorted(clubs.items()):
        source_club = title2club[vid]
        url = (f"https://www.transfermarkt.com/{slug}/kader/verein/{vid}/"
               f"saison_id/{saison}/plus/1")
        squad = parse_squad(fetch(url))
        log(f"  {title}: {len(squad)} players")
        for p in squad:
            n_tm += 1
            raw = p["name"]
            fixed = NAME_FIXES.get(norm(raw))
            pname = fixed or raw
            pid = by_tm.get(str(p["tm_id"]))
            via = "id" if pid else None
            if pid is None:
                pid = idx.resolve_name(pname)
                via = "name" if pid else None
            if pid is None:
                no_match.append(raw)
                continue
            # club-consistency veto: TM squad page club must equal FPL club
            if fpl_club.get(pid) != source_club:
                club_rejected.append((raw, p["tm_id"], title))
                continue
            matched += 1
            if via == "id":
                n_id += 1
            else:
                n_name += 1
            if pid in fpl_dob and p["dob"]:
                dob_checks += 1
                if fpl_dob[pid] != p["dob"]:
                    dob_bad += 1
            rows.append((pid, fetched, p["tm_id"], raw, source_club,
                         p["market_value_eur"], p["contract_until"], p["joined"],
                         p["height_cm"], p["foot"], p["dob"], p["age"],
                         p["position"]))
        time.sleep(0.3)
    idx.close()

    # today's snapshot is replaced wholesale (historical days untouched)
    con.execute("DELETE FROM player_market WHERE fetch_date=?", (fetched,))
    con.executemany(
        "INSERT INTO player_market (player_id, fetch_date, tm_id, tm_name, source_club,"
        " market_value_eur, contract_until, joined_date, height_cm, foot, dob_tm,"
        " age_at_fetch, position_tm) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(player_id, fetch_date) DO UPDATE SET"
        " tm_id=excluded.tm_id, tm_name=excluded.tm_name, source_club=excluded.source_club,"
        " market_value_eur=excluded.market_value_eur,"
        " contract_until=excluded.contract_until, joined_date=excluded.joined_date,"
        " height_cm=excluded.height_cm, foot=excluded.foot, dob_tm=excluded.dob_tm,"
        " age_at_fetch=excluded.age_at_fetch, position_tm=excluded.position_tm",
        rows)
    con.commit()

    n_val = con.execute(
        "SELECT COUNT(*) FROM player_market WHERE fetch_date=? AND market_value_eur IS NOT NULL",
        (fetched,)).fetchone()[0]
    total_eur = con.execute(
        "SELECT COALESCE(SUM(market_value_eur),0) FROM player_market WHERE fetch_date=?",
        (fetched,)).fetchone()[0]
    dob_pct = round(100 * (dob_checks - dob_bad) / dob_checks, 1) if dob_checks else 100.0
    log(f"clubs: {len(clubs)} | tm players: {n_tm} | matched: {matched} "
        f"(id {n_id}, name {n_name}) | unmatched: {len(no_match)}"
        + (f" sample: {no_match[:6]}" if no_match else ""))
    if club_rejected:
        log(f"club veto rejected: {len(club_rejected)} sample: {club_rejected[:4]}")
    log(f"market values: {n_val} players, total \u20ac{total_eur/1e9:.1f}bn | "
        f"DOB agreement: {dob_pct}% ({dob_bad} mismatches of {dob_checks} - "
        f"FPL DOB data is sometimes wrong; QC only)")
    con.close()
    return {"tm_players": n_tm, "matched": matched, "values": n_val,
            "dob_mismatch": dob_bad, "dob_checks": dob_checks,
            "club_rejected": len(club_rejected)}


if __name__ == "__main__":
    run()
