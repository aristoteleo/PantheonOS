from annoy import AnnoyIndex
# NOTE: IntervalTree removed for performance (replaced with searchsorted).
from itertools import cycle, islice
import numpy as np
import operator
import random
import scipy
from scipy.sparse import csc_matrix, csr_matrix, vstack
from sklearn.manifold import TSNE
from sklearn.metrics.pairwise import euclidean_distances
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import normalize
import sys
import warnings

from .utils import plt, dispersion, reduce_dimensionality
from .utils import visualize_cluster, visualize_expr, visualize_dropout
from .utils import handle_zeros_in_scale

# --- Caches to avoid rebuilding expensive indices repeatedly ---
_annoy_cache = {}  # key -> AnnoyIndex

# Default parameters.
ALPHA = 0.10
APPROX = True
BATCH_SIZE = 5000
DIMRED = 100
HVG = None
KNN = 20
N_ITER = 500
PERPLEXITY = 1200
SIGMA = 15
VERBOSE = 2

# Do batch correction on a list of data sets.
def correct(datasets_full, genes_list, return_dimred=False,
            batch_size=BATCH_SIZE, verbose=VERBOSE, ds_names=None,
            dimred=DIMRED, approx=APPROX, sigma=SIGMA, alpha=ALPHA, knn=KNN,
            return_dense=False, hvg=None, union=False, seed=0):
    """Integrate and batch correct a list of data sets.

    Parameters
    ----------
    datasets_full : `list` of `scipy.sparse.csr_matrix` or of `numpy.ndarray`
        Data sets to integrate and correct.
    genes_list: `list` of `list` of `string`
        List of genes for each data set.
    return_dimred: `bool`, optional (default: `False`)
        In addition to returning batch corrected matrices, also returns
        integrated low-dimesional embeddings.
    batch_size: `int`, optional (default: `5000`)
        The batch size used in the alignment vector computation. Useful when
        correcting very large (>100k samples) data sets. Set to large value
        that runs within available memory.
    verbose: `bool` or `int`, optional (default: 2)
        When `True` or not equal to 0, prints logging output.
    ds_names: `list` of `string`, optional
        When `verbose=True`, reports data set names in logging output.
    dimred: `int`, optional (default: 100)
        Dimensionality of integrated embedding.
    approx: `bool`, optional (default: `True`)
        Use approximate nearest neighbors, greatly speeds up matching runtime.
    sigma: `float`, optional (default: 15)
        Correction smoothing parameter on Gaussian kernel.
    alpha: `float`, optional (default: 0.10)
        Alignment score minimum cutoff.
    knn: `int`, optional (default: 20)
        Number of nearest neighbors to use for matching.
    return_dense: `bool`, optional (default: `False`)
        Return `numpy.ndarray` matrices instead of `scipy.sparse.csr_matrix`.
    hvg: `int`, optional (default: None)
        Use this number of top highly variable genes based on dispersion.
    seed: `int`, optional (default: 0)
        Random seed to use.

    Returns
    -------
    corrected, genes
        By default (`return_dimred=False`), returns a two-tuple containing a
        list of `scipy.sparse.csr_matrix` each with batch corrected values,
        and a single list of genes containing the intersection of inputted
        genes.

    integrated, corrected, genes
        When `return_dimred=True`, returns a three-tuple containing a list
        of `numpy.ndarray` with integrated low dimensional embeddings, a list
        of `scipy.sparse.csr_matrix` each with batch corrected values, and a
        a single list of genes containing the intersection of inputted genes.
    """
    np.random.seed(seed)
    random.seed(seed)

    datasets_full = check_datasets(datasets_full)

    datasets, genes = merge_datasets(datasets_full, genes_list,
                                     ds_names=ds_names, union=union)
    datasets_dimred, genes = process_data(datasets, genes, hvg=hvg,
                                          dimred=dimred)

    datasets_dimred = assemble(
        datasets_dimred, # Assemble in low dimensional space.
        expr_datasets=datasets, # Modified in place.
        verbose=verbose, knn=knn, sigma=sigma, approx=approx,
        alpha=alpha, ds_names=ds_names, batch_size=batch_size,
    )

    if return_dense:
        datasets = [ ds.toarray() for ds in datasets ]

    if return_dimred:
        return datasets_dimred, datasets, genes

    return datasets, genes

# Integrate a list of data sets.
def integrate(datasets_full, genes_list, batch_size=BATCH_SIZE,
              verbose=VERBOSE, ds_names=None, dimred=DIMRED, approx=APPROX,
              sigma=SIGMA, alpha=ALPHA, knn=KNN, union=False, hvg=None, seed=0,
              sketch=False, sketch_method='geosketch', sketch_max=10000,):
    """Integrate a list of data sets.

    Parameters
    ----------
    datasets_full : `list` of `scipy.sparse.csr_matrix` or of `numpy.ndarray`
        Data sets to integrate and correct.
    genes_list: `list` of `list` of `string`
        List of genes for each data set.
    batch_size: `int`, optional (default: `5000`)
        The batch size used in the alignment vector computation. Useful when
        correcting very large (>100k samples) data sets. Set to large value
        that runs within available memory.
    verbose: `bool` or `int`, optional (default: 2)
        When `True` or not equal to 0, prints logging output.
    ds_names: `list` of `string`, optional
        When `verbose=True`, reports data set names in logging output.
    dimred: `int`, optional (default: 100)
        Dimensionality of integrated embedding.
    approx: `bool`, optional (default: `True`)
        Use approximate nearest neighbors, greatly speeds up matching runtime.
    sigma: `float`, optional (default: 15)
        Correction smoothing parameter on Gaussian kernel.
    alpha: `float`, optional (default: 0.10)
        Alignment score minimum cutoff.
    knn: `int`, optional (default: 20)
        Number of nearest neighbors to use for matching.
    hvg: `int`, optional (default: None)
        Use this number of top highly variable genes based on dispersion.
    seed: `int`, optional (default: 0)
        Random seed to use.
    sketch: `bool`, optional (default: False)
        Apply sketching-based acceleration by first downsampling the datasets.
        See Hie et al., Cell Systems (2019).
    sketch_method: {'geosketch', 'uniform'}, optional (default: `geosketch`)
        Apply the given sketching method to the data. Only used if
        `sketch=True`.
    sketch_max: `int`, optional (default: 10000)
        If a dataset has more cells than `sketch_max`, downsample to
        `sketch_max` using the method provided in `sketch_method`. Only used
        if `sketch=True`.

    Returns
    -------
    integrated, genes
        Returns a two-tuple containing a list of `numpy.ndarray` with
        integrated low dimensional embeddings and a single list of genes
        containing the intersection of inputted genes.
    """
    np.random.seed(seed)
    random.seed(seed)

    datasets_full = check_datasets(datasets_full)

    datasets, genes = merge_datasets(datasets_full, genes_list,
                                     ds_names=ds_names, union=union)
    datasets_dimred, genes = process_data(datasets, genes, hvg=hvg,
                                          dimred=dimred)

    if sketch:
        datasets_dimred = integrate_sketch(
            datasets_dimred, sketch_method=sketch_method, N=sketch_max,
            integration_fn=assemble, integration_fn_args={
                'verbose': verbose, 'knn': knn, 'sigma': sigma,
                'approx': approx, 'alpha': alpha, 'ds_names': ds_names,
                'batch_size': batch_size,
            },
        )

    else:
        datasets_dimred = assemble(
            datasets_dimred, # Assemble in low dimensional space.
            verbose=verbose, knn=knn, sigma=sigma, approx=approx,
            alpha=alpha, ds_names=ds_names, batch_size=batch_size,
        )

    return datasets_dimred, genes

# Batch correction with scanpy's AnnData object.
def correct_scanpy(adatas, **kwargs):
    """Batch correct a list of `scanpy.api.AnnData`.

    Parameters
    ----------
    adatas : `list` of `scanpy.api.AnnData`
        Data sets to integrate and/or correct.
        `adata.var_names` must be set to the list of genes.
    return_dimred : `bool`, optional (default=`False`)
        When `True`, the returned `adatas` are each modified to
        also have the integrated low-dimensional embeddings in
        `adata.obsm['X_scanorama']`.
    kwargs : `dict`
        See documentation for the `correct()` method for a full list of
        parameters to use for batch correction.

    Returns
    -------
    corrected
        By default (`return_dimred=False`), returns a list of new
        `scanpy.api.AnnData`.
        When `return_dimred=True`, `corrected` also includes the
        integrated low-dimensional embeddings in
        `adata.obsm['X_scanorama']`.
    """
    if 'return_dimred' in kwargs and kwargs['return_dimred']:
        datasets_dimred, datasets, genes = correct(
            [adata.X for adata in adatas],
            [adata.var_names.values for adata in adatas],
            **kwargs
        )
    else:
        datasets, genes = correct(
            [adata.X for adata in adatas],
            [adata.var_names.values for adata in adatas],
            **kwargs
        )

    from anndata import AnnData

    new_adatas = []
    for i in range(len((adatas))):
        adata = AnnData(datasets[i])
        adata.obs = adatas[i].obs
        adata.obsm = adatas[i].obsm

        # Ensure that variables are in the right order,
        # as Scanorama rearranges genes to be in alphabetical
        # order and as the intersection (or union) of the
        # original gene sets.
        adata.var_names = genes
        gene2idx = { gene: idx for idx, gene in
                     zip(adatas[i].var.index,
                         adatas[i].var_names.values) }
        var_idx = [ gene2idx[gene] for gene in genes ]
        adata.var = adatas[i].var.loc[var_idx]

        adata.uns = adatas[i].uns
        new_adatas.append(adata)

    if 'return_dimred' in kwargs and kwargs['return_dimred']:
        for adata, X_dimred in zip(new_adatas, datasets_dimred):
            adata.obsm['X_scanorama'] = X_dimred

    return new_adatas

# Integration with scanpy's AnnData object.
def integrate_scanpy(adatas, **kwargs):
    """Integrate a list of `scanpy.api.AnnData`.

    Parameters
    ----------
    adatas : `list` of `scanpy.api.AnnData`
        Data sets to integrate.
    kwargs : `dict`
        See documentation for the `integrate()` method for a full list of
        parameters to use for batch correction.

    Returns
    -------
    None
    """
    datasets_dimred, genes = integrate(
        [adata.X for adata in adatas],
        [adata.var_names.values for adata in adatas],
        **kwargs
    )

    for adata, X_dimred in zip(adatas, datasets_dimred):
        adata.obsm['X_scanorama'] = X_dimred

# Visualize a scatter plot with cluster labels in the
# `cluster' variable.
def plot_clusters(coords, clusters, s=1, colors=None):
    if coords.shape[0] != clusters.shape[0]:
        sys.stderr.write(
            'Error: mismatch, {} cells, {} labels\n'
            .format(coords.shape[0], clusters.shape[0])
        )
        sys.exit(1)

    if colors is None:
        colors = np.array(
            list(islice(cycle([
                '#377eb8', '#ff7f00', '#4daf4a',
                '#f781bf', '#a65628', '#984ea3',
                '#999999', '#e41a1c', '#dede00',
                '#ffe119', '#e6194b', '#ffbea3',
                '#911eb4', '#46f0f0', '#f032e6',
                '#d2f53c', '#008080', '#e6beff',
                '#aa6e28', '#800000', '#aaffc3',
                '#808000', '#ffd8b1', '#000080',
                '#808080', '#fabebe', '#a3f4ff'
            ]), int(max(clusters) + 1)))
        )

    plt.figure()
    plt.scatter(coords[:, 0], coords[:, 1],
                c=colors[clusters], s=s)

# Put datasets into a single matrix with the intersection of all genes.
def merge_datasets(datasets, genes, ds_names=None, verbose=True,
                   union=False):
    if union:
        sys.stderr.write(
            'WARNING: Integrating based on the union of genes is '
            'highly discouraged, consider taking the intersection '
            'or requantifying gene expression.\n'
        )

    # Find genes in common.
    keep_genes = set()
    for idx, gene_list in enumerate(genes):
        if len(keep_genes) == 0:
            keep_genes = set(gene_list)
        elif union:
            keep_genes |= set(gene_list)
        else:
            keep_genes &= set(gene_list)
        if not union and not ds_names is None and verbose:
            print('After {}: {} genes'.format(ds_names[idx], len(keep_genes)))
        if len(keep_genes) == 0:
            print('Error: No genes found in all datasets, exiting...')
            sys.exit(1)
    if verbose:
        print('Found {} genes among all datasets'
              .format(len(keep_genes)))

    if union:
        union_genes = sorted(keep_genes)
        for i in range(len(datasets)):
            if verbose:
                print('Processing data set {}'.format(i))
            X_new = np.zeros((datasets[i].shape[0], len(union_genes)))
            X_old = csc_matrix(datasets[i])
            gene_to_idx = { gene: idx for idx, gene in enumerate(genes[i]) }
            for j, gene in enumerate(union_genes):
                if gene in gene_to_idx:
                    X_new[:, j] = X_old[:, gene_to_idx[gene]].toarray().flatten()
            datasets[i] = csr_matrix(X_new)
        ret_genes = np.array(union_genes)
    else:
        # Only keep genes in common.
        ret_genes = np.array(sorted(keep_genes))
        for i in range(len(datasets)):
            # Remove duplicate genes.
            uniq_genes, uniq_idx = np.unique(genes[i], return_index=True)
            datasets[i] = datasets[i][:, uniq_idx]

            # Do gene filtering.
            gene_sort_idx = np.argsort(uniq_genes)
            gene_idx = [ idx for idx in gene_sort_idx
                         if uniq_genes[idx] in keep_genes ]
            datasets[i] = datasets[i][:, gene_idx]
            assert(np.array_equal(uniq_genes[gene_idx], ret_genes))

    return datasets, ret_genes

def check_datasets(datasets_full):
    datasets_new = []
    for i, ds in enumerate(datasets_full):
        if issubclass(type(ds), np.ndarray):
            datasets_new.append(csr_matrix(ds))
        elif issubclass(type(ds), csr_matrix):
            datasets_new.append(ds)
        else:
            sys.stderr.write('ERROR: Data sets must be numpy array or '
                             'scipy.sparse.csr_matrix, received type '
                             '{}.\n'.format(type(ds)))
            sys.exit(1)
    return datasets_new

# Randomized SVD.
def dimensionality_reduce(datasets, dimred=DIMRED):
    X = vstack(datasets)
    X = reduce_dimensionality(X, dim_red_k=dimred)
    datasets_dimred = []
    base = 0
    for ds in datasets:
        datasets_dimred.append(X[base:(base + ds.shape[0]), :])
        base += ds.shape[0]
    return datasets_dimred

# Normalize and reduce dimensionality.
def process_data(datasets, genes, hvg=HVG, dimred=DIMRED, verbose=False):
    # Only keep highly variable genes
    if not hvg is None and hvg > 0 and hvg < len(genes):
        if verbose:
            print('Highly variable filter...')
        X = vstack(datasets)
        disp = dispersion(X)
        highest_disp_idx = np.argsort(disp[0])[::-1]
        top_genes = set(genes[highest_disp_idx[range(hvg)]])
        for i in range(len(datasets)):
            gene_idx = [ idx for idx, g_i in enumerate(genes)
                         if g_i in top_genes ]
            datasets[i] = datasets[i][:, gene_idx]
        genes = np.array(sorted(top_genes))

    # Normalize.
    if verbose:
        print('Normalizing...')
    for i, ds in enumerate(datasets):
        datasets[i] = normalize(ds, axis=1)

    # Compute compressed embedding.
    if dimred > 0:
        if verbose:
            print('Reducing dimension...')
        datasets_dimred = dimensionality_reduce(datasets, dimred=dimred)
        if verbose:
            print('Done processing.')
        return datasets_dimred, genes

    if verbose:
        print('Done processing.')

    return datasets, genes

# Plot t-SNE visualization.
def visualize(assembled, labels, namespace, data_names,
              gene_names=None, gene_expr=None, genes=None,
              n_iter=N_ITER, perplexity=PERPLEXITY, verbose=VERBOSE,
              learn_rate=200., early_exag=12., embedding=None,
              shuffle_ds=False, size=1, multicore_tsne=True,
              image_suffix='.svg', viz_cluster=False, colors=None,
              random_state=None,):
    # Fit t-SNE.
    if embedding is None:
        try:
            from MulticoreTSNE import MulticoreTSNE
            tsne = MulticoreTSNE(
                n_iter=n_iter, perplexity=perplexity,
                verbose=verbose, random_state=random_state,
                learning_rate=learn_rate,
                early_exaggeration=early_exag,
                n_jobs=40
            )
        except ImportError:
            multicore_tsne = False

        if not multicore_tsne:
            tsne = TSNE(
                n_iter=n_iter, perplexity=perplexity,
                verbose=verbose, random_state=random_state,
                learning_rate=learn_rate,
                early_exaggeration=early_exag
            )

        tsne.fit(np.concatenate(assembled))
        embedding = tsne.embedding_

    if shuffle_ds:
        rand_idx = range(embedding.shape[0])
        random.shuffle(list(rand_idx))
        embedding = embedding[rand_idx, :]
        labels = labels[rand_idx]

    # Plot clusters together.
    plot_clusters(embedding, labels, s=size, colors=colors)
    plt.title(('Panorama ({} iter, perplexity: {}, sigma: {}, ' +
               'knn: {}, hvg: {}, dimred: {}, approx: {})')
              .format(n_iter, perplexity, SIGMA, KNN, HVG,
                      DIMRED, APPROX))
    plt.savefig(namespace + image_suffix, dpi=500)

    # Plot clusters individually.
    if viz_cluster and not shuffle_ds:
        for i in range(len(data_names)):
            visualize_cluster(embedding, i, labels,
                              cluster_name=data_names[i], size=size,
                              viz_prefix=namespace,
                              image_suffix=image_suffix)

    # Plot gene expression levels.
    if (not gene_names is None) and \
       (not gene_expr is None) and \
       (not genes is None):
        if shuffle_ds:
            gene_expr = gene_expr[rand_idx, :]
        for gene_name in gene_names:
            visualize_expr(gene_expr, embedding,
                           genes, gene_name, size=size,
                           viz_prefix=namespace,
                           image_suffix=image_suffix)

    return embedding

# Exact nearest neighbors search.
def nn(ds1, ds2, knn=KNN, metric_p=2, return_ind=True):
    # Find nearest neighbors of first dataset.
    nn_ = NearestNeighbors(n_neighbors=knn, p=metric_p)
    nn_.fit(ds2)
    ind = nn_.kneighbors(ds1, return_distance=False)
    if return_ind:
        return ind

    # Backwards-compatible slow path (avoid where possible).
    match = set()
    for a, b in zip(range(ds1.shape[0]), ind):
        for b_i in b:
            match.add((a, b_i))
    return match

# Approximate nearest neighbors using locality sensitive hashing.
def nn_approx(ds1, ds2, knn=KNN, metric='manhattan', n_trees=10, return_ind=True):
    # Build/reuse index (major speed win vs rebuilding on every call).
    # Key uses object identity + data pointer (more stable if arrays are views/reallocated).
    try:
        data_ptr = int(ds2.__array_interface__['data'][0])
    except Exception:
        data_ptr = None
    key = (id(ds2), data_ptr, ds2.shape, metric, int(n_trees))

    a = _annoy_cache.get(key, None)
    if a is None:
        a = AnnoyIndex(ds2.shape[1], metric=metric)
        for i in range(ds2.shape[0]):
            a.add_item(i, ds2[i, :])
        a.build(n_trees)
        _annoy_cache[key] = a

    # Search index.
    ind = []
    for i in range(ds1.shape[0]):
        ind.append(a.get_nns_by_vector(ds1[i, :], knn, search_k=-1))
    ind = np.array(ind, dtype=np.int64)

    if return_ind:
        return ind

    # Backwards-compatible slow path (avoid where possible).
    match = set()
    for a_i, b in zip(range(ds1.shape[0]), ind):
        for b_i in b:
            match.add((a_i, int(b_i)))
    return match

# Find mutual nearest neighbors.
def mnn(ds1, ds2, knn=KNN, approx=APPROX):
    # Compute neighbor index matrices.
    if approx:
        ind12 = nn_approx(ds1, ds2, knn=knn, return_ind=True)
        ind21 = nn_approx(ds2, ds1, knn=knn, return_ind=True)
    else:
        ind12 = nn(ds1, ds2, knn=knn, return_ind=True)
        ind21 = nn(ds2, ds1, knn=knn, return_ind=True)

    n1 = ind12.shape[0]
    k1 = ind12.shape[1]
    n2 = ind21.shape[0]
    k2 = ind21.shape[1]

    # Build fast membership structure for ds2 -> ds1 neighbors as CSR adjacency.
    # Row b contains ds1 indices that are neighbors of b.
    row_ids = np.repeat(np.arange(n2, dtype=np.int64), k2)
    col_ids = ind21.reshape(-1).astype(np.int64, copy=False)
    data = np.ones_like(col_ids, dtype=np.int8)
    adj21 = scipy.sparse.csr_matrix((data, (row_ids, col_ids)), shape=(n2, n1))

    # Mutual pairs are those (a, b) where b in ind12[a] and a is present in row b of adj21.
    a_rep = np.repeat(np.arange(n1, dtype=np.int64), k1)
    b_flat = ind12.reshape(-1).astype(np.int64, copy=False)
    mask = adj21[b_flat, a_rep].A1.astype(bool, copy=False)

    a_mut = a_rep[mask]
    b_mut = b_flat[mask]

    # Return as a set of pairs for compatibility with downstream code.
    # (Kept as set because matches are later treated as sets.)
    return set(zip(a_mut.tolist(), b_mut.tolist()))

# Visualize alignment between two datasets.
def plot_mapping(curr_ds, curr_ref, ds_ind, ref_ind):
    tsne = TSNE(n_iter=400, verbose=VERBOSE, random_state=69)

    tsne.fit(curr_ds)
    plt.figure()
    coords_ds = tsne.embedding_[:, :]
    coords_ds[:, 1] += 100
    plt.scatter(coords_ds[:, 0], coords_ds[:, 1])

    tsne.fit(curr_ref)
    coords_ref = tsne.embedding_[:, :]
    plt.scatter(coords_ref[:, 0], coords_ref[:, 1])

    x_list, y_list = [], []
    for r_i, c_i in zip(ds_ind, ref_ind):
        x_list.append(coords_ds[r_i, 0])
        x_list.append(coords_ref[c_i, 0])
        x_list.append(None)
        y_list.append(coords_ds[r_i, 1])
        y_list.append(coords_ref[c_i, 1])
        y_list.append(None)
    plt.plot(x_list, y_list, 'b-', alpha=0.3)
    plt.show()


# Populate a table (in place) that stores mutual nearest neighbors
# between datasets.
def fill_table(table, i, curr_ds, datasets, base_ds=0,
               knn=KNN, approx=APPROX):
    curr_ref = np.concatenate(datasets)
    if approx:
        ind = nn_approx(curr_ds, curr_ref, knn=knn, return_ind=True)
    else:
        ind = nn(curr_ds, curr_ref, knn=knn, metric_p=1, return_ind=True)

    # Replace IntervalTree with prefix sums + searchsorted (vectorized boundaries).
    sizes = np.array([ds.shape[0] for ds in datasets], dtype=int)
    cum_sizes = np.cumsum(sizes)

    # Vectorize (d, r) pairs.
    n = ind.shape[0]
    k = ind.shape[1]
    d_flat = np.repeat(np.arange(n, dtype=np.int64), k)
    r_flat = ind.reshape(-1).astype(np.int64, copy=False)

    # Determine which dataset index r falls into.
    j_rel = np.searchsorted(cum_sizes, r_flat, side='right').astype(np.int64, copy=False)
    j = (base_ds + j_rel).astype(np.int64, copy=False)
    base = np.where(j_rel == 0, 0, cum_sizes[j_rel - 1]).astype(np.int64, copy=False)
    r_local = (r_flat - base).astype(np.int64, copy=False)
    assert np.all(r_local >= 0)

    # Store all nearest-neighbor pairs between datasets in table.
    # (Still uses per-(i,j) Python sets, but avoids per-pair loops building a global set.)
    for j_rel_u in np.unique(j_rel):
        mask = (j_rel == j_rel_u)
        j_u = int(base_ds + j_rel_u)
        if not (i, j_u) in table:
            table[(i, j_u)] = set()
        table[(i, j_u)].update(zip(d_flat[mask].tolist(), r_local[mask].tolist()))

gs_idxs = None

# Fill table of alignment scores.
def find_alignments_table(datasets, knn=KNN, approx=APPROX, verbose=VERBOSE,
                          prenormalized=False, score_t=1.0, score_u=1.0):
    if not prenormalized:
        datasets = [ normalize(ds, axis=1) for ds in datasets ]

    table = {}
    for i in range(len(datasets)):
        if len(datasets[:i]) > 0:
            fill_table(table, i, datasets[i], datasets[:i], knn=knn,
                       approx=approx)
        if len(datasets[i+1:]) > 0:
            fill_table(table, i, datasets[i], datasets[i+1:],
                       knn=knn, base_ds=i+1, approx=approx)

    # Count all mutual nearest neighbors between datasets and compute
    # improved confidence score combining:
    # coverage * exp(-median_dist/t) * exp(-bias_var/u)
    matches = {}
    table1 = {}
    if verbose > 1:
        table_print = np.zeros((len(datasets), len(datasets)))

    for i in range(len(datasets)):
        for j in range(len(datasets)):
            if i >= j:
                continue
            if not (i, j) in table or not (j, i) in table:
                continue

            match_ij = table[(i, j)]
            match_ji = set([ (b, a) for a, b in table[(j, i)] ])
            mutual = match_ij & match_ji
            if len(mutual) == 0:
                continue
            matches[(i, j)] = mutual

            # Coverage term (existing behavior).
            cov_i = float(len(set([ idx for idx, _ in mutual ]))) / datasets[i].shape[0]
            cov_j = float(len(set([ idx for _, idx in mutual ]))) / datasets[j].shape[0]
            coverage = max(cov_i, cov_j)

            # Tightness term: median distance between matched pairs.
            ds1 = datasets[i]
            ds2 = datasets[j]
            ds_ind = np.array([ a for a, _ in mutual ], dtype=int)
            ref_ind = np.array([ b for _, b in mutual ], dtype=int)

            # Ensure dense arrays for distance/bias stats.
            X1 = ds1[ds_ind, :]
            X2 = ds2[ref_ind, :]
            if scipy.sparse.issparse(X1):
                X1 = X1.toarray()
            else:
                X1 = np.asarray(X1)
            if scipy.sparse.issparse(X2):
                X2 = X2.toarray()
            else:
                X2 = np.asarray(X2)

            d = np.linalg.norm(X1 - X2, axis=1)
            med_dist = float(np.median(d))

            # Bias consistency: average variance across dimensions.
            b = X2 - X1
            bias_var = float(np.mean(np.var(b, axis=0)))

            score = coverage * np.exp(-med_dist / float(score_t)) * np.exp(-bias_var / float(score_u))
            table1[(i, j)] = score

            if verbose > 1:
                table_print[i, j] += table1[(i, j)]

    if verbose > 1:
        print(table_print)
        return table1, table_print, matches
    else:
        return table1, None, matches

# Find the matching pairs of cells between datasets.
def find_alignments(datasets, knn=KNN, approx=APPROX, verbose=VERBOSE,
                    alpha=ALPHA, prenormalized=False,):
    table1, _, matches = find_alignments_table(
        datasets, knn=knn, approx=approx, verbose=verbose,
        prenormalized=prenormalized,
    )

    alignments = [ (i, j) for (i, j), val in reversed(
        sorted(table1.items(), key=operator.itemgetter(1))
    ) if val > alpha ]

    # Return alignments, matches, and edge weights (scores) for MST merging.
    return alignments, matches, table1

# Find connections between datasets to identify panoramas.
def connect(datasets, knn=KNN, approx=APPROX, alpha=ALPHA,
            verbose=VERBOSE):
    # Find alignments.
    alignments, _, _ = find_alignments(
        datasets, knn=knn, approx=approx, alpha=alpha,
        verbose=verbose
    )
    if verbose:
        print(alignments)

    panoramas = []
    connected = set()
    for i, j in alignments:
        # See if datasets are involved in any current panoramas.
        panoramas_i = [ panoramas[p] for p in range(len(panoramas))
                        if i in panoramas[p] ]
        assert(len(panoramas_i) <= 1)
        panoramas_j = [ panoramas[p] for p in range(len(panoramas))
                        if j in panoramas[p] ]
        assert(len(panoramas_j) <= 1)

        if len(panoramas_i) == 0 and len(panoramas_j) == 0:
            panoramas.append([ i ])
            panoramas_i = [ panoramas[-1] ]

        if len(panoramas_i) == 0:
            panoramas_j[0].append(i)
        elif len(panoramas_j) == 0:
            panoramas_i[0].append(j)
        elif panoramas_i[0] != panoramas_j[0]:
            panoramas_i[0] += panoramas_j[0]
            panoramas.remove(panoramas_j[0])

        connected.add(i)
        connected.add(j)

    for i in range(len(datasets)):
        if not i in connected:
            panoramas.append([ i ])

    return panoramas

# To reduce memory usage, split bias computation into batches.
# Uses sparse local adaptive kernel smoothing on kNN anchors instead of
# dense global RBF regression.
def batch_bias(curr_ds, match_ds, bias, batch_size=None, sigma=SIGMA,
               k=30, sigma_scale=1.0, anchor_weights=None,
               neigh=None, match_ds_arr=None,
               pre_nn_idx=None, pre_g=None, pre_S=None,
               pre_row_ids=None, pre_indptr=None, pre_indices=None):
    # Convert to dense arrays for vectorized math.
    # Low-dimensional embeddings are already dense; for safety, handle sparse.
    if scipy.sparse.issparse(curr_ds):
        curr_ds_arr = curr_ds.toarray()
    else:
        curr_ds_arr = np.asarray(curr_ds)
    curr_ds_arr = np.asarray(curr_ds_arr, dtype=np.float32)

    if scipy.sparse.issparse(bias):
        bias_arr = bias.toarray()
    else:
        bias_arr = np.asarray(bias)
    bias_arr = np.asarray(bias_arr, dtype=np.float32)

    n = curr_ds_arr.shape[0]

    # --- Fast path: precomputed kNN + base Gaussian weights (and optional CSR pattern) ---
    if pre_nn_idx is not None and pre_g is not None:
        nn_idx_full = np.asarray(pre_nn_idx, dtype=np.int64)
        g_full = np.asarray(pre_g, dtype=np.float32)
        if nn_idx_full.shape != g_full.shape:
            raise ValueError('pre_nn_idx and pre_g must have same shape, got {} vs {}'
                             .format(nn_idx_full.shape, g_full.shape))

        m = int(np.max(nn_idx_full) + 1) if nn_idx_full.size > 0 else 0
        if m == 0 or n == 0:
            return np.zeros((n, bias_arr.shape[1]), dtype=np.float32)

        if anchor_weights is None:
            anchor_w = np.ones((m,), dtype=np.float32)
        else:
            anchor_w = np.asarray(anchor_weights, dtype=np.float32)
            if anchor_w.ndim != 1 or anchor_w.shape[0] != m:
                raise ValueError('anchor_weights must be shape (n_anchors,), got {}'
                                 .format(anchor_w.shape))
            anchor_w = np.maximum(anchor_w, 0.0)
            if float(np.sum(anchor_w)) == 0.0:
                anchor_w = np.ones((m,), dtype=np.float32)

        k_eff = nn_idx_full.shape[1]

        if pre_S is not None:
            # CSR smoother path: avg_bias = S.dot(bias_arr), where S is (n_cells x n_anchors).
            # Hot-loop optimized: avoid reduceat+repeat and avoid rebuilding CSR structure.
            S = pre_S.tocsr()
            indices = pre_indices if pre_indices is not None else S.indices
            indptr = pre_indptr if pre_indptr is not None else S.indptr
            row_ids = pre_row_ids

            g_flat = g_full.reshape(-1).astype(np.float32, copy=False)
            if g_flat.shape[0] != indices.shape[0]:
                raise ValueError('pre_g flattened size must match CSR nnz: {} vs {}'
                                 .format(g_flat.shape[0], indices.shape[0]))

            data = g_flat * anchor_w[indices].astype(np.float32, copy=False)

            # Fast row sums via bincount on cached row ids (k_eff is constant).
            if row_ids is None:
                # Fallback (shouldn't happen for cached edge path).
                row_ids = np.repeat(np.arange(n, dtype=np.int64), int(len(indices) / n) if n > 0 else 0)

            row_sums = np.bincount(
                row_ids.astype(np.int64, copy=False),
                weights=data.astype(np.float32, copy=False),
                minlength=n
            ).astype(np.float32, copy=False)
            row_sums = handle_zeros_in_scale(row_sums, copy=False).astype(np.float32, copy=False)

            inv_row = (np.float32(1.0) / row_sums).astype(np.float32, copy=False)
            data *= inv_row[row_ids].astype(np.float32, copy=False)

            S = scipy.sparse.csr_matrix((data, indices, indptr), shape=S.shape)
            return S.dot(bias_arr).astype(np.float32, copy=False)

        # Fallback: dense weighted average without CSR (still avoids kneighbors/exp).
        w = g_full * anchor_w[nn_idx_full]
        wsum = np.sum(w, axis=1)
        wsum = handle_zeros_in_scale(wsum, copy=False)
        w = (w / wsum[:, np.newaxis]).astype(np.float32, copy=False)

        # Avoid 3D gather via per-dimension weighted sum (keeps memory low).
        out = np.zeros((n, bias_arr.shape[1]), dtype=np.float32)
        for dim in range(bias_arr.shape[1]):
            out[:, dim] = np.sum(w * bias_arr[nn_idx_full, dim], axis=1).astype(np.float32)
        return out

    # --- Legacy path (kept for compatibility) ---
    # Allow passing precomputed anchor array to avoid repeated conversions.
    if match_ds_arr is None:
        if scipy.sparse.issparse(match_ds):
            match_ds_arr = match_ds.toarray()
        else:
            match_ds_arr = np.asarray(match_ds)
    match_ds_arr = np.asarray(match_ds_arr, dtype=np.float32)

    m = match_ds_arr.shape[0]
    if m == 0 or n == 0:
        return np.zeros(curr_ds_arr.shape, dtype=np.float32)

    if anchor_weights is None:
        anchor_w = np.ones((m,), dtype=np.float32)
    else:
        anchor_w = np.asarray(anchor_weights, dtype=np.float32)
        if anchor_w.ndim != 1 or anchor_w.shape[0] != m:
            raise ValueError('anchor_weights must be shape (n_anchors,), got {}'
                             .format(anchor_w.shape))
        anchor_w = np.maximum(anchor_w, 0.0)
        if float(np.sum(anchor_w)) == 0.0:
            anchor_w = np.ones((m,), dtype=np.float32)

    k_eff = int(min(max(1, k), m))

    if neigh is None:
        neigh = NearestNeighbors(n_neighbors=k_eff)
        neigh.fit(match_ds_arr)

    if batch_size is None:
        batch_size = n

    avg_bias = np.zeros((n, bias_arr.shape[1]), dtype=np.float32)

    base = 0
    while base < n:
        batch_idx = np.arange(base, min(base + batch_size, n))
        Xb = curr_ds_arr[batch_idx, :]

        dists, nn_idx = neigh.kneighbors(Xb, return_distance=True)
        dists = np.asarray(dists, dtype=np.float32)

        sigma_i = np.median(dists, axis=1).astype(np.float32) * np.float32(sigma_scale)
        sigma_i = handle_zeros_in_scale(sigma_i, copy=False)

        denom = np.float32(2.0) * (sigma_i ** np.float32(2.0))
        denom = handle_zeros_in_scale(denom, copy=False)
        w = np.exp(-(dists ** np.float32(2.0)) / denom[:, np.newaxis]).astype(np.float32)

        aw = anchor_w[nn_idx]
        w *= aw

        wsum = np.sum(w, axis=1)
        wsum = handle_zeros_in_scale(wsum, copy=False)
        w /= wsum[:, np.newaxis]

        avg_bias[batch_idx, :] = np.einsum(
            'ij,ijk->ik',
            w,
            bias_arr[nn_idx, :]
        ).astype(np.float32)

        base += batch_size

    return avg_bias

# Compute nonlinear translation vectors between dataset
# and a reference.
def transform(curr_ds, curr_ref, ds_ind, ref_ind, sigma=SIGMA, cn=False,
              batch_size=None, k=30, sigma_scale=1.0, shrink=1.0,
              anchor_weights=None):
    # Compute the matching.
    match_ds = curr_ds[ds_ind, :]
    match_ref = curr_ref[ref_ind, :]
    bias = match_ref - match_ds

    # For expression correction (cn=True), operate on dense to compute smoothing,
    # and convert the final bias back to sparse.
    if cn:
        match_ds = match_ds.toarray()
        curr_ds = curr_ds.toarray()
        bias = bias.toarray()

    with warnings.catch_warnings():
        warnings.filterwarnings('error', category=RuntimeWarning)
        try:
            avg_bias = batch_bias(
                curr_ds, match_ds, bias,
                sigma=sigma, batch_size=batch_size,
                k=k, sigma_scale=sigma_scale,
                anchor_weights=anchor_weights
            )
        except RuntimeWarning:
            sys.stderr.write('WARNING: Oversmoothing detected, refusing to batch '
                             'correct, consider lowering sigma value.\n')
            return csr_matrix(curr_ds.shape, dtype=float)
        except MemoryError:
            if batch_size is None:
                sys.stderr.write('WARNING: Out of memory, consider turning on '
                                 'batched computation with batch_size parameter.\n')
            else:
                sys.stderr.write('WARNING: Out of memory, consider lowering '
                                 'the batch_size parameter.\n')
            return csr_matrix(curr_ds.shape, dtype=float)

    # Regularize correction magnitude (shrinkage).
    avg_bias *= float(shrink)

    if cn:
        avg_bias = csr_matrix(avg_bias)

    return avg_bias

# Finds alignments between datasets and uses them to construct
# panoramas. "Merges" datasets by correcting gene expression
# values.
def assemble(datasets, verbose=VERBOSE, view_match=False, knn=KNN,
             sigma=SIGMA, approx=APPROX, alpha=ALPHA, expr_datasets=None,
             ds_names=None, batch_size=None,
             alignments=None, matches=None):
    if len(datasets) == 1:
        return datasets

    # --- Build alignment graph (edges + matches + weights) ---
    edge_weights = None
    if alignments is None and matches is None:
        alignments, matches, edge_weights = find_alignments(
            datasets, knn=knn, approx=approx, alpha=alpha, verbose=verbose,
        )

    # If matches/alignments were passed explicitly, we can't MST without weights.
    # Fall back to uniform weights in that case.
    if edge_weights is None:
        edge_weights = { e: 1.0 for e in (alignments or []) }

    # Build maximum spanning forest (MSF) over datasets to keep edge set sparse/stable.
    n_ds = len(datasets)
    parent = list(range(n_ds))
    rank = [0] * n_ds

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra == rb:
            return False
        if rank[ra] < rank[rb]:
            parent[ra] = rb
        elif rank[ra] > rank[rb]:
            parent[rb] = ra
        else:
            parent[rb] = ra
            rank[ra] += 1
        return True

    edges = []
    for (i, j) in (alignments or []):
        w = edge_weights.get((i, j), edge_weights.get((j, i), 0.0))
        edges.append((w, i, j))
    edges.sort(reverse=True, key=lambda x: x[0])

    mst_edges = []
    for w, i, j in edges:
        if union(i, j):
            mst_edges.append((w, i, j))

    if verbose:
        if ds_names is None:
            print('MST/MSF merge edges: {}'.format([ (i, j) for _, i, j in mst_edges ]))
        else:
            print('MST/MSF merge edges: {}'.format([ (ds_names[i], ds_names[j]) for _, i, j in mst_edges ]))

    # --- Global symmetric iterative optimization over correction fields ---
    # f_d is represented explicitly per cell (same shape as X_d).
    # We iteratively compute robust, locally smoothed residual "pulls" from neighbors.
    T = 5          # outer iterations
    eta = 0.5      # damping / step size
    lam = 0.05     # L2 shrink on f
    mu = 0.10      # within-dataset Laplacian smoothing on f (structure preserving)
    lap_k = min(15, max(5, knn))  # kNN for Laplacian smoothing
    huber_delta = 1.0             # robust correspondence threshold in embedding units

    # Ensure we operate on dense embeddings (low-dimensional space), and use float32.
    X0 = [ np.asarray(X, dtype=np.float32) for X in datasets ]
    f = [ np.zeros_like(X, dtype=np.float32) for X in X0 ]

    # Precompute per-dataset kNN for Laplacian smoothing of f (structure preservation).
    # Store neighbor indices only to keep it light.
    knn_graph = []
    for d in range(n_ds):
        Xd = X0[d]
        k_eff = int(min(max(2, lap_k), Xd.shape[0]))
        neigh = NearestNeighbors(n_neighbors=k_eff)
        neigh.fit(Xd)
        _, idx = neigh.kneighbors(Xd, return_distance=True)
        knn_graph.append(idx)

    # Build adjacency list over MSF edges with weights.
    adj = { i: [] for i in range(n_ds) }
    for w, i, j in mst_edges:
        # clamp weight into a stable range and use as confidence
        w = float(max(0.05, min(1.0, w)))
        adj[i].append((j, w))
        adj[j].append((i, w))

    # Helper: robust (Huber IRLS-style) weights from residual squared norms (sqrt-free for most).
    def huber_weights_r2(r2, delta2, delta):
        r2 = np.asarray(r2, dtype=np.float32)
        w = np.ones_like(r2, dtype=np.float32)
        mask = r2 > np.float32(delta2)
        if np.any(mask):
            w[mask] = np.float32(delta) / (np.sqrt(r2[mask]) + np.float32(1e-12))
        return w

    # Helper: fast approximate quantile via selection (no full sort).
    def approx_quantile(u, q):
        u = np.asarray(u, dtype=np.float32)
        if u.size == 0:
            return np.float32(0.0)
        k = int(q * float(u.size - 1))
        k = max(0, min(u.size - 1, k))
        return np.float32(np.partition(u, k)[k])

    # Helper: apply Laplacian-like smoothing to correction field f_d.
    def smooth_field(field, neigh_idx, strength):
        # field: (n, k), neigh_idx: (n, k_eff)
        # simple neighbor averaging update: field <- (1-strength)*field + strength*mean(neigh)
        if strength <= 0.0:
            return field
        nbr_mean = np.mean(field[neigh_idx, :], axis=1).astype(np.float32)
        return (np.float32(1.0) - np.float32(strength)) * field + np.float32(strength) * nbr_mean

    # --- Change #1/#2: cache per-edge anchors + precomputed all-cells->anchors kNN + base Gaussian weights + CSR pattern ---
    edge_cache = {}
    for _, d, e in mst_edges:
        if (d, e) in matches:
            mutual = matches[(d, e)]
            ds_ind = np.array([ a for a, _ in mutual ], dtype=int)
            ref_ind = np.array([ b for _, b in mutual ], dtype=int)
        elif (e, d) in matches:
            mutual = matches[(e, d)]
            ds_ind = np.array([ b for _, b in mutual ], dtype=int)
            ref_ind = np.array([ a for a, _ in mutual ], dtype=int)
        else:
            continue

        if ds_ind.size == 0:
            continue

        A_d = np.asarray(X0[d][ds_ind, :], dtype=np.float32)
        A_e = np.asarray(X0[e][ref_ind, :], dtype=np.float32)

        k_eff_d = int(min(max(1, 30), A_d.shape[0]))
        k_eff_e = int(min(max(1, 30), A_e.shape[0]))

        neigh_d = NearestNeighbors(n_neighbors=k_eff_d).fit(A_d)
        neigh_e = NearestNeighbors(n_neighbors=k_eff_e).fit(A_e)

        # Precompute for all cells: neighbor anchors + distances.
        dists_d, nn_idx_d = neigh_d.kneighbors(X0[d], return_distance=True)
        dists_e, nn_idx_e = neigh_e.kneighbors(X0[e], return_distance=True)
        dists_d = np.asarray(dists_d, dtype=np.float32)
        dists_e = np.asarray(dists_e, dtype=np.float32)
        nn_idx_d = np.asarray(nn_idx_d, dtype=np.int64)
        nn_idx_e = np.asarray(nn_idx_e, dtype=np.int64)

        # Precompute base Gaussian weights (adaptive bandwidth from median distances).
        sigma_i_d = np.median(dists_d, axis=1).astype(np.float32)
        sigma_i_d = (sigma_i_d * np.float32(1.0)).astype(np.float32, copy=False)
        sigma_i_d = handle_zeros_in_scale(sigma_i_d, copy=False)
        denom_d = np.float32(2.0) * (sigma_i_d ** np.float32(2.0))
        denom_d = handle_zeros_in_scale(denom_d, copy=False)
        g_d = np.exp(-(dists_d ** np.float32(2.0)) / denom_d[:, None]).astype(np.float32)

        sigma_i_e = np.median(dists_e, axis=1).astype(np.float32)
        sigma_i_e = (sigma_i_e * np.float32(1.0)).astype(np.float32, copy=False)
        sigma_i_e = handle_zeros_in_scale(sigma_i_e, copy=False)
        denom_e = np.float32(2.0) * (sigma_i_e ** np.float32(2.0))
        denom_e = handle_zeros_in_scale(denom_e, copy=False)
        g_e = np.exp(-(dists_e ** np.float32(2.0)) / denom_e[:, None]).astype(np.float32)

        # CSR sparsity pattern for smoother S (data filled each iteration).
        indptr_d = np.arange(0, nn_idx_d.size + 1, nn_idx_d.shape[1], dtype=np.int64)
        indptr_e = np.arange(0, nn_idx_e.size + 1, nn_idx_e.shape[1], dtype=np.int64)
        indices_d = nn_idx_d.reshape(-1)
        indices_e = nn_idx_e.reshape(-1)

        # CSR pattern (data filled each iteration).
        S_d = scipy.sparse.csr_matrix(
            (np.ones(indices_d.size, dtype=np.float32), indices_d, indptr_d),
            shape=(X0[d].shape[0], A_d.shape[0]),
        )
        S_e = scipy.sparse.csr_matrix(
            (np.ones(indices_e.size, dtype=np.float32), indices_e, indptr_e),
            shape=(X0[e].shape[0], A_e.shape[0]),
        )

        # Cached row ids for fast normalization without np.repeat(inv_row, np.diff(indptr)).
        row_ids_d = np.repeat(np.arange(X0[d].shape[0], dtype=np.int64), nn_idx_d.shape[1])
        row_ids_e = np.repeat(np.arange(X0[e].shape[0], dtype=np.int64), nn_idx_e.shape[1])

        edge_cache[(d, e)] = {
            'ds_ind': ds_ind,
            'ref_ind': ref_ind,
            'A_d': A_d,
            'A_e': A_e,
            'neigh_d': neigh_d,
            'neigh_e': neigh_e,
            'nn_idx_d': nn_idx_d,
            'nn_idx_e': nn_idx_e,
            'g_d': g_d,
            'g_e': g_e,
            'S_d': S_d,
            'S_e': S_e,
            'row_ids_d': row_ids_d,
            'row_ids_e': row_ids_e,
            'indptr_d': indptr_d,
            'indptr_e': indptr_e,
            'indices_d': indices_d,
            'indices_e': indices_e,
        }

    for it in range(T):
        if verbose:
            print('Global integration iteration {}/{}'.format(it + 1, T))

        Xcorr = [ (X0[d] + f[d]).astype(np.float32, copy=False) for d in range(n_ds) ]

        df = [ np.zeros_like(X, dtype=np.float32) for X in X0 ]
        wsum = [ 0.0 for _ in range(n_ds) ]

        # Per-dataset clipping caps (computed after df accumulation each iter).
        clip_caps = [ None for _ in range(n_ds) ]
        eps = np.float32(1e-12)
        delta2 = np.float32(huber_delta) * np.float32(huber_delta)

        for w_edge, d, e in mst_edges:
            w_edge = float(max(0.05, min(1.0, w_edge)))

            cache = edge_cache.get((d, e), None)
            if cache is None:
                continue

            ds_ind = cache['ds_ind']
            ref_ind = cache['ref_ind']
            if ds_ind.size == 0:
                continue

            Xd_corr = Xcorr[d]
            Xe_corr = Xcorr[e]

            Rd = (Xe_corr[ref_ind, :] - Xd_corr[ds_ind, :]).astype(np.float32, copy=False)
            Re = (Xd_corr[ds_ind, :] - Xe_corr[ref_ind, :]).astype(np.float32, copy=False)

            # Change #5: adaptive per-edge robust threshold (delta) from current residual scale.
            # Use median ||R|| with a safety floor; compute via partition on squared norms.
            r2d = np.sum(Rd * Rd, axis=1).astype(np.float32, copy=False)
            r2e = np.sum(Re * Re, axis=1).astype(np.float32, copy=False)

            # Approx median of ||R|| from r2 using partition (no sort).
            if r2d.size > 0:
                med_r2_d = approx_quantile(r2d, 0.5)
                med_r_d = np.sqrt(med_r2_d).astype(np.float32)
            else:
                med_r_d = np.float32(0.0)
            delta_edge = float(max(0.25, 2.0 * float(med_r_d)))
            delta2_edge = np.float32(delta_edge * delta_edge)

            wd = huber_weights_r2(r2d, delta2_edge, delta_edge)
            we = huber_weights_r2(r2e, delta2_edge, delta_edge)

            # Change #1/#2/#3: Use precomputed neighbors + base Gaussian weights + CSR smoother,
            # and pass cached CSR internals for faster normalization.
            upd_d = batch_bias(
                X0[d], None, Rd,
                batch_size=batch_size, sigma=sigma,
                k=30, sigma_scale=1.0, anchor_weights=wd,
                pre_nn_idx=cache['nn_idx_d'], pre_g=cache['g_d'],
                pre_S=cache['S_d'], pre_row_ids=cache['row_ids_d'],
                pre_indptr=cache['indptr_d'], pre_indices=cache['indices_d']
            )
            upd_e = batch_bias(
                X0[e], None, Re,
                batch_size=batch_size, sigma=sigma,
                k=30, sigma_scale=1.0, anchor_weights=we,
                pre_nn_idx=cache['nn_idx_e'], pre_g=cache['g_e'],
                pre_S=cache['S_e'], pre_row_ids=cache['row_ids_e'],
                pre_indptr=cache['indptr_e'], pre_indices=cache['indices_e']
            )

            # Change #4: clip per-cell update magnitudes using selection (no percentile sort).
            u_d = np.sqrt(np.sum(upd_d * upd_d, axis=1)).astype(np.float32, copy=False)
            cap_d = approx_quantile(u_d, 0.95)
            if cap_d > 0:
                scale_d = np.minimum(np.float32(1.0), cap_d / (u_d + eps)).astype(np.float32, copy=False)
                upd_d = (upd_d * scale_d[:, None]).astype(np.float32, copy=False)

            u_e = np.sqrt(np.sum(upd_e * upd_e, axis=1)).astype(np.float32, copy=False)
            cap_e = approx_quantile(u_e, 0.95)
            if cap_e > 0:
                scale_e = np.minimum(np.float32(1.0), cap_e / (u_e + eps)).astype(np.float32, copy=False)
                upd_e = (upd_e * scale_e[:, None]).astype(np.float32, copy=False)

            df[d] += np.float32(w_edge) * upd_d
            df[e] += np.float32(w_edge) * upd_e
            wsum[d] += w_edge
            wsum[e] += w_edge

        for d in range(n_ds):
            if wsum[d] > 0:
                step = df[d] / np.float32(wsum[d])
            else:
                step = np.float32(0.0)

            f[d] = (np.float32(1.0) - np.float32(eta) * np.float32(lam)) * f[d] + np.float32(eta) * step
            f[d] = smooth_field(f[d], knn_graph[d], mu)

    # Apply final corrections to embeddings.
    for d in range(n_ds):
        datasets[d] = np.asarray(X0[d] + f[d])

    # Change #5: Reduce expression-correction densification (contain blow-up).
    # Practical compromise: a single pass, correct only matched cells (anchors),
    # avoid any toarray() on full expression matrices and avoid batch_bias in gene space.
    if expr_datasets:
        Texpr = 1
        for _ in range(Texpr):
            for w_edge, d, e in mst_edges:
                w_edge = float(max(0.05, min(1.0, w_edge)))
                cache = edge_cache.get((d, e), None)
                if cache is None:
                    continue

                ds_ind = cache['ds_ind']
                ref_ind = cache['ref_ind']
                if ds_ind.size == 0:
                    continue

                # Robust weights computed in embedding space using final corrected geometry.
                Xd_corr = np.asarray(datasets[d], dtype=np.float32)
                Xe_corr = np.asarray(datasets[e], dtype=np.float32)
                Rd = Xe_corr[ref_ind, :] - Xd_corr[ds_ind, :]
                r2d = np.sum(Rd * Rd, axis=1).astype(np.float32, copy=False)
                wd = huber_weights_r2(r2d, delta2, huber_delta).astype(np.float32, copy=False)
                we = wd  # symmetric for anchor-only correction

                # Anchor expression residuals.
                Xd_anchor = expr_datasets[d][ds_ind, :]
                Xe_anchor = expr_datasets[e][ref_ind, :]
                bias_d = Xe_anchor - Xd_anchor
                bias_e = -bias_d

                # Apply robust, per-anchor shrink (no smoothing to all cells in gene space).
                wd_col = wd[:, None]
                we_col = we[:, None]
                expr_datasets[d][ds_ind, :] = expr_datasets[d][ds_ind, :] + (w_edge * eta) * bias_d.multiply(wd_col)
                expr_datasets[e][ref_ind, :] = expr_datasets[e][ref_ind, :] + (w_edge * eta) * bias_e.multiply(we_col)

    return datasets

# Sketch-based acceleration of integration.
def integrate_sketch(datasets_dimred, sketch_method='geosketch', N=10000,
                     integration_fn=assemble, integration_fn_args={}):

    from geosketch import gs, uniform

    if sketch_method.lower() == 'geosketch' or sketch_method.lower() == 'gs':
        sampling_fn = gs
    else:
        sampling_fn = uniform

    # Sketch each dataset.
    sketch_idxs = [
        sorted(set(sampling_fn(X, N, replace=False)))
        if X.shape[0] > N else list(range(X.shape[0]))
        for X in datasets_dimred
    ]
    datasets_sketch = [ X[idx] for X, idx in zip(datasets_dimred, sketch_idxs) ]

    # Integrate the dataset sketches.
    datasets_int = integration_fn(datasets_sketch[:], **integration_fn_args)

    # Apply integrated coordinates back to full data.
    for i, (X_dimred, X_sketch) in enumerate(zip(datasets_dimred, datasets_sketch)):
        X_int = datasets_int[i]

        neigh = NearestNeighbors(n_neighbors=3).fit(X_dimred)
        _, neigh_idx = neigh.kneighbors(X_sketch)

        ds_idxs, ref_idxs = [], []
        for ref_idx in range(neigh_idx.shape[0]):
            for k_idx in range(neigh_idx.shape[1]):
                ds_idxs.append(neigh_idx[ref_idx, k_idx])
                ref_idxs.append(ref_idx)

        bias = transform(X_dimred, X_int, ds_idxs, ref_idxs, 15, batch_size=1000)

        datasets_int[i] = X_dimred + bias

    return datasets_int

# Non-optimal dataset assembly. Simply accumulate datasets into a
# reference.
def assemble_accum(datasets, verbose=VERBOSE, knn=KNN, sigma=SIGMA,
                   approx=APPROX, batch_size=None):
    if len(datasets) == 1:
        return datasets

    for i in range(len(datasets) - 1):
        j = i + 1

        if verbose:
            print('Processing datasets {}'.format((i, j)))

        ds1 = datasets[j]
        ds2 = np.concatenate(datasets[:i+1])
        match = mnn(ds1, ds2, knn=knn, approx=approx)

        ds_ind = [ a for a, _ in match ]
        ref_ind = [ b for _, b in match ]

        bias = transform(ds1, ds2, ds_ind, ref_ind, sigma=sigma,
                         batch_size=batch_size)
        datasets[j] = np.asarray(ds1 + bias)

    return datasets
