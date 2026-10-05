# pl-catalogue — Premier League 2026/27 dataset

A weekly-refreshed catalogue of the Premier League: every club, player, fixture,
appearance, shot, market value and transfer — plus a per-player, per-gameweek
expected-points forecast. This README explains how to navigate the data.

**Season 2026/27 · GW1–5 loaded · 667 players · 380 fixtures · updated every Monday**

```
data/2026-27/
├── players.csv                  # master table: one row per player, all sources flattened
├── gws/
│   ├── gw1.csv … gw5.csv        # every player's row in that gameweek
│   ├── merged_gw.csv            # all gameweeks stacked
│   └── xP1.csv                  # expected points: every player × every GW, forecast vs actual
├── players/<Name_id>/           # one folder per player (667 folders)
│   ├── profile.csv              # their row from players.csv
│   ├── gw.csv                   # their per-GW rows
│   ├── season_stats.csv         # their FBref + Understat season metrics (long format)
│   ├── shots.csv                # their shot events (x, y, xG, body part, assister)
│   ├── history.csv              # prior-season FPL career
│   ├── market.csv               # Transfermarkt value/contract snapshots over time
│   └── transfers.csv            # their transfer history (5 seasons, fees, loans)
├── shots.csv                    # all 1,398 shot events
├── market.csv                   # all market-value snapshots
├── transfers.csv                # all 1,897 transfer rows (root copy)
├── fixtures.csv                 # 380 fixtures: scores, kickoff, Understat link
├── gw_calendar.csv              # GW deadlines, averages, most-* awards
├── clubs.csv                    # 20 clubs: position, form, provider ids
└── manifest.json                # row counts, GW range, export timestamp, sources
```

The tree is a **view**: it is wiped and rebuilt from `catalogue/catalogue.db`
(the source of truth) by `catalogue/export_data.py`.

## Join keys

| Key | Where | Use |
|---|---|---|
| `player_id` (= `element`) | every file except `clubs.csv` | joins a player across all files; folder suffix |
| `fixture_id` | `gw*`, `shots`, `fixtures` | joins a match across files |
| `club_id` | `clubs`, `players`, `fixtures` | joins a team |
| `gw` | `gw*`, `xP1`, `fixtures` | matchweek number (1–38) |

Folder names are `First_Second_playerid` (accents/spaces stripped from the name).

## Key columns

- **Points / prices** — `total_points` per GW; `price` in `players.csv` is £m (6.1),
  `value` in GW files is raw FPL tenths (61 = £6.1m); `selected` = ownership count.
- **Expected goals** — `expected_goals` / `expected_assists` are Opta (via FPL);
  `understat_*` columns are Understat's model. They differ by design.
- **`x`, `y`, `xg`** (shots) — Understat normalized coords (0–1, attacker shoots
  toward x=1) and shot xG.
- **`xp`, `actual`, `err`** (xP1.csv) — modelled expected points for that GW,
  the actual FPL score, and the error (`actual − xp`). `xp` is filled for future
  GWs too, so `xP1.csv` doubles as a weekly forecast table.
- **Transfers** — `fee_type` ∈ permanent / loan / loan_fee / free / end_loan /
  unknown; `fee_eur` in euros (blank = undisclosed); `saison_id` = season ending year.
- **`season_stats.csv`** — long format: `player_id, season, source, metric, value`.
  Sources: `fbref_sot`, `fbref_passing`, `fbref_possession`, `fbref_defense`,
  `fbref_misc`, `understat`.
- Empty files are normal — header-only means "no rows yet" (didn't play / no history).

## Where the data comes from

| Source | Contributes | Refresh |
|---|---|---|
| FPL official API | spine: clubs, players, fixtures, per-GW stats, prices, xG/xA | weekly |
| Reep crosswalk | identity: cross-provider ids, DOB, nationality (95.5% linked) | on release |
| FBref (Opta) | season tables: shooting, passing, possession, defence, misc | on demand¹ |
| Understat | shot events, per-appearance xG/xA | weekly |
| Transfermarkt | market values, contracts, transfer history (5 seasons) | daily / weekly |
| local model `ols-v1` | expected points (`xp_projection` → `gws/xP1.csv`) | weekly |

¹ FBref currently serves empty advanced-stat columns (their Opta feed lapsed in
Jan 2026); scraping is a manual browser step — `python fbref_scrape.py` — and the
pipeline skips what isn't there.

Raw source captures live in **`sources/`** (FBref HTML + extracts, Understat JSON,
Reep CSV, merged season metrics). `catalogue/` holds the pipeline code;
`data/` holds the exported dataset.

## Rebuilding

```bash
python catalogue/validate.py     # 25 checks → should print ALL CHECKS PASSED
python catalogue/export_data.py  # rebuild data/2026-27/ from the committed DB
python catalogue/run_weekly.py   # full weekly refresh (FPL + Understat + TM + xP + export)
```

Weekly automation runs Mondays 06:00 via Task Scheduler (`catalogue/run.bat`).

## Coverage snapshot (GW1–5, 2026-10-05)

| | |
|---|---|
| players / clubs / fixtures | 667 / 20 / 380 |
| player-fixture rows | 3,216 |
| shot events | 1,398 (99.4% player-resolved) |
| season metric rows | 8,628 (420 players) |
| market snapshots | 530 players, €13.1bn |
| transfer rows | 1,897 (839 matched to current players) |
| expected-points rows | 25,346 (backtest MAE 1.19) |
| identity linked to Reep | 95.5% |

Full column dictionary: [`data/README.md`](data/README.md).
