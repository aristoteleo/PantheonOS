"""Create small deterministic scientific inputs only in the chosen audit scratch.

Requires numpy, pandas, anndata and tifffile in the audit Python environment.
Two small public PDB examples are downloaded from RCSB unless --skip-molecules
is set; no large data or binary samples are stored in the repository.
"""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen


def generate(root: Path, skip_molecules: bool = False) -> None:
    import anndata as ad
    import numpy as np
    import pandas as pd
    import tifffile

    work = root.resolve() / "workspace"
    work.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)
    y, x = np.mgrid[:128, :160]
    channels = np.stack([
        45000 * np.exp(-((x - 52) ** 2 + (y - 48) ** 2) / 450),
        55000 * np.exp(-((x - 103) ** 2 + (y - 80) ** 2) / 600),
    ]).astype("uint16")
    tifffile.imwrite(work / "audit.ome.tif", channels, ome=True, metadata={"axes": "CYX"})
    tifffile.imwrite(work / "audit-plain.tif", channels, photometric="minisblack", metadata={"axes": "CYX"})
    adata = ad.AnnData(
        X=rng.poisson(3, (64, 12)).astype("float32"),
        obs=pd.DataFrame({"cell_type": pd.Categorical(["Alpha"] * 32 + ["Beta"] * 32)},
                         index=[f"cell_{i}" for i in range(64)]),
        var=pd.DataFrame(index=[f"Gene_{i}" for i in range(12)]),
        obsm={"spatial": rng.normal(size=(64, 3)).astype("float32"),
              "X_umap": rng.normal(size=(64, 2)).astype("float32")},
    )
    adata.write_h5ad(work / "audit.h5ad")
    del adata.obsm["X_umap"]
    adata.write_h5ad(work / "audit-no-umap.h5ad")
    (work / "audit.cyjs").write_text(json.dumps({"elements": [
        {"data": {"id": "A"}}, {"data": {"id": "B"}},
        {"data": {"id": "E", "source": "A", "target": "B"}},
    ]}))
    (work / "audit.nwk").write_text("((Alpha:1,Beta:2):1,Gamma:3);\n")
    (work / "audit.fasta").write_text(">Alpha\nACGTACGT\n>Beta\nACGTTCGT\n")
    (work / "audit.smi").write_text("CCO Ethanol\nCC(=O)O AceticAcid\n")
    (work / "genome.fa").write_text(">chrAudit\n" + "ACGT" * 250 + "\n")
    (work / "genome.fa.fai").write_text("chrAudit\t1000\t10\t1000\t1001\n")
    if not skip_molecules:
        for name, pdb in (("audit.pdb", "1CRN"), ("alternate.pdb", "1BNA")):
            path = work / name
            if not path.exists():
                with urlopen(f"https://files.rcsb.org/download/{pdb}.pdb", timeout=30) as response:
                    contents = response.read()
                if b"ATOM  " not in contents:
                    raise ValueError(f"RCSB did not return a PDB structure for {pdb}")
                path.write_bytes(contents)
    files = [p for p in work.iterdir() if p.is_file() and p.name.startswith(("audit", "alternate", "genome"))]
    (root / "scientific-inputs.json").write_text(json.dumps({
        "workspace": str(work), "files": {p.name: {"bytes": p.stat().st_size,
        "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(files)},
    }, indent=2))
    print(f"Generated {len(files)} small inputs in {work}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="The same dedicated scratch root as server.py")
    parser.add_argument("--skip-molecules", action="store_true")
    args = parser.parse_args()
    generate(args.root, args.skip_molecules)
