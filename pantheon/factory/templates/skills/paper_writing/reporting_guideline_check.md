---
id: reporting_guideline_check
name: Reporting Guideline Check
description: |
  Verify compliance with domain-specific reporting guidelines (CONSORT, STROBE,
  PRISMA, etc.). Ensures manuscripts meet publication standards for study design.
source: https://www.equator-network.org/ (CONSORT, STROBE, PRISMA standards)
license: CC BY 4.0
---

# Reporting Guideline Check

Verify manuscript compliance with established reporting guidelines for different study types.

## When to Use

- Medical/clinical studies: Check CONSORT or STROBE compliance
- Systematic reviews: Check PRISMA compliance
- Observational studies: Check STROBE compliance
- Before submission to ensure manuscript meets journal requirements

## Supported Guidelines

| Guideline | Study Type | Key Requirements |
|-----------|------------|------------------|
| **CONSORT** | Randomized controlled trials (RCTs) | Trial design, randomization, blinding, flow diagram, outcomes |
| **STROBE** | Observational studies (cohort, case-control, cross-sectional) | Study design, participants, variables, bias, statistical methods |
| **PRISMA** | Systematic reviews and meta-analyses | Search strategy, selection criteria, data extraction, risk of bias |

## CONSORT Checklist (RCTs)

### Required Sections

- [ ] **Title**: Identifies study as randomized trial
- [ ] **Abstract**: Structured (Objective, Design, Setting, Participants, Interventions, Outcomes, Results, Conclusions)
- [ ] **Introduction**: Scientific background and rationale
- [ ] **Methods**:
  - [ ] Trial design (parallel, factorial, crossover)
  - [ ] Participants (eligibility criteria, settings)
  - [ ] Interventions (details for each group)
  - [ ] Outcomes (primary and secondary, how measured)
  - [ ] Sample size (calculation and rationale)
  - [ ] Randomization (sequence generation, allocation concealment)
  - [ ] Blinding (who was blinded, how)
  - [ ] Statistical methods
- [ ] **Results**:
  - [ ] Participant flow diagram (CONSORT diagram)
  - [ ] Baseline characteristics table
  - [ ] Outcomes for each group
  - [ ] Adverse events
- [ ] **Discussion**: Interpretation, limitations, generalizability
- [ ] **Registration**: Trial registration number and name of registry

### Critical Items (Must Have)

1. **CONSORT flow diagram**: Shows participant flow through trial
2. **Randomization method**: How sequence was generated
3. **Blinding**: Who was blinded and how
4. **Primary outcome**: Clearly defined and reported

## STROBE Checklist (Observational Studies)

### Required Sections

- [ ] **Title/Abstract**: Study design in title or abstract
- [ ] **Introduction**: Background, objectives
- [ ] **Methods**:
  - [ ] Study design (cohort, case-control, cross-sectional)
  - [ ] Setting, locations, dates
  - [ ] Participants (eligibility, sources, selection)
  - [ ] Variables (outcomes, exposures, confounders)
  - [ ] Data sources and measurement
  - [ ] Bias (how addressed)
  - [ ] Study size (how determined)
  - [ ] Statistical methods
- [ ] **Results**:
  - [ ] Participant numbers at each stage
  - [ ] Descriptive data (characteristics, exposures)
  - [ ] Outcome data
  - [ ] Main results (estimates, confidence intervals)
- [ ] **Discussion**: Key results, limitations, interpretation

### Critical Items (Must Have)

1. **Study design**: Explicitly stated (cohort/case-control/cross-sectional)
2. **Participant flow**: Numbers at each stage
3. **Bias**: How potential biases were addressed
4. **Confidence intervals**: For all main estimates

## PRISMA Checklist (Systematic Reviews)

### Required Sections

- [ ] **Title**: Identifies as systematic review/meta-analysis
- [ ] **Abstract**: Structured summary
- [ ] **Introduction**: Rationale, objectives, PICO question
- [ ] **Methods**:
  - [ ] Protocol registration (PROSPERO)
  - [ ] Eligibility criteria
  - [ ] Information sources (databases, dates)
  - [ ] Search strategy (full for at least one database)
  - [ ] Selection process
  - [ ] Data extraction
  - [ ] Risk of bias assessment
  - [ ] Synthesis methods (meta-analysis if applicable)
- [ ] **Results**:
  - [ ] PRISMA flow diagram (study selection)
  - [ ] Study characteristics table
  - [ ] Risk of bias assessment results
  - [ ] Synthesis results (forest plots if meta-analysis)
- [ ] **Discussion**: Summary, limitations, conclusions

### Critical Items (Must Have)

1. **PRISMA flow diagram**: Shows study selection process
2. **Search strategy**: Full strategy for at least one database
3. **Risk of bias**: Assessment for included studies
4. **Protocol registration**: PROSPERO or equivalent

## Output Format

```markdown
## Reporting Guideline Compliance Report

**Guideline**: CONSORT (RCT)
**Compliance**: 85% (23/27 items)

### ✅ Compliant Items (23)
- Title identifies as randomized trial
- Abstract is structured
- Trial design clearly stated (parallel-group)
- Randomization method described
- Primary outcome defined
- ...

### ⚠️ Missing Items (4)
1. **CONSORT flow diagram** (Critical)
   - Location: Results section
   - Action: Add participant flow diagram showing enrollment, allocation, follow-up, analysis
   
2. **Sample size calculation** (Important)
   - Location: Methods section
   - Action: Add power calculation and assumptions
   
3. **Trial registration number** (Critical)
   - Location: End of abstract or methods
   - Action: Add ClinicalTrials.gov identifier
   
4. **Adverse events table** (Important)
   - Location: Results section
   - Action: Add table summarizing adverse events by group

### Recommendation
Address 2 critical items (flow diagram, registration) before submission.
Sample size and adverse events can be added during revision if requested.
```

## Quality Gate

- **≥90% compliance**: Excellent, ready for submission
- **80-89% compliance**: Good, address critical items
- **70-79% compliance**: Fair, address all missing items
- **<70% compliance**: Poor, major revision needed

## Integration

Run this during draft review:
```
If study type is RCT/observational/systematic review:
  Read quality/reporting_guideline_check.md
  Identify guideline (CONSORT/STROBE/PRISMA)
  Check manuscript against the checklist
  Generate a compliance report
  If compliance < 80%: flag critical missing items for revision
```

## Constraints

- **Not applicable to all papers**: Only for clinical/medical studies
- **Guideline selection**: Must match study design
- **Flexibility**: Some items may not apply to specific contexts
- **Journal-specific**: Some journals have additional requirements
