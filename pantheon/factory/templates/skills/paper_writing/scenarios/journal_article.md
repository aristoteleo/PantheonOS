---
id: journal_article_scenario
name: Journal Article Scenario
description: Journal article route for SCI, Nature-style, or target-journal manuscripts.
tags: [paper_writing, journal, article]
---

# Journal Article Scenario

Use when the user names a journal, article type, or asks for SCI/high-impact
journal writing.

| Field | Contract |
|---|---|
| Trigger | journal article, SCI, Nature, Cell, research article, short communication |
| Inputs | target journal, article type, data/code availability, figures, SI needs |
| Read next | `workflow/literature_review.md`, `workflow/figure_storyline.md`, `evidence/data_availability.md` |
| Outputs | journal article draft, Data/Code Availability, figure/table captions, editable HTML |
| Gates | data availability, citation check, reviewer rubric, reporting guideline if applicable |
| Forbidden | inventing accession IDs, repository URLs, DOI, ethical approval, or SI files |

Required sections unless the target journal says otherwise:
Title, Abstract, Keywords, Introduction, Results, Discussion, Methods, Data
Availability, Code Availability, Acknowledgements, References, Supplementary
Information note.

Sources: nature-data/SKILL.md, nature-figure/SKILL.md, scientific-writing/SKILL.md.
