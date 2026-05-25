---
id: citation_grounding
name: Citation Grounding
description: |
  Verify that citations actually support the claims they are used for.
  Assesses grounding strength (strong/partial/weak/unsupported) and identifies
  misattributions or overclaiming.
source: https://github.com/nature-citation (nature citation verification practices)
license: MIT
---

# Citation Grounding

Verify that each citation properly supports the claim it is used for.

## When to Use

- Completing a draft and running self-check
- Reviewing draft quality before rendering
- Addressing reviewer comments about citation accuracy

## Input

- **Claim**: The statement being made in the paper
- **Citations**: List of papers cited to support the claim
- **Context**: Surrounding text (optional, for disambiguation)

## Process

1. **Extract claim**: Identify the specific assertion being made
2. **Read cited papers**: Extract relevant sections (abstract, results, discussion)
3. **Assess grounding**: Determine if citations support the claim
4. **Grade strength**: Strong / Partial / Weak / Unsupported
5. **Suggest action**: Keep / Narrow wording / Add evidence / Remove

## Grounding Strength Levels

| Level | Definition | Example | Action |
|-------|------------|---------|--------|
| **Strong** | Citation directly supports claim with experimental evidence | "Method X achieves 95% accuracy [1]" where [1] reports 95% in Table 2 | ✅ Keep |
| **Partial** | Citation supports claim but with caveats or limited scope | "Method X outperforms baselines [1]" where [1] only tested 3 baselines | ⚠️ Narrow wording ("outperforms three baselines") |
| **Weak** | Citation tangentially related but doesn't directly support claim | "Method X is widely used [1]" where [1] mentions X once in passing | ⚠️ Add stronger evidence or remove claim |
| **Unsupported** | Citation does not support claim at all (misattribution) | "Method X is the best [1]" where [1] doesn't compare methods | ❌ Remove or downgrade claim |

## Output Format

```markdown
## Citation Grounding Report

### Claim 1: "Our method achieves 95% accuracy on benchmark X"
- **Citations**: [12], [15]
- **Grounding**: Strong
- **Evidence**:
  - [12] Table 2 reports 95.2% accuracy on benchmark X
  - [15] Figure 3 shows consistent performance across datasets
- **Action**: ✅ Keep as-is

### Claim 2: "This is the first unsupervised approach"
- **Citations**: [8], [9]
- **Grounding**: Weak
- **Evidence**:
  - [8] mentions unsupervised methods exist but doesn't claim novelty
  - [9] is about supervised learning, not relevant
- **Action**: ⚠️ Narrow to "This is the first unsupervised approach for domain X" or add survey citation

### Claim 3: "Our method will revolutionize the field"
- **Citations**: None
- **Grounding**: Unsupported
- **Evidence**: No citations provided, subjective claim
- **Action**: ❌ Remove or move to Future Work with hedging ("may have significant impact")
```

## Quality Gates

- ✅ **Strong grounding**: Claim has direct experimental support from citations
- ⚠️ **Partial grounding**: Claim is supported but scope is narrower than stated
- ❌ **Weak/Unsupported**: Claim lacks proper evidence

## Common Issues

### Overclaiming
- **Problem**: "Our method is the best" but only compared to 3 baselines
- **Fix**: "Our method outperforms three widely-used baselines"

### Misattribution
- **Problem**: Citing a review paper for a specific experimental result
- **Fix**: Cite the original paper that reported the result

### Missing context
- **Problem**: "Method X achieves 95% accuracy" without specifying dataset
- **Fix**: "Method X achieves 95% accuracy on ImageNet [1]"

### Circular citation
- **Problem**: Citing your own paper as evidence for a claim that paper also doesn't support
- **Fix**: Find external validation or remove claim

## Integration with Drafting

After completing the draft:
1. Read `evidence/citation_grounding.md`
2. Extract major claims from Abstract and Introduction
3. For each claim, check grounding strength
4. Generate a grounding report
5. Fix weak / unsupported claims
6. Re-run the check until all claims are strong or partial

## Constraints

- **Scope**: Focus on major claims (Abstract, Introduction, Conclusion)
- **Depth**: Don't need to verify every citation, focus on bold claims
- **Pragmatism**: Partial grounding is acceptable if claim is appropriately hedged
- **Honesty**: Better to weaken a claim than to overclaim

## Example Workflow

```
1. Complete draft/paper.md
2. Read evidence/citation_grounding.md
3. Extract claims:
   - "Our method achieves 95% accuracy"
   - "This is the first unsupervised approach"
   - "Results show significant improvement"
4. Check each claim against citations
5. Generate a grounding report
6. Fix weak claims:
   - Claim 2: Add "for protein structure prediction" to narrow scope
   - Claim 3: Add citation [23] that reports p < 0.001
7. Re-run the check: all claims now strong or partial
8. Write quality_check_report.md with the grounding summary
```
