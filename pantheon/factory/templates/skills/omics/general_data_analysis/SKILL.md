---
id: general_data_analysis_index
name: General Data Analysis Skills Index
description: |
  General-purpose skills for data analysis infrastructure: workspace file
  organization, environment management, parallel computing, and performance.
tags: [organization, workspace, environment, parallel, performance, conda, gpu]
---

# General Data Analysis Skills

Cross-cutting skills for workspace hygiene, environment setup, and computational
performance.

## Available Skills

### Workspace File Organization

Give every analysis task its own descriptively-named folder (notebook, figures,
data, reports inside it) — never scatter files at the workspace root or dump
figures into internal dirs.

**Skill file**: [file_organization.md](./file_organization.md)

### Environment Management

Detect and configure the best available environment manager (Conda/Mamba/venv)
for reproducible Python environments.

**Skill file**: [environment_management.md](./environment_management.md)

### Parallel Computing & Performance

Multi-core CPU, GPU acceleration, and memory optimization strategies for
large-scale data analysis.

**Skill file**: [parallel_computing.md](./parallel_computing.md)

### HPC Data Transfer via SSH

Transfer data to/from HPC clusters with interactive authentication
(password + Duo/MFA) using pexpect and SSH ControlMaster.

**Skill file**: [hpc_data_transfer.md](./hpc_data_transfer.md)

**When to use**:
- Downloading data from Stanford Sherlock or similar HPC clusters
- Need to handle Duo two-factor authentication programmatically
- Bulk data transfer with rsync over persistent SSH connections
