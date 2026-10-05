"""Expected-points projection (model 'ols-v1').

Predicts FPL points for every (player, gw) of the season; stores the forecast
for future GWs and the actual total for finished ones. One OLS fold per
finished GW trained strictly on earlier GWs, future GWs trained on all
finished GWs (features are always leakage-free).

Features (all scaled by expected minutes share, min_frac):
  const, min_frac, min_frac*base (prior+recent FPL pp90 blend),
  min_frac*xgi90 (rolling Understat xG+xA per90), min_frac*own_attack,
  min_frac*opp_defence (rolling goal rates; the 2026 FPL API ships
  strength_* as 0, so we derive them from finished fixtures),
  min_frac*is_home, pos dummies (GK = base).
Position-default pp90 fallbacks: GK 2.0 / DEF 3.0 / MID 4.5 / FWD 5.0.
Blank GW (no fixture) -> xp 0; negative fits clip to 0.

Writes xp_projection (full replace). Logs beta, train R2, backtest MAE.
"""
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

DB = Path(__file__).parent / "catalogue.db"
MODEL = "ols-v1"
POS_DEF_PP90 = {1: 2.0, 2: 3.0, 3: 4.5, 4: 5.0}
N_HISTORY = 3
XGI_FALLBACK = 0.15          # league-ish xG+xA per90 prior when untracked


def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    club_fx = {}
    for r in con.execute("SELECT gw, home_club, away_club FROM fixtures"):
        club_fx.setdefault(r["home_club"], {})[r["gw"]] = (r["away_club"], 1)
        club_fx.setdefault(r["away_club"], {})[r["gw"]] = (r["home_club"], 0)

    finished_gws = {r["gw"] for r in con.execute("SELECT gw FROM gws WHERE finished=1")}
    all_gws = sorted({r["gw"] for r in con.execute("SELECT gw FROM fixtures")})
    max_gw = max(all_gws, default=38)

    # rolling per-club attack/defence rates as of gw g (finished, gw < g)
    gf_arr, ga_arr, n_arr = {}, {}, {}
    for r in con.execute(
            "SELECT gw, home_club, away_club, home_score, away_score"
            " FROM fixtures WHERE finished=1 AND home_score IS NOT NULL"):
        for cid, gf, ga in ((r["home_club"], r["home_score"], r["away_score"]),
                            (r["away_club"], r["away_score"], r["home_score"])):
            gf_arr.setdefault(cid, np.zeros(max_gw + 2))
            ga_arr.setdefault(cid, np.zeros(max_gw + 2))
            n_arr.setdefault(cid, np.zeros(max_gw + 2))
            gf_arr[cid][r["gw"]] += gf
            ga_arr[cid][r["gw"]] += ga
            n_arr[cid][r["gw"]] += 1

    def prefix(arr):
        out = np.zeros_like(arr)
        for g in range(2, len(out)):
            out[g] = out[g - 1] + arr[g - 1]
        return out

    cum_gf = {cid: prefix(a) for cid, a in gf_arr.items()}
    cum_ga = {cid: prefix(a) for cid, a in ga_arr.items()}
    cum_n = {cid: prefix(a) for cid, a in n_arr.items()}

    league_gf = np.zeros(max_gw + 2)
    league_ga = np.zeros(max_gw + 2)
    league_n = np.zeros(max_gw + 2)
    for cid in cum_gf:
        league_gf += cum_gf[cid]
        league_ga += cum_ga[cid]
        league_n += cum_n[cid]
    with np.errstate(divide="ignore", invalid="ignore"):
        lg_gf_pg = np.where(league_n > 0, league_gf / np.maximum(league_n, 1), 1.0)
        lg_ga_pg = np.where(league_n > 0, league_ga / np.maximum(league_n, 1), 1.0)

    def rates(cid, g):
        n = cum_n.get(cid)
        if n is None or n[g] == 0:
            return 1.0, 1.0
        att = float(np.clip((cum_gf[cid][g] / n[g]) / lg_gf_pg[g], 0.4, 2.5))
        dfn = float(np.clip((cum_ga[cid][g] / n[g]) / lg_ga_pg[g], 0.4, 2.5))
        return att, dfn

    players = {r["player_id"]: r for r in con.execute(
        "SELECT player_id, club_id, element_type, web_name"
        " FROM players WHERE removed=0")}

    hist = {}
    actual = {}
    for r in con.execute(
            "SELECT pf.player_id, pf.gw, pf.minutes, pf.total_points,"
            " f.finished FROM player_fixture pf"
            " JOIN fixtures f ON f.fixture_id = pf.fixture_id"):
        hist.setdefault(r["player_id"], []).append(
            {"gw": r["gw"], "minutes": r["minutes"] or 0,
             "points": r["total_points"] or 0})
    for pid, rows in hist.items():
        rows.sort(key=lambda x: x["gw"])
        for row in rows:
            if row["gw"] in finished_gws:
                actual[(pid, row["gw"])] = float(row["points"])

    us_hist = {}
    for r in con.execute(
            "SELECT ua.player_id, f.gw, ua.minutes, ua.xg, ua.xa"
            " FROM us_appearances ua"
            " JOIN fixtures f ON f.fixture_id = ua.fixture_id"
            " WHERE f.finished = 1 AND ua.player_id IS NOT NULL"):
        us_hist.setdefault(r["player_id"], []).append(
            {"gw": r["gw"], "minutes": r["minutes"] or 0,
             "xgi": (r["xg"] or 0) + (r["xa"] or 0)})
    for pid, rows in us_hist.items():
        rows.sort(key=lambda x: x["gw"])

    prior = {}
    for r in con.execute(
            "SELECT player_id, season_name, total_points, minutes, starts"
            " FROM player_season_prior"):
        p = prior.get(r["player_id"])
        if p is None or r["season_name"] > p["season_name"]:
            prior[r["player_id"]] = r

    def base_features(pid, g):
        """Leakage-free feature vector; None if blank (no fixture / unknown)."""
        pl = players.get(pid)
        if pl is None:
            return None, None, None
        fx = club_fx.get(pl["club_id"], {}).get(g)
        if fx is None:
            return None, None, None
        opp, was_home = fx
        et = pl["element_type"] or 3

        rows = [r for r in hist.get(pid, []) if r["gw"] < g][-N_HISTORY:]
        if rows:
            exp_min = float(np.mean([r["minutes"] for r in rows]))
            recent_pts = sum(r["points"] for r in rows)
            recent_min = sum(r["minutes"] for r in rows)
            recent = recent_pts / (recent_min / 90.0) if recent_min >= 90 else None
        else:
            exp_min, recent = None, None

        pr = prior.get(pid)
        if exp_min is None and pr is not None:
            starts = pr["starts"] or 0
            exp_min = (float(np.clip((pr["minutes"] or 0) / starts, 0, 90))
                       if starts > 0 else 45.0)
        if exp_min is None:
            exp_min = 45.0

        prior_pp90 = None
        if pr is not None and (pr["minutes"] or 0) >= 90:
            prior_pp90 = (pr["total_points"] or 0) / ((pr["minutes"] or 0) / 90.0)
        if recent is not None and prior_pp90 is not None:
            base = 0.6 * recent + 0.4 * prior_pp90
        elif recent is not None:
            base = recent
        elif prior_pp90 is not None:
            base = prior_pp90
        else:
            base = POS_DEF_PP90[et]

        urows = [r for r in us_hist.get(pid, []) if r["gw"] < g][-N_HISTORY:]
        umin = sum(r["minutes"] for r in urows)
        xgi90 = (sum(r["xgi"] for r in urows) / (umin / 90.0)
                 if umin >= 90 else XGI_FALLBACK)

        own_att, _ = rates(pl["club_id"], g)
        _, opp_def = rates(opp, g)

        f = exp_min / 90.0
        x = [1.0, f, f * base, f * xgi90, f * own_att, f * opp_def,
             f * float(was_home),
             1.0 if et == 2 else 0.0, 1.0 if et == 3 else 0.0,
             1.0 if et == 4 else 0.0]
        return x, (opp, was_home), exp_min

    def training_rows(upto_gw):
        X, y = [], []
        for pid in players:
            for row in hist.get(pid, []):
                if row["gw"] >= upto_gw:
                    continue
                x, _, _ = base_features(pid, row["gw"])
                if x is None:
                    continue
                X.append(x)
                y.append(float(row["points"]))
        return (np.asarray(X, dtype=float), np.asarray(y)) if X else (None, None)

    backtest = {}
    for g in sorted(finished_gws):
        X, y = training_rows(g)
        if X is None:
            X, y = training_rows(g + 1)  # GW1 opener: fit in-sample
        if X is None or len(y) < 30:
            continue
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        for pid in players:
            x, _, _ = base_features(pid, g)
            if x is not None:
                backtest[(pid, g)] = float(np.asarray(x) @ beta)

    X_f, y_f = training_rows(float("inf"))
    future_beta = None
    if X_f is not None and len(y_f) >= 30:
        future_beta, *_ = np.linalg.lstsq(X_f, y_f, rcond=None)
        pred_resid = y_f - X_f @ future_beta
        r2 = 1.0 - float(np.sum(pred_resid ** 2)) / float(
            np.sum((y_f - y_f.mean()) ** 2)) if len(y_f) > 1 else 0.0
        train_rows = int(len(y_f))
        fitted = X_f @ future_beta
        print("beta:", [round(float(b), 3) for b in future_beta])
        print(f"train y: mean={float(y_f.mean()):.2f} sd={float(y_f.std()):.2f} "
              f"| fitted: sd={float(fitted.std()):.2f} "
              f"min={float(fitted.min()):.2f} max={float(fitted.max()):.2f}")
        corr = (float(np.corrcoef(fitted, y_f)[0, 1])
                if float(fitted.std()) > 0 else 0.0)
        print(f"corr(fitted, y)={corr:.3f}")
    else:
        r2, train_rows = None, int(len(y_f)) if X_f is not None else 0

    bt_pairs = [(k, v) for k, v in backtest.items()
                if k[1] != min(finished_gws, default=0)]
    if bt_pairs:
        errs = [abs(v - actual.get(k, 0.0)) for k, v in bt_pairs]
        bt_mae = float(np.mean(errs))
        ys = np.array([actual.get(k, 0.0) for k, _ in bt_pairs])
        ps = np.array([v for _, v in bt_pairs])
        bt_r2 = 1.0 - float(np.sum((ys - ps) ** 2)) / float(
            np.sum((ys - ys.mean()) ** 2)) if len(ys) > 1 else None
    else:
        bt_mae, bt_r2 = None, None

    fitted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for pid in players:
        for g in all_gws:
            x, fxinfo, exp_min = base_features(pid, g)
            if x is None:
                xp = 0.0
            elif (pid, g) in backtest:
                xp = backtest[(pid, g)]
            elif future_beta is not None:
                xp = float(np.asarray(x) @ future_beta)
            else:
                xp = exp_min / 90.0 * POS_DEF_PP90[players[pid]["element_type"] or 3]
            xp = float(max(xp, 0.0))
            act = actual.get((pid, g))
            if act is None and g in finished_gws:
                act = 0.0
            rows.append((pid, g, xp, act, MODEL, fitted_at))

    con.execute("DELETE FROM xp_projection")
    con.executemany(
        "INSERT INTO xp_projection (player_id, gw, xp, actual, model, fitted_at)"
        " VALUES (?,?,?,?,?,?)", rows)
    con.commit()

    next_gw = min((g for g in all_gws if g not in finished_gws),
                  default=max(all_gws, default=1))
    top = sorted((r for r in rows if r[1] == next_gw), key=lambda r: -r[2])
    print(f"xp_projection: {len(rows)} rows, train={train_rows}, "
          f"r2={r2 if r2 is None else round(r2, 3)}, "
          f"backtest_mae={bt_mae if bt_mae is None else round(bt_mae, 3)}, "
          f"backtest_r2={None if bt_r2 is None else round(bt_r2, 3)}")
    print("next GW top xp:", ", ".join(
        f"{players[r[0]]['web_name']}({round(r[2], 1)})" for r in top[:10]))
    con.close()
    return {"rows": len(rows), "train_rows": train_rows, "r2": r2,
            "backtest_mae": bt_mae, "backtest_r2": bt_r2}


if __name__ == "__main__":
    res = main()
    if res["rows"] == 0:
        sys.exit(1)
