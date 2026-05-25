---
id: paper_writing_context_answering
name: Context Answering
description: Answer only from supplied context and return cannot-answer when evidence is insufficient.
tags: [paper_writing, context, qa]
---

# Context Answering

Use when deciding whether a supplied paper, PDF chunk, figure, or user material
supports a claim.

## Rules

- Answer only from the provided context.
- Cite context keys, evidence IDs, pages, or material IDs.
- If the context is insufficient, write `I cannot answer from the provided context`
  and list what is missing.

Sources: PaperQA prompts.py.
