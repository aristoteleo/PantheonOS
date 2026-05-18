---
id: format_lint
name: Format Lint
description: |
  Check manuscript formatting: section structure, figure/table numbering,
  reference formatting, word/page limits, and other journal requirements.
source: 本地 PDF (general formatting best practices)
license: MIT
---

# Format Lint

Validate manuscript formatting and structure against common journal requirements.

## When to Use

- Before submission to catch formatting issues
- After major revisions to ensure consistency
- When preparing camera-ready version

## Format Checklist

### Section Structure

- [ ] **Title**: Present and concise (<150 characters)
- [ ] **Abstract**: Present and within word limit (150-300 words typical)
- [ ] **Keywords**: 3-6 keywords provided (if required)
- [ ] **Introduction**: Present
- [ ] **Methods**: Present (or Materials and Methods)
- [ ] **Results**: Present
- [ ] **Discussion**: Present (or combined Results and Discussion)
- [ ] **Conclusion**: Present (if required by journal)
- [ ] **References**: Present and formatted consistently
- [ ] **Acknowledgements**: Present (if applicable)
- [ ] **Author Contributions**: Present (if required)
- [ ] **Competing Interests**: Present (if required)

### Figure Numbering

- [ ] **Sequential**: Figures numbered 1, 2, 3, ... (no gaps)
- [ ] **Referenced**: Every figure referenced in text
- [ ] **Captions**: Every figure has a caption
- [ ] **Format**: Captions follow journal style (e.g., "Figure 1: ..." or "Fig. 1. ...")

### Table Numbering

- [ ] **Sequential**: Tables numbered 1, 2, 3, ... (no gaps)
- [ ] **Referenced**: Every table referenced in text
- [ ] **Captions**: Every table has a caption
- [ ] **Format**: Captions follow journal style

### References

- [ ] **Numbered**: References numbered sequentially [1], [2], [3], ...
- [ ] **No gaps**: No missing numbers in sequence
- [ ] **All cited**: Every reference in list is cited in text
- [ ] **All listed**: Every citation in text appears in reference list
- [ ] **Format**: Consistent format (journal style)
- [ ] **DOI**: DOIs provided where available (if required)

### Word/Page Limits

- [ ] **Abstract**: Within limit (check journal requirements)
- [ ] **Main text**: Within limit (check journal requirements)
- [ ] **Figures**: Within limit (check journal requirements)
- [ ] **References**: Within limit (if applicable)

### Supplementary Materials

- [ ] **Referenced**: All supplementary files referenced in main text
- [ ] **Numbered**: Supplementary figures/tables numbered (S1, S2, ...)
- [ ] **Described**: Brief description of each supplementary file

## Output Format

```markdown
## Format Lint Report

**Compliance**: 90% (27/30 items)

### ✅ Correct (27 items)
- All required sections present
- Figures numbered sequentially (1-8)
- Tables numbered sequentially (1-4)
- All figures referenced in text
- All tables referenced in text
- References numbered sequentially (1-45)
- Abstract within word limit (287 words)
- ...

### ⚠️ Issues Found (3)

1. **Figure 5 not referenced in text** (Important)
   - Location: Results section
   - Action: Add reference to Figure 5 or remove figure

2. **Reference [23] not cited in text** (Minor)
   - Location: Reference list
   - Action: Remove [23] or add citation in text

3. **Table caption format inconsistent** (Minor)
   - Current: Mix of "Table 1:" and "Table 2."
   - Action: Use consistent format (e.g., "Table N: ...")

### Word Count
- Abstract: 287 words (limit: 300) ✅
- Main text: 6,234 words (limit: 7,000) ✅
- References: 45 (no limit) ✅

### Recommendation
Fix Figure 5 reference before submission. Other issues are minor.
```

## Common Issues

### Section Order

❌ **Bad**: Results before Methods  
✅ **Good**: Introduction → Methods → Results → Discussion

### Figure References

❌ **Bad**: "The results are shown in the figure below"  
✅ **Good**: "The results are shown in Figure 3"

### Reference Gaps

❌ **Bad**: [1], [2], [4], [5] (missing [3])  
✅ **Good**: [1], [2], [3], [4], [5]

### Caption Format

❌ **Bad**: Mix of "Figure 1:" and "Fig. 2." and "Figure 3 -"  
✅ **Good**: Consistent "Figure N: ..." throughout

## Journal-Specific Requirements

Different journals have different requirements. Common variations:

| Journal Type | Abstract Limit | Main Text Limit | Figure Limit | Reference Limit |
|--------------|----------------|-----------------|--------------|-----------------|
| Nature/Science | 150-200 words | 3,000-4,000 words | 4-6 figures | 30-50 refs |
| PLOS ONE | 300 words | No limit | No limit | No limit |
| Cell | 150 words | 5,000 words | 7 figures | 80 refs |
| NeurIPS | 250 words | 8 pages | No limit | No limit |
| JMLR | 200 words | No limit | No limit | No limit |

**Always check the specific journal's author guidelines.**

## Quality Gate

- **100% compliance**: Perfect formatting
- **≥90% compliance**: Excellent, minor issues only
- **80-89% compliance**: Good, fix flagged issues
- **<80% compliance**: Poor, major formatting problems

## Integration

Leader can run this check during Step 7 (draft review):
```
1. Read quality/format_lint.md
2. Check manuscript structure
3. Verify figure/table numbering
4. Check reference formatting
5. Verify word/page limits
6. Generate format lint report
7. If compliance < 90%: flag issues to writer
```

## Constraints

- **Automated checks**: Can catch numbering and structure issues
- **Manual review**: Still needed for caption quality, figure clarity
- **Journal-specific**: This is a general checklist; always check journal guidelines
- **Flexibility**: Some journals allow variations (e.g., combined Results/Discussion)
