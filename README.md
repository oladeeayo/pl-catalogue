# pl-catalogue

**A weekly-refreshed SQLite catalogue of the English Premier League** — every club,
player, fixture, appearance, shot and market value, identity-linked across data
providers and gated by validation on every run.

*Season 2026/27 · FPL spine · Understat shots · Transfermarkt values · Reep identity*

---

## What's in the box

| Layer | Source | Scale (GW5, 2026-10-05) |
|---|---|---|
| Spine: clubs, players, fixtures, per-fixture stats, prices, snapshots | **FPL official API** | 20 clubs · 667 players · 380 fixtures · 3,216 appearance rows |
| Identity: cross-provider ids, DOB, nationality, height | **Reep crosswalk** | 95.4% linked · 99.9% DOB · 91.9% nationality |
| Shot events: x/y coords, xG, situation, body part, assister | **Understat** | 1,398 shots · **99.4%** resolved to a player |
| Per-fixture model stats for every appearance | **Understat** | 1,538 rows (xG/xA/minutes/position) |
| Season totals: SoT, shots, goals + xG, xA, npg, key passes | **FBref + Understat** | 4,041 metric rows · 419 players |
| Market values, contracts, physicals (daily snapshot) | **Transfermarkt** | 530 players · **€13.1bn** · 422 id-matched / 108 name-matched |
| Quality gate | — | **22 validation checks, all passing** |

## Architecture

```
                     ┌─ weekly (Task Scheduler, Monday 06:00) ─────────────────────────┐
                     │                                                                 │
 FPL API  ──────────►│ fpl_spine ─► identity ─► stats_seed ─► enrich_understat ─►      │
                     │                              ▲            │                     │
 Reep CSV ───────────┼──────────────────────────────┘            │                     │
                     │                              enrich_transfermarkt ─► validate   │
 FBref (browser) ────┼─ fbref_scrape ─► fbref_extract ─┐          │                     │
 Understat API ──────┼─ understat_fetch ───────────────┴─ merge_sot_xg ─► (stats CSV) ─┘
                     └─────────────────────────────────────────────────────────────────┘

 Standalone: build_top50_final.py ─► pl_sot_vs_xg_top50.csv ─► plot_quadrants.py ─► quadrant chart
```

Design rules:

- **Idempotent upserts everywhere** — a failed run leaves the catalogue consistent.
- **Append-only facts** — per-fixture rows are never mutated; prices/values are
  point-in-time snapshots.
- **Loud validation** — structural, referential, per-GW and cross-provider checks;
  `run_weekly.py` exits non-zero on any failure.
- **Each source is an independent module** — one provider breaking can't take the
  pipeline down with it (missing optional inputs degrade to a warning, not a crash).

## Data flow

```
fbref_scrape.py ─► pl_shooting.html ─► fbref_extract.py ─► fbref_sot_2026_27.csv ─┐
                                                                                 ├─► merge_sot_xg.py
understat_fetch.py ─► understat_league.json ─────────────────────────────────────┘        │
                                                                      pl_sot_xg_all_matched.csv
                                                                       │            │
                                            stats_seed.py ◄───────────┘   build_top50_final.py
                                                                                │
                                                            plot_quadrants.py ◄─┘ ─► quadrant PNG
```

## Quickstart

**Requirements:** Python 3.10+ (`pip install pandas requests matplotlib`), Chrome
(for the FBref browser fetch only).

```bash
git clone https://github.com/oladeeayo/pl-catalogue.git
cd pl-catalogue

# 1. external identity crosswalk (~65 MB, one-time):
#    https://github.com/withqwerty/reep  →  place reep_people.csv in the project root

# 2. one-time stat sources (optional; weekly run works without them):
python fbref_scrape.py        # browser + Cloudflare turnstile → pl_shooting.html
python fbref_extract.py       # → fbref_sot_2026_27.csv
python understat_fetch.py     # → understat_league.json
python merge_sot_xg.py        # → pl_sot_xg_all_matched.csv

# 3. build the catalogue:
python catalogue/run_weekly.py
```

Without `reep_people.csv` / the stat CSVs the pipeline **skips those steps with a
warning** (keeping whatever is already in the DB); validation then tells you exactly
what coverage is missing.

## Weekly automation

```bat
schtasks /Create /TN "pl-catalogue" /SC WEEKLY /D MON /ST 06:00 ^
    /TR "C:\path\to\pl-catalogue\catalogue\run.bat"
```

- Runs `fpl_spine → identity → stats_seed → enrich_understat → enrich_transfermarkt → validate`
  (~15–20 min), logs to `catalogue/run.log`, exits `0` only if every check passes.
- Interactive-only session (no browser needed — all weekly steps are plain HTTP).

## Schema (SQLite, `catalogue/catalogue.db`)

| Table | Grain |
|---|---|
| `clubs`, `gws`, `fixtures` | one row each (FPL ids; `us_match_id` links Understat) |
| `players` | one row per FPL element + Reep bio + provider key columns |
| `player_fixture` | **(player, fixture)** — points, minutes, xG/xA, price, ownership |
| `v_player_gw` | view: per player per GW (double-GW safe) |
| `player_snapshot` | (player, date) — price, form, ownership over time |
| `player_season_prior` | (player, season) — career history from FPL |
| `season_stats` | (player, season, source, metric) — long-format enrichment |
| `us_shots` | one row per Understat shot event |
| `us_appearances` | (fixture, understat player) — per-appearance model stats |
| `player_market` | (player, date) — Transfermarkt value/contract snapshot |
| `v_club_table` | view: league table from finished fixtures |

## Validation (sample)

```
per-GW detail:
[PASS] GW5: 10 fixtures, 667 player rows, 32.4 xG, 966 pts
xG cross-check (finished): understat=166.9 vs FPL/Opta=153.1 (diff 13.8, expect same ballpark)
[PASS] understat shot resolve: 99.4% of shots resolve to an FPL player (>=75% required)
market values (2026-10-05): 530/667 players (79.5%), 530 valued, total €13.1bn
ALL CHECKS PASSED
```

The DOB cross-check (Transfermarkt vs FPL, 97.4% agreement) is deliberately
informational: FPL DOB data is occasionally wrong (we found one player born "1952"),
so identity decisions use **club-consistency vetoes**, not date equality.

## Data sources & access notes

| Provider | Access | Notes |
|---|---|---|
| FPL API | free JSON | no auth, polite rate (~0.05 s/element) |
| Understat | XHR header | no browser needed |
| FBref | browser fetch | Cloudflare turnstile — run `fbref_scrape.py` while at the machine |
| Transfermarkt | plain HTTP, 20 pages/run | 0.3 s between requests |
| Reep | GitHub CSV | identity crosswalk (v0, frozen pre-window) |
| Sofascore / Capology / WhoScored | **blocked** (403 CF) | deferred — browser tier |
| Opta / Wyscout / SkillCorner | licensed | out of scope |

Scrape modestly and only what you use; respect each site's terms.

## What's left

- **Browser-tier enrichers** (Pydoll + turnstile, needs a human for the first solve):
  FBref per-match logs (touches/pressures/SCA per fixture), Sofascore match ratings,
  Capology wages
- **Presentation layer**: leaderboards, squad-value views, CSV/dash exports
- **CI mirror**: headless-safe half (FPL + Understat) as a GitHub Actions cron,
  keeping the browser half local
- Reep v1 (live, weekly) when released — should lift identity coverage past 98%

## Repo layout

```
catalogue/                  # the product: schema + 6-step weekly pipeline
  schema.sql                # canonical DDL
  fpl_spine.py              # FPL API → clubs/gws/fixtures/player_fixture/snapshots
  identity.py               # Reep → players (code join + name fallback)
  matching.py               # shared name-matching helpers
  stats_seed.py             # season_stats from merged FBref+Understat CSV
  enrich_understat.py       # shots, appearances, season xG/xA
  enrich_transfermarkt.py   # market values, contracts (club-consistency veto)
  validate.py               # 22 checks → validation_report.txt, exit code
  run_weekly.py / run.bat   # pipeline driver / Task Scheduler target
fbref_scrape.py             # Pydoll + Cloudflare turnstile fetch
fbref_extract.py            # shooting table → CSV
understat_fetch.py          # season JSON fetch
merge_sot_xg.py             # merge FBref + Understat → stats_seed input
build_top50_final.py        # top-50 table for the chart
plot_quadrants.py           # SoT vs xG quadrant chart (overlap-verified)
reep_coverage.py            # provider coverage diagnostic
```
