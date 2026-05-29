---
id: journal_article_scenario
name: Journal Article Scenario
description: Journal article route for SCI, Nature-style, or target-journal manuscripts; covers data/code availability statements.
tags: [paper_writing, journal, article, data_availability]
---

# Journal Article Scenario

Use when the user names a journal, article type, or asks for SCI/high-impact
journal writing.

| Field | Contract |
|---|---|
| Trigger | journal article, SCI, Nature, Cell, research article, short communication |
| Inputs | target journal, article type, data/code availability, figures, SI needs |
| Read next | [../workflow/SKILL.md](../workflow/SKILL.md) (literature review + figure storyline phases), [../SKILL.md](../SKILL.md) |
| Outputs | journal article draft, Data/Code Availability statements (see below), figure/table captions, editable HTML |
| Gates | data availability, citation check, reviewer rubric, reporting guideline if applicable |
| Forbidden | inventing accession IDs, repository URLs, DOI, ethical approval, or SI files |

Required sections unless the target journal says otherwise:
Title, Abstract, Keywords, Introduction, Results, Discussion, Methods, Data
Availability, Code Availability, Acknowledgements, References, Supplementary
Information note.

## Data and Code Availability

Read this section during Methods drafting and again at finalize.

### Output

| Asset | Type | Repository/access | Identifier | Restrictions | Status |
|---|---|---|---|---|---|

### Rules

- Do not invent repository links, accession IDs, DOIs, or licenses.
- If data/code is unavailable, state why and what the reader can request.
- Keep generated statement consistent with methods, ethics, and supplementary material sections.

### Statement templates

- **Public data**: "<Dataset> publicly available at <URL>. Preprocessed data and trained models at <repo>."
- **Restricted data**: "Patient data cannot be shared due to privacy. Aggregated statistics and analysis code at <repo>."
- **New data**: "All data generated in this study available at Zenodo (DOI: ...) under <license>."
- **Open code**: "All code at <URL> under <license>. Installation in README."
- **Proprietary code**: "Code available for research purposes upon reasonable request to corresponding author."

Sources: nature-data/SKILL.md, nature-figure/SKILL.md, scientific-writing/SKILL.md, K-Dense citation-management.
