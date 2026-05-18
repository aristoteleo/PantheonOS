---
id: paper_writing_skills_index
name: Paper Writing Skills Index
description: |
  Skills for the Paper Write Team: rendering templates, scenario workflows,
  writing guidelines, themes, evidence gathering, and quality assurance.
  Supports 6 specialized scenarios, section-specific best practices, and
  comprehensive quality checks from proven academic writing frameworks.
---

# Paper Writing Skills

Comprehensive skills for the Paper Write Team, including rendering templates,
scenario-specific workflows, and writing quality guidelines.

## 1. Rendering Templates

| Template | File | Style | Use Case |
|----------|------|-------|----------|
| `report_standard` | [report_standard.md](./report_standard.md) | Professional report (Manus-style), HTML+CSS | Default for all reports |
| `report_academic` | [report_academic.md](./report_academic.md) | Formal academic paper, HTML+CSS | HTML preview for academic papers |
| `latex_cn` | [latex_cn.md](./latex_cn.md) | Chinese academic paper, LaTeX | Chinese academic PDF via Tectonic |
| `latex_en` | [latex_en.md](./latex_en.md) | English academic paper, LaTeX | English academic PDF via Tectonic |

## 2. Scenarios

Scenario-specific workflows for different use cases. Leader reads these during Step 1 (triage) when user input matches scenario keywords.

| Scenario | File | Trigger Keywords | Purpose |
|----------|------|------------------|---------|
| Paper Submission | [scenarios/paper_submission.md](./scenarios/paper_submission.md) | "投稿", "paper submission", "manuscript", "submit" | Full academic paper workflow with peer review simulation |
| Revision Response | [scenarios/revision_response.md](./scenarios/revision_response.md) | "审稿返修", "reviewer comments", "revision", "rebuttal" | Parse reviewer comments, generate point-by-point response |
| Group Report | [scenarios/group_report.md](./scenarios/group_report.md) | "组会", "lab meeting", "progress report", "weekly report" | Organize scattered progress into clear research narrative |
| Conference Talk | [scenarios/conference_talk.md](./scenarios/conference_talk.md) | "会议演讲", "conference talk", "presentation", "oral" | Transform paper into talk with storyline and slide structure |
| Workshop Share | [scenarios/workshop_share.md](./scenarios/workshop_share.md) | "workshop", "tutorial", "教学", "hands-on", "training" | Create tutorial with step-by-step instructions and code examples |
| Grant Proposal | [scenarios/grant_proposal.md](./scenarios/grant_proposal.md) | "基金申请", "grant proposal", "funding application", "NIH", "NSF" | Package research ideas into aims, plans, and impact statements |

**How to use scenarios**:
- Leader reads this index during Step 1 (triage)
- If user input matches scenario keywords, leader reads the scenario file
- Follow the workflow defined in the scenario file (may override default Steps 2-10)
- If no scenario matches, use default workflow

## 3. Writing Guidelines

Section-specific writing best practices from proven academic frameworks. Writer reads these before drafting each section.

| Section | File | Source | When to Use |
|---------|------|--------|-------------|
| Abstract | [writing/abstract.md](./writing/abstract.md) | Research-Paper-Writing-Skills | Writer drafting Abstract (3 proven templates) |
| Introduction | [writing/introduction.md](./writing/introduction.md) | Research-Paper-Writing-Skills | Writer drafting Introduction (Logic Map structure) |
| Methods | [writing/method.md](./writing/method.md) | Research-Paper-Writing-Skills | Writer drafting Methods (reproducibility checklist) |
| Results | [writing/results.md](./writing/results.md) | Research-Paper-Writing-Skills | Writer drafting Results (figure reference requirements) |
| Discussion | [writing/discussion.md](./writing/discussion.md) | Research-Paper-Writing-Skills | Writer drafting Discussion (4-part structure) |
| Claim-Evidence Check | [writing/claim_evidence_check.md](./writing/claim_evidence_check.md) | Research-Paper-Writing-Skills | Writer self-check after completing draft |
| Reviewer Rubric | [writing/reviewer_rubric.md](./writing/reviewer_rubric.md) | AI-Scientist (NeurIPS standard) | Leader simulating peer review (paper_submission scenario) |

**How to use writing guidelines**:
- Writer reads the corresponding skill file before writing each section
- Follow the templates and best practices provided
- Run claim-evidence check after completing draft (aim for ≥80% alignment)
- Leader can optionally run peer review simulation for paper_submission scenario

**Index for sub-skills**: [writing/SKILL.md](./writing/SKILL.md)

## 4. Themes

Visual styling and typography for rendered documents. Reporter applies these when generating HTML output.

| Theme | File | Style | Use Case |
|-------|------|-------|----------|
| Kami Academic | [themes/kami_academic.css](./themes/kami_academic.css) | Warm parchment palette, serif typography, print-optimized | Academic papers requiring professional, readable styling |

**How to use themes**:
- Leader specifies `theme_id` in triage.md (e.g., `theme_id: kami_academic`)
- Reporter reads the theme file and applies CSS to HTML output
- Themes are optimized for both screen viewing and print-to-PDF

## 5. Evidence Skills

Tools for gathering and validating evidence to support claims.

| Skill | File | Purpose | Source |
|-------|------|---------|--------|
| Paper Fetch | [evidence/paper_fetch.md](./evidence/paper_fetch.md) | Fetch papers from DOI/arXiv/PMID automatically | Future-House/paper-qa |
| Citation Grounding | [evidence/citation_grounding.md](./evidence/citation_grounding.md) | Verify citations support claims (strong/partial/weak) | nature-citation |

**How to use evidence skills**:
- Writer calls these when drafting and needs to fetch papers or verify citations
- Researcher uses paper_fetch during literature review
- Writer uses citation_grounding during self-check after drafting

**Index for sub-skills**: [evidence/SKILL.md](./evidence/SKILL.md)

## 6. Quality Skills

Quality assurance checks for manuscript completeness and compliance.

| Skill | File | Purpose | Source |
|-------|------|---------|--------|
| Reporting Guideline Check | [quality/reporting_guideline_check.md](./quality/reporting_guideline_check.md) | Verify CONSORT/STROBE/PRISMA compliance | EQUATOR Network |
| Reproducibility Check | [quality/reproducibility_check.md](./quality/reproducibility_check.md) | Ensure methods are reproducible | nature-polishing |
| Format Lint | [quality/format_lint.md](./quality/format_lint.md) | Check formatting (sections, numbering, references) | General best practices |
| Manuscript Coverage Check | [quality/manuscript_coverage_check.md](./quality/manuscript_coverage_check.md) | Verify all required sections present | Review guidelines |

**How to use quality skills**:
- Writer runs these after completing draft (self-check)
- Leader runs these during Step 7 (draft review) as quality gates
- Aim for ≥80% compliance; flag critical issues for revision

**Index for sub-skills**: [quality/SKILL.md](./quality/SKILL.md)

## 7. How to Use Templates

### Report style (default)

1. Reporter reads this skill index
2. Reporter reads `report_standard.md` — contains both the HTML template and CSS
3. Reporter reads paper.md, parses frontmatter, converts Markdown body to HTML
4. Reporter fills the HTML template with metadata + CSS + content
5. Reporter writes the final HTML file
6. The UI exports the HTML to PDF on user request (browser print-to-PDF using the `@media print` CSS rules)

### Academic style

1. Reporter reads this skill index
2. Reporter reads the LaTeX template (`latex_cn.md` or `latex_en.md` based on lang)
3. Reporter reads paper.md, parses frontmatter, converts Markdown body to LaTeX
4. Reporter fills the LaTeX template with metadata + content, writes .tex file
5. Reporter runs Tectonic to compile PDF
6. Reporter also reads `report_academic.md` to generate an HTML preview

### Custom templates

Users can add their own `.md` template files to this directory following the
same format (frontmatter + HTML/CSS or LaTeX in code blocks).
