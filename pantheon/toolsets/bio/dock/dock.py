"""Molecular Docking Toolset - Protein-ligand docking with Vina"""

import os
import subprocess
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from ...utils.log import logger
from ...utils.toolset import ToolSet, tool
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

class MolecularDockingToolSet(ToolSet):
    """Molecular Docking Toolset for protein-ligand interactions"""
    
    def __init__(
        self,
        name: str = "dock",
        workspace_path: str | Path | None = None,
        worker_params: dict | None = None,
        **kwargs,
    ):
        super().__init__(name, worker_params, **kwargs)
        self.workspace_path = Path(workspace_path) if workspace_path else Path.cwd()
        self.console = Console()
        
    @tool
    def Dock_Workflow(self, workflow_type: str, description: str = None):
        """Run a specific molecular docking workflow"""
        if workflow_type == "init":
            return self.run_workflow_init()
        elif workflow_type == "check_dependencies":
            return self.run_workflow_check_dependencies()
        elif workflow_type == "prepare_receptor":
            return self.run_workflow_prepare_receptor()
        elif workflow_type == "prepare_ligand":
            return self.run_workflow_prepare_ligand()
        elif workflow_type == "docking_vina":
            return self.run_workflow_docking_vina()
        elif workflow_type == "analyze_interactions":
            return self.run_workflow_analyze_interactions()
        elif workflow_type == "batch_docking":
            return self.run_workflow_batch_docking()
        else:
            return "Invalid workflow type"
    
    def run_workflow_init(self):
        """Initialize molecular docking project structure"""
        logger.info("Running molecular docking project initialization")
        init_response = f"""
# Initialize Molecular Docking Project

# Create project directory structure
mkdir -p docking/{{receptors,ligands,prepare,output,dock,analysis,scripts,logs}}

# Create config file
cat > docking/config.json << EOF
{{
  "project_name": "molecular_docking",
  "docking_software": "autodock_vina",
  "exhaustiveness": 32,
  "n_poses": 20,
  "box_size": [20, 20, 20],
  "created": "$(date)",
  "note": "Grid center auto-detected from receptor/ligand geometry"
}}
EOF

echo "Docking project structure created successfully!"
echo ""
echo "Next steps:"
echo "  1. Place receptor PDB files in receptors/ automatically"
echo "  2. Place ligand SDF/MOL2 files in ligands/ automatically"
echo "  3. Update docking_pairs.tsv with your targets"
echo "  4. Run: /bio dock check_dependencies"
        """
        return init_response
    
    def run_workflow_check_dependencies(self):
        """Check molecular docking dependencies"""
        logger.info("Running dependency check for molecular docking")
        check_deps_response = """
# Check Molecular Docking Tool Dependencies

# Check if packages are installed
echo "Checking Python packages..."
pip list | grep -q meeko && echo "✓ meeko installed" || echo "✗ meeko missing"
pip list | grep -q vina && echo "✓ vina installed" || echo "✗ vina missing"
pip list | grep -q pandas && echo "✓ pandas installed" || echo "✗ pandas missing"
pip list | grep -q numpy && echo "✓ numpy installed" || echo "✗ numpy missing"

echo ""
echo "Installing required packages..."
pip install meeko vina

echo ""
echo "Checking installations..."
pip list | grep -E "(meeko|vina|pandas|numpy)"

echo ""
echo "Dependency check complete!"
        """
        return check_deps_response
    
    def run_workflow_prepare_receptor(self):
        """Prepare receptor for docking"""
        logger.info("Running receptor preparation workflow")
        prepare_receptor_response = f"""
# Prepare Receptor for Molecular Docking

# Using meeko to prepare receptor
python << EOF
from meeko import MoleculePreparation
from meeko import PDBQTWriterLegacy
import os

# Single receptor preparation
receptor_pdb = "receptor.pdb"  # Replace with your PDB file
receptor_pdbqt = "prepare/receptor.pdbqt"

# Method 1: Using mk_prepare_receptor.py from meeko
os.system(f"mk_prepare_receptor.py -i {{receptor_pdb}} -o {{receptor_pdbqt}}")

# Method 2: Using prepare_receptor (if AutoDockTools available)
# os.system(f'prepare_receptor -r {{receptor_pdb}} -o {{receptor_pdbqt}} -A "hydrogens"')

print(f"Receptor prepared: {{receptor_pdbqt}}")
EOF

# Batch receptor preparation
for pdb in receptors/*.pdb; do
    base=$(basename "$pdb" .pdb)
    echo "Preparing receptor: $base"
    mk_prepare_receptor.py -i "$pdb" -o "prepare/${{base}}.pdbqt"
done

echo "Receptor preparation complete!"
        """
        return prepare_receptor_response
    
    def run_workflow_prepare_ligand(self):
        """Prepare ligand for docking"""
        logger.info("Running ligand preparation workflow")
        prepare_ligand_response = f"""
# Prepare Ligand for Molecular Docking

# Using meeko to prepare ligand
python << EOF
from meeko import MoleculePreparation
from meeko import PDBQTWriterLegacy
from rdkit import Chem
import os

# Single ligand preparation
ligand_sdf = "ligand.sdf"  # Replace with your SDF file
ligand_pdbqt = "prepare/ligand.pdbqt"

# Method 1: Using mk_prepare_ligand.py from meeko
os.system(f"mk_prepare_ligand.py -i {{ligand_sdf}} -o {{ligand_pdbqt}}")

# Method 2: Using Python API
mol = Chem.SDMolSupplier(ligand_sdf, removeHs=False)[0]
if mol:
    prep = MoleculePreparation()
    mol_prep = prep.prepare(mol)
    writer = PDBQTWriterLegacy()
    writer.write_string(mol_prep)
    
print(f"Ligand prepared: {{ligand_pdbqt}}")
EOF

# Batch ligand preparation
for sdf in ligands/*.sdf; do
    base=$(basename "$sdf" .sdf)
    echo "Preparing ligand: $base"
    mk_prepare_ligand.py -i "$sdf" -o "prepare/${{base}}.pdbqt"
done

echo "Ligand preparation complete!"
        """
        return prepare_ligand_response
    
    def run_workflow_docking_vina(self):
        """Run Vina docking"""
        logger.info("Running Vina docking workflow")
        docking_vina_response = f"""
# Molecular Docking with AutoDock Vina

# Generate single docking Python script
cat > scripts/single_docking.py << 'EOF'
from vina import Vina
import os
import numpy as np

# Setup parameters
receptor_pdbqt = "prepare/receptor.pdbqt"
ligand_pdbqt = "prepare/ligand.pdbqt"
output_pdbqt = "output/docked_poses.pdbqt"

# Method 1: Find binding site center from known ligand or active site
def get_binding_site_center(receptor_file, reference_ligand=None, active_site_residues=None):
    \"\"\"
    Calculate binding site center from:
    1. Reference ligand position
    2. Active site residues
    3. Geometric center of receptor
    \"\"\"
    if reference_ligand and os.path.exists(reference_ligand):
        # Get center from reference ligand
        coords = []
        with open(reference_ligand, 'r') as f:
            for line in f:
                if line.startswith('ATOM') or line.startswith('HETATM'):
                    x = float(line[30:38].strip())
                    y = float(line[38:46].strip())
                    z = float(line[46:54].strip())
                    coords.append([x, y, z])
        if coords:
            return np.array(coords).mean(axis=0).tolist()
    
    elif active_site_residues:
        # Get center from specified residues
        coords = []
        with open(receptor_file, 'r') as f:
            for line in f:
                if line.startswith('ATOM'):
                    resnum = int(line[22:26].strip())
                    if resnum in active_site_residues:
                        x = float(line[30:38].strip())
                        y = float(line[38:46].strip())
                        z = float(line[46:54].strip())
                        coords.append([x, y, z])
        if coords:
            return np.array(coords).mean(axis=0).tolist()
    
    # Default: geometric center of receptor
    coords = []
    with open(receptor_file, 'r') as f:
        for line in f:
            if line.startswith('ATOM'):
                x = float(line[30:38].strip())
                y = float(line[38:46].strip())
                z = float(line[46:54].strip())
                coords.append([x, y, z])
    if coords:
        return np.array(coords).mean(axis=0).tolist()
    
    return [0.0, 0.0, 0.0]

# Calculate binding site center
# Option 1: If you have a reference ligand
# center = get_binding_site_center(receptor_pdbqt, reference_ligand="reference_ligand.pdbqt")

# Option 2: If you know active site residues (e.g., residues 100, 105, 110, 150)
# center = get_binding_site_center(receptor_pdbqt, active_site_residues=[100, 105, 110, 150])

# Option 3: Use geometric center (default)
center = get_binding_site_center(receptor_pdbqt)

print(f"Binding site center: {{center}}")

# Box size (can be adjusted based on binding site size)
box_size = [20, 20, 20]   # Typical box size for small molecule docking

# Initialize Vina
v = Vina(sf_name='vina')

# Set receptor and ligand
v.set_receptor(receptor_pdbqt)
v.set_ligand_from_file(ligand_pdbqt)

# Compute Vina maps
v.compute_vina_maps(center=center, box_size=box_size)

# Score the current pose
energy = v.score()
print(f'Score before minimization: {{energy[0]:.3f}} kcal/mol')

# Minimize locally the current pose
energy_minimized = v.optimize()
print(f'Score after minimization: {{energy_minimized[0]:.3f}} kcal/mol')
v.write_pose('output/minimized.pdbqt', overwrite=True)

# Dock the ligand
v.dock(exhaustiveness=32, n_poses=20)
v.write_poses(output_pdbqt, n_poses=5, overwrite=True)

print(f"Docking complete! Results saved to {{output_pdbqt}}")

# Extract and display binding scores from PDBQT file
print("\\nBinding scores (kcal/mol):")
with open(output_pdbqt, 'r') as f:
    pose_count = 0
    for line in f:
        if line.startswith('REMARK VINA RESULT:'):
            pose_count += 1
            parts = line.split()
            if len(parts) >= 4:
                energy = parts[3]
                rmsd_lb = parts[4] if len(parts) > 4 else "N/A"
                rmsd_ub = parts[5] if len(parts) > 5 else "N/A"
                print(f"  Pose {{pose_count}}: {{energy}} kcal/mol (RMSD l.b.={{rmsd_lb}}, u.b.={{rmsd_ub}})")
            if pose_count >= 5:  # Show top 5 poses
                break
EOF

# Execute the docking script
echo "Running single docking script..."
python scripts/single_docking.py

# Convert PDBQT to PDB for visualization
obabel output/docked_poses.pdbqt -O output/docked_poses.pdb -m

echo "Docking complete! Check output/ directory for results"
        """
        return docking_vina_response
    
    def run_workflow_analyze_interactions(self):
        """Analyze protein-ligand interactions"""
        logger.info("Running interaction analysis workflow")
        analyze_interactions_response = f"""
# Analyze Protein-Ligand Interactions

# Extract binding energies from docking results
python << 'EOF'
import glob
import os

print("Docking results summary:")
print("=" * 40)

for pdbqt_file in glob.glob("output/*.pdbqt"):
    print(f"\\nFile: {{os.path.basename(pdbqt_file)}}")
    with open(pdbqt_file, 'r') as f:
        pose_count = 0
        for line in f:
            if line.startswith('REMARK VINA RESULT:'):
                pose_count += 1
                parts = line.split()
                if len(parts) >= 4:
                    energy = parts[3]
                    rmsd_lb = parts[4] if len(parts) > 4 else "N/A"
                    rmsd_ub = parts[5] if len(parts) > 5 else "N/A"
                    print(f"  Pose {{pose_count}}: {{energy}} kcal/mol (RMSD l.b.={{rmsd_lb}}, u.b.={{rmsd_ub}})")
                if pose_count >= 5:  # Show top 5 poses
                    break

# Simple PDBQT file analysis
print("\\nBinding site analysis from PDBQT files:")
print("=" * 45)

receptor_file = "prepare/receptor.pdbqt"
if os.path.exists(receptor_file):
    print(f"Receptor: {{receptor_file}}")
    # Count atoms in receptor
    with open(receptor_file, 'r') as f:
        atom_count = sum(1 for line in f if line.startswith('ATOM'))
    print(f"  Receptor atoms: {{atom_count}}")

# Analyze ligand poses
for pdbqt_file in glob.glob("output/*.pdbqt"):
    print(f"\\nLigand poses: {{os.path.basename(pdbqt_file)}}")
    with open(pdbqt_file, 'r') as f:
        model_count = 0
        atom_count = 0
        for line in f:
            if line.startswith('MODEL'):
                model_count += 1
            elif line.startswith('ATOM') or line.startswith('HETATM'):
                atom_count += 1
        print(f"  Models: {{model_count}}, Ligand atoms per pose: {{atom_count // max(model_count, 1)}}")

print("\\nBasic structure analysis complete!")
print("For detailed interaction analysis, use external tools like:")
print("  - ChimeraX")  
print("  - VMD")
print("  - PyMOL")
print("  - LigPlot+")
EOF

echo "Interaction analysis complete!"
        """
        return analyze_interactions_response
    
    def run_workflow_batch_docking(self):
        """Run batch docking for multiple protein-ligand pairs"""
        logger.info("Running batch docking workflow")
        batch_docking_response = f"""
# Batch Molecular Docking Pipeline

# Generate batch docking Python script
cat > scripts/batch_docking.py << 'EOF'
import os
import sys
import pandas as pd
from vina import Vina
from pathlib import Path

# Add dock directory to path for prepare_gpf.py
dock_dir = Path(__file__).parent / "dock"
if dock_dir.exists():
    sys.path.insert(0, str(dock_dir))

# Read docking pairs from CSV/TSV file
# Expected columns: receptor, ligand, target_name
data = pd.read_csv('docking_pairs.csv')  # or your data file

# Process each protein-ligand pair
for protein in data['target'].unique():
    pdb_files = [i for i in os.listdir('./') if 'pdb' in i]
    pdb_file_name = [i for i in pdb_files if protein in i][0]
    
    # Get ligands for this protein
    ingres = [i.replace(' ','_') for i in list(set(data.loc[data['target']==protein,'ligand_name'].tolist()))]
    ingre_names = [i for i in ingres if f'{{i}}.sdf' in os.listdir('./')]
    
    for ingre in ingre_names:
        print(f"{{protein}} - {{ingre}} started")
        
        # Prepare receptor
        os.system(f'mk_prepare_receptor.py -i {{pdb_file_name}} -o prepare/{{protein}}.pdbqt')
        
        # Prepare ligand
        os.system(f'mk_prepare_ligand.py -i {{ingre}}.sdf -o {{ingre}}.pdbqt')
        
        # Calculate grid center using geometric center from PDBQT
        loc = [0.0, 0.0, 0.0]
        try:
            with open(f'prepare/{{protein}}.pdbqt', 'r') as f:
                coords = []
                for line in f:
                    if line.startswith('ATOM') or line.startswith('HETATM'):
                        x = float(line[30:38].strip())
                        y = float(line[38:46].strip())
                        z = float(line[46:54].strip())
                        coords.append([x, y, z])
                if coords:
                    import numpy as np
                    coords_array = np.array(coords)
                    loc = coords_array.mean(axis=0).tolist()
        except:
            pass
        
        # Option 3: Try using pythonsh if available (AutoDockTools)
        # Check if pythonsh and prepare_gpf.py are available
        gpf_script = Path(__file__).parent / "prepare_gpf.py"
        pythonsh_available = os.system('which pythonsh > /dev/null 2>&1') == 0
        
        if gpf_script.exists() and pythonsh_available:
            print(f"Using pythonsh with prepare_gpf.py for {{protein}}-{{ingre}}")
            # Using ligand for -l and receptor for -r
            result = os.system(f'pythonsh {{gpf_script}} -l {{ingre}}.pdbqt -r prepare/{{protein}}.pdbqt -y -o prepare/{{ingre}}.gpf')
            
            if result == 0 and os.path.exists(f'prepare/{{ingre}}.gpf'):
                # Successfully created GPF file, extract grid center
                with open(f'prepare/{{ingre}}.gpf','r') as f:
                    for line in f.readlines():
                        if 'gridcenter' in line:
                            parts = line.split()
                            if len(parts) >= 4:
                                try:
                                    loc = [float(parts[1]), float(parts[2]), float(parts[3])]
                                    print(f"Grid center from GPF: {{loc}}")
                                except ValueError:
                                    print("Failed to parse grid center from GPF, using default")
            else:
                print(f"pythonsh command failed or GPF not created, using geometric center")
    
        # Initialize Vina
        v = Vina(sf_name='vina')
        
        # Set receptor and ligand
        v.set_receptor(f'prepare/{{protein}}.pdbqt')
        v.set_ligand_from_file(f'{{ingre}}.pdbqt')
        v.compute_vina_maps(center=loc, box_size=[100, 100, 100])
        
        # Score the current pose
        energy = v.score()
        print('Score before minimization: %.3f (kcal/mol)' % energy[0])
        
        # Minimized locally the current pose
        energy_minimized = v.optimize()
        print('Score after minimization : %.3f (kcal/mol)' % energy_minimized[0])
        v.write_pose(f'output/{{protein}}_{{ingre}}_minimized.pdbqt', overwrite=True)
        
        # Dock the ligand
        v.dock(exhaustiveness=32, n_poses=20)
        v.write_poses(f'output/{{protein}}_{{ingre}}.pdbqt', n_poses=5, overwrite=True)
        
        # Extract and display binding scores
        with open(f'output/{{protein}}_{{ingre}}.pdbqt', 'r') as f:
            for line in f:
                if line.startswith('REMARK VINA RESULT:'):
                    parts = line.split()
                    if len(parts) >= 4:
                        energy = parts[3]
                        print(f"  Best binding energy: {{energy}} kcal/mol")
                    break
        
        # Log docking completion  
        print(f"  Docking results saved: output/{{protein}}_{{ingre}}.pdbqt")
        
        print(f"{{protein}} - {{ingre}} finished")

print("Batch docking complete!")

# Summarize results
import glob

results = []
for pdbqt in glob.glob('output/*.pdbqt'):
    with open(pdbqt, 'r') as f:
        lines = f.readlines()
        for line in lines:
            if line.startswith('REMARK VINA RESULT:'):
                parts = line.split()
                energy = float(parts[3])
                results.append({
                    'file': os.path.basename(pdbqt),
                    'binding_energy': energy
                })
                break

results_df = pd.DataFrame(results)
results_df.to_csv('docking_results_summary.csv', index=False)
print(f"Results summary saved to docking_results_summary.csv")
print(results_df.head())
EOF

# Execute the batch docking script
echo "Running batch docking script..."
python scripts/batch_docking.py

echo "Batch docking complete! Check output/ and dock/ directories for results"
        """
        return batch_docking_response