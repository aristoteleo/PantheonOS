---
id: conference_talk_scenario
name: "Conference Talk Scenario"
description: |
  Workflow for transforming paper content into audience-facing conference talk
  with clear storyline and slide structure.
source: https://github.com/assafelovic/academic-research-skills
license: Apache 2.0
---

# Conference Talk Scenario

## When to Use

- User says: "会议演讲", "conference talk", "presentation", "oral presentation"
- User provides: Paper (draft or published) or research materials
- Goal: Transform research into engaging 10-15 minute talk
- Output: Talk outline + slide structure + speaker notes

---

## Workflow Overview

```
Extract Core Message → Build Storyline → Design Slide Structure 
  → Write Speaker Notes → Generate Slide Outline
```

---

## Detailed Steps

### Step 1: Extract Core Message

**Leader action**: Identify the single most important message

**Questions to answer**:
1. What is the ONE thing the audience should remember?
2. Why should they care?
3. What makes this work different?

**Deliverable**: `{workdir}/core_message.md`

**Example**:
```markdown
# Core Message

**One-sentence takeaway**: 
Cell-type-specific batch correction preserves rare cell types that uniform methods lose.

**Why it matters**: 
Rare cell types are often biologically important but easily lost during data integration.

**What's different**: 
First unsupervised method to automatically adjust correction strength per cell type.
```

---

### Step 2: Build Storyline

**Leader action**: Structure talk as a story, not a paper

**Story arc** (10-15 minutes):
1. **Hook** (30 sec): Grab attention
2. **Problem** (2 min): Why this matters
3. **Insight** (1 min): Key realization
4. **Solution** (3 min): What we did
5. **Evidence** (4 min): Main results
6. **Impact** (1 min): What this enables
7. **Takeaway** (30 sec): Reinforce core message

**Deliverable**: `{workdir}/storyline.md`

**Example**:
```markdown
# Talk Storyline

## 1. Hook (30 sec)
Show striking image: rare cell population visible with our method, invisible with others.
"This rare cell type represents 0.3% of cells but is critical for disease progression. 
Standard methods lose it. We don't."

## 2. Problem (2 min)
- Single-cell atlases integrate data from many experiments
- Batch effects obscure biology
- Current methods face a trade-off: remove batch effects OR preserve rare cells
- Show Figure: uniform correction overcorrects rare cells

## 3. Insight (1 min)
Key realization: different cell types need different correction strengths.
- Abundant cells: can handle aggressive correction
- Rare cells: need gentle correction
Show schematic: cell-type-specific correction concept

## 4. Solution (3 min)
AdaptiveHarmony: automatically estimates optimal correction per cell type
- Algorithm overview (1 slide)
- How it works (1 slide with animation)
- Unsupervised, no labels needed

## 5. Evidence (4 min)
Three key results:
- Benchmark: 95% marker preservation vs. 78% for baselines (1 slide)
- Case study: discovered rare population in HCA data (1 slide)
- Efficiency: comparable speed to existing methods (1 slide)

## 6. Impact (1 min)
Enables:
- More accurate rare cell type discovery
- Better atlas construction
- Improved biological insights

## 7. Takeaway (30 sec)
Reinforce core message: cell-type-specific correction preserves rare cells.
Code available at github.com/username/repo
```

---

### Step 3: Design Slide Structure

**Leader calls**: `writer`

**Instruction**:
```
Design slide structure for conference talk. Workdir: {workdir}.
Storyline: {workdir}/storyline.md
Core message: {workdir}/core_message.md

Guidelines:
- 10-12 slides for 15-minute talk (1-1.5 min per slide)
- Each slide has ONE main point
- Minimize text, maximize visuals
- Use build animations for complex slides
- Include slide numbers

Deliverable: {workdir}/draft/slide_outline.md
```

**Slide design principles**:
- **One idea per slide**: Don't cram multiple points
- **Visual > Text**: Use figures, diagrams, photos
- **Large fonts**: 24pt minimum for body text
- **High contrast**: Dark text on light background
- **Minimal bullets**: 3-5 bullets max, or none

**Deliverable**: `{workdir}/draft/slide_outline.md`

**Example**:
```markdown
# Slide Outline

## Slide 1: Title
- Title: AdaptiveHarmony: Cell-Type-Specific Batch Correction
- Authors, affiliations
- Conference logo
- Visual: Key result figure as background (faded)

## Slide 2: Hook
- Visual: Split screen comparison
  - Left: Standard method (rare cells lost)
  - Right: Our method (rare cells preserved)
- Text: "0.3% of cells, 100% critical"
- No bullets, just the image and one line

## Slide 3: The Problem
- Title: "Batch Effects Obscure Rare Cell Types"
- Visual: UMAP showing batch effects
- 3 bullets:
  - Single-cell atlases integrate many experiments
  - Batch effects must be corrected
  - Current methods lose rare cells

## Slide 4: The Trade-Off
- Title: "Uniform Correction Fails for Rare Cells"
- Visual: Diagram showing overcorrection
- Annotation: "Too aggressive for rare cells"

## Slide 5: Key Insight
- Title: "Different Cell Types Need Different Correction"
- Visual: Schematic with cell types and correction strengths
- Build animation: show correction strength per cell type

## Slide 6: Our Solution
- Title: "AdaptiveHarmony: Automatic Cell-Type-Specific Correction"
- Visual: Algorithm flowchart
- 3 steps:
  1. Estimate cell type structure
  2. Calculate optimal correction per type
  3. Apply adaptive correction

## Slide 7: How It Works
- Title: "Unsupervised, No Labels Needed"
- Visual: Animated diagram
- Build: Show each step appearing

## Slide 8: Benchmark Results
- Title: "95% Marker Preservation vs. 78% for Baselines"
- Visual: Bar chart comparing methods
- Highlight: Our method in color, others in gray

## Slide 9: Case Study
- Title: "Discovered Rare Population in Human Cell Atlas"
- Visual: UMAP with rare population highlighted
- Inset: Marker gene expression

## Slide 10: Computational Efficiency
- Title: "Fast and Scalable"
- Visual: Runtime comparison chart
- Text: "8 minutes for 100K cells"

## Slide 11: Impact
- Title: "Enables Accurate Rare Cell Discovery"
- Visual: Three application examples
- 3 bullets:
  - Better atlas construction
  - Novel cell state discovery
  - Improved biological insights

## Slide 12: Takeaway
- Title: "Cell-Type-Specific Correction Preserves Rare Cells"
- Visual: Core message diagram
- Text: "Code: github.com/username/repo"
- Text: "Questions?"
```

---

### Step 4: Write Speaker Notes

**Leader calls**: `writer`

**Instruction**:
```
Write speaker notes for each slide. Workdir: {workdir}.
Slide outline: {workdir}/draft/slide_outline.md

For each slide, write:
- What to say (word-for-word or bullet points)
- Timing (how long to spend)
- Transitions (how to move to next slide)
- Backup explanations (if audience asks)

Deliverable: {workdir}/draft/speaker_notes.md
```

**Speaker notes format**:

```markdown
## Slide 2: Hook

**What to say**:
"Let me start with this image. On the left, you see what happens with standard batch correction methods. This rare cell population—representing just 0.3% of cells—completely disappears. On the right, our method preserves it. And this isn't just any cell population. It's critical for understanding disease progression."

**Timing**: 30 seconds

**Transition**: "So why do standard methods fail? Let me show you the problem."

**Backup**: If asked "How rare is 0.3%?": "In a typical dataset of 50,000 cells, that's only 150 cells. But these 150 cells can be the difference between understanding a disease mechanism or missing it entirely."
```

**Deliverable**: `{workdir}/draft/speaker_notes.md`

---

### Step 5: Generate Slide Outline Document

**Leader calls**: `reporter`

**Instruction**:
```
Generate talk outline document. Workdir: {workdir}.
Source: {workdir}/draft/slide_outline.md + speaker_notes.md
Theme: talk_outline (if available, else academic_minimal)

Output: {workdir}/report/talk_outline.html
```

**Deliverable**: `{workdir}/report/talk_outline.html`

---

## Output Structure

```
{workdir}/
├── core_message.md              # One-sentence takeaway
├── storyline.md                 # Story arc
├── draft/
│   ├── slide_outline.md         # Slide-by-slide structure
│   └── speaker_notes.md         # What to say
└── report/
    └── talk_outline.html        # Combined outline
```

---

## Talk Design Principles

### Principle 1: Story > Structure

❌ **Bad**: Follow paper structure (Intro → Methods → Results → Discussion)
✅ **Good**: Follow story arc (Problem → Insight → Solution → Evidence → Impact)

**Why**: Papers are for reading, talks are for listening. Stories engage, structures bore.

---

### Principle 2: Show > Tell

❌ **Bad**: "Our method achieves 95% marker preservation."
✅ **Good**: [Show bar chart with 95% vs. 78%]

**Why**: Visuals are processed faster and remembered longer than text.

---

### Principle 3: One Idea Per Slide

❌ **Bad**: Slide with 3 figures, 2 tables, 10 bullets
✅ **Good**: Slide with 1 figure, 1 main point

**Why**: Audience can't read and listen simultaneously. One idea = one slide.

---

### Principle 4: Minimize Text

❌ **Bad**: Full sentences in bullets
✅ **Good**: 3-5 words per bullet, or no bullets at all

**Why**: If they're reading, they're not listening.

---

### Principle 5: Build Complexity Gradually

❌ **Bad**: Show complete complex diagram all at once
✅ **Good**: Use build animations to reveal parts sequentially

**Why**: Reduces cognitive load, guides attention.

---

## Timing Guidelines

For a **15-minute talk**:

| Section | Time | Slides |
|---------|------|--------|
| Hook | 30 sec | 1 |
| Problem | 2 min | 2-3 |
| Insight | 1 min | 1 |
| Solution | 3 min | 2-3 |
| Evidence | 4 min | 3-4 |
| Impact | 1 min | 1 |
| Takeaway | 30 sec | 1 |
| **Total** | **12 min** | **11-14** |

**Buffer**: 3 minutes for questions or overrun.

**Rule of thumb**: 1-1.5 minutes per slide.

---

## Common Mistakes to Avoid

### 1. Too Many Slides

❌ **Bad**: 25 slides for 15 minutes
✅ **Good**: 12 slides for 15 minutes

**Fix**: Merge slides, remove non-essential content.

---

### 2. Text-Heavy Slides

❌ **Bad**: Paragraphs of text
✅ **Good**: One sentence or image

**Fix**: Convert text to visuals or speaker notes.

---

### 3. No Clear Takeaway

❌ **Bad**: Talk ends with "Thank you"
✅ **Good**: Talk ends with core message reinforced

**Fix**: Add explicit takeaway slide.

---

### 4. Skipping the Hook

❌ **Bad**: Start with "Today I'll talk about..."
✅ **Good**: Start with striking visual or surprising fact

**Fix**: Design a compelling opening.

---

### 5. Methods-Heavy

❌ **Bad**: 5 slides on algorithm details
✅ **Good**: 1-2 slides on key idea, rest on results

**Fix**: Move details to backup slides or paper.

---

## Quality Checklist

Before finalizing talk:

- [ ] **Core message is clear** (one-sentence takeaway)
- [ ] **Story arc is compelling** (Hook → Problem → Solution → Evidence → Impact)
- [ ] **Slide count is appropriate** (10-14 slides for 15 min)
- [ ] **Each slide has one main point**
- [ ] **Visuals dominate text** (figures > bullets)
- [ ] **Fonts are large** (≥24pt body text)
- [ ] **Timing is realistic** (1-1.5 min per slide)
- [ ] **Takeaway is reinforced** (last slide restates core message)

---

## Example: Complete Talk Outline

See `slide_outline.md` example above for complete structure.

**Key characteristics**:
- 12 slides for 15-minute talk
- Each slide has one main visual
- Minimal text (3-5 bullets max)
- Clear story progression
- Strong hook and takeaway

---

## Success Metrics

A successful conference talk:
- Delivers core message in 12-15 minutes
- Engages audience with visuals and story
- Leaves audience with clear takeaway
- Generates questions and interest
- Fits within time limit (with buffer)
