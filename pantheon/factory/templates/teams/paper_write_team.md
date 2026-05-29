---
category: scientific_writing
description: |
  AI team for autonomous report and academic paper writing.
  Markdown-first architecture: writer produces paper.md (SSoT) in standard Markdown,
  reporter renders HTML preview and (for academic style) compiles PDF via Tectonic.
  - Report style (default): HTML template + CSS. UI handles HTML → PDF export.
  - Academic style: LaTeX template + Tectonic → PDF. HTML preview also generated.
icon: 📝
id: paper_write_team
name: Paper Write Team
type: team
version: 3.1.0
agents:
  - paper_write/leader
  - researcher
  - paper_write/writer
  - paper_write/reporter
---

# Paper Write Team

A Markdown-first AI team for autonomous report and academic paper writing. The writer produces a single `paper.md` (standard Markdown) as the source of truth. The reporter converts it to HTML preview and, for academic style, compiles a Tectonic PDF. For report style, the UI exports the HTML to PDF on demand.

## Architecture

```
paper.md (SSoT — standard Markdown + lightweight frontmatter)
    │
    ├── Report style (DEFAULT)
    │   ├── Reporter reads HTML template + CSS from skill
    │   ├── Reporter converts MD → HTML, fills template → preview.html
    │   ├── UI exports HTML → PDF (on user request)
    │   └── pandoc → DOCX (optional)
    │
    └── Academic style (user explicitly requests)
        ├── Reporter reads LaTeX template from skill
        ├── Reporter converts MD → LaTeX, fills template → .tex
        ├── Tectonic → PDF
        ├── Reporter also generates HTML preview (academic template)
        └── pandoc → DOCX (optional)
```

## Team Structure

| Agent | Role | Key Capabilities |
|-------|------|------------------|
| **leader** | Orchestrator | Input triage, style/template config, writer/reporter scheduling, user feedback routing |
| **researcher** | Generalist support | Literature review, bibtex generation, data EDA, environment audit, package installation |
| **writer** | Document author | Produces `paper.md` in standard Markdown; calls researcher for evidence gaps |
| **reporter** | Conversion engine | Report: MD → HTML. Academic: MD → LaTeX → Tectonic → PDF + HTML preview |

## Deliverables

- `report/<slug>_preview.html` — always generated; UI renders for preview/editing and PDF export
- `report/<slug>.pdf` — academic style only (via Tectonic); report style PDF is exported by the UI from the HTML
- `report/<slug>.tex` — academic style only (LaTeX source)
- `report/<slug>.docx` — on demand (via pandoc)
- `report/DELIVERY.md` — final delivery summary

## Styles

| Style | Default? | Trigger | Pipeline | Tools |
|-------|----------|---------|----------|-------|
| `report` | YES (no PDF requested) | "报告"/"分析"/"调研" without explicit PDF ask, or unspecified output format | HTML template + CSS (UI exports to PDF) | None (agent does string ops only) |
| `academic` | YES (PDF requested) | "论文"/"paper"/"投稿"/"综述"/"academic" — **or any mention of PDF**: "PDF"/"pdf"/"导出 PDF"/"生成 PDF"/"compile PDF"/"high-quality PDF" | LaTeX template → Tectonic → PDF | Tectonic |

**PDF override rule (highest priority):** If the user explicitly asks for a PDF artefact — by saying "PDF", "导出 PDF", "生成 PDF report", "compile PDF", or otherwise referencing a `.pdf` file as the deliverable — **always pick `academic` style**, regardless of other triage signals. Rationale: the `report` pipeline does not produce a `.pdf` file (it only produces HTML + relies on the UI's browser print-to-PDF, which has lower typesetting quality than LaTeX/Tectonic for fonts, page breaks, equations, and tables). Picking `academic` here gives the user a real PDF file directly.

## Templates (via paper_writing skill)

Each template is a self-contained markdown file with HTML+CSS or LaTeX in code blocks.

```
.pantheon/skills/paper_writing/
├── SKILL.md                       # Skill index (reporter reads this first)
├── report_standard.md             # Report: HTML template + CSS (Manus-style)
├── report_academic.md             # Academic: HTML template + CSS (LaTeX-like)
├── latex_cn.md                    # Chinese academic LaTeX template
└── latex_en.md                    # English academic LaTeX template
```

## Supported Input Shapes

| Input | Branch |
|---|---|
| Upstream workdir (e.g., `single_cell_team` output) | Skip literature review → inventory → outline → writer |
| Raw materials (data, drafts, references) | Researcher organizes → literature fill → writer |
| Topic only | Researcher deep literature review → outline → writer |
| Outline + partial materials | Researcher fills gaps → writer expands |

## Work Intensity Levels

| Level | Keyword | Behavior |
|-------|---------|----------|
| Low | "draft", "quick", "初稿" | Skip literature review if materials sufficient; 1 writer pass |
| Medium | (default) | Full workflow |
| High | "deep", "submission", "投稿" | 2 researcher passes; abstract + cover letter; PDF layout verification |

## Workdir Layout

```
{workdir}/
├── triage.md                        # input classification + style + output config
├── environment.md                   # tool audit
├── materials/                       # user-provided inputs
│   ├── data/
│   ├── figures/
│   ├── drafts/
│   ├── references_seed.bib
│   └── inventory.md
├── research/                        # researcher output
│   ├── literature_review.md
│   ├── references.bib
│   └── gap_analysis.md
├── references/                      # canonical reference registry (agentic_general)
│   ├── references.json              # aggregated
│   └── refs_researcher.json         # per-agent entries
├── draft/                           # writer output (SSoT layer)
│   ├── outline.md
│   ├── paper.md                     # THE source of truth
│   └── references.bib              # merged bibtex (academic style)
└── report/                          # reporter output (preview + exports)
    ├── <slug>_preview.html          # always generated
    ├── <slug>.pdf                   # always generated
    ├── <slug>.tex                   # academic style only
    ├── <slug>.docx                  # on demand
    └── DELIVERY.md
```

## Core Workflow

```
User Message
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 1  TRIAGE (leader)                                  │
│   input_type ∈ {A, B, C, D}                              │
│   style ∈ {report, academic}  (default: report)          │
│   output_config: template, lang, exports                  │
│   work_intensity ∈ {low, medium, high}                   │
│   → triage.md                                            │
└──────────────────────────────────────────────────────────┘
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 2  ENVIRONMENT AUDIT (researcher)                   │
│   Report style: none (agent-only)                        │
│   Academic style: Tectonic                               │
│   Optional: pandoc (DOCX only)                           │
│   → environment.md                                       │
└──────────────────────────────────────────────────────────┘
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 3  MATERIAL INVENTORY (researcher)                  │
│   Condition: input type A, B, or D                       │
│   → materials/inventory.md                               │
└──────────────────────────────────────────────────────────┘
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 4  LITERATURE REVIEW (researcher)                   │
│   Condition: input type B, C, or D                       │
│   → research/literature_review.md + references.bib       │
└──────────────────────────────────────────────────────────┘
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 5  OUTLINE (writer) → draft/outline.md              │
│   Leader reviews and approves                            │
└──────────────────────────────────────────────────────────┘
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 6  DRAFTING (writer) → draft/paper.md (SSoT)        │
│   Writer may call researcher for evidence gaps           │
└──────────────────────────────────────────────────────────┘
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 7  DRAFT REVIEW (leader)                            │
│   If issues → writer fixes → re-check                    │
└──────────────────────────────────────────────────────────┘
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 8  RENDERING (reporter)                             │
│   Report: MD → HTML template + CSS → preview.html        │
│           (UI exports HTML to PDF)                       │
│   Academic: MD → LaTeX template → Tectonic → PDF         │
│             + HTML preview                               │
└──────────────────────────────────────────────────────────┘
     │
     ▼
┌──────────────────────────────────────────────────────────┐
│ Step 9  USER REVIEW                                      │
│   Feedback → writer edits → re-render                    │
│   Approve → Step 10                                      │
└──────────────────────────────────────────────────────────┘
     │
     ▼
Step 10  DELIVERY → report/DELIVERY.md → User
```

## Agent Call Relationships

```
                              [User]
                                │
                                ▼
                          ┌───────────┐
                          │   leader  │
                          └───────────┘
                    ┌──────────┼──────────┬──────────┐
                    ▼          ▼          ▼          │
              ┌──────────┐ ┌──────┐ ┌──────────┐    │
              │researcher│ │writer│ │ reporter │    │
              └──────────┘ └──────┘ └──────────┘    │
                   ▲          │          │           │
                   └──────────┘          │           │
                   (evidence gaps)       │           │
                              (tool install)         │
```

| Caller | Can Call | Purpose |
|--------|----------|---------|
| **leader** | `researcher`, `writer`, `reporter` | Orchestrate end-to-end |
| **writer** | `researcher` | Fill evidence gaps, generate citations |
| **reporter** | `researcher` | Install missing tools |
| **researcher** | _(none)_ | Leaf node — provides services |

## paper.md Contract (writer's output specification)

Every `paper.md` uses standard Markdown with a lightweight frontmatter:

```yaml
---
title: "Document Title"
authors:
  - name: Author Name
    affiliation: Institution
date: 2026-04-29
lang: zh
---
```

**Report style** — numbered citations `[1]`, `[2]` with reference list at end:

```markdown
## 摘要

本报告旨在分析... 150-300 words.

## 1. 引言

AI4S 利用 AI 的强大数据处理能力 [1]。多项研究支持这一观点 [2, 3]。

![Figure 1: Overview of the pipeline](figures/overview.png)

## 2. 主要发现

**Table 1: Key metrics**

| Metric | Value |
|--------|------:|
| Accuracy | 95.2% |

## 参考文献

1. Author A. "Title." Journal, 2024.
2. Author B. "Title." Conference, 2023.
```

**Academic style** — `[@key]` citations with references.bib:

```markdown
## Abstract

This paper presents... [@smith2024].

## Introduction

@jones2023 demonstrated that...
```

## Key Design Principles

1. **Markdown is SSoT.** `paper.md` is the only authoritative document. All other formats are derived.
2. **Report style is default.** Most use cases are reports, not academic papers.
3. **Standard Markdown only.** No pandoc-specific extensions. Writer writes plain Markdown.
4. **Two rendering pipelines.** Report: HTML only (UI exports PDF). Academic: LaTeX+Tectonic → PDF.
5. **Templates via skills.** HTML templates, CSS themes, and LaTeX templates live in the paper_writing skill.
6. **Strict responsibility layering.** Writer writes, reporter converts, researcher investigates, leader coordinates.
7. **UI decoupled.** Agents only read/write `paper.md`; how the user edits it is the UI's concern.

## Artifact Matrix

| Artifact | Step | Produced by | Required? | Purpose |
|---|---|---|---|---|
| `triage.md` | 1 | leader | Always | Records triage decision |
| `environment.md` | 2 | researcher | Always | Tool availability |
| `materials/inventory.md` | 3 | researcher | If A/B/D | Material index |
| `research/literature_review.md` | 4 | researcher | If B/C/D | Literature synthesis |
| `research/references.bib` | 4 | researcher | If B/C/D | Auto bibtex |
| `references/refs_researcher.json` | 4 | researcher | If B/C/D | Canonical structured references |
| `references.json` | 4 | leader | If references used | Aggregated reference registry |
| `draft/outline.md` | 5 | writer | Always | Structure skeleton |
| **`draft/paper.md`** | 6 | writer | **Always (SSoT)** | **Single source of truth** |
| `draft/references.bib` | 6 | writer | Academic only | Merged bibtex |
| **`report/<slug>_preview.html`** | 8 | reporter | **Always** | **HTML preview** |
| **`report/<slug>.pdf`** | 8 | reporter | **Always** | **PDF export** |
| `report/<slug>.tex` | 8 | reporter | Academic only | LaTeX source |
| `report/<slug>.docx` | 8 | reporter | On demand | Word document |
| `report/DELIVERY.md` | 10 | leader | Always | Final delivery summary |

## Priority Chain

```
User's explicit instructions (style, topic, materials, outline)
  > triage.md decisions (style, input type, intensity, output_config)
    > researcher outputs (literature, bibtex, inventory)
      > writer output (paper.md)
        > reporter rendering (template, CSS/LaTeX, HTML/Tectonic)
```
