---
id: paper_writing_skills_index
name: Paper Writing Skills Index
description: |
  Routing and workflow skill family for paper-writing tasks. Covers manuscript
  drafting, journal and conference papers, grant proposals, lab reports,
  group-meeting reports, talks, workshop notes, reviewer rebuttals, academic
  HTML/PDF/LaTeX output with editable-block contracts, citation grounding,
  evidence checking, and pre-submission quality gates.
tags: [paper_writing, manuscript, grant, rebuttal, citation, html, latex]
---

# Paper Writing Skills

## Mode

Two modes control which files and gates are loaded. Set `mode` in `triage.md` during Phase 0.

| Mode | Default for | Trigger words (override default) |
|---|---|---|
| `report` | all scenarios | *(default — no trigger needed)* |
| `submission` | journal_article, conference_paper, paper_submission, grant_proposal, revision_response | 投稿、submit、journal、conference、SCI、Nature、Cell、NeurIPS、ICLR、grant、基金、申请书、rebuttal、revision、peer review、深度调研、systematic review、meta-analysis |

If any submission trigger word appears in the user request, set `mode: submission` regardless of scenario. Otherwise default to `report`.

| What changes in `submission` mode |
|---|
| Load [paper_fetch.md](./paper_fetch.md) during Phase 1 |
| Load [writing/claim_evidence_check.md](./writing/claim_evidence_check.md) during Phase 4 |
| Load [writing/reviewer_rubric.md](./writing/reviewer_rubric.md) during Phase 4 |
| Load [reporting_guideline_check.md](./reporting_guideline_check.md) during Phase 4 (clinical/observational/systematic review only) |

## Routing + Sequential Pipeline

| Phase | Entry criteria | Actions | Exit criteria |
|---|---|---|---|
| 0. Triage | a user request and any UI scenario labels | Read [workflow/SKILL.md](./workflow/SKILL.md) ("Triage" section). Choose `scenario_id`, `format_id`, `theme_id`, `mode`, language, audience, outputs, constraints. | `{workdir}/triage.md` exists or is updated |
| 1. Materials and evidence | triage is known | Read the chosen scenario file under `scenarios/`. Inventory materials. `submission` only: fetch/search papers via [paper_fetch.md](./paper_fetch.md), build evidence registry (see Evidence Layer below). | `{workdir}/materials/inventory.md`; `submission`: also `claim_evidence_map.md` |
| 2. Outline + claim boundary | materials are known | Read [workflow/SKILL.md](./workflow/SKILL.md) ("Paper Outline" + "Figure Storyline" sections). `submission` + novelty claim: also read Knowledge Lineage Audit in [scenarios/grant_proposal.md](./scenarios/grant_proposal.md). | outline |
| 3. Section drafting | outline exists | Read [writing/SKILL.md](./writing/SKILL.md). Draft Markdown. | `{workdir}/draft/paper.md` |
| 4. Quality gates | draft exists | Run gates per mode (see Quality Gates below). | quality reports under `{workdir}/quality/` |
| 5. Editable output | draft + quality notes exist | Apply the chosen rendering template + theme per the Editable HTML Contract section below. | `{workdir}/report/<slug>_preview.html` |

## Scenario Routing

| User intent | `scenario_id` | Required scenario file | Default mode |
|---|---|---|---|
| manuscript, paper submission, write a paper | `paper_submission` | [scenarios/paper_submission.md](./scenarios/paper_submission.md) | submission |
| journal article, SCI, Nature-style paper | `journal_article` | [scenarios/journal_article.md](./scenarios/journal_article.md) | submission |
| conference paper, workshop paper | `conference_paper` | [scenarios/conference_paper.md](./scenarios/conference_paper.md) | submission |
| grant, proposal, funding application | `grant_proposal` | [scenarios/grant_proposal.md](./scenarios/grant_proposal.md) | submission |
| lab report, experiment report | `lab_report` | [scenarios/lab_report.md](./scenarios/lab_report.md) | report |
| group meeting, weekly report | `group_report` | [scenarios/group_report.md](./scenarios/group_report.md) | report |
| conference talk, workshop sharing | `conference_talk` / `workshop_share` | [scenarios/conference_talk.md](./scenarios/conference_talk.md) / [scenarios/workshop_share.md](./scenarios/workshop_share.md) | report |
| reviewer comments, rebuttal, revision response | `revision_response` | [scenarios/revision_response.md](./scenarios/revision_response.md) | submission |

## Family Index

| Layer | File | Contents |
|---|---|---|
| Workflow phases | [workflow/SKILL.md](./workflow/SKILL.md) | 9 phases inlined: triage, material inventory, research question, literature review, paper outline, data analysis summary, figure storyline, reader testing, finalize packet |
| Section writing | [writing/SKILL.md](./writing/SKILL.md) | abstract, introduction, method, results, discussion, claim_evidence_check (citation grounding inlined), reviewer_rubric (+ reviewer_rubric_example), response_letter |
| Evidence layer | inlined below | paper_fetch, evidence registry, evidence summary, context-bound answering |
| Quality gates | inlined below | manuscript coverage, format lint, reproducibility; reporting_guideline_check (clinical) at [reporting_guideline_check.md](./reporting_guideline_check.md) |
| Editable HTML contract | inlined below | block shape, attributes, validation pass criteria |
| Themes | [themes/kami_academic.md](./themes/kami_academic.md) + [themes/kami_academic.css](./themes/kami_academic.css) | warm parchment academic theme |

## Scenario Format Index

HTML output for any format must satisfy the Editable HTML Contract below.

| `format_id` | Scenario file | Required structure | Special constraints |
|---|---|---|---|
| `journal_article` | [scenarios/journal_article.md](./scenarios/journal_article.md) | Title, Abstract, Keywords, Introduction, Results, Discussion, Methods, Data Availability, Code Availability, Acknowledgements, References, SI note | figure/table numbering, data/code statements, guideline checks when applicable |
| `conference_paper` | [scenarios/conference_paper.md](./scenarios/conference_paper.md) | Title, Abstract, Introduction, Related Work, Method, Experiments, Results, Discussion, Conclusion, References, Appendix | page limit, baseline and ablation completeness, optional LaTeX |
| `grant_application` | [scenarios/grant_proposal.md](./scenarios/grant_proposal.md) | title, abstract, background, gap, aims, research content, key scientific questions, technical route, innovation, feasibility, plan, expected outcomes, team, budget, references | form-like blocks, word limits, aim-route consistency |
| `lab_report` | [scenarios/lab_report.md](./scenarios/lab_report.md) | experiment name, date, operator, purpose, materials, steps, raw observations, data processing, results, abnormal events, conclusion, next step | raw observation separated from interpretation |
| `group_report` | [scenarios/group_report.md](./scenarios/group_report.md) | progress, core question, evidence, current conclusion, blockers, discussion questions, next plan | scan-friendly, short sections |
| `conference_talk` | [scenarios/conference_talk.md](./scenarios/conference_talk.md) | opening, audience context, problem, key idea, evidence sequence, takeaway, Q&A, speaker notes | every figure has a spoken takeaway |
| `workshop_share` | [scenarios/workshop_share.md](./scenarios/workshop_share.md) | prerequisites, materials, steps, checkpoints, expected outputs, troubleshooting, Q&A | reproducible from a clean environment |
| `revision_response` | [scenarios/revision_response.md](./scenarios/revision_response.md) | Reviewer X Comment Y, Comment, Response, Changes Made, Status | preserve every comment |

## Required Outputs

- `{workdir}/triage.md`
- `{workdir}/draft/paper.md` (Markdown source of truth)
- `{workdir}/report/<slug>_preview.html`
- `{workdir}/quality/claim_evidence_report.md`
- `{workdir}/quality/reviewer_report.md` — submissions, grants, rebuttals, high-risk tasks only

## Rendering Templates

| Template | File | Output |
|---|---|---|
| `report_standard` | [report_standard.md](./report_standard.md) | Professional report (Manus-style), HTML+CSS |
| `report_academic` | [report_academic.md](./report_academic.md) | Formal academic paper, HTML+CSS |
| `latex_cn` | [latex_cn.md](./latex_cn.md) | Chinese academic paper, LaTeX → PDF |
| `latex_en` | [latex_en.md](./latex_en.md) | English academic paper, LaTeX → PDF |

### Standard rendering flow

1. Read [report_standard.md](./report_standard.md) for the HTML+CSS template.
2. Convert `draft/paper.md` to HTML, wrapping major sections per the Editable HTML Contract below.
3. Apply the selected theme. Write `{workdir}/report/<slug>_preview.html`.

### Academic rendering flow (LaTeX → PDF)

1. Read the LaTeX template (`latex_cn.md` or `latex_en.md` based on language).
2. Convert `draft/paper.md` to LaTeX, fill the template, write `<slug>.tex`.
3. Read [COMPILE.md](./COMPILE.md) — probe available engines before compiling.
4. Also produce an HTML preview via [report_academic.md](./report_academic.md).

---

## Evidence Layer

Evidence skills decide where facts come from. Claims may not be invented or
grounded in model memory; they must trace back to a registered piece of evidence.

| Need | Where |
|---|---|
| Search and fetch open-access papers by DOI / arXiv / PMID | [paper_fetch.md](./paper_fetch.md) |
| Register claims and supporting evidence | Evidence Registry below |
| Ground citations to specific text segments, rerank, attribute sentences | [writing/claim_evidence_check.md](./writing/claim_evidence_check.md) ("Citation Grounding" section) |
| Write data and code availability statements | [scenarios/journal_article.md](./scenarios/journal_article.md) ("Data and Code Availability" section) |

**Default pipeline**: `paper_fetch → evidence_summary → evidence_registry → draft → citation grounding`

### Evidence Registry — `claim_evidence_map.md`

| Claim ID | Claim | Evidence type | Source | Strength | Risk | Action |
|---|---|---|---|---|---|---|
| C1 | … | citation/figure/table/data/user_material/missing | S001 | strong | low | keep |

Evidence types: `citation`, `figure`, `table`, `experimental_data`, `statistical_result`, `user_material`, `missing`. Claims with `missing` evidence cannot appear as firm conclusions. If support is partial, narrow the wording.

### Evidence Summary — output table

| Evidence ID | Source | Passage/page | Query answered | Summary | Score | Claim IDs |
|---|---|---|---|---|---|---|

Preserve locator detail for later attribution. Separate the source's statement from the agent's interpretation. If evidence does not answer the query, say so; do not force fit.

### Context-Bound Answering

Answer only from provided context. Cite context keys, evidence IDs, pages, or material IDs. If context is insufficient, write `I cannot answer from the provided context` and list what is missing. Do not fall back to model memory.

---

## Quality Gates

Gates produce reports and risk flags — guardrails, not gatekeepers. Flag issues and suggest fixes; do not silently rewrite a draft to hide problems. Aim for ≥80% compliance; prioritize critical issues (missing data, unsupported claims, fabricated citations) over cosmetic formatting.

| Gate | Where | `report` | `submission` |
|---|---|---|---|
| Manuscript coverage | inlined below | ✅ | ✅ |
| Format lint | inlined below | ✅ | ✅ |
| Reproducibility | inlined below | ✅ | ✅ |
| HTML editability | Editable HTML Contract below | ✅ | ✅ |
| Claim / evidence + citation grounding | [writing/claim_evidence_check.md](./writing/claim_evidence_check.md) | ❌ | ✅ |
| Reviewer simulation (NeurIPS-style) | [writing/reviewer_rubric.md](./writing/reviewer_rubric.md) | ❌ | ✅ |
| Reporting guideline (CONSORT/STROBE/PRISMA) | [reporting_guideline_check.md](./reporting_guideline_check.md) | ❌ | ✅ clinical only |
| Response consistency | [scenarios/revision_response.md](./scenarios/revision_response.md) | ❌ | ✅ revision_response only |

**Run order**: manuscript coverage → format lint → reproducibility → claim/evidence → reporting guideline → reviewer simulation → HTML editability → response consistency.

### Manuscript Coverage Check

Core sections (required): Title, Abstract, Introduction, Methods, Results, Discussion, References.
Optional (journal-dependent): Keywords, Conclusion, Acknowledgements, Author Contributions, Competing Interests, Data/Code Availability, Supplementary Materials.

| Section | Must contain |
|---|---|
| Abstract | background → gap → method → results → conclusion |
| Introduction | background → related work → gap → contribution |
| Methods | design, materials, procedure, parameters, analysis |
| Results | main findings, figures, tables, statistics, subsections by question |
| Discussion | interpretation, comparison, limitations, future, take-home |

Output: `## Manuscript Coverage Report` with completeness %, present/complete list, missing/incomplete list, recommendation. Gate: ≥90% excellent / 80-89% good / <80% major sections missing.

### Format Lint

Check: section structure complete; figure/table numbering sequential with captions; references numbered, no gaps, consistent format; word/page limits met; supplementary files referenced and numbered.

Output: `## Format Lint Report` with compliance %, correct categories, issues (severity + location + action), word count vs limit, recommendation.

Common limits (always verify against journal guidelines):

| Journal | Abstract | Main text | Figures | Refs |
|---|---|---|---|---|
| Nature/Science | 150-200 | 3-4k words | 4-6 | 30-50 |
| Cell | 150 | 5k | 7 | 80 |
| NeurIPS | 250 | 8 pages | — | — |
| PLOS ONE | 300 | none | none | none |

Gate: ≥90% excellent / 80-89% fix flagged / <80% major problems.

### Reproducibility Check

Audit categories (each must be present and concrete): software & tools (names, versions, OS, libraries); parameters & settings (hyperparameters, seeds, hardware); data (source, version, splits, availability); code (repo URL, license, dependencies, reproducibility script).

Output: `## Reproducibility Check Report` with compliance %, sufficient detail list, missing/vague list (current text + fix), availability statement templates, recommendation. Gate: ≥90% excellent / 80-89% good / 70-79% fair / <70% major revision.

---

## Editable HTML Contract

All HTML outputs must be standalone working files, not app-only previews.

```html
<section class="editable-block" contenteditable="true"
  data-block-id="abstract-001" data-section="abstract"
  data-source="draft/paper.md" data-format-role="summary">
  <h2>Abstract</h2><p>…</p>
</section>
```

Required: single file with embedded CSS; semantic tags (`section`, `figure`, `figcaption`, `table`, `caption`); `@page` or `@media print`; no frontend runtime required; no absolute positioning for body layout.

| Attribute | Meaning |
|---|---|
| `data-block-id` | stable local block identifier |
| `data-section` | manuscript/report section |
| `data-source` | source file, usually `draft/paper.md` |
| `data-format-role` | `summary`, `claim`, `evidence`, `method`, `response` |

**Validation** (run after every HTML generation): file opens standalone; CSS embedded; main text is semantic HTML not an image; major blocks have `contenteditable="true"` and all four `data-*` attributes; print CSS present; figures/tables use semantic tags. If any criterion fails, fix the renderer or source Markdown — do not relax the contract.

---

## Non-Negotiable Rules

- Do not write unsupported claims. Mark missing evidence and downgrade or remove the claim.
- Do not invent citations, DOIs, accession IDs, page numbers, data repositories, reviewer changes, or experimental results.
- Do not let a search result become evidence until it is summarized, attributed, and bound to a specific claim in the Evidence Registry above.
- Do not use Sci-Hub or any access-control bypass. Open PDF fetching may use only legal OA routes in [paper_fetch.md](./paper_fetch.md).
- Do not output screenshot-like reports. Main text must remain semantic and editable HTML per the Editable HTML Contract above.
- Keep each `SKILL.md` below 500 lines.

## Sources

- Anthropic skill design — <https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md>
- Anthropic doc coauthoring — <https://github.com/anthropics/skills/blob/main/skills/doc-coauthoring/SKILL.md>
- Trail of Bits workflow skill design — <https://github.com/trailofbits/skills/blob/main/plugins/workflow-skill-design/skills/designing-workflow-skills/SKILL.md>
- PaperQA evidence RAG — <https://github.com/Future-House/paper-qa>
- OpenScholar attribution — <https://github.com/akariasai/openscholar>
- Nature-style response/figure/citation/data skills — <https://github.com/Yuan1z0825/nature-skills>
- DeepScientist outline/review/rebuttal/writer skills — <https://github.com/ResearAI/DeepScientist>
- Research-Paper-Writing-Skills (Master-cai, MIT) — abstract / introduction / method / results / discussion section guides
- AI-Scientist (SakanaAI, MIT) — NeurIPS-style reviewer rubric
- EQUATOR Network — reporting guideline checklists
- tw93/Kami (MIT) — academic theme
