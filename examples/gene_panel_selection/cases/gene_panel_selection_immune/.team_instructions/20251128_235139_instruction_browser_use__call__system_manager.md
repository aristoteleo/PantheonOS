Caller: selection_expert

Task: Execute a Python script in the active conda environment to compute ARI vs panel size curves (quick pass) and derive a candidate_subpanel based purely on separability.

Inputs (existing in workdir):
- Adata: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/results/adata_downsampled_3k.h5ad
- Precomputed top lists/scores:
  - SpaPROS top: results/gene_panels/spapros/spapros_top_1500.csv (headerless single column)
  - scGeneFit scores: results/gene_panels/scgenefit/scgenefit_scores.csv (columns: gene, score)
  - RandomForest top: results/gene_panels/random_forest/rf_top_1500.csv (single-column CSV)
  - HVG top: results/gene_panels/hvg/hvg_top_1500.csv (headerless)
  - DE top: results/gene_panels/de/de_top_1500.csv (headerless)
  - Aggregated evidence: results/candidate_subpanel_evidence.csv

Outputs to produce in results/:
- results/ari_vs_panelsize.csv
- results/figures/ari_vs_panel_size.png
- results/candidate_subpanel.csv (selected using best method/size by ARI; include per-method evidence from candidate_subpanel_evidence.csv plus panel_rank)

Constraints:
- CPU-only; keep runtime light. Use a stratified subsample to ~6000 cells, sizes=[50,100,200,400,700,1000], PCA components <=30, single-pass (no CV).

Please run the following Python script content inline using the environment’s python and ensure it writes the outputs to the specified paths. Report back upon completion or any errors.

Python script:
"""
import scanpy as sc, pandas as pd, numpy as np, os
import matplotlib.pyplot as plt, seaborn as sns
from sklearn.metrics import adjusted_rand_score

workdir = "/home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert"
results_dir = f"{workdir}/results"
fig_dir = f"{results_dir}/figures"
os.makedirs(fig_dir, exist_ok=True)

adata = sc.read_h5ad(f"{results_dir}/adata_downsampled_3k.h5ad")
label_key = 'cell_type'

# stratified subsample to ~6000 cells
np.random.seed(42)
max_total = 6000
if adata.n_obs > max_total:
    cats = adata.obs[label_key].astype('category')
    idxs = []
    frac = max_total / adata.n_obs
    for cat in cats.cat.categories:
        ids = np.where(adata.obs[label_key].values == cat)[0]
        take = max(1, int(len(ids) * frac))
        if take > len(ids):
            take = len(ids)
        if take>0:
            idxs.extend(np.random.choice(ids, size=take, replace=False))
    adata = adata[sorted(set(idxs))].copy()

if adata.raw is None:
    adata.raw = adata
sc.pp.normalize_total(adata, target_sum=1e4)
sc.pp.log1p(adata)

# Load top lists
spapros_top1500 = pd.read_csv(f"{results_dir}/gene_panels/spapros/spapros_top_1500.csv", header=None)[0].tolist()
scgenefit_scores = pd.read_csv(f"{results_dir}/gene_panels/scgenefit/scgenefit_scores.csv")
rf_top1500 = pd.read_csv(f"{results_dir}/gene_panels/random_forest/rf_top_1500.csv").iloc[:,0].tolist()
hvg_top1500 = pd.read_csv(f"{results_dir}/gene_panels/hvg/hvg_top_1500.csv", header=None)[0].tolist()
de_top1500 = pd.read_csv(f"{results_dir}/gene_panels/de/de_top_1500.csv", header=None)[0].tolist()

panels = {
    'SpaPROS': spapros_top1500,
    'scGeneFit': list(scgenefit_scores.sort_values('score', ascending=False).head(1500)['gene']),
    'RandomForest': rf_top1500,
    'HVG': hvg_top1500,
    'DE': de_top1500,
}

sizes = [50, 100, 200, 400, 700, 1000]
recs = []

for method, glist in panels.items():
    genes_in = [g for g in glist if g in adata.var_names]
    for sz in sizes:
        genes = genes_in[:min(sz, len(genes_in))]
        if len(genes) < 10:
            continue
        sub = adata[:, genes].copy()
        sc.pp.scale(sub, max_value=10)
        ncomp = int(max(2, min(30, sub.n_obs - 1, sub.n_vars - 1)))
        sc.tl.pca(sub, n_comps=ncomp)
        sc.pp.neighbors(sub, n_neighbors=15, n_pcs=ncomp)
        sc.tl.leiden(sub, resolution=1.0)
        ari = adjusted_rand_score(sub.obs[label_key], sub.obs['leiden'])
        recs.append({'method': method, 'size': len(genes), 'ARI': float(ari)})

ari_df = pd.DataFrame(recs)
ari_df.to_csv(f"{results_dir}/ari_vs_panelsize.csv", index=False)

plt.figure(figsize=(8,6))
sns.lineplot(data=ari_df, x='size', y='ARI', hue='method', marker='o')
plt.title('ARI vs panel size (no-CV, ~6k subsample)')
plt.xlabel('Panel size (top-N)'); plt.ylabel('ARI (Leiden vs cell_type)')
plt.tight_layout(); plt.savefig(f"{fig_dir}/ari_vs_panel_size.png", dpi=200)

# Choose best method/size
best = ari_df.sort_values('ARI', ascending=False).head(1)
print('Best ARI row:', best.to_dict(orient='records')[0])

# Build candidate_subpanel.csv
merged = pd.read_csv(f"{results_dir}/candidate_subpanel_evidence.csv")
best_method = best.iloc[0]['method']
best_size = int(best.iloc[0]['size'])

if best_method == 'SpaPROS':
    order_list = panels['SpaPROS']
elif best_method == 'scGeneFit':
    order_list = list(scgenefit_scores.sort_values('score', ascending=False)['gene'])
elif best_method == 'RandomForest':
    order_list = panels['RandomForest']
elif best_method == 'HVG':
    hvg_tbl = pd.read_csv(f"{results_dir}/gene_panels/hvg/hvg_scores.csv")
    order_list = hvg_tbl.sort_values('hvg_score', ascending=False)['gene'].tolist()
elif best_method == 'DE':
    de_tbl = pd.read_csv(f"{results_dir}/gene_panels/de/de_aggregated_scores.csv")
    order_list = de_tbl.sort_values('rank_score', ascending=False)['gene'].tolist()
else:
    order_list = panels[best_method]

panel_genes = [g for g in order_list if g in merged['gene'].values][:best_size]
rank_map = {g:i+1 for i,g in enumerate(panel_genes)}

cand = merged[merged['gene'].isin(panel_genes)].copy()
cand['panel_rank'] = cand['gene'].map(rank_map)
cand = cand.sort_values('panel_rank')

cand.to_csv(f"{results_dir}/candidate_subpanel.csv", index=False)
print(f"Saved candidate_subpanel.csv with {len(cand)} genes using {best_method} size={best_size}")
"""