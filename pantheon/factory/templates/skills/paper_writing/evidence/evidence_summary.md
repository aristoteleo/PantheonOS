---
id: paper_writing_evidence_summary
name: Evidence Summary
description: Summarize paper chunks and retrieved contexts before using them in writing.
tags: [paper_writing, evidence_summary, rag]
---

# Evidence Summary

Use after retrieval and before writing from papers or notes.

## Output

| Evidence ID | Source | Passage/page | Query answered | Summary | Score | Claim IDs |
|---|---|---|---|---|---|---|

## Rules

- Preserve enough locator detail for later attribution.
- Separate the source's statement from the agent's interpretation.
- If evidence does not answer the query, say so; do not force fit.

Sources: Future-House/paper-qa README.md, paperqa/prompts.py.
