#!/usr/bin/env python
"""Direct gene-panel evolution on panel-selection-bench (MOUSE EMBRYONIC HEART), via Pantheon Evolution.

The genome is a GENE PANEL (panel.txt), not code. A full-capability coding agent edits which genes
are in the panel — reasoning biologically from the per-dimension feedback + a dataset brief — verifies
with run_evaluator (scored remotely on Modal), and submits. Goal: lift quality_score, mainly the
HIDDEN dim7 (biological prior coverage), while holding the dim1/3/4 sub-score.

Unlike Janesick, the mouse-heart prior is small (449 genes; 430 detectable) so a 500-gene panel can
FULLY cover it — dim7 has real headroom here (ceiling ~1.0 vs Janesick's capacity-bound ~0.39).

    python run_evolution.py --iterations 6 --workers 1 --output results/

Requires: the mouse-heart Modal service deployed (PANELBENCH_DATASET=mouseheart modal deploy
panel-selection-bench/score_app.py) and OPENROUTER_API_KEY in ~/.env.
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
_HERE = Path(__file__).parent

for _l in Path.home().joinpath(".env").read_text().splitlines():
    if _l.startswith("OPENROUTER_API_KEY="):
        os.environ["OPENROUTER_API_KEY"] = _l.split("=", 1)[1].strip().strip('"').strip("'")

DATA_BRIEF = (_HERE / "data_brief.md").read_text() if (_HERE / "data_brief.md").exists() else ""
ADATA_PATH = str(_HERE / "data" / "adata_6000.h5ad")   # 6000 cells x ~18204 genes, log-norm, obs['cell_type']
SCORE_APP = "panelbench-evo-score-mouseheart"

PANEL_SYSTEM_PROMPT = f"""You are an expert cardiac / developmental biologist designing a targeted gene \
panel for a MOUSE EMBRYONIC HEART study (E10.5-E14.5, Feng et al. 2022). You edit a gene LIST — you do \
not write algorithms.

Your workspace files:
- panel.txt: the current panel (one MOUSE gene symbol per line, Title-case e.g. Nkx2-5, Gata4, Tbx5) — \
the thing you edit.
- candidates.txt: the ~18200 VALID gene names. Use it ONLY to CHECK SPELLING / that a gene really \
exists in this dataset (genes not in it are silently dropped and shrink your panel). Note MOUSE casing.
- de_genes.txt: the original DE-marker seed genes (your starting point — know which to keep vs. swap out).

RESEARCH TOOLS — this is a real research task, so USE them instead of guessing from memory:
- web_search(query): look up which genes / markers / pathways matter for the DEVELOPING MOUSE HEART and \
its cell types — cardiomyocyte maturation & chamber (atrial/ventricular) specification, endocardium / \
endothelium, epicardium, valve / cushion (endocardial-to-mesenchymal), cardiac conduction system, cardiac \
neural crest, and developmental signalling (Wnt / BMP / Notch / Tgf-beta), plus congenital-heart-disease genes.
- python + the expression data at {ADATA_PATH}: an AnnData (6000 cells x ~18204 genes, log-normalized, \
obs['cell_type'] with 8 cardiac cell types) — the SAME cells the evaluator scores on. Analyze it to check \
a candidate gene is actually expressed and cell-type-informative before adding it.

HARD FORMAT RULES (breaking these wastes your turn):
- panel.txt must be EXACTLY 500 unique gene symbols, one per line, every one present in candidates.txt.
- ALWAYS use the ABSOLUTE path to your working directory (given in the task) when reading/writing files \
in python — your interpreter's current directory may differ, and the evaluator only scores files in that \
directory. All of panel.txt / candidates.txt / de_genes.txt live there.
- When you edit, WRITE THE COMPLETE 500-GENE LIST via python (load, modify a set, write) — never a \
fragment, never duplicates. Validate with python (len==500, unique, all in candidates) BEFORE evaluating.

HOW TO WORK (be efficient — budget ~4 evaluations):
1. run_evaluator once to see quality_score, the per-dimension breakdown (dim1 identifiability, dim3 \
structure, dim4 reconstruction, dim7 biological relevance) and quality_134 (the dim1/3/4 sub-score).
2. Make ONE focused, biologically-motivated change: web_search + analyze the adata to find genes that \
are biologically important for the developing mouse heart (cardiomyocyte/chamber programs, endocardium, \
epicardium, valve/cushion, conduction, developmental signalling) AND are expressed, then swap out the \
most REDUNDANT DE markers for them. Write the full validated 500-gene panel.
3. run_evaluator to confirm quality_score went UP and quality_134 did NOT drop much. Keep it only if so.
4. submit(summary=...) with your best VALID panel. The evolution loop refines across generations — a \
small verified gain, submitted quickly, is the goal; don't fully optimize in one turn.

- Robustness: ALWAYS leave panel.txt valid and at least as good as the seed. Never submit something worse."""

OBJECTIVE = f"""Design a 500-gene panel for a MOUSE EMBRYONIC HEART (E10.5-E14.5) spatial-transcriptomics \
study that MAXIMIZES quality_score.

quality_score = mean over FOUR dimensions (higher is better):
- dim1 identifiability: how well the panel separates the 8 cardiac cell types.
- dim3 structure: how well it preserves the global cell manifold.
- dim4 reconstruction: how well held-out genes are predicted from the panel.
- dim7 BIOLOGICAL RELEVANCE: a HIDDEN score that rewards covering the study's key biology. It is NOT \
revealed to you and you cannot read it off — you must FIGURE OUT which genes are biologically important \
for the DEVELOPING MOUSE HEART yourself (research + data analysis), and dim7 measures whether you got it right.

The seed panel is 500 DE markers (also in de_genes.txt): dim1 tends to be SATURATED (well above ceiling, \
so removing a redundant marker barely hurts), dim3/dim4 moderate, and the HIDDEN dim7 is LOW (pure \
statistical markers miss much of the relevant cardiac-developmental biology). Confirm the exact numbers \
with run_evaluator.

>>> The real opportunity is dim7, and here you have LOTS of room (unlike a large-prior dataset): at 500 \
genes a panel can cover the whole relevant biology. dim1 is saturated, so REPLACE the most REDUNDANT DE \
markers (many cell types have dozens of overlapping markers) with genes that are biologically important \
for THIS developing-heart sample — cardiomyocyte maturation & chamber specification, endocardium/ \
endothelium, epicardium, valve/cushion morphogenesis, conduction, cardiac neural crest, and developmental \
signalling (Wnt/BMP/Notch/Tgf-beta) — so quality_134 stays flat (or rises) while the hidden dim7 climbs. \
Swap boldly (tens of genes per round is fine). Use web_search to find the right genes and the expression \
data to confirm they're informative.

This is genuine biological reasoning + research — the whole point is that a smart agent can BALANCE the \
hidden biological relevance against the other metrics, lifting the total without breaking anything.

{DATA_BRIEF}
"""


async def run(iterations, output_dir, model, workers, verbose, resume):
    from pantheon.evolution import EvolutionConfig, EvolutionTeam
    from pantheon.evolution.program import CodebaseSnapshot

    if model.startswith(("openai/", "openrouter/")) and os.environ.get("OPENROUTER_API_KEY"):
        os.environ["OPENAI_API_KEY"] = os.environ["OPENROUTER_API_KEY"]
        os.environ["OPENAI_API_BASE"] = "https://openrouter.ai/api/v1"

    seed = CodebaseSnapshot(files={
        "panel.txt": (_HERE / "seed_panel.txt").read_text(),
        "candidates.txt": (_HERE / "candidates.txt").read_text(),   # ~18204 valid mouse names (spell check)
        "de_genes.txt": (_HERE / "de_genes.txt").read_text(),        # original DE seed markers (reference)
    })
    evaluator_code = (_HERE / "evaluator.py").read_text()

    config = EvolutionConfig(
        max_iterations=iterations, num_workers=workers, num_islands=2, num_inspirations=2,
        # MAP-Elites behaviour descriptors = the prior-coverage vs general-quality tradeoff
        # (dim7 vs the dim1/3/4 sub-score). Keeps the archive diverse across "prior-heavy" and
        # "quality-heavy" panels. QD grid axes only; fitness stays combined_score {1,3,4,7}.
        feature_dimensions=["dim7_prior", "quality_134"],
        function_weight=1.0, llm_weight=0.0, mutator_model=model,
        single_agent_mutation=True, mutation_system_prompt=PANEL_SYSTEM_PROMPT,
        mutation_web_search=True,                       # agent researches genes itself (prior is hidden)
        # Action budget: web_search + adata analysis + edits + run_evaluator all count (submit does not).
        max_tool_calls_per_mutation=22,
        evaluation_timeout=240, mutation_timeout=600,
        early_stop_generations=max(50, iterations), checkpoint_interval=2,
        db_path=output_dir, log_level="DEBUG" if verbose else "INFO", log_iterations=True)

    print("=" * 64)
    print(f"Direct panel evolution | MOUSE HEART size-500 | model {model} | "
          f"iters {iterations} workers {workers}")
    print("seed = DE-500; lift the hidden dim7 (fully coverable at 500) while holding q134")
    print("=" * 64)

    team = EvolutionTeam(config=config)

    def _cb(i, s, **kw):
        progs = list(team.database.programs.values())
        valid = [p for p in progs if p.metrics.get("combined_score", 0) > 0]
        if not valid:
            print(f"  iter{i}: (no valid program yet) progs={len(progs)}", flush=True)
            return
        best = max(valid, key=lambda p: p.metrics.get("combined_score", 0))
        m = best.metrics
        print(f"  iter{i}: best quality={m.get('combined_score', 0):.4f} "
              f"(q134={m.get('quality_134', 0):.3f}) "
              f"[dim1={m.get('dim1_identifiability', 0):.3f} "
              f"dim3={m.get('dim3_structure', 0):.3f} "
              f"dim4={m.get('dim4_reconstruction', 0):.3f} "
              f"dim7={m.get('dim7_prior', 0):.3f}] "
              f"size={m.get('panel_size', '?')} progs={len(progs)} valid={len(valid)}", flush=True)

    result = await team.evolve(initial_code=seed, evaluator_code=evaluator_code,
                               objective=OBJECTIVE, resume_from=resume, progress_callback=_cb)

    print("\n" + "=" * 64)
    print(result.get_summary())
    if result.best_program:
        m = result.best_program.metrics
        print(f"BEST (6000-proxy) quality = {m.get('quality_score', 0):.4f} | "
              f"dim1={m.get('dim1_identifiability', 0):.3f} dim3={m.get('dim3_structure', 0):.3f} "
              f"dim4={m.get('dim4_reconstruction', 0):.3f} dim7={m.get('dim7_prior', 0):.3f}")
        try:
            import modal
            genes = [g.strip() for g in
                     result.best_program.snapshot.files["panel.txt"].splitlines()
                     if g.strip() and not g.lstrip().startswith("#")]
            fr = modal.Function.from_name(SCORE_APP, "score_panel").remote(genes, 500, True)
            print(f"BEST (FULL cells)  quality = {fr['quality']:.4f} dims={fr['dims']}")
        except Exception as e:  # noqa: BLE001
            print(f"(full-cell verify failed: {e})")
        if output_dir:
            out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
            (out / "panel_best.txt").write_text(result.best_program.snapshot.files["panel.txt"])
            result.save_report(str(out / "evolution_report.json"))
            print(f"best panel -> {out / 'panel_best.txt'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=6)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--model", default="openai/anthropic/claude-opus-4.8")
    ap.add_argument("--output", default="results")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--resume", default=None)
    a = ap.parse_args()
    asyncio.run(run(a.iterations, a.output, a.model, a.workers, a.verbose, a.resume))
