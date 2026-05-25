---
id: paper_writing_rerank_and_attribution
name: Rerank And Attribution
description: Rerank candidate evidence and attribute draft sentences back to exact sources.
tags: [paper_writing, attribution, rerank]
---

# Rerank And Attribution

Use when multiple contexts compete, when an answer lacks citations, or before
final citation checks.

## Output

| Draft sentence | Source ID | Passage locator | Attribution strength | Risk |
|---|---|---|---|---|

## Rules

- Sentence-level scientific claims need source-level attribution.
- If the best source only supports part of the sentence, split or narrow it.
- Do not keep unattributed conclusion sentences in high-stakes drafts.

Sources: OpenScholar open_scholar.py, PaperQA tools.py.
