---
id: reproducibility_check
name: Reproducibility Check
description: |
  Verify that methods section contains sufficient detail for reproduction.
  Checks for software versions, parameters, data availability, and code sharing.
source: https://github.com/nature-polishing (Nature reproducibility standards)
license: MIT
---

# Reproducibility Check

Ensure methods section provides sufficient detail for independent reproduction of results.

## When to Use

- Writer completing methods section
- Leader reviewing draft before submission
- Addressing reviewer concerns about reproducibility

## Reproducibility Checklist

### Software & Tools

- [ ] **Software names**: All software tools explicitly named
- [ ] **Version numbers**: Specific versions for all critical software
- [ ] **Operating system**: OS and version specified if relevant
- [ ] **Programming language**: Language and version (e.g., Python 3.9)
- [ ] **Key libraries**: Major dependencies with versions (e.g., NumPy 1.21, PyTorch 2.0)

### Parameters & Settings

- [ ] **Hyperparameters**: All tunable parameters specified
- [ ] **Random seeds**: Seeds specified for stochastic methods
- [ ] **Hardware**: GPU/CPU specs if relevant to performance
- [ ] **Training details**: Epochs, batch size, learning rate, optimizer
- [ ] **Preprocessing**: All data preprocessing steps detailed

### Data

- [ ] **Data source**: Where data was obtained
- [ ] **Data version**: Version or access date for public datasets
- [ ] **Sample size**: Number of samples, train/val/test splits
- [ ] **Inclusion/exclusion criteria**: How samples were selected
- [ ] **Data availability**: Statement on how to access data

### Code

- [ ] **Code availability**: GitHub/GitLab repository or supplementary code
- [ ] **License**: Code license specified (MIT, Apache, GPL, etc.)
- [ ] **Documentation**: README with installation and usage instructions
- [ ] **Dependencies**: requirements.txt or environment.yml
- [ ] **Reproducibility script**: Script to reproduce main results

## Output Format

```markdown
## Reproducibility Check Report

**Compliance**: 75% (15/20 items)

### ✅ Sufficient Detail (15 items)
- Software names provided (PyTorch, scikit-learn, pandas)
- Version numbers for major tools (PyTorch 2.0.1, Python 3.9)
- Hyperparameters specified (learning rate 0.001, batch size 32)
- Data source identified (ImageNet-1K)
- Sample sizes reported (train: 1.2M, val: 50K, test: 100K)
- ...

### ⚠️ Missing or Vague (5 items)

1. **Random seeds not specified** (Important)
   - Current: "We used standard random initialization"
   - Fix: "We set random seeds to 42 for NumPy, PyTorch, and Python random"
   
2. **Hardware specs missing** (Important)
   - Current: "Trained on GPUs"
   - Fix: "Trained on 4× NVIDIA A100 40GB GPUs"
   
3. **Data preprocessing steps vague** (Critical)
   - Current: "Images were preprocessed"
   - Fix: "Images resized to 224×224, normalized with ImageNet mean/std, random horizontal flip with p=0.5"
   
4. **Code availability not stated** (Critical)
   - Current: No mention
   - Fix: "Code available at https://github.com/user/repo under MIT license"
   
5. **Optimizer details incomplete** (Important)
   - Current: "We used Adam optimizer"
   - Fix: "Adam optimizer (β1=0.9, β2=0.999, ε=1e-8, weight decay=1e-4)"

### Recommendation
Address 2 critical items (preprocessing, code availability) before submission.
Add hardware specs and random seeds for full reproducibility.
```

## Common Issues

### Vague Methods

❌ **Bad**: "We used standard preprocessing"  
✅ **Good**: "Images resized to 224×224, normalized with mean=[0.485, 0.456, 0.406] and std=[0.229, 0.224, 0.225]"

❌ **Bad**: "We trained the model"  
✅ **Good**: "Trained for 100 epochs with batch size 32, learning rate 0.001, Adam optimizer (β1=0.9, β2=0.999)"

❌ **Bad**: "Data was obtained from public sources"  
✅ **Good**: "ImageNet-1K dataset (ILSVRC2012, accessed 2024-01-15) from https://image-net.org"

### Missing Details

❌ **Bad**: "We used PyTorch"  
✅ **Good**: "PyTorch 2.0.1 with CUDA 11.8 on Ubuntu 20.04"

❌ **Bad**: "Code will be made available"  
✅ **Good**: "Code available at https://github.com/user/repo under MIT license"

## Quality Gate

- **≥90% compliance**: Excellent reproducibility
- **80-89% compliance**: Good, minor details missing
- **70-79% compliance**: Fair, address critical gaps
- **<70% compliance**: Poor, major revision needed

## Data Availability Statement

Every paper should include a data availability statement:

**Public data**:
> "ImageNet-1K dataset is publicly available at https://image-net.org. Preprocessed data and trained models are available at https://github.com/user/repo."

**Restricted data**:
> "Patient data cannot be shared due to privacy restrictions. Aggregated statistics and analysis code are available at https://github.com/user/repo."

**New data**:
> "All data generated in this study are available at Zenodo (DOI: 10.5281/zenodo.1234567) under CC BY 4.0 license."

## Code Availability Statement

**Open source**:
> "All code is available at https://github.com/user/repo under MIT license. Installation and usage instructions are provided in the README."

**Proprietary with research access**:
> "Code is available for research purposes upon reasonable request to the corresponding author."

**Commercial**:
> "Code is proprietary. A demo is available at https://demo.example.com."

## Integration

Writer should run this check after completing methods section:
```
1. Read quality/reproducibility_check.md
2. Check methods section against checklist
3. Generate reproducibility report
4. Fix missing/vague items
5. Add data availability and code availability statements
6. Re-run check until ≥80% compliance
```

## Constraints

- **Balance**: Enough detail for reproduction, not overwhelming
- **Supplementary**: Move extensive details to supplementary materials
- **Standard methods**: Can reference established protocols instead of repeating
- **Proprietary**: Some details may be withheld for commercial reasons (state this explicitly)
