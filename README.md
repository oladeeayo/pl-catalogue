# pl-catalogue

**The complete Premier League season as clean CSV files — players, fixtures, per-gameweek stats, shot events with xG, market values, transfers and expected-points forecasts. Refreshed weekly.**

![Updated weekly](https://img.shields.io/badge/data-updated%20weekly-brightgreen)
![Season 2026/27](https://img.shields.io/badge/season-2026%2F27-blue)
![CSV](https://img.shields.io/badge/format-CSV-informational)
![No API key](https://img.shields.io/badge/API%20key-none-brightgreen)
![Free for non-commercial use](https://img.shields.io/badge/use-free%20non--commercial-lightgrey)

A ready-to-use **Premier League / Fantasy Premier League (FPL) dataset** for
2026/27: every club, player, fixture, appearance, shot, market value and
transfer — plus a per-player, per-gameweek **expected-points forecast** with
actuals, so you can backtest the model yourself. Built for analysts, FPL
researchers, forecasters and journalists.

No API key, no signup, no rate limits — plain UTF-8 CSV you can `curl`, load
with pandas/DuckDB/R, or commit into your own project.

```bash
curl -O https://raw.githubusercontent.com/oladeeayo/pl-catalogue/main/data/2026-27/players.csv
```

---

## Contents

- [Get the data](#get-the-data)
- [Quick start](#quick-start)
- [What you get](#what-you-get)
- [Join keys](#join-keys)
- [Key columns](#key-columns)
- [Data dictionary](#data-dictionary)
- [Where the data comes from](#where-the-data-comes-from)
- [Coverage and freshness](#coverage-and-freshness)
- [License and attribution](#license-and-attribution)
- [Need more advanced data?](#need-more-advanced-data)
- [Repository layout](#repository-layout)

## Get the data

Four ways, pick whichever fits you:

**1. One-off download — no git needed.** Grab the whole dataset as a ZIP:

```
https://github.com/oladeeayo/pl-catalogue/archive/refs/heads/main.zip
```

**2. Single file with `curl`.**

```bash
curl -O https://raw.githubusercontent.com/oladeeayo/pl-catalogue/main/data/2026-27/gws/merged_gw.csv
```

**3. Shallow clone (only the current state, no history):**

```bash
git clone --depth 1 https://github.com/oladeeayo/pl-catalogue.git
```

**4. Hotlink via CDN.** [jsDelivr](https://www.jsdelivr.com/) mirrors the
repository with edge caching — friendlier for web apps and repeated reads:

```
https://cdn.jsdelivr.net/gh/oladeeayo/pl-catalogue@main/data/2026-27/players.csv
```

For reproducible research, pin a commit SHA instead of `main`:

```bash
git rev-parse HEAD
# then use .../pl-catalogue/<sha>/data/...
```

## Quick start

### pandas

```python
import pandas as pd

BASE = "https://raw.githubusercontent.com/oladeeayo/pl-catalogue/main/data/2026-27"

players = pd.read_csv(f"{BASE}/players.csv")        # one row per player
gw      = pd.read_csv(f"{BASE}/gws/merged_gw.csv")  # every player × gameweek
xp      = pd.read_csv(f"{BASE}/gws/xP1.csv")        # forecast vs actual points

gw[gw.gw == 7]                                      # one gameweek
xp[(xp.gw <= 7) & xp.actual.notna()]                # backtest rows
```

### DuckDB (query the CSV over HTTP, no download)

```sql
SELECT name, club, total_points, now_cost
FROM read_csv_auto(
  'https://raw.githubusercontent.com/oladeeayo/pl-catalogue/main/data/2026-27/gws/merged_gw.csv'
)
WHERE gw = 7
ORDER BY total_points DESC
LIMIT 20;
```

### R

```r
df <- read.csv("https://raw.githubusercontent.com/oladeeayo/pl-catalogue/main/data/2026-27/players.csv")
subset(df, club == "Arsenal")
```

## What you get

Season **2026/27**, GW1–7 loaded (exported 2026-10-22) — live numbers are in
[`data/2026-27/manifest.json`](data/2026-27/manifest.json):

| | |
|---|---|
| **Players / clubs / fixtures** | 668 / 20 / 380 |
| **Player-fixture rows** | 4,551 |
| **Shot events** | 1,932 (with xG, coordinates, body part, assister) |
| **Season metric rows** | 38,245 (Opta, FBref, Understat) |
| **Market-value snapshots** | 1,062 |
| **Transfer rows** | 1,899 (5 seasons, fees, loans) |
| **Expected-points rows** | 25,384 (forecast + actual + error) |
| **CSV files** | 4,693 |

```
data/2026-27/
├── players.csv                  # master table: one row per player, all sources flattened
├── club_stats.csv               # Opta team season metrics (long format)
├── gws/
│   ├── gw1.csv … gwN.csv        # every player's row in that gameweek
│   ├── merged_gw.csv            # all gameweeks stacked
│   └── xP1.csv                  # expected points: every player × every GW, forecast vs actual
├── players/<Name_id>/           # one folder per player (668 folders)
│   ├── profile.csv              # their row from players.csv
│   ├── gw.csv                   # their per-GW rows
│   ├── season_stats.csv         # their FBref + Understat season metrics (long format)
│   ├── shots.csv                # their shot events (x, y, xG, body part, assister)
│   ├── history.csv              # prior-season FPL career
│   ├── market.csv               # Transfermarkt value/contract snapshots over time
│   └── transfers.csv            # their transfer history (5 seasons, fees, loans)
├── shots.csv                    # all shot events
├── market.csv                   # all market-value snapshots
├── transfers.csv                # all transfer rows
├── fixtures.csv                 # 380 fixtures: scores, kickoff, Understat link
├── gw_calendar.csv              # GW deadlines, averages, most-* awards
├── clubs.csv                    # 20 clubs: position, form, provider ids
└── manifest.json                # row counts, GW range, export timestamp, sources
```

The tree is a **view**: it is wiped and rebuilt from a source-of-truth SQLite
database by the build pipeline (run privately — see [Repository
layout](#repository-layout)).

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

## Data dictionary

Full field-by-field documentation for every file: **[data/README.md](data/README.md)**.

## Where the data comes from

| Source | Contributes | Refresh |
|---|---|---|
| FPL official API | spine: clubs, players, fixtures, per-GW stats, prices, xG/xA | weekly |
| Reep crosswalk | identity: cross-provider ids, DOB, nationality (95.5% linked) | on release |
| FBref (Opta) | season tables: shooting, passing, possession, defence, misc | on demand¹ |
| The Analyst / Opta (sdapi) | season totals FBref can't serve: goalkeeping, set pieces, duels, discipline; full team-level tables | weekly² |
| Understat | shot events, per-appearance xG/xA | weekly |
| Transfermarkt | market values, contracts, transfer history (5 seasons) | daily / weekly |
| local model `ols-v1` | expected points (`gws/xP1.csv`) | weekly |

¹ FBref currently serves empty advanced-stat columns (their Opta feed lapsed in
Jan 2026); scraping is a manual step and the pipeline skips what isn't there.

² Opta season totals come from The Analyst's public sdapi endpoint, parsed fresh
each run; FBref pages remain the source of truth for shooting detail.

Raw source captures and the entire build pipeline live in a **private
repository**; this one stays focused on the published data and its documentation.

## Coverage and freshness

- **Updated weekly**, Tuesdays at 10:00 UTC, by GitHub Actions; a red run means
  the dataset was *not* published, so what you see here always passes the
  pipeline's 25 validation checks.
- `manifest.json` records the row counts, GW range and export timestamp of the
  current build — cite it when precision matters.
- GW files exist for every gameweek; future GWs contain forecasts, not actuals.

## License and attribution

This compilation is shared for **informational, non-commercial use**.

- **You may** use it in personal projects, research, journalism, dashboards and
  open-source tools, with attribution.
- **You may not** sell it, sub-license it, charge for access, or present it as a
  commercial data feed.
- **Attribute** by linking to this repository and naming `pl-catalogue` as the source.
- **Provider rights remain with the providers.** The Fantasy Premier League API,
  FBref, Understat, Transfermarkt and The Analyst have their own terms; this
  repository's compilation does not override them.

There is no OSI-approved license file because the upstream data is
non-commercial; the terms above are the license.

## Need more advanced data?

The published CSVs are deliberately broad and free. If you need something
**more sophisticated or advanced** — prior seasons, xG models, player-level
event streams, odds, a bespoke export for your model or study — here's how to
reach out:

- **Open an issue** for questions, data requests, or ideas. This is the best
  first step: we can scope what you need, and the answer stays visible to
  everyone who wants the same thing.
- **Open a pull request** when you already have a concrete fix or addition —
  a correction backed by an official source, a new column, or a schema
  improvement. Keep the CSV schema stable and describe your source in the PR.
- **For research collaborations or larger custom datasets**, open an issue and
  say what you're building; we'll work out the details there.

Feature requests are welcome — this dataset grows because users ask for things.

## Repository layout

```
data/2026-27/               the published dataset (this repo)
data/README.md               field-by-field data dictionary
README.md                    this file

# everything that produces the data — source code, SQLite state, raw provider
# captures, validation — lives in the private build repository and publishes
# here automatically every week.
```

*Not affiliated with the Premier League, any club, the Fantasy Premier League
game, or any commercial data provider.*
