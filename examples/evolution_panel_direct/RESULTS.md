# Direct gene-panel evolution — results

Applying the Pantheon Evolution machinery to a **non-code genome (a gene panel)**: a coding agent
edits `panel.txt`, researches the domain itself (web_search + analysis of the expression data), and
is scored on `panel-selection-bench` (Janesick breast-cancer scRNA), scored remotely on Modal.

## Setup

- **Genome**: a gene panel (list of symbols), not code. Candidate pool = all **16,818 detectable
  genes** (expressed in ≥10 cells) — a real whole-transcriptome pool, not a pre-filtered short list.
- **Fitness** `quality_score` = mean over four dimensions of `relative` (panel / full-transcriptome
  ceiling): **dim1** identifiability, **dim3** structure, **dim4** reconstruction, **dim7** biological
  prior coverage. We also track `quality_134` (the dim1/3/4 sub-score) to check we don't break the
  other dims while pushing dim7.
- **The biological prior (dim7) is HIDDEN** from the agent (giving it would be teaching to the test).
  The agent must *discover* the relevant genes itself with a `web_search` tool + by analysing the
  6000-cell AnnData. `candidates.txt` is only a spelling/validity whitelist.
- **Model**: Claude Opus 4.8 (via OpenRouter). glm-5.2 was too slow/error-prone for this
  research-heavy, precise-editing task.
- Eval proxy = 6000 cells (~15-20s; the winner is re-scored on all cells). Seed = top DE markers.

## Two experiments: panel size 100 vs 500

| size | seed quality | best quality | **lift** | dim7 (seed→best) | quality_134 (held) |
|---|---|---|---|---|---|
| 100 | 0.5433 | 0.5477 | **+0.0044** | 0.392 → 0.405 (**+0.013**) | 0.594 → 0.595 ✅ |
| 500 | 0.5880 | 0.5928 | **+0.0048** | 0.494 → 0.507 (**+0.013**) | 0.619 → 0.621 ✅ |

Best panels: `results_opus/panel_best.txt` (100), `results_opus_500/panel_best.txt` (500).

## Findings

1. **The mechanism works and the value proposition holds.** In both experiments the agent lifted the
   hidden dim7 (~+0.013) **while keeping quality_134 flat (or slightly up)** — i.e. it raised the
   overall score by improving biological-prior coverage *without breaking the other metrics*, using
   genuine research (web search) + data analysis (expression, redundancy, co-expression), never the
   prior itself. It found a real (if small) verified improvement and correctly rejected net-negative
   swaps.

2. **The lift is small and the same at both sizes** — 500 ends higher only because its DE seed starts
   higher, not because evolution gains more. This is explained quantitatively:

3. **dim7 is capacity-bound, not near half-empty.** dim7 = mean of `target_gene_coverage` and
   `program_functional_recovery` (this dataset's prior has no LR pairs → that sub-metric is undefined):
   - The prior gene-set **union is 1,342 genes (1,276 detectable)**. A panel of N genes can cover at
     most N/1276 of it, so `target_gene_coverage` **caps at 500/1276 ≈ 0.39** even if *every* panel
     gene were a prior gene (DE-500 is at 0.11).
   - `program_functional_recovery` is already **~0.87** (near-maxed) for DE markers.
   - ⇒ dim7's real ceiling is ≈ **(0.39 + 0.88)/2 ≈ 0.64**, not 1.0. DE-500 already sits at 0.49, and
     reaching 0.64 would require devoting almost all 500 slots to prior genes (destroying dim1/3/4).
     So the movable dim7 headroom, *while preserving the other dimensions*, is only ~+0.02 — exactly
     what the agent found ("dim7 is very sticky").

## Takeaway

The method (non-code genome + research tools + hidden-prior balancing) is validated and clean. To
demonstrate a larger effect, pick a setup where dim7 actually has room: a **panel budget comparable
to the prior union (~1300+ genes)** so `target_gene_coverage` can approach 1.0, or a dataset whose
prior is smaller / more concentrated.
