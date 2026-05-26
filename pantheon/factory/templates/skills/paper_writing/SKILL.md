---
id: paper_writing_skills_index
name: Paper Writing Skills Index
description: |
  Routing and workflow skill family for paper-writing tasks. Covers manuscript
  drafting, journal and conference papers, grant proposals, lab reports,
  group-meeting reports, talks, workshop notes, reviewer rebuttals, academic
  HTML/PDF/LaTeX output with editable-block contracts, citation grounding,
  evidence checking, and pre-submission quality gates. Agent-neutral: any
  orchestrator or single agent can call into this skill family.
tags: [paper_writing, manuscript, grant, rebuttal, citation, html, latex]
---

# Paper Writing Skills

Comprehensive skill family for paper-writing tasks. Files in this skill are
agent-neutral: they describe the work to do, the inputs and outputs, and the
quality bars; they do not assume a particular orchestrator, sub-agent layout,
or step numbering.

The family has four layers, all loaded from this index:

- **Routing + workflow** decides what kind of task this is and which phases to run.
- **Writing** turns evidence and outline into prose, section by section.
- **Evidence + quality** keep claims grounded and outputs reviewable.
- **Formats + themes + rendering templates** produce the final editable HTML, LaTeX, and PDF.

## Routing + Sequential Pipeline

Run these phases in order. Load only the files referenced for the current
scenario; the workflow files are short contracts, not narratives.

| Phase | Entry criteria | Actions | Exit criteria |
|---|---|---|---|
| 0. Triage | a user request and any UI scenario labels | Read [workflow/SKILL.md](./workflow/SKILL.md) ("Triage" section). Choose `scenario_id`, `format_id`, `theme_id`, language, audience, outputs, constraints. | `{workdir}/triage.md` exists or is updated |
| 1. Materials and evidence | triage is known | Read the chosen scenario file under `scenarios/`. Inventory materials, fetch/search papers only when needed, build the evidence registry. See [workflow/SKILL.md](./workflow/SKILL.md) ("Material Inventory" section) and [evidence/SKILL.md](./evidence/SKILL.md). | `{workdir}/materials/inventory.md` and/or `claim_evidence_map.md` |
| 2. Outline + claim boundary | evidence and materials are known | Read [workflow/SKILL.md](./workflow/SKILL.md) ("Paper Outline" + "Figure Storyline" sections), or read the Knowledge Lineage Audit in [scenarios/grant_proposal.md](./scenarios/grant_proposal.md) when the task asserts novelty. | manuscript-view + evidence-view outline |
| 3. Section drafting | outline + evidence boundary exist | Read [writing/SKILL.md](./writing/SKILL.md). Draft Markdown only within evidence bounds. | `{workdir}/draft/paper.md` |
| 4. Quality gates | draft exists | Read [quality/SKILL.md](./quality/SKILL.md). Run scenario-specific gates. | quality reports under `{workdir}/quality/` |
| 5. Editable output | draft + quality notes exist | Read [formats/html_editable_contract.md](./formats/html_editable_contract.md). Apply the chosen rendering template + theme. | `{workdir}/report/<slug>_preview.html` and the final resume packet |

## Scenario Routing

Pick the scenario by user intent. See [scenarios/SKILL.md](./scenarios/SKILL.md)
for the full router.

| User intent | `scenario_id` | Required scenario file | Format / theme | Quality gates |
|---|---|---|---|---|
| manuscript, paper submission, write a paper | `paper_submission` | [scenarios/paper_submission.md](./scenarios/paper_submission.md) | `journal_article` or `conference_paper`, `editable_article` | claim/evidence, reviewer rubric, format lint, manuscript coverage |
| journal article, SCI, Nature-style paper | `journal_article` | [scenarios/journal_article.md](./scenarios/journal_article.md) | `journal_article` | data availability, citation grounding, reviewer rubric |
| conference paper, workshop paper, double-column | `conference_paper` | [scenarios/conference_paper.md](./scenarios/conference_paper.md) | `conference_paper` | page limit, baseline / evaluation, reviewer rubric |
| grant, proposal, funding application | `grant_proposal` | [scenarios/grant_proposal.md](./scenarios/grant_proposal.md) | `grant_application` | gap-aim-route feasibility, word limits, claim/evidence |
| lab report, experiment report | `lab_report` | [scenarios/lab_report.md](./scenarios/lab_report.md) | `lab_report` | reproducibility, raw observation / interpretation separation |
| group meeting, weekly report | `group_report` | [scenarios/group_report.md](./scenarios/group_report.md) | `group_report` | evidence summary, discussion questions |
| conference talk, workshop sharing | `conference_talk` / `workshop_share` | [scenarios/conference_talk.md](./scenarios/conference_talk.md) / [scenarios/workshop_share.md](./scenarios/workshop_share.md) | `conference_talk` / `workshop_share` | storyline, speaker notes, reproducible steps |
| reviewer comments, rebuttal, revision response | `revision_response` | [scenarios/revision_response.md](./scenarios/revision_response.md) | `revision_response` | every-comment response, manuscript / response consistency |

## Family Index

| Layer | Index | Files |
|---|---|---|
| Workflow phases | [workflow/SKILL.md](./workflow/SKILL.md) | All 9 phases inlined as one file: triage, material inventory, research question, literature review, paper outline, data analysis summary, figure storyline, reader testing, finalize packet |
| Section writing | [writing/SKILL.md](./writing/SKILL.md) | abstract, introduction, method, results, discussion, claim_evidence_check (with citation grounding inlined), reviewer_rubric (+ reviewer_rubric_example), response_letter |
| Evidence layer | [evidence/SKILL.md](./evidence/SKILL.md) | paper_fetch (search + retrieve); evidence registry, summary, and context-bound answering rules inlined in evidence/SKILL.md; citation grounding lives in writing/claim_evidence_check.md; data availability lives in scenarios/journal_article.md |
| Quality gates | [quality/SKILL.md](./quality/SKILL.md) | manuscript coverage, format lint, reproducibility (all inlined in quality/SKILL.md); reporting_guideline_check (clinical); claim_evidence_check + reviewer_rubric live in writing/; HTML editability validation lives in formats/html_editable_contract.md; response consistency check lives in scenarios/revision_response.md |
| Output formats | [formats/html_editable_contract.md](./formats/html_editable_contract.md) | editable HTML contract (per-format section structure lives in each scenario file; cross-format index below) |
| Themes | [themes/kami_academic.md](./themes/kami_academic.md) (contract) + [themes/kami_academic.css](./themes/kami_academic.css) (stylesheet) | warm parchment academic theme; only theme bundled today — add new themes as `<name>.md` + `<name>.css` pairs |

## Scenario Format Index

Quick cross-format comparison. Authoritative section structure and constraints
live in each scenario file (linked); HTML output for any of these formats must
also satisfy [formats/html_editable_contract.md](./formats/html_editable_contract.md).

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

Default artifacts for full paper-writing tasks (narrow when the user narrows
the request):

- `{workdir}/triage.md`
- `{workdir}/draft/paper.md` (Markdown source of truth)
- `{workdir}/report/<slug>_preview.html`
- `{workdir}/quality/claim_evidence_report.md`
- `{workdir}/quality/reviewer_report.md` for submissions, grants, rebuttals, or
  high-risk tasks

`{workdir}` is a placeholder for the working directory the calling system
provides. The skill family does not own that path; it only writes inside it.

## Rendering Templates

| Template | File | Output |
|---|---|---|
| `report_standard` | [report_standard.md](./report_standard.md) | Professional report (Manus-style), HTML+CSS — default for non-academic reports |
| `report_academic` | [report_academic.md](./report_academic.md) | Formal academic paper, HTML+CSS — default HTML preview for papers |
| `latex_cn` | [latex_cn.md](./latex_cn.md) | Chinese academic paper, LaTeX → PDF via Tectonic |
| `latex_en` | [latex_en.md](./latex_en.md) | English academic paper, LaTeX → PDF via Tectonic |

Both `report_standard` and `report_academic` honor
[formats/html_editable_contract.md](./formats/html_editable_contract.md): major
sections are wrapped as `<section class="editable-block" contenteditable="true"
data-block-id=… data-section=… data-source="draft/paper.md" data-format-role=…>`.

### Standard rendering flow (HTML report)

1. Load this skill index and the chosen scenario file.
2. Read [report_standard.md](./report_standard.md) for the HTML+CSS template.
3. Convert `draft/paper.md` to HTML, wrapping major sections per the editable
   block contract.
4. Apply the selected theme (`themes/<theme>.css` or the contract in `.md`).
5. Write `{workdir}/report/<slug>_preview.html`. The host UI handles
   print-to-PDF via the embedded `@media print` rules.

### Academic rendering flow (LaTeX → PDF)

1. Load this skill index and the chosen scenario file.
2. Read the LaTeX template (`latex_cn.md` or `latex_en.md` based on language).
3. Convert `draft/paper.md` to LaTeX, fill the template, write `<slug>.tex`.
4. Read [COMPILE.md](./COMPILE.md) for engine probing and compile commands —
   do not hard-code Tectonic; probe what's available first.
5. Compile to produce `<slug>.pdf`.
6. Also read [report_academic.md](./report_academic.md) and produce an HTML
   preview for in-app viewing; the editable-block contract still applies.

### Custom templates

Drop additional `.md` templates into this directory following the same shape
(frontmatter + HTML/CSS or LaTeX in fenced code blocks). Add an entry to the
table above and reference [formats/html_editable_contract.md](./formats/html_editable_contract.md)
if the template emits HTML.

## Non-Negotiable Rules

- Do not write unsupported claims. Mark missing evidence and downgrade or
  remove the claim.
- Do not invent citations, DOIs, accession IDs, page numbers, data
  repositories, reviewer changes, or experimental results.
- Do not let a search result become evidence until it is summarized,
  attributed, and bound to a specific claim — see the Evidence Registry
  section in [evidence/SKILL.md](./evidence/SKILL.md).
- Do not use Sci-Hub or any access-control bypass. Open PDF fetching may use
  only legal OA routes described in
  [evidence/paper_fetch.md](./evidence/paper_fetch.md).
- Do not output screenshot-like reports. Main text must remain semantic and
  editable HTML per
  [formats/html_editable_contract.md](./formats/html_editable_contract.md).
- Keep each `SKILL.md` below 500 lines. Put long templates, guidelines, and
  examples in adjacent one-hop files.

## Maintaining this skill

When changing this skill family, audit the change against this checklist:

- Root `description` includes real trigger terms users would actually say.
- Root skill uses Routing + Sequential Pipeline — not narrative prose.
- Workflow files specify entry criteria, actions, and exit criteria.
- References from any file are at most one hop from `SKILL.md`.
- Each `SKILL.md` stays below 500 lines.
- Long templates and worked examples live in adjacent files, not in `SKILL.md`.
- At least three pressure scenarios were considered: a typical happy path, a
  scenario with missing evidence, and a scenario with reviewer pushback.

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
