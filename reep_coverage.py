import re

import pandas as pd

html = open("pl_shooting.html", encoding="utf-8").read()
ids = re.findall(r'/players/([0-9a-f]{8})/[^"]*"[^>]*>([^<]+)</a>', html)
seen = {}
for i, n in ids:
    n = n.strip()
    if n in ("Matches", "Match Log", "News", "Squad Number"):
        continue
    seen.setdefault(i, n)
print("links found:", len(ids), "unique:", len(seen))
fbids = pd.DataFrame(seen.items(), columns=["key_fbref", "name_html"])
fbids.to_csv("_fbref_ids.csv", index=False)

rp = pd.read_csv(
    "reep_people.csv",
    dtype=str,
    low_memory=False,
)
rp = rp[rp["type"] == "player"]

m = fbids.merge(rp, on="key_fbref", how="left")
print("matched to reep:", m["reep_id"].notna().sum(), "/", len(m))

keys = [c for c in rp.columns if c.startswith("key_")]
rows = []
for k in keys:
    s = m[k]
    n = int((s.notna() & (s.str.strip() != "") & (s != "nan")).sum())
    rows.append((k, n, round(100 * n / len(m), 1)))
rows.sort(key=lambda r: -r[1])
out = "\n".join(f"{k}\t{n}\t{pc}%" for k, n, pc in rows)
open("_reep_epl2627_coverage.txt", "w", encoding="utf-8").write(out)
print("written")

# also bio fields
bio = ["date_of_birth", "nationality", "position", "position_detail", "height_cm", "full_name"]
brows = []
for b in bio:
    s = m[b]
    n = int((s.notna() & (s.str.strip() != "") & (s != "nan")).sum())
    brows.append(f"{b}\t{n}\t{round(100*n/len(m),1)}%")
open("_reep_epl2627_bio.txt", "w", encoding="utf-8").write("\n".join(brows))
print("bio written")
