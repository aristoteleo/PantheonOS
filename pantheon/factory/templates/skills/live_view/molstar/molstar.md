---
id: molstar_live_view
name: View 3D Molecular Structures with Mol*
description: |
  Open and drive an interactive Mol* (molstar) 3D structure viewer in the
  Pantheon sidebar via the live_view tools — proteins, nucleic acids,
  complexes from the RCSB PDB, the AlphaFold DB, or a local structure file.
tags: [molstar, structure, protein, pdb, alphafold, mmcif, 3d, live-view, structural-biology]
---

# Viewing Molecular Structures with Mol*

Mol* (molstar.org) is the standard web viewer for 3D macromolecular
structures — proteins, nucleic acids, complexes. Open it as a LiveView to
show a structure the user can rotate, zoom, and inspect.

## When to use

- Showing a protein / nucleic-acid **3D structure** — experimental (PDB) or
  predicted (AlphaFold).
- Visualising an AlphaFold prediction, coloured by pLDDT confidence.
- Any `.pdb` / `.cif` (mmCIF) structure file the user produced or downloaded.

For *getting* or *predicting* structures (AlphaFold, ColabFold, fetching
from databases), see the `structural_biology` skill — this skill is just the
viewer.

## Quick demo — built in

```
open_live_view(view_type="molstar", title="Structure Viewer")
```

No `state` → loads the bundled `demo.json` (PDB `1CBS`, a small protein).

## The state — what Mol* shows

Provide **exactly one** structure source:

```jsonc
{
  "pdbId": "1CBS"                        // an RCSB PDB entry id
}
// — or —
{
  "alphafold": "P00533"                  // AlphaFold DB, by UniProt accession
}                                        //   (Mol* colours it by pLDDT)
// — or —
{
  "url": "http://127.0.0.1:PORT/.../model.cif",  // a structure FILE
  "format": "mmcif"                              // "mmcif" | "pdb"
}
```

## Loading structures

- **Experimental (RCSB PDB)** — pass `{"pdbId": "1CBS"}`. Mol* fetches it.
- **Predicted (AlphaFold DB)** — pass `{"alphafold": "<UniProt accession>"}`.
  Mol* fetches the AlphaFold model and colours residues by **pLDDT**
  (per-residue confidence: blue = high, orange = low).
- **A local / generated structure file** — the user's own `.pdb` / `.cif`
  (a ColabFold output, a downloaded model, a docking result): serve it,
  then pass its URL:
  ```
  serve_local_data("/path/to/model.cif")   -> { url }
  open_live_view("molstar", title, state={ "url": <that url>, "format": "mmcif" })
  ```

To switch structures later, `live_view_set_state(view_id, {...})` with a new
source.

## Verify it

`live_view_screenshot` works for Mol* — the adapter uses Mol*'s own
screenshot helper (`viewportScreenshot.getImageDataUri`), so the returned
image is the real 3D render. `observe_images` it after opening to confirm
the structure actually loaded. Also check `live_view_get_state`: `status`
should be `ready`, `diagnostics` empty (a diagnostic typically means a bad
PDB id / UniProt accession or an unreachable URL).
