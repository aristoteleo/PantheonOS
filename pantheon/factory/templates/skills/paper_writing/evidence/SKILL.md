---
id: paper_writing_evidence
name: Paper Writing Evidence Layer
description: |
  Evidence layer for paper search/fetch, claim-evidence registry, evidence
  summaries, and context-bound answering. Citation grounding (strength
  levels, sentence-level attribution) and data/code availability statements
  live elsewhere — see the index below.
tags: [paper_writing, evidence, citations, rag]
---

# Evidence Skills

Evidence skills decide where facts come from. Manuscript claims may not be
invented or grounded in model memory; they must trace back to a registered
piece of evidence.

## Skills

| Need | Where | Source |
|---|---|---|
| Search and fetch open-access papers by DOI / arXiv / PMID | [paper_fetch.md](./paper_fetch.md) | Future-House/paper-qa |
| Register claims and supporting evidence | inlined below ("Evidence Registry") | DeepScientist |
| Ground citations to specific text segments (strong/partial/weak), rerank candidates, attribute sentences | [../writing/claim_evidence_check.md](../writing/claim_evidence_check.md) ("Citation Grounding" section) | nature-citation, OpenScholar |
| Write data and code availability statements | [../scenarios/journal_article.md](../scenarios/journal_article.md) ("Data and Code Availability" section) | nature-data |

## Default Pipeline

```text
paper_fetch (search + retrieve) → evidence_summary → evidence_registry
   → draft (writing/) → citation grounding (claim_evidence_check)
```

## Evidence Registry

Use before and after drafting. The registry is the source of truth for what
the paper may claim.

### Output — `claim_evidence_map.md`

| Claim ID | Claim | Evidence type | Source | Strength | Risk | Action |
|---|---|---|---|---|---|---|
| C1 | ... | citation/figure/table/data/user_material/missing | S001 | strong | low | keep |

### Rules

- Evidence types are only `citation`, `figure`, `table`, `experimental_data`, `statistical_result`, `user_material`, or `missing`.
- Claims with `missing` evidence cannot appear as firm conclusions.
- If support is partial, narrow the wording.

## Evidence Summary Protocol

Use after retrieval and before writing from papers or notes.

### Output table

| Evidence ID | Source | Passage/page | Query answered | Summary | Score | Claim IDs |
|---|---|---|---|---|---|---|

### Rules

- Preserve enough locator detail for later attribution.
- Separate the source's statement from the agent's interpretation.
- If evidence does not answer the query, say so; do not force fit.

## Context-Bound Answering

When deciding whether a supplied paper, PDF chunk, figure, or user material
supports a claim:

- Answer only from the provided context.
- Cite context keys, evidence IDs, pages, or material IDs.
- If the context is insufficient, write `I cannot answer from the provided context` and list what is missing. Do not fall back to model memory.

## What This Layer Prevents

- Unsupported claims
- Misattributed citations
- Missing evidence trails
- Overclaiming beyond what citations support
- Sci-Hub or any access-control bypass — see allowed OA routes in [paper_fetch.md](./paper_fetch.md)

Sources for inlined sections: DeepScientist paper-outline/SKILL.md,
paper-review.md, PaperQA prompts.py and tools.py, OpenScholar README.md and
open_scholar.py.
