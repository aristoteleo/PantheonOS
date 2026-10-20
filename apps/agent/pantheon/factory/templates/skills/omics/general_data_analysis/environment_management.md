---
id: environment_management
name: Environment Management
description: |
  Detect and use the best available environment manager (Conda/Mamba/venv)
  for reproducible Python environment setup in analysis projects.
tags: [environment, conda, mamba, venv, setup]
---

# Environment Management

Efficient environment management for reproducible analysis. Detect the user's available tools and create isolated environments accordingly.

---

## Detection Strategy

Before creating any environment, detect what is available on the system. Prefer Mamba > Conda > venv (in that order) for performance and dependency resolution.

```bash
# Step 1: Detect available environment managers
if command -v mamba &> /dev/null; then
    ENV_MANAGER="mamba"
    echo "Detected: Mamba (fast Conda drop-in)"
elif command -v conda &> /dev/null; then
    ENV_MANAGER="conda"
    echo "Detected: Conda"
else
    ENV_MANAGER="venv"
    echo "No Conda/Mamba found. Falling back to venv."
fi

echo "Using: $ENV_MANAGER"
```

---

## Option 1: Mamba / Conda Environment

Use when Mamba or Conda is detected. Mamba is preferred for faster dependency resolution.

### Create Environment

```bash
# Create with a specific Python version
$ENV_MANAGER create -n <env_name> python=3.11 -y

# Activate
conda activate <env_name>
```

### Install Packages

```bash
# Prefer conda-forge channel for scientific packages
$ENV_MANAGER install -n <env_name> -c conda-forge \
    scanpy anndata numpy pandas scipy matplotlib seaborn \
    -y

# For packages not on conda-forge, fall back to pip INSIDE the env
conda activate <env_name>
pip install <package>
```

### Export / Reproduce

```bash
# Export full environment spec
conda env export -n <env_name> --no-builds > environment.yml

# Reproduce on another machine
$ENV_MANAGER env create -f environment.yml
```

---

## Option 2: venv (Fallback)

Use when neither Conda nor Mamba is available. Creates the environment inside the working directory.

### Create Environment

```bash
# Create venv in the current working directory
python3 -m venv .venv

# Activate
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows
```

### Install Packages

```bash
# Upgrade pip first
pip install --upgrade pip

# Install from requirements file if available
if [ -f requirements.txt ]; then
    pip install -r requirements.txt
else
    pip install scanpy anndata numpy pandas scipy matplotlib seaborn
fi
```

### Export / Reproduce

```bash
# Freeze current environment
pip freeze > requirements.txt

# Reproduce on another machine
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

---

## Unified Workflow

Use this decision flow at the start of every project:

```bash
# 1. Detect environment manager
if command -v mamba &> /dev/null; then
    ENV_MANAGER="mamba"
elif command -v conda &> /dev/null; then
    ENV_MANAGER="conda"
else
    ENV_MANAGER="venv"
fi

# 2. Create or activate environment
ENV_NAME="analysis_env"

if [ "$ENV_MANAGER" != "venv" ]; then
    # Conda/Mamba path
    if conda env list | grep -q "$ENV_NAME"; then
        echo "Environment '$ENV_NAME' already exists. Activating..."
        conda activate "$ENV_NAME"
    else
        echo "Creating Conda environment '$ENV_NAME'..."
        $ENV_MANAGER create -n "$ENV_NAME" python=3.11 -y
        conda activate "$ENV_NAME"
    fi
else
    # venv path
    if [ -d ".venv" ]; then
        echo "venv already exists. Activating..."
        source .venv/bin/activate
    else
        echo "Creating venv in working directory..."
        python3 -m venv .venv
        source .venv/bin/activate
        pip install --upgrade pip
    fi
fi

echo "Environment ready: $(python --version) at $(which python)"
```

## Best Practices

- **One environment per project**: Never install analysis packages into the base/system Python.
- **Pin versions for reproducibility**: Use `environment.yml` (Conda) or `requirements.txt` (venv) to lock dependency versions.
- **Prefer Conda for compiled dependencies**: Packages like `numpy`, `scipy`, `hdf5`, and `igraph` install faster and more reliably via Conda.
- **Use pip inside Conda only when necessary**: Some packages (e.g., `scrublet`, `rapids-singlecell`) are pip-only. Always install Conda packages first, then pip packages, to avoid dependency conflicts.
- **Name environments descriptively**: Use project-specific names (e.g., `spatial_analysis`) rather than generic ones (e.g., `env1`).
