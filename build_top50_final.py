import html as htmllib
import json
import re
import unicodedata
from difflib import SequenceMatcher

import pandas as pd

MANUAL = {
    "rayan cherki": "mathis cherki",
    "gabriel magalhaes": "gabriel",
    "abdoul ouattara": "guemissongui ouattara",
    "bachir belloumi": "mohamed belloumi",
}


def norm(s):
    s = htmllib.unescape(str(s))
    for a, b in (
        ("Ø", "o"), ("ø", "o"), ("Đ", "d"), ("đ", "d"), ("Ł", "l"), ("ł", "l"),
        ("Æ", "ae"), ("æ", "ae"), ("Œ", "oe"), ("œ", "oe"), ("ß", "ss"),
        ("Ð", "d"), ("ð", "d"), ("Þ", "th"), ("þ", "th"),
    ):
        s = s.replace(a, b)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", "", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def ratio(a, b):
    return SequenceMatcher(None, a, b).ratio()


# ---------- load sources ----------
h = open("pl_shooting.html", encoding="utf-8").read()
block = next(x for x in re.findall(r"<!--(.*?)-->", h, re.S) if "<table" in x and "Player" in x)
name_to_id = {
    n.strip(): pid
    for pid, n in re.findall(r'/en/players/([0-9a-f]+)/[^>]*>([^<]{2,60})</a>', block)
    if n != "Matches"
}

fb = pd.read_csv("fbref_sot_2026_27.csv", encoding="utf-8")
fb = fb[(fb["Player"] != "Player")].dropna(subset=["Player"])
fb["fbref_id"] = fb["Player"].map(name_to_id)

rp = pd.read_csv("reep_people.csv", dtype=str, usecols=["reep_id", "name", "full_name", "key_fbref", "key_understat"])
rp = rp.dropna(subset=["key_fbref"]).drop_duplicates(subset=["key_fbref"]).set_index("key_fbref")

us = pd.DataFrame(json.load(open("understat_league.json"))["players"])
for c in ["games", "time", "goals", "xG", "xA", "shots"]:
    us[c] = pd.to_numeric(us[c], errors="coerce")
us["nkey"] = us["player_name"].map(norm)
us_by_id = {str(r["id"]): r for _, r in us.iterrows()}
us_by_key = {}
for _, r in us.iterrows():
    us_by_key.setdefault(r["nkey"], r)

# ---------- aggregate mid-season transfers (fbref splits, understat combines) ----------
fb["nkey"] = fb["Player"].map(norm)
agg = []
for _, g in fb.groupby("fbref_id", sort=False):
    r = g.iloc[0].copy()
    if len(g) > 1:
        r["Squad"] = " > ".join(dict.fromkeys(g["Squad"]))
        for c in ["90s", "Sh", "SoT", "Gls"]:
            r[c] = g[c].sum()
        r["SoT%"] = round(r["SoT"] / r["Sh"] * 100, 1) if r["Sh"] else 0.0
    agg.append(r)
fb = pd.DataFrame(agg).reset_index(drop=True)

# ---------- match stages ----------
pairs = {}   # fb index -> (understat row, source)
used = set()


def claim(i, u, src):
    if u is None:
        return
    key = str(u["id"])
    if key in used:
        return
    pairs[i] = (u, src)
    used.add(key)


for i, r in fb.iterrows():
    # 1. reep fbref_id -> understat id
    if isinstance(r["fbref_id"], str) and r["fbref_id"] in rp.index:
        uid = rp.at[r["fbref_id"], "key_understat"]
        if isinstance(uid, str) and uid in us_by_id:
            claim(i, us_by_id[uid], "reep-id")

for i, r in fb.iterrows():
    if i in pairs:
        continue
    # 2. exact name
    if r["nkey"] in us_by_key:
        claim(i, us_by_key[r["nkey"]], "name")

for i, r in fb.iterrows():
    if i in pairs:
        continue
    # 3. reep canonical/full name
    if isinstance(r["fbref_id"], str) and r["fbref_id"] in rp.index:
        for col in ("name", "full_name"):
            v = rp.at[r["fbref_id"], col]
            if isinstance(v, str) and norm(v) in us_by_key:
                claim(i, us_by_key[norm(v)], "reep-name")
                if i in pairs:
                    break

for i, r in fb.iterrows():
    if i in pairs:
        continue
    # 4. manual overrides
    tgt = MANUAL.get(r["nkey"])
    if tgt and tgt in us_by_key:
        claim(i, us_by_key[tgt], "manual")

remaining = [i for i in fb.index if i not in pairs]
for i in remaining:
    # 5. fuzzy
    r = fb.loc[i]
    best, best_score = None, 0.0
    for _, u in us.iterrows():
        if str(u["id"]) in used:
            continue
        a, b = r["nkey"], u["nkey"]
        if not a or not b:
            continue
        if a in b or b in a:
            score = 0.95
        else:
            ta, tb = a.split(), b.split()
            if len(ta) >= 2 and len(tb) >= 2 and ta[0] == tb[0] and ta[-1] == tb[-1]:
                score = 0.9
            else:
                score = ratio(a, b)
        if score > best_score:
            best, best_score = u, score
    if best is not None and best_score >= 0.75:
        claim(i, best, "fuzzy")

# ---------- assemble + validate ----------
rows = []
for i, (u, src) in pairs.items():
    f = fb.loc[i]
    rows.append(
        {
            "Player": f["Player"],
            "Squad": f["Squad"],
            "Pos": f["Pos"],
            "Age": f["Age"],
            "90s": f["90s"],
            "Sh": f["Sh"],
            "SoT": f["SoT"],
            "SoT%": f["SoT%"],
            "Gls": f["Gls"],
            "xG": round(u["xG"], 2),
            "min_diff": round(abs(f["90s"] * 90 - u["time"]), 1),
            "sh_diff": abs(f["Sh"] - u["shots"]),
            "src": src,
            "uPlayer": u["player_name"],
        }
    )

df = pd.DataFrame(rows)
print("matched:", len(df), "of", len(fb))
print("\nby source:\n", df["src"].value_counts().to_string())

flags = df[(df["sh_diff"] > 2) | (df["min_diff"] > 30)]
print("\nflagged (shots/minutes mismatch):", len(flags))
if len(flags):
    print(flags[["Player", "Squad", "uPlayer", "Sh", "SoT", "xG", "min_diff", "sh_diff", "src"]].to_string(index=False))

un = fb.loc[[i for i in fb.index if i not in pairs], ["Player", "Squad", "SoT", "Gls"]]
print("\nunmatched:", len(un))
if len(un):
    print(un.sort_values("SoT", ascending=False).head(15).to_string(index=False))

df["G-xG"] = (df["Gls"] - df["xG"]).round(2)
df["SoT-xG"] = (df["SoT"] - df["xG"]).round(2)
df["SoT/xG"] = (df["SoT"] / df["xG"]).round(2)

top = df.sort_values("xG", ascending=False).head(50).reset_index(drop=True)
out = top[["Player", "Squad", "Pos", "Age", "90s", "Sh", "SoT", "SoT%", "Gls", "xG", "G-xG", "SoT-xG", "SoT/xG"]]
out.insert(0, "Rank", range(1, len(out) + 1))
out.to_csv("pl_sot_vs_xg_top50.csv", index=False)
df.to_csv("pl_sot_xg_all_matched.csv", index=False)

pd.set_option("display.max_rows", 60)
pd.set_option("display.width", 220)
print("\nTOP 50 BY xG (SoT vs xG):")
print(out.to_string(index=False))
