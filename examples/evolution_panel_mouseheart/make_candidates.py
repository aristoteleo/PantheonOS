#!/usr/bin/env python
"""Regenerate candidates.txt -- the list of valid gene symbols the mutation agent spell-checks against.

The list is derived from the reference dataset, not authored, so it is not tracked: 18k lines of gene
symbols in the repository would be a cache, and it changes whenever the reference or the filters do.

    python make_candidates.py --h5ad /path/to/reference.h5ad
"""
import argparse

import scanpy as sc


def main(a):
    A = sc.read_h5ad(a.h5ad)
    sc.pp.filter_genes(A, min_cells=a.min_cells)
    names = [str(g) for g in A.var_names]
    with open(a.out, 'w') as f:
        f.write('\n'.join(names) + '\n')
    print(f'{a.h5ad} -> {a.out}: {len(names)} symbols (min_cells={a.min_cells})')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--h5ad', required=True)
    p.add_argument('--out', default='candidates.txt')
    p.add_argument('--min-cells', type=int, default=10)
    main(p.parse_args())
