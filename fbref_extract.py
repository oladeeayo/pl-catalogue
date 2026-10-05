import io
import re

import pandas as pd

html = open("pl_shooting.html", encoding="utf-8").read()

block = next(b for b in re.findall(r"<!--(.*?)-->", html, re.S) if "<table" in b and "Player" in b)
df = pd.read_html(io.StringIO(block))[0]

df.columns = [col[1] if isinstance(col, tuple) else str(col) for col in df.columns]
df = df.loc[:, ~df.columns.duplicated()]

keep = {}
for c in df.columns:
    flat = str(c)
    if flat in ("Player", "Squad", "Pos", "Age", "90s", "Sh", "SoT", "SoT%", "Gls", "Matches"):
        keep[c] = flat
df = df.rename(columns=keep)[list(keep.values())]

for c in ["90s", "Sh", "SoT", "SoT%", "Gls"]:
    df[c] = pd.to_numeric(df[c], errors="coerce")

df = df.dropna(subset=["Player"]).drop_duplicates(subset=["Player", "Squad"])
df.to_csv("fbref_sot_2026_27.csv", index=False)
print(df.shape)
print(df.sort_values("SoT", ascending=False).head(15).to_string(index=False))
