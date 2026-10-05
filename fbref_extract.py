"""Extract FBref PL tables from saved HTML -> flat CSVs (in sources/).

    sources/pl_shooting.html     -> sources/fbref_sot_2026_27.csv        (merge_sot_xg.py input)
    sources/fbref_passing.html   -> sources/fbref_passing_2026_27.csv
    sources/fbref_possession.html -> sources/fbref_possession_2026_27.csv
    sources/fbref_defense.html   -> sources/fbref_defense_2026_27.csv
    sources/fbref_misc.html      -> sources/fbref_misc_2026_27.csv

Duplicate stat headers keep the first occurrence (Total). Run:
    python fbref_extract.py [page ...]
"""
import io
import re
import sys
from pathlib import Path

import pandas as pd

SRC = Path(__file__).parent / "sources"
PAGES = {
    "shooting": ("pl_shooting.html", "fbref_sot_2026_27.csv"),
    "passing": ("fbref_passing.html", "fbref_passing_2026_27.csv"),
    "possession": ("fbref_possession.html", "fbref_possession_2026_27.csv"),
    "defense": ("fbref_defense.html", "fbref_defense_2026_27.csv"),
    "misc": ("fbref_misc.html", "fbref_misc_2026_27.csv"),
}

KEEP = ["Player", "Squad", "Pos", "Age", "90s"]


def extract(html_path, out_path):
    html_text = Path(html_path).read_text(encoding="utf-8")
    m = [b for b in re.findall(r"<!--(.*?)-->", html_text, re.S)
         if "<table" in b and "Player" in b]
    if not m:
        m = [html_text]
    df = pd.read_html(io.StringIO(m[0]))[0]
    df.columns = [col[1] if isinstance(col, tuple) else str(col) for col in df.columns]
    df = df.loc[:, ~df.columns.duplicated()]
    df = df.dropna(subset=["Player"]).drop_duplicates(subset=["Player", "Squad"])
    for c in df.columns:
        if c not in KEEP and c not in ("Player", "Squad"):
            try:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            except (ValueError, TypeError):
                pass
    df.to_csv(out_path, index=False, encoding="utf-8")
    return df


if __name__ == "__main__":
    wanted = sys.argv[1:] or list(PAGES)
    for page in wanted:
        html_name, out_name = PAGES[page]
        html_path, out_path = SRC / html_name, SRC / out_name
        if not Path(html_path).exists():
            print(f"skip {page}: {html_path} missing")
            continue
        df = extract(html_path, out_path)
        print(f"{page}: {df.shape} -> {out_path}")
        print("   cols:", list(df.columns))
