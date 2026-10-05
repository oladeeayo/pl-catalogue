"""Fetch Transfermarkt's PL transfer history (league transfers page, per season).

Reads the league-level transfers page once per season window (saison_id =
season start year), parses every club box's In/Out rows, matches players to
FPL ids via players.key_transfermarkt / player_market.tm_id, and fully
replaces transfer_history rows for the seasons fetched successfully.

Local fetch, idempotent per season, non-fatal per season (a failed season
keeps its existing rows).
"""
import re
import sqlite3
import time
from datetime import date, datetime, timezone
from pathlib import Path

import lxml.html as LH
import requests

DB = Path(__file__).parent / "catalogue.db"
SEASONS = [2026, 2025, 2024, 2023, 2022]   # TM saison_id (= season start year)
URL = ("https://www.transfermarkt.com/premier-league/transfers/"
       "wettbewerb/GB1/saison_id/{s}")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}


def parse_amount(text):
    """'€2.80m' -> 2800000.0, '€800k' -> 800000.0, '€1,500,000' -> 1500000.0."""
    if not text:
        return None
    m = re.search(r"([\d.,]+)\s*([mkb])?", text.replace("\xa0", " "), re.I)
    if not m:
        return None
    num, suffix = m.group(1), (m.group(2) or "").lower()
    try:
        if suffix:
            base = float(num.replace(",", ""))
            return base * {"m": 1e6, "k": 1e3, "b": 1e9}[suffix]
        return float(num.replace(",", "").replace(".", ""))
    except ValueError:
        return None


def parse_fee(text):
    t = re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip().lower()
    if not t or t in ("-", "?", "€-"):
        return None, "unknown"
    if "free transfer" in t:
        return 0.0, "free"
    if "end of loan" in t:
        return None, "end_loan"
    if "loan fee" in t:
        v = parse_amount(t)
        return v, "loan_fee" if v else "unknown"
    if "loan" in t:
        return None, "loan"
    v = parse_amount(t)
    if v:
        return v, "permanent"
    return None, "unknown"


def cell_text(td):
    return re.sub(r"\s+", " ", td.text_content()).strip()


def parse_page(html, saison_id, fetched_at):
    doc = LH.fromstring(html)
    rows = []
    for box in doc.cssselect("div.box"):
        h2s = box.cssselect('h2[id^=to-]')
        if not h2s:
            continue
        h2 = h2s[0]
        m = re.match(r"to-(\d+)", h2.get("id") or "")
        tm_club_id = int(m.group(1)) if m else None
        anchors = h2.cssselect("a")
        club_name = anchors[-1].text_content().strip() if anchors else \
            (h2.get("title") or h2.text_content()).strip()
        tables = box.cssselect("table")
        if not tables:
            continue
        direction, idx = "in", 0
        for tr in tables[0].iter("tr"):
            ths = tr.cssselect("th")
            if ths:
                t = cell_text(ths[0]).lower()
                if t in ("in", "out"):
                    direction = t
                continue
            player_links = [a for a in tr.cssselect("td a")
                            if "/profil/spieler/" in (a.get("href") or "")]
            if not player_links:
                t = cell_text(tr).lower()
                if t in ("in", "out"):
                    direction = t
                continue
            a = player_links[0]
            pm = re.search(r"/profil/spieler/(\d+)", a.get("href") or "")
            if not pm:
                continue
            idx += 1
            tm_id = int(pm.group(1))
            player_name = a.get("title") or a.text_content().strip()

            def find_td(frag):
                for td in tr.cssselect("td"):
                    cls = td.get("class") or ""
                    if frag in cls:
                        return td
                return None

            age_t = find_td("alter-transfer-cell")
            pos_t = find_td("pos-transfer-cell")
            mv_t = find_td("mw-transfer-cell")
            age = None
            if age_t is not None:
                am = re.search(r"\d+", cell_text(age_t))
                age = int(am.group(0)) if am else None
            club_tds = [td for td in tr.cssselect("td")
                        if "verein-flagge-transfer-cell" in (td.get("class") or "")]
            titles = []
            for td in club_tds:
                links = td.cssselect("a[title]")
                titles.append(links[0].get("title").strip()
                              if links else cell_text(td))
            if direction == "in":
                to_club = club_name
                from_club = next((t for t in titles if t != club_name),
                                 titles[0] if titles else None)
            else:
                from_club = club_name
                to_club = next((t for t in titles if t != club_name),
                               titles[-1] if titles else None)

            fee_text, transfer_id, fee_eur, fee_type = None, None, None, "unknown"
            for td in tr.cssselect("td"):
                for link in td.cssselect('a[href*="jumplist/transfers"]'):
                    fee_text = cell_text(td)
                    fm = re.search(r"transfer_id/(\d+)", link.get("href") or "")
                    if fm:
                        transfer_id = int(fm.group(1))
            if fee_text is None:
                for td in tr.cssselect("td.rechts"):
                    t = cell_text(td)
                    if t and ("free" in t.lower() or "loan" in t.lower()
                              or "€" in t):
                        fee_text = t
                        break
            if fee_text is not None:
                fee_eur, fee_type = parse_fee(fee_text)

            rows.append({
                "row_key": f"{saison_id}:{tm_club_id}:{direction}:{idx}",
                "saison_id": saison_id, "tm_club_id": tm_club_id,
                "club_name": club_name, "direction": direction, "tm_id": tm_id,
                "player_name": player_name, "age": age,
                "position": cell_text(pos_t) if pos_t is not None else None,
                "market_value_eur": parse_amount(cell_text(mv_t))
                if mv_t is not None else None,
                "from_club": from_club, "to_club": to_club,
                "fee_eur": fee_eur, "fee_text": fee_text, "fee_type": fee_type,
                "transfer_id": transfer_id, "fetched_at": fetched_at,
            })
    return rows


def main(verbose=True):
    def log(m):
        if verbose:
            print(m, flush=True)

    con = sqlite3.connect(DB)
    # tm_id -> player_id maps from identity keys and market enrichments
    tm_to_pid = {}
    for pid, kt in con.execute(
            "SELECT player_id, key_transfermarkt FROM players"
            " WHERE key_transfermarkt IS NOT NULL"):
        d = re.search(r"(\d+)\s*$", str(kt).strip())
        if d:
            tm_to_pid.setdefault(int(d.group(1)), pid)
    for pid, tm_id in con.execute(
            "SELECT player_id, tm_id FROM player_market WHERE tm_id IS NOT NULL"):
        tm_to_pid.setdefault(int(tm_id), pid)

    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ok_saisons, total, matched = [], 0, 0
    for s in SEASONS:
        try:
            r = requests.get(URL.format(s=s), headers=H, timeout=30)
            r.raise_for_status()
            rows = parse_page(r.text, s, fetched_at)
        except Exception as e:  # noqa: BLE001 - keep other seasons alive
            log(f"saison {s}: FAILED ({type(e).__name__}: {e}) - keeping existing rows")
            time.sleep(1.0)
            continue
        for row in rows:
            row["player_id"] = tm_to_pid.get(row["tm_id"])
        n_match = sum(1 for r0 in rows if r0["player_id"] is not None)
        con.execute("DELETE FROM transfer_history WHERE saison_id=?", (s,))
        con.executemany(
            """INSERT INTO transfer_history
               (row_key, saison_id, tm_club_id, club_name, direction, tm_id,
                player_name, age, position, market_value_eur, from_club,
                to_club, fee_eur, fee_text, fee_type, transfer_id, player_id,
                fetched_at)
               VALUES (:row_key, :saison_id, :tm_club_id, :club_name, :direction,
                       :tm_id, :player_name, :age, :position, :market_value_eur,
                       :from_club, :to_club, :fee_eur, :fee_text, :fee_type,
                       :transfer_id, :player_id, :fetched_at)""", rows)
        con.commit()
        ok_saisons.append(s)
        total += len(rows)
        matched += n_match
        log(f"saison {s}: {len(rows)} rows, {n_match} matched to FPL players")
        time.sleep(0.6)

    log(f"transfer_history: {total} rows over seasons {ok_saisons}, "
        f"{matched} matched")
    con.close()
    return {"rows": total, "seasons": ok_saisons, "matched": matched}


if __name__ == "__main__":
    main()
