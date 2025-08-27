# Molecular Docking Toolset

## Overview
This toolset provides molecular docking capabilities using AutoDock Vina for protein-ligand interaction studies.

## Dependencies
- `pip install meeko` - For preparing PDBQT files
- `pip install vina` - For molecular docking calculations

## About prepare_gpf.py
The `prepare_gpf.py` script from AutoDockTools calculates grid parameters for docking. 

### Grid Center Detection Priority
The toolset tries multiple methods in order:

1. **pythonsh with prepare_gpf.py** (if available) - Uses AutoDockTools' pythonsh command
2. **Geometric center from PDBQT** - Calculates center from atom coordinates (default)
3. **Reference ligand-based** - Uses a known ligand position (if provided)
4. **Active site residues** - Calculates from specified residues (if provided)

### Using pythonsh (Recommended if available)
If you have AutoDockTools installed with `pythonsh`:
```bash
# The toolset will automatically detect and use pythonsh
pythonsh prepare_gpf.py -l ligand.pdbqt -r receptor.pdbqt -y -o grid.gpf
```

### Python 3 Fallback
If `pythonsh` is not available, the toolset automatically falls back to Python 3 methods for grid center calculation, ensuring compatibility without AutoDockTools.

## Workflows

### 1. Initialize Project
```bash
/bio dock init
```
Creates directory structure for docking project.

### 2. Check Dependencies
```bash
/bio dock check
```
Verifies and installs required Python packages.

### 3. Prepare Receptor
```python
dock.Dock_Workflow('prepare_receptor')
```
Converts PDB files to PDBQT format for docking.

### 4. Prepare Ligand
```python
dock.Dock_Workflow('prepare_ligand')
```
Converts SDF/MOL2 files to PDBQT format.

### 5. Run Docking
```python
dock.Dock_Workflow('docking_vina')
```
Performs molecular docking with automatic grid center detection.

### 6. Batch Docking
```python
dock.Dock_Workflow('batch_docking')
```
Processes multiple protein-ligand pairs automatically.

### 7. Analyze Interactions
```python
dock.Dock_Workflow('analyze_interactions')
```
Extracts binding energies and provides basic structural analysis from PDBQT files.

## Grid Center Detection Methods

### Method 1: Geometric Center (Default)
Calculates the geometric center of all atoms in the receptor.

### Method 2: Reference Ligand
Uses a known ligand position to define the binding site:
```python
center = get_binding_site_center(receptor_pdbqt, reference_ligand="known_ligand.pdbqt")
```

### Method 3: Active Site Residues
Specifies important residues in the binding site:
```python
center = get_binding_site_center(receptor_pdbqt, active_site_residues=[100, 105, 110, 150])
```

## Example Usage

```python
# 1. Initialize project
dock.Dock_Workflow('init')

# 2. Check dependencies
dock.Dock_Workflow('check_dependencies')

# 3. Place your files:
#    - Receptor PDB files in receptors/
#    - Ligand SDF files in ligands/

# 4. Run batch docking
dock.Dock_Workflow('batch_docking')

# 5. Analyze results
dock.Dock_Workflow('analyze_interactions')
```

## Notes
- Default box size is 20x20x20 Å (suitable for small molecules)
- Exhaustiveness is set to 32 for thorough sampling
- Generates top 5 poses per ligand
- Results saved in PDBQT and PDB formats