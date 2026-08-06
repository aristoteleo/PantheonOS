"""Emit LaTeX table + macros with exact numbers from data.json / traj.json (FFP composite)."""
import json
HERE = "/Users/weizexu/Projects/PantheonOS/examples/evolution_panel_mouseheart/report"
data = json.load(open(f"{HERE}/data.json")); traj = json.load(open(f"{HERE}/traj.json"))
rows = data["rows"]
order = ["Evolved (ours)", "DE seed", "DE", "scGeneFit", "SCMER", "PERSIST", "spapros", "ReconST", "HVG", "random"]
rows = sorted(rows, key=lambda r: order.index(r["method"]) if r["method"] in order else 99)

def esc(s): return str(s).replace("_", r"\_")
def f(x, n=3): return f"{x:.{n}f}" if isinstance(x, (int, float)) else "--"

lines = [r"\begin{tabular}{lccccccc}", r"\toprule",
         r"method & FFP & Identity & Struct & Recon & Prior & CHD$\cap$ & KO$\cap$\\",
         r"\midrule"]
for r in rows:
    b = r["method"] == "Evolved (ours)"
    nm = (r"\textbf{" + esc(r["method"]) + "}") if b else esc(r["method"])
    lines.append(f"{nm} & {f(r['ffp'])} & {f(r['dim1'],2)} & {f(r['dim3'],2)} & "
                 f"{f(r['dim4'],2)} & {f(r['dim7'],2)} & {r['chd']} & {r['ko']}\\\\")
    if r["method"] == "DE seed": lines.append(r"\midrule")
lines += [r"\bottomrule", r"\end{tabular}"]
open(f"{HERE}/results_table.tex", "w").write("\n".join(lines))

ev = next(r for r in rows if r["method"] == "Evolved (ours)")
de = next(r for r in rows if r["method"] == "DE seed")
deBench = next(r for r in rows if r["method"] == "DE")
t0, t1 = traj["trajectory"][0], traj["trajectory"][-1]
best_ref = max((r for r in rows if r["method"] not in ("Evolved (ours)", "DE seed")), key=lambda r: r["ffp"] or 0)
best_ko = max(rows, key=lambda r: r["ko"])
m = {
    "evFFP": f(ev["ffp"]), "deSeedFFP": f(de["ffp"]), "deBenchFFP": f(deBench["ffp"]),
    "evDimSeven": f(ev["dim7"]), "deDimSeven": f(de["dim7"]),
    "bestRefName": esc(best_ref["method"]), "bestRefFFP": f(best_ref["ffp"]),
    "maxRefDimSeven": f(max((r["dim7"] or 0) for r in rows if r["method"] not in ("Evolved (ours)", "DE seed"))),
    "seedDimSeven": f(t0["dim7"], 3), "bestDimSeven": f(t1["dim7"], 3),
    "seedQual": f(t0["quality"], 3), "bestQual": f(t1["quality"], 3),
    "evCHD": str(ev["chd"]), "deCHD": str(de["chd"]), "chdN": str(data["chd_n"]),
    "evKO": str(ev["ko"]), "deKO": str(de["ko"]), "koN": str(data["ko_n"]),
    "bestKOName": esc(best_ko["method"]), "bestKO": str(best_ko["ko"]),
    "nAdded": str(traj["n_added"]), "nRemoved": str(traj["n_removed"]),
}
open(f"{HERE}/macros.tex", "w").write("\n".join(rf"\newcommand{{\{k}}}{{{v}}}" for k, v in m.items()) + "\n")
print("wrote results_table.tex + macros.tex"); print(m)
