#!/bin/bash
# Public CELLxGENE Discover datasets for the single-cell tasks (molt_biology.md): download to
# $SCRATCH/evolve/biodata and print what each file holds (obs columns, batches, labels, raw counts).
#SBATCH --job-name=bio-fetch
#SBATCH --partition=xiaojie,normal
#SBATCH --cpus-per-task=4
#SBATCH --mem=48G
#SBATCH --time=04:00:00
#SBATCH --output=/scratch/users/wzxu/evolve/logs/%x-%j.out
set -uo pipefail
EVO=$SCRATCH/evolve; BIO=$EVO/biodata; U=https://datasets.cellxgene.cziscience.com
# resumable (-C -), the six files in parallel: single transfers ran at 0.4-2.5 MB/s (2026-11-12)
dl() { [ -s "$BIO/$1.h5ad" ] || { curl -sSL --retry 8 -C - -o "$BIO/$1.h5ad.part" "$U/$2.h5ad" && mv "$BIO/$1.h5ad.part" "$BIO/$1.h5ad"; }; ls -la "$BIO/$1.h5ad"; }
dl hypomap 2408fb24-d43f-42a2-8962-72ad13b899e8 &
dl tms_muscle a0560e6a-33db-4b82-8666-ba5480b4e23f &
dl kidney_dkd 473d2e1c-a579-4682-96aa-b5b166d7b23e &
dl heart 49312f8c-a75a-4672-9c8a-9078acd35bdb &
dl zebrafish_5dpf 1117ea08-ca7e-4b08-9e73-8bae9fd196ad &
dl gtex 9130af6d-b035-4c2c-868a-ef9a572270be &
wait
apptainer exec --bind /scratch,/home/groups "$EVO/py312.sif" "$GROUP_HOME/wzxu/venv-bio/bin/python" - <<'PY'
import anndata as ad, numpy as np, os
B = os.path.expandvars("$SCRATCH/evolve/biodata")
for n in ("hypomap", "tms_muscle", "kidney_dkd", "heart", "zebrafish_5dpf", "gtex"):
    a = ad.read_h5ad(f"{B}/{n}.h5ad", backed="r")
    o = a.obs
    print(f"\n## {n}: {a.shape} raw={'yes' if a.raw is not None else 'no'} cols={list(o.columns)[:40]}")
    for c in ("donor_id", "assay", "cell_type", "tissue", "suspension_type", "is_primary_data", "disease", "development_stage"):
        if c in o:
            v = o[c].value_counts()
            print(f"   {c}: {len(v)} values; top {dict(v.head(6))}; min {int(v.min())}")
    print("   distinct values per column:", {c: int(o[c].nunique()) for c in o.columns if 1 < o[c].nunique() < 500})
    for c in ("Dataset", "Batch_ID", "C7_named", "C25_named", "Author_Class_Curated", "cell_source", "region", "sample", "fish", "timepoint", "zebrafish_anatomy_ontology_class", "tissue", "Broad cell type", "Granular cell type", "Sample ID", "prep", "batch"):
        if c in o:
            v = o[c].value_counts()
            print(f"   {c}: {len(v)} values; {dict(v.head(30))}; min {int(v.min())}")
    if "donor_id" in o and "assay" in o:
        print("   donor x assay:", o.groupby(["donor_id", "assay"], observed=True).size().to_dict() if o["donor_id"].nunique() < 40 else "many donors")
    X = a.raw.X if a.raw is not None else a.X
    blk = X[:200]
    blk = blk.toarray() if hasattr(blk, "toarray") else np.asarray(blk)
    print("   counts block: max", float(blk.max()), "integer", bool(np.allclose(blk, np.rint(blk))), "per-cell sum median", float(np.median(blk.sum(1))))
    print("   var cols", list(a.var.columns)[:8], "index[:2]", list(a.var_names[:2]))
PY
echo "[$(date)] done"
