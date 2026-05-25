---
id: paper_writing_citation_check
name: Citation Check
description: Audit references, citation metadata, and claim support before finalizing academic writing.
tags: [paper_writing, citation, quality]
---

# Citation Check

Use when a draft contains citations or references.

## Output

| Citation | Draft claim | Metadata status | Support status | Issue | Action |
|---|---|---|---|---|---|

Rules:

- Do not invent or silently repair DOI, PMID, arXiv ID, year, volume, issue, or
  pages.
- Verify citation support using `evidence/citation_grounding.md`.
- Flag duplicate references and inconsistent citation keys.
- Treat uncited references and unsupported citation-backed claims as issues.

Sources: nature-citation/SKILL.md, PaperQA prompts.py, K-Dense citation-management/SKILL.md.
