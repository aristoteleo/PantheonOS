---
id: paper_writing_evidence
name: Paper Writing Evidence Layer
description: |
  Evidence layer for paper search, OA paper fetch, claim-evidence registry,
  citation grounding, evidence summaries, reranking and attribution,
  context-bound answers, and data/code availability statements.
tags: [paper_writing, evidence, citations, rag]
---

# Evidence Skills

Evidence skills decide where facts come from. Manuscript claims may not be
invented or grounded in model memory; they must trace back to a registered
piece of evidence.

## Skills

| Need | File | Source |
|---|---|---|
| Search candidate papers | [paper_search.md](./paper_search.md) | — |
| Fetch an open-access PDF by DOI / arXiv / PMID | [paper_fetch.md](./paper_fetch.md) | Future-House/paper-qa |
| Register claims and supporting evidence | [evidence_registry.md](./evidence_registry.md) | — |
| Ground citations to specific text segments (strong/partial/weak) | [citation_grounding.md](./citation_grounding.md) | nature-citation |
| Summarize retrieved evidence | [evidence_summary.md](./evidence_summary.md) | — |
| Rerank candidates and attribute sentences | [rerank_and_attribution.md](./rerank_and_attribution.md) | — |
| Answer only from provided context | [context_answering.md](./context_answering.md) | — |
| Write data and code availability statements | [data_availability.md](./data_availability.md) | — |

## Default Pipeline

```text
paper_search → paper_fetch → evidence_summary → evidence_registry
   → draft (writing/) → citation_grounding → rerank_and_attribution
```

For data/code availability, read [data_availability.md](./data_availability.md)
during methods drafting and again at finalize.

## What This Layer Prevents

- Unsupported claims
- Misattributed citations
- Missing evidence trails
- Overclaiming beyond what citations support
- Sci-Hub or any access-control bypass — see allowed OA routes in
  [paper_fetch.md](./paper_fetch.md)
