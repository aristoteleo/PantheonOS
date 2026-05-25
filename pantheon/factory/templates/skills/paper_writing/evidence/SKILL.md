---
id: paper_writing_evidence
name: Paper Writing Evidence Layer
description: |
  Evidence layer for paper fetch, claim-evidence registry, citation grounding
  with reranking and attribution, evidence summaries, context-bound answers,
  and data/code availability statements.
tags: [paper_writing, evidence, citations, rag]
---

# Evidence Skills

Evidence skills decide where facts come from. Manuscript claims may not be
invented or grounded in model memory; they must trace back to a registered
piece of evidence.

## Skills

| Need | File | Source |
|---|---|---|
| Search and fetch open-access papers by DOI / arXiv / PMID | [paper_fetch.md](./paper_fetch.md) | Future-House/paper-qa |
| Register claims and supporting evidence | [evidence_registry.md](./evidence_registry.md) | — |
| Ground citations to specific text segments (strong/partial/weak); rerank candidates and attribute sentences | [citation_grounding.md](./citation_grounding.md) | nature-citation, OpenScholar |
| Write data and code availability statements | [data_availability.md](./data_availability.md) | nature-data |

## Default Pipeline

```text
paper_fetch (search + retrieve) → evidence_summary → evidence_registry
   → draft (writing/) → citation_grounding (with rerank + attribution)
```

For data/code availability, read [data_availability.md](./data_availability.md)
during methods drafting and again at finalize.

## Evidence Summary Protocol

Use after retrieval and before writing from papers or notes.

Output table:

| Evidence ID | Source | Passage/page | Query answered | Summary | Score | Claim IDs |
|---|---|---|---|---|---|---|

Rules:

- Preserve enough locator detail for later attribution.
- Separate the source's statement from the agent's interpretation.
- If evidence does not answer the query, say so; do not force fit.

## Context-Bound Answering

When deciding whether a supplied paper, PDF chunk, figure, or user material
supports a claim:

- Answer only from the provided context.
- Cite context keys, evidence IDs, pages, or material IDs.
- If the context is insufficient, write `I cannot answer from the provided context`
  and list what is missing. Do not fall back to model memory.

## What This Layer Prevents

- Unsupported claims
- Misattributed citations
- Missing evidence trails
- Overclaiming beyond what citations support
- Sci-Hub or any access-control bypass — see allowed OA routes in
  [paper_fetch.md](./paper_fetch.md)

Sources for inlined sections: PaperQA prompts.py and tools.py, OpenScholar
README.md and open_scholar.py.
