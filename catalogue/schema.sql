-- Premier League full catalogue schema v1 (SQLite)
-- Keys: FPL element id = spine PK; Reep reep_id = cross-provider identity.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS clubs (
    club_id              INTEGER PRIMARY KEY,   -- FPL team id (1-20)
    fpl_code             INTEGER,               -- FPL team code
    reep_id              TEXT,                  -- Reep team id (identity step)
    name                 TEXT NOT NULL,
    short_name           TEXT,
    position             INTEGER,               -- league position (bootstrap)
    played INTEGER, win INTEGER, draw INTEGER, loss INTEGER, points INTEGER,
    form                 TEXT,
    strength_attack_home INTEGER,
    strength_attack_away INTEGER,
    strength_defence_home INTEGER,
    strength_defence_away INTEGER,
    pulse_id             INTEGER,
    us_team_id           INTEGER                -- Understat team id
);

CREATE TABLE IF NOT EXISTS players (
    player_id         INTEGER PRIMARY KEY,      -- FPL element id
    fpl_code          INTEGER UNIQUE,           -- = Reep key_opta_numeric
    reep_id           TEXT,                     -- Reep canonical identity
    web_name          TEXT NOT NULL,
    first_name        TEXT,
    second_name       TEXT,
    known_name        TEXT,
    full_name         TEXT,                     -- Reep birth/legal name
    club_id           INTEGER REFERENCES clubs(club_id),
    element_type      INTEGER,                  -- 1 GK 2 DEF 3 MID 4 FWD
    position_detail   TEXT,                     -- Reep granular position
    date_of_birth     TEXT,                     -- FPL ISO, Reep wins when present
    region            INTEGER,                  -- FPL region id
    nationality       TEXT,                     -- Reep country name
    height_cm         INTEGER,                  -- Reep
    squad_number      INTEGER,
    status            TEXT,                     -- a available d doubtful i injured s suspended n
    news              TEXT,
    opta_code         TEXT,                     -- FPL Opta person code (p<id>)
    removed           INTEGER DEFAULT 0,
    key_wikidata      TEXT,
    key_fbref         TEXT,
    key_understat     TEXT,
    key_transfermarkt TEXT,
    key_sofascore     TEXT,
    key_fotmob        TEXT,
    key_premier_league TEXT,
    key_whoscored     TEXT
);

CREATE TABLE IF NOT EXISTS gws (
    gw                   INTEGER PRIMARY KEY,
    name                 TEXT,
    deadline_time        TEXT,
    finished             INTEGER,
    is_current           INTEGER,
    is_next              INTEGER,
    average_entry_score  INTEGER,
    highest_score        INTEGER,
    transfers_made       INTEGER,
    most_selected        INTEGER,               -- element id
    most_transferred_in  INTEGER,
    most_captained       INTEGER,
    most_vice_captained  INTEGER
);

CREATE TABLE IF NOT EXISTS fixtures (
    fixture_id     INTEGER PRIMARY KEY,
    gw             INTEGER,
    home_club      INTEGER REFERENCES clubs(club_id),
    away_club      INTEGER REFERENCES clubs(club_id),
    kickoff_time   TEXT,
    finished       INTEGER,
    started        INTEGER,
    home_score     INTEGER,
    away_score     INTEGER,
    home_difficulty INTEGER,
    away_difficulty INTEGER,
    pulse_id       INTEGER,
    us_match_id    INTEGER                       -- Understat match id
);
CREATE INDEX IF NOT EXISTS idx_fixtures_gw ON fixtures(gw);

-- The spine: one row per (player, match appearance record)
CREATE TABLE IF NOT EXISTS player_fixture (
    player_id    INTEGER NOT NULL REFERENCES players(player_id),
    fixture_id   INTEGER NOT NULL REFERENCES fixtures(fixture_id),
    gw           INTEGER NOT NULL,
    opponent_team INTEGER,
    was_home     INTEGER,
    minutes      INTEGER,
    starts       INTEGER,
    goals_scored INTEGER,
    assists      INTEGER,
    clean_sheets INTEGER,
    goals_conceded INTEGER,
    own_goals    INTEGER,
    penalties_saved INTEGER,
    penalties_missed INTEGER,
    yellow_cards INTEGER,
    red_cards    INTEGER,
    saves        INTEGER,
    bonus        INTEGER,
    bps          INTEGER,
    influence    REAL,
    creativity   REAL,
    threat       REAL,
    ict_index    REAL,
    clearances_blocks_interceptions INTEGER,
    recoveries   INTEGER,
    tackles      INTEGER,
    defensive_contribution INTEGER,
    expected_goals REAL,                        -- Opta xG per match (FPL)
    expected_assists REAL,
    expected_goal_involvements REAL,
    expected_goals_conceded REAL,
    total_points INTEGER,
    value        INTEGER,                       -- price (60 = 6.0)
    selected     INTEGER,                       -- ownership count
    transfers_in INTEGER,
    transfers_out INTEGER,
    transfers_balance INTEGER,
    PRIMARY KEY (player_id, fixture_id)
);
CREATE INDEX IF NOT EXISTS idx_pf_gw ON player_fixture(gw);
CREATE INDEX IF NOT EXISTS idx_pf_fixture ON player_fixture(fixture_id);

-- Point-in-time market/availability snapshot, one row per player per run date
CREATE TABLE IF NOT EXISTS player_snapshot (
    player_id    INTEGER NOT NULL REFERENCES players(player_id),
    fetch_date   TEXT NOT NULL,                 -- YYYY-MM-DD
    gw           INTEGER,
    now_cost     INTEGER,
    form         REAL,
    points_per_game REAL,
    selected_by_percent REAL,
    total_points INTEGER,
    event_points INTEGER,
    transfers_in_event  INTEGER,
    transfers_out_event INTEGER,
    cost_change_event   INTEGER,
    cost_change_start   INTEGER,
    dreamteam_count     INTEGER,
    ep_next     TEXT,
    ep_this     TEXT,
    status      TEXT,
    news        TEXT,
    chance_of_playing_next_round INTEGER,
    chance_of_playing_this_round INTEGER,
    PRIMARY KEY (player_id, fetch_date)
);

-- Prior-season totals from element-summary history_past (free career history)
CREATE TABLE IF NOT EXISTS player_season_prior (
    player_id   INTEGER NOT NULL REFERENCES players(player_id),
    season_name TEXT NOT NULL,                  -- '2021/22'
    total_points INTEGER,
    minutes     INTEGER,
    goals_scored INTEGER,
    assists     INTEGER,
    clean_sheets INTEGER,
    goals_conceded INTEGER,
    own_goals   INTEGER,
    penalties_saved INTEGER,
    penalties_missed INTEGER,
    yellow_cards INTEGER,
    red_cards   INTEGER,
    saves       INTEGER,
    bonus       INTEGER,
    bps         INTEGER,
    influence   REAL,
    creativity  REAL,
    threat      REAL,
    ict_index   REAL,
    expected_goals REAL,
    expected_assists REAL,
    expected_goal_involvements REAL,
    expected_goals_conceded REAL,
    starts      INTEGER,
    tackles     INTEGER,
    recoveries  INTEGER,
    clearances_blocks_interceptions INTEGER,
    defensive_contribution INTEGER,
    start_cost  INTEGER,
    end_cost    INTEGER,
    PRIMARY KEY (player_id, season_name)
);

-- Enrichment layer: external source metrics, long format
-- source: 'fbref' | 'understat' | 'sofascore' | 'fotmob' | ...
CREATE TABLE IF NOT EXISTS season_stats (
    player_id   INTEGER NOT NULL REFERENCES players(player_id),
    season      TEXT NOT NULL,                  -- '2026-27'
    source      TEXT NOT NULL,
    metric      TEXT NOT NULL,
    value       REAL,
    fetched_at  TEXT,
    PRIMARY KEY (player_id, season, source, metric)
);

-- Market values / contract / physicals (Transfermarkt), point-in-time
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

-- Convenience view: per player per GW (double-GW safe: fixtures summed)
CREATE VIEW IF NOT EXISTS v_player_gw AS
SELECT player_id,
       gw,
       SUM(total_points)     AS points,
       SUM(minutes)          AS minutes,
       SUM(goals_scored)     AS goals,
       SUM(assists)          AS assists,
       SUM(expected_goals)   AS xg,
       SUM(expected_assists) AS xa,
       MAX(value)            AS price,
       MAX(selected)         AS selected,
       COUNT(*)              AS matches
FROM player_fixture
GROUP BY player_id, gw;

-- Convenience view: club table from finished fixtures
CREATE VIEW IF NOT EXISTS v_club_table AS
SELECT c.club_id, c.name,
       SUM(CASE WHEN f.home_club = c.club_id THEN 1 ELSE 0 END) AS played_h,
       SUM(CASE WHEN f.away_club = c.club_id THEN 1 ELSE 0 END) AS played_a,
       SUM(CASE WHEN f.home_club = c.club_id AND f.finished = 1 THEN
             CASE WHEN f.home_score > f.away_score THEN 3 WHEN f.home_score = f.away_score THEN 1 ELSE 0 END
            WHEN f.away_club = c.club_id AND f.finished = 1 THEN
             CASE WHEN f.away_score > f.home_score THEN 3 WHEN f.away_score = f.home_score THEN 1 ELSE 0 END
            ELSE 0 END) AS pts,
       SUM(CASE WHEN f.home_club = c.club_id THEN COALESCE(f.home_score,0)
                WHEN f.away_club = c.club_id THEN COALESCE(f.away_score,0) ELSE 0 END) AS gf,
       SUM(CASE WHEN f.home_club = c.club_id THEN COALESCE(f.away_score,0)
                WHEN f.away_club = c.club_id THEN COALESCE(f.home_score,0) ELSE 0 END) AS ga
FROM clubs c
LEFT JOIN fixtures f ON (f.home_club = c.club_id OR f.away_club = c.club_id) AND f.finished = 1
GROUP BY c.club_id;

-- Understat layer: shot-level events + per-appearance model stats
-- Enriched by enrich_understat.py; player_id nullable (not all Understat
-- players are FPL-registered).
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
    last_action        TEXT,
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

-- Expected-points projection (xp_model.py, model 'ols-v1').
-- One row per (player, gw) for the whole season: forecast for future GWs,
-- backtest actual for finished ones. Rebuilt every run.
CREATE TABLE IF NOT EXISTS xp_projection (
    player_id   INTEGER NOT NULL REFERENCES players(player_id),
    gw          INTEGER NOT NULL,
    xp          REAL NOT NULL,               -- predicted FPL points
    actual      REAL,                        -- total_points when the GW finished
    model       TEXT NOT NULL,               -- e.g. 'ols-v1'
    fitted_at   TEXT NOT NULL,               -- ISO timestamp of the fit
    PRIMARY KEY (player_id, gw)
);

-- Transfermarkt transfer history (enrich_transfers.py), full replace per fetch.
CREATE TABLE IF NOT EXISTS transfer_history (
    row_key          TEXT PRIMARY KEY,       -- saison:club:direction:row_index
    saison_id        INTEGER NOT NULL,       -- TM season id (2026 = 2026/27)
    tm_club_id       INTEGER,                -- TM club the file is listed under
    club_name        TEXT,
    direction        TEXT,                   -- 'in' | 'out' (from that club's view)
    tm_id            INTEGER,                -- TM player id
    player_name      TEXT,
    age              INTEGER,
    position         TEXT,
    market_value_eur REAL,
    from_club        TEXT,
    to_club          TEXT,
    fee_eur          REAL,                   -- parsed fee, NULL if not numeric
    fee_text         TEXT,                   -- verbatim, e.g. 'Loan fee: €2.80m'
    fee_type         TEXT,                   -- permanent|loan|loan_fee|free|end_loan|unknown
    transfer_id      INTEGER,                -- TM transfer id (when present)
    player_id        INTEGER REFERENCES players(player_id),  -- matched FPL player
    fetched_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_th_player ON transfer_history(player_id);
CREATE INDEX IF NOT EXISTS idx_th_saison ON transfer_history(saison_id);
