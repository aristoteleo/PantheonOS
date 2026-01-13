# Checkpoint Layout Conventions

This document describes the standard organization for model checkpoints in the SCFM toolset.

## Base Directory

All checkpoints are stored under a common base directory:

```
~/.cache/scfm/
```

This can be overridden by setting the `SCFM_CACHE_DIR` environment variable.

## Directory Structure

Each model has its own subdirectory with versioned checkpoints:

```
~/.cache/scfm/
├── scgpt/
│   ├── whole-human-2024/
│   │   ├── model.pt
│   │   ├── vocab.json
│   │   └── config.json
│   └── brain-2024/
│       └── ...
├── geneformer/
│   ├── v2-106M/
│   │   └── pytorch_model.bin
│   └── v2-30M/
│       └── ...
├── uce/
│   └── 4-layer/
│       └── model.ckpt
├── scfoundation/
│   └── 50M/
│       └── model.pt
└── <model_name>/
    └── <version>/
        └── <checkpoint_files>
```

## Naming Conventions

### Model Directories

- Use lowercase model name
- Match the `name` field in ModelSpec
- Examples: `scgpt`, `geneformer`, `uce`, `scbert`

### Version Directories

- Use descriptive version names
- Include size/variant when applicable
- Examples:
  - `whole-human-2024` (scGPT trained on whole human)
  - `v2-106M` (Geneformer version 2, 106M parameters)
  - `4-layer` (UCE 4-layer variant)
  - `50M` (scFoundation 50M cells)

### Checkpoint Files

Common checkpoint file patterns:

| File | Purpose |
|------|---------|
| `model.pt` | PyTorch state dict |
| `pytorch_model.bin` | HuggingFace format |
| `model.ckpt` | Lightning checkpoint |
| `config.json` | Model configuration |
| `vocab.json` | Tokenizer vocabulary |
| `gene_vocab.json` | Gene vocabulary |
| `special_tokens.json` | Special token mapping |

## Model-Specific Layouts

### scGPT

```
scgpt/
└── whole-human-2024/
    ├── best_model.pt          # Main checkpoint
    ├── args.json              # Training arguments
    └── vocab.json             # Gene vocabulary
```

### Geneformer

```
geneformer/
└── v2-106M/
    ├── pytorch_model.bin      # Model weights
    ├── config.json            # Model config
    └── tokenizer.json         # Gene tokenizer
```

### UCE

```
uce/
└── 4-layer/
    ├── model.pt               # Checkpoint
    └── all_tokens.torch       # Gene tokens
```

### scFoundation

```
scfoundation/
└── 50M/
    ├── model.pt               # Main model
    └── gene_vocab.json        # Gene vocabulary
```

## Download Sources

Checkpoints are typically downloaded from:

| Source | Pattern |
|--------|---------|
| HuggingFace | `huggingface_hub.hf_hub_download(repo_id, filename)` |
| GitHub | `git clone` or release download |
| Direct URL | `wget` or `curl` |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SCFM_CACHE_DIR` | `~/.cache/scfm` | Base cache directory |
| `HF_HOME` | `~/.cache/huggingface` | HuggingFace cache |
| `TORCH_HOME` | `~/.cache/torch` | PyTorch cache |

## Auto-Discovery

The adapter's `_find_checkpoint()` method searches for checkpoints in order:

1. Explicit `checkpoint_dir` parameter
2. `SCFM_CACHE_DIR/<model>/<version>/`
3. `~/.cache/scfm/<model>/<version>/`
4. Model's `default_path` from spec

## Validation

Each adapter validates checkpoints by checking for:

1. Directory exists
2. Required files present
3. File sizes reasonable
4. Loadable without errors

Example validation:

```python
def _validate_checkpoint(self, path: Path) -> bool:
    required_files = ["model.pt", "config.json"]
    return all((path / f).exists() for f in required_files)
```

## Size Reference

Approximate checkpoint sizes:

| Model | Size | Notes |
|-------|------|-------|
| scGPT | ~150MB | Whole-human checkpoint |
| Geneformer | ~400MB | v2-106M |
| UCE | ~100MB | 4-layer |
| scFoundation | ~200MB | 50M variant |
| scBERT | ~300MB | Base model |

## Cache Management

Clear cache for a specific model:

```bash
rm -rf ~/.cache/scfm/<model_name>
```

Clear entire SCFM cache:

```bash
rm -rf ~/.cache/scfm
```

## Troubleshooting

### Checkpoint Not Found

```
FileNotFoundError: Checkpoint not found at ~/.cache/scfm/scgpt/whole-human-2024
```

**Solution:** Download checkpoint or specify `checkpoint_dir` parameter.

### Corrupted Checkpoint

```
RuntimeError: Error loading checkpoint: invalid data
```

**Solution:** Delete and re-download the checkpoint.

### Disk Space

Before downloading large checkpoints, ensure sufficient disk space:

```bash
df -h ~/.cache/scfm
```
