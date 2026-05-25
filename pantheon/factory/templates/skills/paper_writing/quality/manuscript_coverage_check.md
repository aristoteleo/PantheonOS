---
id: manuscript_coverage_check
name: Manuscript Coverage Check
description: |
  Verify that all required manuscript sections are present and complete.
  Checks for missing sections, incomplete content, and structural issues.
source: review/SKILL.md (peer review standards)
license: MIT
---

# Manuscript Coverage Check

Ensure manuscript has all required sections and each section is complete.

## When to Use

- Completing the first draft
- Reviewing a draft before rendering
- Before submission to catch missing sections

## Coverage Checklist

### Core Sections (Required for most papers)

- [ ] **Title**: Descriptive, specific, <150 characters
- [ ] **Abstract**: 150-300 words, structured or unstructured
- [ ] **Introduction**: Background, gap, contribution
- [ ] **Methods**: Sufficient detail for reproduction
- [ ] **Results**: Findings with figures/tables
- [ ] **Discussion**: Interpretation, limitations, future work
- [ ] **References**: Complete reference list

### Optional Sections (Journal-dependent)

- [ ] **Keywords**: 3-6 keywords
- [ ] **Conclusion**: Separate conclusion section
- [ ] **Acknowledgements**: Funding, contributions
- [ ] **Author Contributions**: Who did what
- [ ] **Competing Interests**: Conflicts of interest
- [ ] **Data Availability**: Where to access data
- [ ] **Code Availability**: Where to access code
- [ ] **Supplementary Materials**: Additional files

## Section Completeness

### Abstract Completeness

- [ ] **Background**: 1-2 sentences on context
- [ ] **Gap/Problem**: What is missing or needs solving
- [ ] **Method**: Brief description of approach
- [ ] **Results**: Key findings (quantitative if possible)
- [ ] **Conclusion**: Significance or impact

### Introduction Completeness

- [ ] **Background**: Establishes context (2-3 paragraphs)
- [ ] **Related work**: Reviews relevant literature
- [ ] **Gap**: Identifies what is missing
- [ ] **Contribution**: States paper's contribution
- [ ] **Organization**: Optional roadmap of paper structure

### Methods Completeness

- [ ] **Design**: Study/experiment design described
- [ ] **Materials**: Data, tools, software specified
- [ ] **Procedure**: Step-by-step process
- [ ] **Parameters**: All settings and hyperparameters
- [ ] **Analysis**: Statistical or computational methods

### Results Completeness

- [ ] **Main findings**: Primary results presented
- [ ] **Figures**: Key results visualized
- [ ] **Tables**: Quantitative results tabulated
- [ ] **Statistics**: Statistical significance reported
- [ ] **Subsections**: Organized by research question

### Discussion Completeness

- [ ] **Interpretation**: What results mean
- [ ] **Comparison**: How results relate to prior work
- [ ] **Limitations**: Study limitations acknowledged
- [ ] **Future work**: Next steps or open questions
- [ ] **Conclusion**: Take-home message

## Output Format

```markdown
## Manuscript Coverage Report

**Completeness**: 85% (17/20 sections)

### ✅ Present and Complete (17 sections)
- Title: ✅ Descriptive and specific
- Abstract: ✅ 287 words, includes background/gap/method/results/conclusion
- Introduction: ✅ 4 paragraphs, covers background/related work/gap/contribution
- Methods: ✅ Design, materials, procedure, parameters all present
- Results: ✅ 4 subsections with 6 figures and 3 tables
- Discussion: ✅ Interpretation, comparison, limitations present
- References: ✅ 45 references, properly formatted
- ...

### ⚠️ Missing or Incomplete (3 sections)

1. **Data Availability Statement** (Critical for many journals)
   - Status: Missing
   - Action: Add statement: "Data available at [repository] under [license]"
   - Location: After Discussion, before References

2. **Limitations subsection in Discussion** (Important)
   - Status: Incomplete (only 1 sentence)
   - Action: Expand to 1 paragraph covering: sample size, generalizability, assumptions
   - Location: Discussion section

3. **Code Availability Statement** (Important for computational papers)
   - Status: Missing
   - Action: Add statement: "Code available at https://github.com/user/repo under MIT license"
   - Location: After Data Availability

### Recommendation
Add Data Availability and Code Availability statements before submission.
Expand Limitations discussion to meet journal standards.
```

## Common Issues

### Missing Sections

❌ **Bad**: No Data Availability statement  
✅ **Good**: "Data available at Zenodo (DOI: 10.5281/zenodo.1234567)"

❌ **Bad**: No Limitations discussed  
✅ **Good**: Dedicated Limitations subsection in Discussion

### Incomplete Sections

❌ **Bad**: Methods says "standard preprocessing"  
✅ **Good**: Methods details every preprocessing step

❌ **Bad**: Results only shows figures, no text  
✅ **Good**: Results describes findings, then references figures

### Structural Issues

❌ **Bad**: Discussion repeats Results without interpretation  
✅ **Good**: Discussion interprets Results and compares to literature

❌ **Bad**: Introduction jumps to contribution without establishing gap  
✅ **Good**: Introduction: background → related work → gap → contribution

## Quality Gate

- **100% coverage**: All sections present and complete
- **≥90% coverage**: Excellent, minor gaps only
- **80-89% coverage**: Good, address missing sections
- **<80% coverage**: Poor, major sections missing

## Section Length Guidelines

| Section | Typical Length | Notes |
|---------|----------------|-------|
| Abstract | 150-300 words | Journal-specific |
| Introduction | 2-4 pages | Establishes context |
| Methods | 2-5 pages | Enough for reproduction |
| Results | 3-6 pages | Figures + text |
| Discussion | 2-4 pages | Interpretation + limitations |
| Conclusion | 0.5-1 page | Optional, often part of Discussion |

## Integration

Run this check after completing the first draft:
```
1. Read quality/manuscript_coverage_check.md
2. Check all required sections present
3. Check each section is complete
4. Generate coverage report
5. Add missing sections
6. Expand incomplete sections
7. Re-run check until ≥90% coverage
```

## Constraints

- **Journal-specific**: Requirements vary by journal
- **Paper type**: Review papers have different structure than research articles
- **Field-specific**: Some fields have unique section requirements
- **Flexibility**: Some sections can be combined (e.g., Results and Discussion)
