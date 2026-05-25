---
id: paper_writing_paper_search
name: Paper Search
description: Search and rank candidate papers for claims, topics, and related work before evidence grounding.
tags: [paper_writing, literature_search, evidence]
---

# Paper Search

Use when the draft lacks literature, citations, or related-work positioning.

## Output

| Candidate ID | Query | Title | Authors/year | Source | Why candidate | Next action |
|---|---|---|---|---|---|---|

## Rules

- A search result is a candidate, not evidence.
- Record query, source, and reason for inclusion.
- Route promising candidates to `evidence_summary.md` or `citation_grounding.md`.
- Prefer primary sources for factual claims and recent/foundational balance for
  related work.

Sources: PaperQA tools.py, OpenScholar README.md.
