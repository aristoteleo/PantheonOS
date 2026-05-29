---
id: structural_biology
name: Protein Structures — AlphaFold & PDB
description: |
  Obtain and predict protein 3D structures — fetch AlphaFold predicted
  models from the AlphaFold DB, experimental structures from the RCSB PDB,
  or predict a novel sequence with ColabFold — and visualise them in the
  Mol* LiveView.
tags: [structural-biology, alphafold, esmfold, protein, structure, pdb, colabfold, plddt, molstar]
---

# Protein Structures — AlphaFold & PDB

How to get a protein's 3D structure and show it to the user. Most "predict
the structure of protein X" requests do **not** require running AlphaFold —
the AlphaFold DB already holds a precomputed prediction for nearly every
known protein. Run a prediction only for a sequence that is not a known
UniProt entry.

## 1. AlphaFold DB — predicted structures (the usual path)

The AlphaFold Database has precomputed AlphaFold models for ~200M+ UniProt
proteins. If the protein is a known UniProt entry, its predicted structure
already exists — just fetch it (instant, free).

**Use the API — do not hand-build file URLs** (the model version, currently
`v6`, changes; the API always returns the live URLs):

```python
import requests

acc = "P00533"  # UniProt accession (human EGFR)
meta = requests.get(
    f"https://alphafold.ebi.ac.uk/api/prediction/{acc}", timeout=30
).json()[0]
cif_url = meta["cifUrl"]          # also: pdbUrl, bcifUrl
with open(f"{acc}.cif", "wb") as fh:
    fh.write(requests.get(cif_url, timeout=60).content)
```

**No UniProt accession yet?** Resolve a gene name / protein name to an
accession first via the UniProt REST API
(`https://rest.uniprot.org/uniprotkb/search?query=<gene>+AND+organism_id:9606&format=json`)
or the `gget` package — pick the reviewed (Swiss-Prot) entry.

## 2. RCSB PDB — experimental structures

For a solved, experimental structure, download from the RCSB PDB by id:

```
https://files.rcsb.org/download/<PDBID>.cif      (or .pdb)
```

## 3. Predicting a novel sequence

If the sequence is **not** a known UniProt protein (a designed, mutant, or
synthetic sequence), the AlphaFold DB has nothing — you must predict it.

### ESMFold API — the practical path (no GPU)

ESMFold (Meta's structure predictor) has a **public folding API**: POST a
raw amino-acid sequence, get a PDB structure back. No GPU, no install, no
databases — this is the realistic way to predict a novel sequence here.

```python
import requests

seq = "MALWMRLLPLLALLALWGPDPAAA..."          # one-letter, a single chain
resp = requests.post("https://api.esmatlas.com/foldSequence/v1/pdb/",
                     data=seq, timeout=180)
resp.raise_for_status()
with open("predicted.pdb", "w") as fh:
    fh.write(resp.text)
```

Caveats — state them when reporting a prediction: ESMFold is a
*single-sequence* language-model predictor (**no MSA**), so it is **less
accurate than AlphaFold2** on low-homology / hard targets; it predicts a
**single chain** (no complexes); there is a length limit (~400 residues).
It is a free public service — it can be slow or briefly unavailable.

### ColabFold / local AlphaFold — only with a real GPU

AlphaFold2-quality prediction (with MSA) means ColabFold (`colabfold_batch`)
or local AlphaFold — both need a **CUDA GPU**. This is **not** available on
a Mac or any machine without an NVIDIA GPU. Run `nvidia-smi` to check
first; never assume this route exists. (Full local AlphaFold2/3 also needs
hundreds of GB of databases — impractical.) Without a GPU, use the ESMFold
API above, or the ColabFold notebook in a browser (not agent-drivable).

## Confidence — pLDDT

AlphaFold reports a per-residue confidence, **pLDDT** (0–100): >90 very
high, 70–90 confident, 50–70 low, <50 very low (often intrinsically
disordered). Always caveat low-pLDDT regions when interpreting a predicted
model. AlphaFold also outputs **PAE** (predicted aligned error) — use it to
judge inter-domain / inter-chain placement confidence.

## Visualising the structure

Show the structure in the **Mol\*** LiveView viewer (see the `live_view`
skill, `molstar` viewer):

```
# an AlphaFold model — Mol* fetches it and colours by pLDDT
open_live_view(view_type="molstar", title="EGFR (AlphaFold)",
               state={"alphafold": "P00533"})

# an experimental PDB entry
open_live_view(view_type="molstar", title="...", state={"pdbId": "1CBS"})

# a local structure file (an ESMFold / ColabFold output, a downloaded model)
serve_local_data("predicted.pdb")               # -> { url }
open_live_view(view_type="molstar", title="...",
               state={"url": <that url>, "format": "pdb"})
```
