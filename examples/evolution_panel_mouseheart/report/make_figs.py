"""Generate the figures for the mouse-heart panel-evolution report."""
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = "/Users/weizexu/Projects/PantheonOS/examples/evolution_panel_mouseheart/report"
data = json.load(open(f"{HERE}/data.json")); traj = json.load(open(f"{HERE}/traj.json"))
rows = data["rows"]
order = ["Evolved (ours)", "DE seed", "DE", "scGeneFit", "SCMER", "PERSIST", "spapros", "ReconST", "HVG", "random"]
rows = sorted(rows, key=lambda r: order.index(r["method"]) if r["method"] in order else 99)
labels = [r["method"] for r in rows]
def col(m): return "#d62728" if m == "Evolved (ours)" else ("#ff7f0e" if m == "DE seed" else "#4c72b0")
colors = [col(m) for m in labels]
x = np.arange(len(labels))

# ---- Fig 1: optimization trajectory (proxy quality + dim7 vs generation) ----
t = traj["trajectory"]; g = [p["gen"] for p in t]
fig, ax = plt.subplots(figsize=(7, 4.3))
ax.plot(g, [p["quality"] for p in t], "-o", color="#d62728", lw=2, label="proxy quality (dims 1,3,4,7)")
ax.plot(g, [p["dim7"] for p in t], "-s", color="#2ca02c", lw=2, label="dim7 (hidden biological prior)")
ax.plot(g, [p["q134"] for p in t], "-^", color="#7f7f7f", lw=1.6, label="q134 (dims 1,3,4)")
ax.set_xlabel("generation (best-so-far)"); ax.set_ylabel("score (optimization proxy)")
ax.set_title("Panel evolution on mouse embryonic heart\n(agent lifts the hidden dim7 while holding q134)")
ax.legend(fontsize=9, loc="center right"); ax.grid(alpha=0.25)
ax.annotate(f"dim7 {t[0]['dim7']:.2f} → {t[-1]['dim7']:.2f}", xy=(g[-1], t[-1]["dim7"]),
            xytext=(-10, 8), textcoords="offset points", fontsize=9, color="#2ca02c")
fig.tight_layout(); fig.savefig(f"{HERE}/figs/trajectory.png", dpi=140); plt.close()

# ---- Fig 2: FFP composite + dim7 (prior) by method ----
ffp = [r["ffp"] or 0 for r in rows]; d7 = [r["dim7"] or 0 for r in rows]
w = 0.4
fig, ax = plt.subplots(figsize=(8.5, 4.3))
ax.bar(x - w/2, ffp, w, color=colors, label="FFP composite")
ax.bar(x + w/2, d7, w, color=colors, alpha=0.45, label="dim7 (prior)")
for i, v in enumerate(ffp): ax.text(i - w/2, v + 0.006, f"{v:.3f}", ha="center", fontsize=7)
ax.axhline(next(r["ffp"] for r in rows if r["method"] == "DE"), ls="--", color="gray", lw=1)
ax.set_xticks(x); ax.set_xticklabels(labels, rotation=25, ha="right", fontsize=8.5)
ax.set_ylabel("score (test split)"); ax.set_title("Fit-for-purpose composite & prior coverage by method (size 500)")
ax.legend(fontsize=9); ax.grid(alpha=0.2, axis="y")
fig.tight_layout(); fig.savefig(f"{HERE}/figs/method_quality.png", dpi=140); plt.close()

# ---- Fig 3: CHD & Runx1t1-KO-DE overlap by method ----
chd = [r["chd"] for r in rows]; ko = [r["ko"] for r in rows]
fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.2))
for ax2, vals, ttl in [(axes[0], chd, f"CHD genes recovered (of {data['chd_n']})"),
                       (axes[1], ko, f"Runx1t1-KO DE genes recovered (of {data['ko_n']})")]:
    ax2.bar(x, vals, color=colors)
    for i, v in enumerate(vals): ax2.text(i, v + 0.4, str(v), ha="center", fontsize=8)
    ax2.set_xticks(x); ax2.set_xticklabels(labels, rotation=30, ha="right", fontsize=7.8)
    ax2.set_ylabel("# genes in panel"); ax2.set_title(ttl, fontsize=10); ax2.grid(alpha=0.2, axis="y")
fig.suptitle("Biological recovery: overlap with CHD genes and Runx1t1-KO responsive genes", fontsize=11)
fig.tight_layout(); fig.savefig(f"{HERE}/figs/overlap.png", dpi=140); plt.close()

# ---- Fig 4: added genes by cardiac pathway ----
pw = dict(sorted(traj["added_pathways"].items(), key=lambda kv: -len(kv[1])))
fig, ax = plt.subplots(figsize=(7, 3.6))
ax.barh(list(pw.keys())[::-1], [len(v) for v in pw.values()][::-1], color="#d62728")
ax.set_xlabel("# genes added"); ax.set_title(f"What the agent added: {traj['n_added']} cardiac genes swapped in (by pathway)")
ax.grid(alpha=0.2, axis="x")
fig.tight_layout(); fig.savefig(f"{HERE}/figs/pathways.png", dpi=140); plt.close()
print("figures written")
