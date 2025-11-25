# Guideline for mapping single-cell data to spatial data

## Mapping with MOSCOT

This file describes how to use moscot(https://moscot.readthedocs.io/en/latest/) to map single-cell data to spatial data with Optimal Transport.
And how to impute the genes/features(cell types, cell states, etc) that are not observed in the spatial data, but observed in the single-cell data.

### Prerequisites

Install moscot: `pip install moscot`.

### Mapping

1. Load the data
Assume you have already loaded the single-cell data and the spatial data into the adata object.

```python
adata_sc = ... # single-cell data
adata_sp = ... # spatial data
```

2. Check the data
Before perform the mapping, you should

a. Check the number of cells and genes/features/observations in the single-cell data and the spatial data.
b. Make sure the single-cell data and the spatial data are
normalized equally, for example, the single-cell data is processed with `log1p`, the spatial data
should also be processed with `log1p`. You can detect whether the data is normalized equally by
checking the min/mean/max of the data.

3. Filter out the cell cycle genes(Optional)

```python
s_genes = ["Rrm2", "Dscc1","Prim1","Gmnn","Ccne2","E2f8","Exo1","Rad51ap1","Wdr76","Usp1","Nasp","Casp8ap2","Rad51","Msh2","Pcna"
    "Fen1","Rrm1","Cdc6","Clspn","Pola1","Tyms","Slbp","Cenpu","Mcm5","Tipin","Mcm4","Mcm6","Rfc2","Ung","Chaf1b","Cdc45"
    "Hells","Mrpl36","Polr1b","Blm","Cdca7","Dtl","Uhrf1","Ubr7","Mcm7","Gins2"]
g2m_genes = ["Nek2","Cdca8","Smc4","Lbr","Anp32e","Hmmr","Aurkb","Cdc20","Kif11","Rangap1","Cdk1","Gtse1","Tpx2","Ndc80","Ckap2"
"Mki67","Ect2","G2e3","Cenpe","Ncapd2","Pimreg","Cdc25c","Cenpf","Tubb4b","Cenpa","Bub1","Psrc1","Nuf2","Top2a","Gas2l3","Nusap1"
,"Tacc3","Cbx5","Aurka","Cdca3","Kif2c","Birc5","Hmgb2","Kif20b","Ttk","Tmpo","Ube2c","Cks2","Dlgap5","Ckap2l","Anln","Ckap5","Hjurp"
,"Ccnb2","Cks1b","Cdca2","Kif23","Ctcf"]
cellcycle_genes = s_genes + g2m_genes
cellcycle_genes = [g.upper() for g in cellcycle_genes]
filter_genes = adata_sc.var_names[adata_sc.var_names.isin(adata_sp.var_names) & ~adata_sc.var_names.isin(cellcycle_genes)]
adata_sc = adata_sc[:, filter_genes]
adata_sp = adata_sp[:, filter_genes]
```

4. Perform the mapping

```python
from moscot.problems.space import MappingProblem

mp = MappingProblem(adata_sc, adata_sp)
mp.solve(alpha=0, tau_a=1, tau_b=0.8)
```

The meaning of the parameters here:
- alpha (float) – Parameter in `[0, 1]` that interpolates between the quadratic term and the linear term. 
`alpha=1` corresponds to the pure Gromov-Wasserstein problem while `alpha=0` corresponds to the pure linear problem.
- tau_a (float) – Parameter in `(0, 1]` that defines how much unbalanced is the problem on the source marginals. If 1, the problem is balanced.
- tau_b (float) – Parameter in `(0, 1]` that defines how much unbalanced is the problem on the target marginals. If 1, the problem is balanced.

The detailed documentation of the `mp.solve` function, if you need more details about the parameters, please read:
https://moscot.readthedocs.io/en/latest/user/genapi/moscot.problems.space.MappingProblem.solve.html

### Imputation

After the mapping, you can get the mapping matrix,
and then use the mapping matrix to impute the genes/features
that are not observed in the spatial data, but observed in the single-cell data.

```python
def map_sc_2_sp_numpy(pi, label):
    """
    pi: numpy.ndarray, shape=(n_sc_cells, n_sp_cells)
    label: array-like of length n_sp_cells
    """
    # Find all unique labels, and the index of the original labels in the unique labels array
    labels, inv = np.unique(label, return_inverse=True)
    # construct one-hot matrix: shape = (n_sp_cells, n_labels)
    # M[j, k] = 1 iff label[j] == labels[k]
    M = np.eye(len(labels), dtype=pi.dtype)[inv]
    # pi.dot(M) -> shape (n_sc_cells, n_labels), the score sum of each sc cell for each label
    score_sums = pi.dot(M)
    # for each row, get the index of the label with the highest score, and then map back to the real label name
    best_idx = score_sums.argmax(axis=1)
    score_sums = score_sums * score_sums.shape[0]
    return labels[best_idx].tolist(), score_sums, labels 

# Get the mapping matrix
pi = mp.solutions[('src', 'tgt')].transport_matrix
pi = np.array(pi)

# Example: get the imputed gene expression matrix
gexp_sc = adata_sc.X
imputed_gexp = pi.dot(gexp_sc)
adata_pred = AnnData(X=imputed_gexp, obsm=adata_sp.obsm.copy(), obs=adata_sp.obs.copy())
adata_pred.obs_names = adata_sp.obs_names
adata_pred.var_names = adata_sc.var_names

# Example: get the imputed cell types(defined in the single-cell data)
mapped_label, score_sums, unique_label = map_sc_2_sp_numpy(pi, adata_sc.obs['celltype'].values)
adata_sp.obs['mapped_celltype'] = mapped_label

```

### Large dataset handling

When the dataset is very large, you should consider using the subset of the dataset to perform the mapping.
