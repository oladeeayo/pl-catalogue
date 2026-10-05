import json
import re
import unicodedata

import pandas as pd

TEAM_MAP = {
    "manchester utd": "manchester united",
    "man utd": "manchester united",
    "manchester utd": "manchester united",
    "wolves": "wolverhampton wanderers",
    "tottenham": "tottenham hotspur",
    "spurs": "tottenham hotspur",
    "nottm forest": "nottingham forest",
    "nottingham": "nottingham forest",
    "west ham": "west ham united",
    "brighton": "brighton and hove albion",
    "aston villa": "aston villa",
    "newcastle": "newcastle united",
    "sheffield u": "sheffield united",
}


def norm(s):
    s = str(s)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", "", s.lower())
    s = re.sub(r"\s+", " ", s).strip()
    return s


def norm_team(s):
    s = norm(s)
    return TEAM_MAP.get(s, s)


fb = pd.read_csv("fbref_sot_2026_27.csv")
fb["nkey"] = fb["Player"].map(norm)
fb["tkey"] = fb["Squad"].map(norm_team)

us = pd.DataFrame(json.load(open("understat_league.json"))["players"])
for c in ["games", "time", "goals", "xG", "xA", "shots", "npxG"]:
    us[c] = pd.to_numeric(us[c], errors="coerce")
us["nkey"] = us["player_name"].map(norm)
us["tkey"] = us["team_title"].map(norm_team)

# primary match on name+team
m = fb.merge(us, on=["nkey", "tkey"], how="left")

# second pass: name-only merge for rows still missing xG
still = m["xG"].isna()
if still.any():
    alt = fb.loc[still].merge(us, on="nkey", how="left", suffixes=("", "_us"))
    for col in ["xG", "goals", "shots", "games", "time", "xA", "position", "team_title"]:
        m.loc[still, col] = alt[col].values

unmatched = m.loc[m["xG"].isna(), ["Player", "Squad", "SoT", "Gls"]].sort_values("SoT", ascending=False)
print("unmatched:", len(unmatched))
print(unmatched.head(10).to_string(index=False))

matched = m.dropna(subset=["xG"]).copy()
print("matched:", len(matched))

top = matched.sort_values("xG", ascending=False).head(50).copy()
top["G-xG"] = (top["Gls"] - top["xG"]).round(2)
top["SoT-xG"] = (top["SoT"] - top["xG"]).round(2)
top["SoT/xG"] = (top["SoT"] / top["xG"]).round(2)

out = top[
    ["Player", "Squad", "Pos", "Age", "90s", "Sh", "SoT", "SoT%", "Gls", "xG", "G-xG", "SoT-xG", "SoT/xG"]
].reset_index(drop=True)
out.insert(0, "Rank", range(1, len(out) + 1))
out.to_csv("pl_sot_vs_xg_top50.csv", index=False)

pd.set_option("display.max_rows", 60)
pd.set_option("display.width", 220)
print(out.to_string(index=False))
