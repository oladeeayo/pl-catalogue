"""Seed season_stats from the FBref + Understat data we already scraped.

Resolution chain per player row:
  1. FBref name -> _fbref_ids.csv (key_fbref) -> Reep -> key_opta_numeric -> FPL code
  2. Reep name / full_name -> normalized FPL name match
  3. FBref name -> normalized FPL name match (first+second / first+web / web)
Local only, idempotent.
"""
import html
import re
import sqlite3
import unicodedata
from datetime import date
from pathlib import Path

import pandas as pd

DB = Path(__file__).parent / "catalogue.db"
ROOT = Path(__file__).parent.parent
SEASON = "2026-27"

LETTERS = {"ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "ł": "l", "Ł": "L",
           "æ": "ae", "Æ": "AE", "ß": "ss", "œ": "oe", "Œ": "OE"}


def norm(s):
    s = html.unescape(str(s or "")).strip()
    for k, v in LETTERS.items():
        s = s.replace(k, v)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def run(verbose=True):
    def log(m):
        if verbose:
            print(m, flush=True)

    con = sqlite3.connect(DB)
    stats_path = ROOT / "pl_sot_xg_all_matched.csv"
    if not stats_path.exists():
        log(f"SKIP stats_seed: {stats_path.name} missing (see README data setup); "
            "keeping existing season_stats")
        con.close()
        return {"rows": 0, "players": 0, "unmatched": 0, "skipped": True}
    stats = pd.read_csv(stats_path, encoding="utf-8")
    fbids = (pd.read_csv(ROOT / "_fbref_ids.csv", encoding="utf-8")
             if (ROOT / "_fbref_ids.csv").exists() else None)
    rp_path = ROOT / "reep_people.csv"
    rp = (pd.read_csv(rp_path, dtype=str, low_memory=False)
          if rp_path.exists() else None)
    if fbids is None or rp is None:
        log("WARN stats_seed: missing _fbref_ids.csv and/or reep_people.csv - "
            "will name-match only")
    fb2row = {}
    if rp is not None:
        rp = rp[rp["type"] == "player"]
        for _, r in rp[rp["key_fbref"].notna() & (rp["key_fbref"].str.strip() != "")].iterrows():
            fb2row.setdefault(r["key_fbref"].strip(), r)
        del rp

    players = pd.read_sql(
        "SELECT player_id, fpl_code, first_name, second_name, web_name FROM players", con)
    by_code = {}
    by_name = {}
    by_last = {}
    first_tok = {}
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

    # non-prefix nickname pairs (extendable)
    NICK_FIRST = {"kostas": "konstantinos"}

    def prefix_pair(a, b):
        if a == b:
            return True
        if min(len(a), len(b)) < 3:
            return False
        return a.startswith(b) or b.startswith(a)

    def loose(fbname):
        """Last-name + first-name-prefix heuristic; only exact-unique hits."""
        toks = fbname.split()
        if len(toks) < 2:
            return None
        t_first = NICK_FIRST.get(toks[0], toks[0])
        hits = {pid for pid in by_last.get(toks[-1], set())
                if prefix_pair(t_first, first_tok.get(pid, ""))}
        return next(iter(hits)) if len(hits) == 1 else None


    name2fbid = {}
    if fbids is not None:
        for _, r in fbids.iterrows():
            name2fbid.setdefault(norm(r["name_html"]), r["key_fbref"])

    def pick(keys):
        """Return (pid, via) - via in {'id','name'} or (None, 'ambiguous'|'no match')."""
        ambiguous = False
        for k in keys:
            if not k:
                continue
            cands = set(by_name.get(k, []))
            if len(cands) == 1:
                return next(iter(cands)), "name"
            if len(cands) > 1:
                ambiguous = True
        return None, ("ambiguous" if ambiguous else "no match")

    fetched = date.today().isoformat()
    n_id = n_name = n_loose = 0
    no_match = []
    loose_hits = []
    upserts = []
    METRICS = [("fbref", "nineties", "90s"), ("fbref", "goals", "Gls"),
               ("fbref", "shots", "Sh"), ("fbref", "sot", "SoT"),
               ("fbref", "sot_pct", "SoT%"), ("understat", "xg", "xG")]

    for _, r in stats.iterrows():
        pid, via = None, "no match"
        fbname = norm(r["Player"])
        fbid = name2fbid.get(fbname)
        rrow = fb2row.get(str(fbid).strip()) if fbid is not None else None
        # 1) exact id chain
        if rrow is not None:
            code = rrow.get("key_opta_numeric")
            if pd.notna(code) and str(code).strip() != "":
                pid = by_code.get(str(int(float(code))))
                if pid is not None:
                    via = "id"
        # 2) reep name bridge, then 3) direct fbref name
        if pid is None:
            keys = []
            if rrow is not None:
                for col in ("name", "full_name"):
                    v = rrow.get(col)
                    if pd.notna(v) and str(v).strip() and str(v).strip().lower() != "nan":
                        keys.append(norm(v))
            keys.append(fbname)
            pid, via = pick(keys)
        # 4) conservative surname + first-name-prefix heuristic
        if pid is None:
            pid = loose(fbname)
            if pid is not None:
                via = "loose"
                loose_hits.append((r["Player"], pid))
        if pid is None:
            no_match.append((r["Player"], via))
            continue
        if via == "id":
            n_id += 1
        elif via == "loose":
            n_loose += 1
        else:
            n_name += 1
        for source, metric, col in METRICS:
            v = r.get(col)
            if pd.notna(v):
                upserts.append((int(pid), SEASON, source, metric, float(v), fetched))

    con.executemany(
        """INSERT INTO season_stats (player_id, season, source, metric, value, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(player_id, season, source, metric)
           DO UPDATE SET value=excluded.value, fetched_at=excluded.fetched_at""",
        upserts,
    )
    con.commit()
    n_players = len({u[0] for u in upserts})
    log(f"stats rows: {len(upserts)} for {n_players}/{len(stats)} players "
        f"(id-chain {n_id}, name {n_name}, loose {n_loose}, unmatched {len(no_match)})")
    if no_match[:10]:
        log(f"unmatched sample: {no_match[:10]}")
    if loose_hits:
        log(f"loose matches: {loose_hits}")
    con.close()
    return {"rows": len(upserts), "players": n_players, "unmatched": len(no_match)}


if __name__ == "__main__":
    run()
