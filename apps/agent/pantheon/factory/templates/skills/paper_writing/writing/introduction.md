---
id: introduction_writing
name: "Introduction Writing Best Practices"
description: |
  Logic map and backward reasoning approach for clear, compelling introductions.
  Based on Research-Paper-Writing-Skills.
source: https://github.com/Master-cai/Research-Paper-Writing-Skills
license: MIT
tags: [introduction, writing, best-practices, logic-map]
---

# Introduction Writing Best Practices

Source: [Research-Paper-Writing-Skills](https://github.com/Master-cai/Research-Paper-Writing-Skills)

## Overview

The introduction establishes context, identifies the gap, and presents your contribution. Use the **Logic Map** structure for clarity and impact.

---

## Logic Map Structure

### Four-Part Framework

```
Task → Challenge → Solution → Advantage
```

1. **Task**: What is the research area and why does it matter?
2. **Challenge**: What specific problem or gap exists?
3. **Solution**: What did we do to address it?
4. **Advantage**: Why is our approach better than alternatives?

---

## Detailed Structure

### Paragraph 1: Task (Background)

**Purpose**: Establish the research area and its importance.

**Content**:
- General context (1-2 sentences)
- Why this area matters (1 sentence)
- Current state of the field (1-2 sentences)

**Example**:
> Single-cell RNA sequencing (scRNA-seq) has transformed our ability to study cellular heterogeneity at unprecedented resolution. This technology enables the profiling of thousands of individual cells, revealing rare cell types and transient states that bulk methods cannot detect. As scRNA-seq datasets grow in size and complexity, integrating data from multiple experiments has become essential for comprehensive biological insights.

**Key phrases**:
- "has revolutionized..."
- "enables researchers to..."
- "is critical for understanding..."

---

### Paragraph 2: Challenge (Knowledge Gap)

**Purpose**: Identify the specific problem your paper addresses.

**Content**:
- What is the limitation of current approaches? (2-3 sentences)
- Why is this limitation important? (1 sentence)
- What are the consequences of not solving it? (1 sentence)

**Example**:
> However, integrating scRNA-seq datasets from different experiments remains challenging due to batch effects—systematic technical variations that obscure biological signals. Existing batch correction methods often face a trade-off: aggressive correction removes batch effects but also eliminates subtle biological variation, while conservative correction preserves biology but leaves residual batch effects. This trade-off is particularly problematic for rare cell types, which are easily overcorrected and lost in the integration process.

**Key phrases**:
- "However, ..."
- "A major challenge is..."
- "Current methods struggle with..."
- "This limitation prevents..."

---

### Paragraph 3: Solution (Your Contribution)

**Purpose**: Present your approach and key innovations.

**Content**:
- What did we develop? (1 sentence)
- What is the key innovation? (1-2 sentences)
- How does it work? (1-2 sentences, high-level)

**Example**:
> To address this challenge, we developed AdaptiveHarmony, a batch correction method that applies cell-type-specific correction strengths. Our key insight is that different cell types require different levels of correction: abundant cell types benefit from aggressive correction, while rare cell types need conservative correction to avoid overcorrection. AdaptiveHarmony automatically estimates the optimal correction strength for each cell type using a data-driven approach, balancing batch effect removal with biological variation preservation.

**Key phrases**:
- "We present..."
- "Our key insight is..."
- "The method works by..."
- "Unlike previous approaches..."

---

### Paragraph 4: Advantage (Why It's Better)

**Purpose**: Explain why your solution is superior to alternatives.

**Content**:
- Comparison to existing methods (1-2 sentences)
- Quantitative improvements (1 sentence with numbers)
- Broader impact (1 sentence)

**Example**:
> Compared to existing methods that apply uniform correction across all cell types, AdaptiveHarmony achieves superior performance on datasets with rare populations. On benchmark datasets, AdaptiveHarmony preserves 95% of rare cell type markers while achieving 0.90 ARI for batch mixing, outperforming Harmony (0.85 ARI, 78% marker preservation) and Seurat CCA (0.82 ARI, 81% marker preservation). This enables more accurate identification and characterization of rare cell types in integrated datasets.

**Key phrases**:
- "Compared to..."
- "Our method achieves..."
- "This represents a X% improvement over..."
- "This enables..."

---

### Paragraph 5: Paper Roadmap (Optional)

**Purpose**: Guide the reader through the paper structure.

**Content**:
- Brief overview of remaining sections (2-3 sentences)

**Example**:
> The remainder of this paper is organized as follows. We first describe the AdaptiveHarmony algorithm and its theoretical foundation. We then present benchmark results on 10 datasets, demonstrating superior performance on rare cell type preservation. Finally, we apply AdaptiveHarmony to a large-scale atlas integration task, revealing previously undetected rare immune cell populations.

**When to include**: Papers with complex structure or multiple contributions.

---

## Writing Strategy: Backward Reasoning

### Step 1: Clarify the Problem First

Before writing, answer:
- What is the specific problem we solve?
- Why is it important?
- What makes it hard?

### Step 2: Write Solution First

Draft Paragraph 3 (Solution) first:
- Forces you to be clear about your contribution
- Helps identify what background is needed

### Step 3: Write Challenge Second

Draft Paragraph 2 (Challenge):
- Now you know what gap to emphasize
- Highlight limitations that your solution addresses

### Step 4: Write Task Third

Draft Paragraph 1 (Background):
- Provide only the context needed for the challenge
- Avoid unnecessary background

### Step 5: Write Advantage Last

Draft Paragraph 4 (Advantage):
- Compare to specific alternatives
- Use concrete numbers from your results

### Step 6: Reorder and Polish

Rearrange to Task → Challenge → Solution → Advantage order.

---

## Quality Checklist

Before finalizing introduction:

- [ ] **Logic Map complete**: All four parts present
- [ ] **Specific challenge**: Not vague "X is important" but "X fails when..."
- [ ] **Clear contribution**: Reader knows exactly what you did
- [ ] **Quantitative advantage**: Concrete numbers, not "better"
- [ ] **Smooth transitions**: Each paragraph flows to the next
- [ ] **No premature details**: Save technical details for Methods
- [ ] **Citations present**: Key claims backed by references
- [ ] **Length appropriate**: 4-6 paragraphs, 1-1.5 pages

---

## Common Mistakes to Avoid

1. **Too much background**: Introduction is not a review paper
   - ❌ "Single-cell sequencing was invented in 2009..."
   - ✅ "Single-cell sequencing enables..."

2. **Vague challenge**: Problem not specific enough
   - ❌ "Batch effects are a problem"
   - ✅ "Batch effects cause rare cell types to be overcorrected"

3. **Missing advantage**: Ends with solution, no comparison
   - ❌ "We developed method X"
   - ✅ "Method X achieves 15% better accuracy than Y"

4. **Premature details**: Technical details belong in Methods
   - ❌ "We use a graph neural network with 3 layers..."
   - ✅ "We use a graph-based approach..."

5. **No roadmap**: Reader doesn't know what's coming
   - Add Paragraph 5 if paper structure is complex

---

## Example: Complete Introduction

> **[Task]** Single-cell RNA sequencing (scRNA-seq) has transformed our ability to study cellular heterogeneity at unprecedented resolution. This technology enables the profiling of thousands of individual cells, revealing rare cell types and transient states that bulk methods cannot detect. As scRNA-seq datasets grow in size and complexity, integrating data from multiple experiments has become essential for comprehensive biological insights.
>
> **[Challenge]** However, integrating scRNA-seq datasets from different experiments remains challenging due to batch effects—systematic technical variations that obscure biological signals. Existing batch correction methods often face a trade-off: aggressive correction removes batch effects but also eliminates subtle biological variation, while conservative correction preserves biology but leaves residual batch effects. This trade-off is particularly problematic for rare cell types, which are easily overcorrected and lost in the integration process.
>
> **[Solution]** To address this challenge, we developed AdaptiveHarmony, a batch correction method that applies cell-type-specific correction strengths. Our key insight is that different cell types require different levels of correction: abundant cell types benefit from aggressive correction, while rare cell types need conservative correction to avoid overcorrection. AdaptiveHarmony automatically estimates the optimal correction strength for each cell type using a data-driven approach, balancing batch effect removal with biological variation preservation.
>
> **[Advantage]** Compared to existing methods that apply uniform correction across all cell types, AdaptiveHarmony achieves superior performance on datasets with rare populations. On benchmark datasets, AdaptiveHarmony preserves 95% of rare cell type markers while achieving 0.90 ARI for batch mixing, outperforming Harmony (0.85 ARI, 78% marker preservation) and Seurat CCA (0.82 ARI, 81% marker preservation). This enables more accurate identification and characterization of rare cell types in integrated datasets.

---

## Template for Quick Start

Use this template and fill in the blanks:

```markdown
## Introduction

[TASK] {Research area} has {importance}. This technology/approach enables {capability}. As {trend}, {why integration/advancement matters}.

[CHALLENGE] However, {specific problem} remains challenging due to {root cause}. Existing methods {limitation 1} or {limitation 2}. This trade-off is particularly problematic for {specific case}, which {consequence}.

[SOLUTION] To address this challenge, we developed {method name}, a {method type} that {key innovation}. Our key insight is that {insight}. {Method name} {how it works at high level}.

[ADVANTAGE] Compared to existing methods that {baseline approach}, {method name} achieves {quantitative improvement}. On benchmark datasets, {method name} {metric 1} while {metric 2}, outperforming {baseline 1} and {baseline 2}. This enables {broader impact}.
```
