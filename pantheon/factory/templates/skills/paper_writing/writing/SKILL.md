---
id: writing_skills_index
name: "Writing Skills Index"
description: |
  Section-specific writing best practices for scientific papers.
  Based on Research-Paper-Writing-Skills and AI-Scientist.
---

# Writing Skills Index

Best practices for writing each section of a scientific paper. Writer agent should read the relevant skill file before writing each section.

---

## Available Skills

### Abstract Writing

Three proven abstract templates for different paper types.

**Skill file**: [abstract.md](./abstract.md)

**When to use**:
- Before writing the Abstract section
- Choose template based on paper structure (single contribution vs. multiple contributions vs. insight-driven)

**Source**: [Research-Paper-Writing-Skills](https://github.com/Master-cai/Research-Paper-Writing-Skills) (MIT License)

---

### Introduction Writing

Logic map and backward reasoning approach for clear, compelling introductions.

**Skill file**: [introduction.md](./introduction.md)

**When to use**:
- Before writing the Introduction section
- Follow Task → Challenge → Solution → Advantage structure

**Source**: [Research-Paper-Writing-Skills](https://github.com/Master-cai/Research-Paper-Writing-Skills) (MIT License)

---

### Methods Writing

Reproducibility checklist and essential details for methods sections.

**Skill file**: [method.md](./method.md)

**When to use**:
- Before writing the Methods section
- Ensure all reproducibility requirements are met

**Source**: [Research-Paper-Writing-Skills](https://github.com/Master-cai/Research-Paper-Writing-Skills) (MIT License)

---

### Results Writing

Guidelines for presenting experimental results with proper evidence.

**Skill file**: [results.md](./results.md)

**When to use**:
- Before writing the Results section
- Ensure each subsection references at least one figure/table

**Source**: [Research-Paper-Writing-Skills](https://github.com/Master-cai/Research-Paper-Writing-Skills) (MIT License)

---

### Discussion Writing

Structure for interpretation, comparison, limitations, and future work.

**Skill file**: [discussion.md](./discussion.md)

**When to use**:
- Before writing the Discussion section
- Follow Interpretation → Comparison → Limitations → Future structure

**Source**: [Research-Paper-Writing-Skills](https://github.com/Master-cai/Research-Paper-Writing-Skills) (MIT License)

---

### Claim-Evidence Alignment Check

Protocol for verifying that every major claim has supporting evidence.

**Skill file**: [claim_evidence_check.md](./claim_evidence_check.md)

**When to use**:
- After completing the draft (self-check)
- Before submitting to leader for review
- Target: ≥80% of claims must be supported

**Source**: [Research-Paper-Writing-Skills](https://github.com/Master-cai/Research-Paper-Writing-Skills) (MIT License)

---

### Reviewer Rubric

NeurIPS-standard peer review scoring rubric for pre-submission quality check.

**Skill file**: [reviewer_rubric.md](./reviewer_rubric.md)

**When to use**:
- Leader uses this for peer review simulation
- Not for writer's direct use

**Source**: [AI-Scientist](https://github.com/SakanaAI/AI-Scientist) (MIT License)

---

## Usage Workflow

### For Writer Agent

1. **Before writing each section**: Read the corresponding skill file
2. **Follow the structure**: Apply the templates and guidelines
3. **After completing draft**: Run claim-evidence check
4. **If alignment < 80%**: Revise and re-check

### For Leader Agent

1. **After writer completes draft**: Optionally call reviewer simulation
2. **Use reviewer_rubric.md**: Guide the review process
3. **If overall score < 5**: Identify issues and ask writer to revise

---

## Quality Standards

All writing skills aim to achieve:
- **Clarity**: Clear, concise, unambiguous language
- **Evidence**: Every claim backed by citation or data
- **Structure**: Logical flow and organization
- **Reproducibility**: Sufficient detail for replication
- **Impact**: Clear contribution and significance
