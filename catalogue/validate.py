"""Catalogue validation: structural checks, per-GW completeness, coverage.

Writes catalogue/validation_report.txt and returns a list of FAIL lines
(empty = healthy). Exits 1 on any FAIL when run as a script.
"""
import sqlite3
from datetime import date
from pathlib import Path

DB = Path(__file__).parent / "catalogue.db"
OUT = Path(__file__).parent / "validation_report.txt"

TABLES = ["clubs", "players", "gws", "fixtures", "player_fixture",
          "player_snapshot", "player_season_prior", "season_stats"]


def run(verbose=True):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    lines = [f"PL catalogue validation - {date.today().isoformat()}", "=" * 60]
    fails = []

    def check(name, ok, detail):
        lines.append(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        if not ok:
            fails.append(name)

    # --- structural counts ---
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
    lines.append("row counts: " + ", ".join(f"{t}={n}" for t, n in counts.items()))
    lines.append("")
    check("clubs", counts["clubs"] == 20, f"{counts['clubs']} (expect 20)")
    check("gws", counts["gws"] == 38, f"{counts['gws']} (expect 38)")
    check("fixtures", counts["fixtures"] == 380, f"{counts['fixtures']} (expect 380)")
    check("players", counts["players"] >= 600, f"{counts['players']} (expect ~667)")
    check("player_fixture has rows", counts["player_fixture"] > 0, str(counts["player_fixture"]))

    # --- referential integrity ---
    orphan_fx = con.execute(
        "SELECT COUNT(*) FROM player_fixture pf LEFT JOIN fixtures f "
        "ON pf.fixture_id=f.fixture_id WHERE f.fixture_id IS NULL").fetchone()[0]
    orphan_pl = con.execute(
        "SELECT COUNT(*) FROM player_fixture pf LEFT JOIN players p "
        "ON pf.player_id=p.player_id WHERE p.player_id IS NULL").fetchone()[0]
    no_club = con.execute("SELECT COUNT(*) FROM players WHERE club_id IS NULL").fetchone()[0]
    no_kickoff_gw = con.execute(
        "SELECT COUNT(*) FROM fixtures WHERE gw IS NULL").fetchone()[0]
    check("player_fixture->fixtures", orphan_fx == 0, f"{orphan_fx} orphan rows")
    check("player_fixture->players", orphan_pl == 0, f"{orphan_pl} orphan rows")
    check("players have club", no_club == 0, f"{no_club} without club")
    check("fixtures scheduled", no_kickoff_gw == 0, f"{no_kickoff_gw} without gw")

    # --- per-GW completeness ---
    lines.append("")
    lines.append("per-GW detail:")
    fin = con.execute("SELECT gw FROM gws WHERE finished=1 ORDER BY gw").fetchall()
    finished_gws = [r["gw"] for r in fin]
    for gw in finished_gws:
        nfx = con.execute("SELECT COUNT(*) FROM fixtures WHERE gw=? AND finished=1", (gw,)).fetchone()[0]
        nrows = con.execute("SELECT COUNT(*) FROM player_fixture WHERE gw=?", (gw,)).fetchone()[0]
        xg = con.execute("SELECT COALESCE(SUM(expected_goals),0) FROM player_fixture WHERE gw=?", (gw,)).fetchone()[0]
        pts = con.execute("SELECT COALESCE(SUM(total_points),0) FROM player_fixture WHERE gw=?", (gw,)).fetchone()[0]
        ok = nfx > 0 and nrows > 0
        check(f"GW{gw}", ok, f"{nfx} fixtures, {nrows} player rows, {xg:.1f} xG, {pts} pts")
    # every finished fixture's GW covered?
    check("finished fixtures in player_fixture",
          con.execute(
              "SELECT COUNT(*) FROM fixtures f WHERE f.finished=1 AND NOT EXISTS "
              "(SELECT 1 FROM player_fixture pf WHERE pf.fixture_id=f.fixture_id)"
          ).fetchone()[0] == 0,
          "finished fixtures with zero player rows (bench-only matches can be legit - inspect)")

    # --- identity + enrichment coverage ---
    lines.append("")
    tot = counts["players"] or 1
    for col in ["reep_id", "key_fbref", "key_understat", "key_transfermarkt", "date_of_birth", "nationality"]:
        n = con.execute(f"SELECT COUNT(*) FROM players WHERE {col} IS NOT NULL").fetchone()[0]
        pct = round(100 * n / tot, 1)
        lines.append(f"coverage {col}: {n}/{tot} ({pct}%)")
    n_stats = con.execute("SELECT COUNT(DISTINCT player_id) FROM season_stats").fetchone()[0]
    lines.append(f"coverage season_stats players: {n_stats}/{tot} ({round(100*n_stats/tot,1)}%)")
    n_snap = con.execute("SELECT COUNT(*) FROM player_snapshot WHERE fetch_date=(SELECT MAX(fetch_date) FROM player_snapshot)").fetchone()[0]
    lines.append(f"latest snapshot: {n_snap} players")
    check("identity coverage", con.execute(
        "SELECT COUNT(*) FROM players WHERE reep_id IS NOT NULL").fetchone()[0] / tot > 0.7,
        "reep_id >70% required")
    check("season_stats coverage", n_stats / tot > 0.4, "stats >40% required (grows with enrichment)")

    # --- understat layer (enrich_understat) ---
    lines.append("")
    missing = [t for t in ("us_shots", "us_appearances")
               if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                  (t,)).fetchone()]
    if missing:
        check("understat tables", False, f"missing {missing} (enrich_understat not run)")
    else:
        has_us_col = any(r[1] == "us_match_id" for r in con.execute("PRAGMA table_info(fixtures)"))
        fin_fx = con.execute("SELECT COUNT(*) FROM fixtures WHERE finished=1").fetchone()[0]
        linked_fin = con.execute(
            "SELECT COUNT(*) FROM fixtures WHERE finished=1 AND us_match_id IS NOT NULL"
        ).fetchone()[0] if has_us_col else 0
        n_shots = con.execute("SELECT COUNT(*) FROM us_shots").fetchone()[0]
        n_app = con.execute("SELECT COUNT(*) FROM us_appearances").fetchone()[0]
        res = con.execute("SELECT COUNT(*) FROM us_shots WHERE player_id IS NOT NULL").fetchone()[0]
        res_pct = round(100 * res / n_shots, 1) if n_shots else 0.0
        us_xg = con.execute(
            "SELECT COALESCE(SUM(a.xg),0) FROM us_appearances a "
            "JOIN fixtures f ON a.fixture_id=f.fixture_id WHERE f.finished=1").fetchone()[0]
        fpl_xg = con.execute(
            "SELECT COALESCE(SUM(pf.expected_goals),0) FROM player_fixture pf "
            "JOIN fixtures f ON pf.fixture_id=f.fixture_id WHERE f.finished=1").fetchone()[0]
        lines.append(f"understat: us_shots={n_shots} ({res_pct}% resolved), "
                     f"us_appearances={n_app}, finished fixtures linked={linked_fin}/{fin_fx}")
        lines.append(f"xG cross-check (finished): understat={us_xg:.1f} vs "
                     f"FPL/Opta={fpl_xg:.1f} (diff {abs(us_xg - fpl_xg):.1f}, "
                     f"expect same ballpark)")
        check("understat fixture linkage",
              fin_fx == 0 or linked_fin == fin_fx,
              "every finished fixture has us_match_id")
        check("understat shots", n_shots > 0, f"{n_shots} shot rows")
        check("understat shot resolve", res_pct >= 75.0,
              f"{res_pct}% of shots resolve to an FPL player (>=75% required)")

    # --- transfermarkt layer (enrich_transfermarkt) ---
    lines.append("")
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='player_market'"
                       ).fetchone():
        check("market table", False, "missing player_market (enrich_transfermarkt not run)")
    else:
        latest = con.execute("SELECT MAX(fetch_date) FROM player_market").fetchone()[0]
        n_mkt = con.execute(
            "SELECT COUNT(*) FROM player_market WHERE fetch_date=?", (latest,)).fetchone()[0] \
            if latest else 0
        n_val = con.execute(
            "SELECT COUNT(*) FROM player_market WHERE fetch_date=? AND market_value_eur IS NOT NULL",
            (latest,)).fetchone()[0] if latest else 0
        total_eur = con.execute(
            "SELECT COALESCE(SUM(market_value_eur),0) FROM player_market WHERE fetch_date=?",
            (latest,)).fetchone()[0] if latest else 0
        cov = round(100 * n_mkt / tot, 1)
        lines.append(f"market values ({latest}): {n_mkt}/{tot} players ({cov}%), "
                     f"{n_val} valued, total €{total_eur/1e9:.1f}bn")
        check("market coverage", n_mkt / tot >= 0.72,
              f"{cov}% of players in latest snapshot (>=72% required)")
        check("market values present", n_val > 0, f"{n_val} valued rows")

    # --- expected points (xp_model) ---
    lines.append("")
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                       " AND name='xp_projection'").fetchone():
        check("xp table", False, "missing xp_projection (xp_model not run)")
    else:
        n_xp = con.execute("SELECT COUNT(*) FROM xp_projection").fetchone()[0]
        n_xp_players = con.execute(
            "SELECT COUNT(DISTINCT player_id) FROM xp_projection").fetchone()[0]
        n_eligible = con.execute(
            "SELECT COUNT(*) FROM players WHERE removed=0").fetchone()[0]
        bt = con.execute(
            "SELECT AVG(ABS(actual - xp)), COUNT(*) FROM xp_projection"
            " WHERE actual IS NOT NULL AND gw > (SELECT MIN(gw) FROM gws"
            " WHERE finished=1)").fetchone()
        bt_mae, bt_n = bt[0], bt[1]
        nxt = con.execute(
            "SELECT gw FROM gws WHERE finished=0 ORDER BY gw LIMIT 1").fetchone()
        n_next = con.execute(
            "SELECT COUNT(*) FROM xp_projection WHERE gw=?", (nxt["gw"],)).fetchone()[0] \
            if nxt else 0
        lines.append(f"xp_projection: {n_xp} rows, {n_xp_players} players, "
                     f"backtest MAE={None if bt_mae is None else round(bt_mae, 3)} "
                     f"over {bt_n} rows")
        check("xp coverage", n_xp_players == n_eligible,
              f"{n_xp_players}/{n_eligible} eligible players projected")
        check("xp rows", n_xp > 0 and (not nxt or n_next == n_xp_players),
              f"{n_xp} rows; next GW (#{nxt['gw'] if nxt else '-'}) has {n_next}")
        check("xp backtest mae", bt_mae is None or bt_mae < 2.0,
              f"{None if bt_mae is None else round(bt_mae, 3)} (<2.0 required)")

    # --- transfer history (enrich_transfers): informational ---
    lines.append("")
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                       " AND name='transfer_history'").fetchone():
        lines.append("[INFO] transfer_history not present (enrich_transfers not run)")
    else:
        n_th = con.execute("SELECT COUNT(*) FROM transfer_history").fetchone()[0]
        n_saisons = con.execute(
            "SELECT COUNT(DISTINCT saison_id) FROM transfer_history").fetchone()[0]
        n_matched = con.execute(
            "SELECT COUNT(*) FROM transfer_history WHERE player_id IS NOT NULL"
        ).fetchone()[0]
        n_fees = con.execute(
            "SELECT COUNT(*) FROM transfer_history WHERE fee_eur IS NOT NULL"
        ).fetchone()[0]
        lines.append(f"transfer_history: {n_th} rows over {n_saisons} seasons, "
                     f"{n_matched} matched to FPL players, {n_fees} with fees")

    lines.append("")
    lines.append(("ALL CHECKS PASSED" if not fails else f"FAILURES: {', '.join(fails)}"))
    report = "\n".join(lines)
    OUT.write_text(report, encoding="utf-8")
    if verbose:
        print(report, flush=True)
    con.close()
    return fails


if __name__ == "__main__":
    raise SystemExit(1 if run() else 0)
