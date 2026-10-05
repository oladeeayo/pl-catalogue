import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import pandas as pd
from matplotlib.text import Text

df = pd.read_csv("pl_sot_vs_xg_top50.csv", encoding="utf-8")

x, y = df["xG"], df["SoT"]
med_x, med_y = x.median(), y.median()
lim = max(x.max(), y.max()) * 1.1

QUAD = {
    "TR": ("Sustained threat\nhigh xG · high SoT", "#2e7d32", (0.98, 0.96), "top"),
    "TL": ("Overperformers\nlow xG · high SoT", "#1565c0", (0.02, 0.96), "top"),
    "BR": ("Underperformers\nhigh xG · low SoT", "#c62828", (0.97, 0.02), "bottom"),
    "BL": ("Quiet\nlow xG · low SoT", "#616161", (0.03, 0.02), "bottom"),
}


def quad(r):
    if r["xG"] >= med_x:
        return "TR" if r["SoT"] >= med_y else "BR"
    return "TL" if r["SoT"] >= med_y else "BL"


df["quad"] = df.apply(quad, axis=1)
colors = df["quad"].map({k: QUAD[k][1] for k in QUAD})

fig, ax = plt.subplots(figsize=(14, 10), dpi=150)

ax.add_patch(mpatches.Rectangle((med_x, med_y), lim - med_x, lim - med_y,
                                fc="#e8f5e9", ec="none", zorder=0))
ax.add_patch(mpatches.Rectangle((-0.1, med_y), med_x + 0.1, lim - med_y,
                                fc="#e3f2fd", ec="none", zorder=0))
ax.add_patch(mpatches.Rectangle((med_x, -0.1), lim - med_x, med_y + 0.1,
                                fc="#ffebee", ec="none", zorder=0))
ax.add_patch(mpatches.Rectangle((-0.1, -0.1), med_x + 0.1, med_y + 0.1,
                                fc="#f5f5f5", ec="none", zorder=0))

ax.axvline(med_x, color="#9e9e9e", lw=1.2, zorder=1)
ax.axhline(med_y, color="#9e9e9e", lw=1.2, zorder=1)
ax.plot([0, lim], [0, lim], color="#555555", lw=1.3, ls="--", zorder=2,
        label="SoT = xG (par)")

ax.scatter(x, y, s=100, c=colors, edgecolors="#333333", linewidths=0.8, zorder=4)

ax.set_xlim(-0.1, lim)
ax.set_ylim(-0.1, lim)
ax.set_xlabel("xG (Understat)", fontsize=12)
ax.set_ylabel("Shots on Target (FBref)", fontsize=12)
ax.set_title(
    "Premier League 2026/27 — Top 50 players by xG: 4-quadrant view\n"
    "Shots on Target vs Expected Goals (after 5 matches)",
    fontsize=14, pad=12,
)
ax.grid(True, alpha=0.2, ls=":")

static_texts = []
for key, (label, color, (hx, hy), va) in QUAD.items():
    static_texts.append(ax.text(
        hx, hy, label, transform=ax.transAxes, ha="right" if hx > 0.5 else "left",
        va=va, fontsize=11.5, fontweight="bold",
        color=color, alpha=0.85, zorder=3,
    ))

static_texts.append(ax.text(
    med_x + 0.35, med_y + 0.35,
    f"median lines: xG = {med_x:.2f} · SoT = {med_y:.0f}",
    fontsize=8, color="#666666", zorder=5,
))

static_texts.append(ax.text(
    0.48, 0.92,
    "Above dashed line = more SoT than xG suggests · Below = fewer\n"
    "Source: FBref (SoT) · Understat (xG) · small sample, 5 matches",
    transform=ax.transAxes, ha="left", va="top", fontsize=8.5, color="#555555",
))

handles = [mpatches.Patch(color=QUAD[k][1], label=QUAD[k][0].replace("\n", " — "))
           for k in ("TR", "TL", "BR", "BL")]
handles.append(plt.Line2D([], [], color="#555555", ls="--", label="SoT = xG (par)"))
ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.075),
          ncol=5, fontsize=9.5, frameon=False)

fig.tight_layout()
fig.canvas.draw()

renderer = fig.canvas.get_renderer()
fig_w, fig_h = fig.get_size_inches()[0] * 150, fig.get_size_inches()[1] * 150


def overlaps(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def measure(text, fs=7.0):
    bb = Text(0, 0, text, figure=fig, fontsize=fs).get_window_extent(renderer)
    return bb.width + 7, bb.height + 4


marker_boxes = []
for xv, yv in zip(x, y):
    px, py = ax.transData.transform((xv, yv))
    marker_boxes.append((px - 11, py - 11, px + 11, py + 11))

placed = []
for t in static_texts:
    bb = t.get_window_extent(renderer)
    placed.append((bb.x0 - 5, bb.y0 - 5, bb.x1 + 5, bb.y1 + 5))

RING = [(d * math.cos(math.radians(a)), d * math.sin(math.radians(a)))
        for d in (16, 26, 37, 48, 60, 73, 87, 102, 118)
        for a in range(0, 360, 24)]

sizes = {}
for i in df.sort_values("xG", ascending=False).index:
    r = df.loc[i]
    px, py = ax.transData.transform((r["xG"], r["SoT"]))
    name = r["Player"]
    if name not in sizes:
        sizes[name] = measure(name)
    w, h = sizes[name]
    best, best_pen = None, None
    for dx, dy in RING:
        cx, cy = px + dx, py + dy
        box = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        if box[0] < 2 or box[1] < 2 or box[2] > fig_w - 2 or box[3] > fig_h - 2:
            continue
        pen = 30 * sum(overlaps(box, pb) for pb in placed)
        pen += 7 * sum(overlaps(box, mb) for mb in marker_boxes)
        pen += math.hypot(dx, dy) * 0.025
        if best_pen is None or pen < best_pen:
            best, best_pen = (dx, dy), pen
        if best_pen is not None and best_pen < 0.5:
            break
    if best is None:
        best = (14, 10)
    dx, dy = best
    placed.append((px + dx - w / 2, py + dy - h / 2, px + dx + w / 2, py + dy + h / 2))
    ax.annotate(
        name, (r["xG"], r["SoT"]), xytext=(dx, dy), textcoords="offset pixels",
        fontsize=7.0, color="#222222", zorder=6, ha="center", va="center",
        bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="none", alpha=0.78),
    )

fig.canvas.draw()
renderer = fig.canvas.get_renderer()
anns = [c for c in ax.texts if isinstance(c, matplotlib.text.Annotation)]
ext = [a.get_window_extent(renderer) for a in anns]
bad = []
for i in range(len(ext)):
    for j in range(i + 1, len(ext)):
        a, b = ext[i], ext[j]
        if not (a.x1 <= b.x0 or b.x1 <= a.x0 or a.y1 <= b.y0 or b.y1 <= a.y0):
            bad.append(("ann-ann", anns[i].get_text(), anns[j].get_text()))
static_ext = [t.get_window_extent(renderer) for t in static_texts]
static_names = ["TR", "TL", "BR", "BL", "median", "note"]
for i, e in enumerate(ext):
    for j, s in enumerate(static_ext):
        if not (e.x1 <= s.x0 or s.x1 <= e.x0 or e.y1 <= s.y0 or s.y1 <= e.y0):
            bad.append(("ann-static", anns[i].get_text(), static_names[j]))
for a in anns:
    e = a.get_window_extent(renderer)
    box = (e.x0 - 1, e.y0 - 1, e.x1 + 1, e.y1 + 1)
    for n, mb in zip(df["Player"], marker_boxes):
        if n != a.get_text() and overlaps(box, mb):
            bad.append(("ann-marker", a.get_text(), n))
print("actual rendered overlaps:", len(bad))
for t in bad:
    print("  ", t)

fig.savefig("pl_sot_vs_xg_quadrants.png", bbox_inches="tight")
df.to_csv("pl_sot_xg_top50_quadrants.csv", index=False)
print("saved pl_sot_vs_xg_quadrants.png")
print(df.groupby("quad")["Player"].apply(list).to_string())
