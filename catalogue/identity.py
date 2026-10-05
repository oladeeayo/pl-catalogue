"""Reep identity step: link FPL players to Reep IDs, bio and provider keys.

Join order:
  1. key_opta_numeric (Reep) == fpl_code (FPL)  - exact, ~70% coverage
  2. normalized name match within Reep's PL-registered players - fallback
Local only (reads reep_people.csv), no network. Idempotent.
"""
import html
import re
import sqlite3
import unicodedata
from pathlib import Path

import pandas as pd

DB = Path(__file__).parent / "catalogue.db"
REEP = Path(__file__).parent.parent / "sources" / "reep_people.csv"

LETTERS = {"ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "ł": "l", "Ł": "L",
           "æ": "ae", "Æ": "AE", "ß": "ss", "œ": "oe", "Œ": "OE", "ð": "d", "þ": "th"}


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
    if not REEP.exists():
        log(f"SKIP identity: {REEP.name} missing (see README data setup) - "
            "identity coverage will fail validation until it is placed")
        con.close()
        return {"code": 0, "name": 0, "unmatched": 0, "skipped": True}
    rp = pd.read_csv(REEP, dtype=str, low_memory=False)
    rp = rp[rp["type"] == "player"].copy()
    has_pl = (rp["key_premier_league"].notna()) & (rp["key_premier_league"].astype(str).str.strip() != "")
    has_fb = (rp["key_fbref"].notna()) & (rp["key_fbref"].astype(str).str.strip() != "")
    has_tm = (rp["key_transfermarkt"].notna()) & (rp["key_transfermarkt"].astype(str).str.strip() != "")
    has_us = (rp["key_understat"].notna()) & (rp["key_understat"].astype(str).str.strip() != "")
    cand = rp[has_pl | has_fb | has_tm | has_us].copy()
    log(f"reep players: {len(rp)} | candidate universe: {len(cand)}")

    fpl = pd.read_sql("SELECT player_id, fpl_code, first_name, second_name, web_name FROM players", con)
    code_map = {}
    for _, r in rp[rp["key_opta_numeric"].notna()
                   & (rp["key_opta_numeric"].astype(str).str.strip() != "")].iterrows():
        code_map[str(r["key_opta_numeric"]).strip()] = r

    # candidate universe for name fallback, keyed by name + full_name
    rows_by_name = {}
    for _, r in cand.iterrows():
        rows_by_name.setdefault(norm(r["name"]), []).append(r)
        fn = norm(r["full_name"]) if pd.notna(r["full_name"]) else ""
        if fn:
            rows_by_name.setdefault(fn, []).append(r)

    n_code = n_name = 0
    updates = []
    unmatched = []
    for _, p in fpl.iterrows():
        r = None
        if pd.notna(p["fpl_code"]):
            r = code_map.get(str(int(p["fpl_code"])))
            if r is not None:
                n_code += 1
        if r is None:
            full = norm(f"{p['first_name']} {p['second_name']}")
            fw = norm(f"{p['first_name']} {p['web_name']}")
            web = norm(p["web_name"])
            cands = rows_by_name.get(full) or rows_by_name.get(fw) or rows_by_name.get(web) or []
            uniq = {x["reep_id"]: x for x in cands}
            if len(uniq) == 1:
                r = next(iter(uniq.values()))
                n_name += 1
            elif len(uniq) > 1:
                unmatched.append((p["player_id"], p["web_name"], "ambiguous"))
                continue
        if r is None:
            unmatched.append((p["player_id"], p["web_name"], "no match"))
            continue
        def g(col):
            v = r.get(col)
            if pd.isna(v) or str(v).strip() == "":
                return None
            return str(v).strip()
        updates.append((
            g("reep_id"), g("full_name"), g("date_of_birth"), g("nationality"),
            g("height_cm"), g("position_detail"),
            g("key_wikidata"), g("key_fbref"), g("key_understat"),
            g("key_transfermarkt"), g("key_sofascore"), g("key_fotmob"),
            g("key_premier_league"), g("key_whoscored"),
            int(p["player_id"]),
        ))

    con.executemany(
        """UPDATE players SET
             reep_id=?, full_name=?,
             date_of_birth=COALESCE(?, date_of_birth),
             nationality=COALESCE(?, nationality),
             height_cm=COALESCE(?, height_cm),
             position_detail=COALESCE(?, position_detail),
             key_wikidata=?, key_fbref=?, key_understat=?,
             key_transfermarkt=?, key_sofascore=?, key_fotmob=?,
             key_premier_league=?, key_whoscored=?
           WHERE player_id=?""",
        updates,
    )
    con.commit()
    total = len(fpl)
    log(f"matched: {n_code} by code + {n_name} by name = {n_code + n_name}/{total} "
        f"({round(100 * (n_code + n_name) / total, 1)}%)")
    log(f"unmatched: {len(unmatched)}")
    con.close()
    return {"code": n_code, "name": n_name, "unmatched": len(unmatched)}


if __name__ == "__main__":
    run()
