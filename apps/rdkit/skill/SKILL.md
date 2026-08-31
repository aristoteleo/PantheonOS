---
id: rdkit
name: 2D Small Molecules with RDKit-JS
description: |
  Render 2D depictions of small molecules from SMILES or MOL-block input
  using RDKit (the cheminformatics standard) compiled to WebAssembly.
  Complements the `molstar` viewer, which handles 3D macromolecules —
  RDKit is the canonical 2D view for small organics, drug-like molecules,
  metabolites, and reaction substrates.
tags: [rdkit, smiles, molecule, cheminformatics, chemistry, 2d, mol, sdf]
---

# 2D Small Molecules with RDKit-JS

RDKit is the de-facto standard cheminformatics toolkit; this is its
WebAssembly build, so you get the same parser / depiction logic in the
browser. State is a list of `molecules` (SMILES or MOL block) and a few
drawing options.

## When to use

- A SMILES string (or list) you want to *see* as a 2D structure.
- Pull a drug / metabolite from a database (ChEMBL, DrugBank, PubChem)
  → render before further work.
- Display the substrate / product of a reaction.
- Highlight a substructure or a set of atoms on the molecule.

For **3D macromolecules** (proteins, nucleic acids, complexes) → use
`molstar`. For **comparing many molecules at once** → consider a static
grid via RDKit-Python's `MolsToGridImage` instead (faster for hundreds).

## Quick demo — built in

```
desktop_open(app="rdkit", title="Small-molecule gallery")
```

No `state` → opens the bundled `demo.json` (eight common molecules:
ethanol, caffeine, aspirin, ibuprofen, sulfadiazine, ...).

## The state — SMILES list + drawing options

```jsonc
{
  // REQUIRED — a list of {smiles | molblock, name?} objects
  "molecules": [
    { "smiles":   "CCO",                          "name": "Ethanol" },
    { "smiles":   "CN1C=NC2=C1C(=O)N(C(=O)N2C)C", "name": "Caffeine" },
    { "molblock": "<MOL file content as string>", "name": "From SDF" }
  ],

  // Optional drawing options applied to every molecule
  "draw_options": {
    "width":  320,
    "height": 220,
    "addAtomIndices": false,
    // Atom / bond highlights are by 0-based index (applied to all
    // molecules — for per-molecule highlights, render one at a time).
    "highlightAtoms": [],
    "highlightBonds": []
  }
}
```

Renders a responsive grid (auto-fit, min `width` px per cell) of SVG
depictions, each with the `name` label below.

## Where SMILES come from

### PubChem / ChEMBL / DrugBank — by name

```python
import requests
def smiles_for(name):
    r = requests.get(
        f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
        f"{requests.utils.quote(name)}/property/CanonicalSMILES/JSON",
        timeout=15,
    )
    if not r.ok: return None
    return r.json()["PropertyTable"]["Properties"][0]["CanonicalSMILES"]

mols = []
for nm in ["aspirin", "ibuprofen", "paracetamol", "caffeine"]:
    s = smiles_for(nm)
    if s: mols.append({"smiles": s, "name": nm})

desktop_open(app="rdkit", title="Common analgesics",
               state={"molecules": mols})
```

### From an SDF file

```python
from rdkit import Chem
mols = []
for m in Chem.SDMolSupplier("compounds.sdf"):
    if m is None: continue
    mols.append({
        "molblock": Chem.MolToMolBlock(m),
        "name":     m.GetProp("_Name") or "",
    })
desktop_open(app="rdkit", title="SDF gallery",
               state={"molecules": mols})
```

### From a CSV of SMILES

```python
import pandas as pd
df = pd.read_csv("library.csv")  # cols: id, smiles, name
mols = [{"smiles": r.smiles, "name": r.name} for r in df.itertuples()]
desktop_open(app="rdkit", title="Library",
               state={"molecules": mols})
```

## Driving the view

The state is small; just replace:

```python
# add a molecule
g = desktop_read(window_id)
new_mols = [*g["state"]["molecules"], {"smiles": "NC(=O)c1ccccc1", "name": "Benzamide"}]
desktop_set(window_id, {"molecules": new_mols,
                              "draw_options": g["state"].get("draw_options")})

# highlight an atom set on a single-molecule view
desktop_set(window_id, {
    "molecules": [{"smiles": "CC(=O)OC1=CC=CC=C1C(=O)O", "name": "Aspirin (highlighted)"}],
    "draw_options": {"width": 360, "height": 260,
                     "highlightAtoms": [0, 1, 2]},
})
```

## Verify it

`desktop_read` — `status: ready`, empty `diagnostics`. The most
common failure is an unparseable SMILES — the adapter shows it as a red
error card in-line (so a partially-bad batch still renders the good ones).
`desktop_screenshot` captures the SVG grid via html2canvas (clean,
high-resolution since the source is vector).

## Notes

- The first call to `desktop_open(app="rdkit")` downloads ~3 MB
  of WASM (cached afterwards). The view shows a "Loading RDKit (WASM)…"
  placeholder while it initialises (usually < 1 s on a warm cache).
- `molblock` input lets you carry over 2D coordinates from an SDF — the
  drawing matches the original layout instead of being recomputed.

---

## Desktop runtime (Atrium)

This viewer is installed as the desktop app `rdkit` ("RDKit Molecules"). Everything above drives it through the `desktop` tools, which reach every window of it — including ones the USER opened. What follows is what is specific to this app.

- **Open a file**: `desktop_open(path="/path/to/file")` — `.sdf`, `.mol`, `.smi` route here through the app's own open pipeline (format conversion, backend prepare) — no serve_local_data needed. Returns `window_id`.
- **Open by state**: `desktop_open(app="rdkit", state={...})` with the state contract documented above.
- **Force this viewer** for a file another app also claims: `desktop_open(app="rdkit", path=...)`. `desktop_apps()` lists every installed app with its id and file claims.
- **Drive any window** (yours or the user's): `desktop_windows()` lists them; `desktop_read(window_id)` returns the current state; `desktop_update(window_id, patch)` deep-merges; `desktop_call(window_id, action, args)` runs the same handlers the app's menus trigger. `desktop_call(w, "$close")` closes.
- **Fix in place**: when a view comes out wrong, correct THAT window (update/set/call, or `desktop_open(path=..., window_id=...)` for a different file) — do not open another window.

### Actions

- `toggleIndices()` — Number every atom in the depiction
