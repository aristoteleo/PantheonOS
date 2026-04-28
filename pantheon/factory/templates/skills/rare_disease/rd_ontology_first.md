---
id: rd_ontology_first
name: Rare Disease Ontology-First Workflow
description: |
  Ontology-first workflow for rare disease support. Use Orphanet/OMIM/HPO
  normalized data layer first, then online evidence (BioMCP/Web/DB API).
tags: [rare_disease, ontology, orphanet, omim, hpo, normalization]
---

# Rare Disease Ontology-First Workflow

## Goal

Before literature retrieval or ranking, first complete deterministic normalization against the local `rd_ontology` layer.

## What "Ontology Layer" Means

For this project, ontology layer is **not** generic web search and not only BioMCP disease search.
It is your controlled local knowledge base built from:
- Orphanet
- OMIM
- HPO

Typical normalized entities:
- disease canonical name / aliases
- cross references (ORPHA / OMIM / MONDO / HPO links when available)
- phenotype associations

## Execution Order (Mandatory)

1. **Normalize input terms first**
   - map free text phenotype → normalized clinical phrase
   - map disease aliases → canonical disease entity

2. **Resolve cross-IDs**
   - keep traceable IDs when possible (ORPHA / OMIM / MONDO / HPO)
   - if multiple matches exist, keep top candidates + ambiguity note

3. **Build ontology package**
   - output concise structured package for downstream agents:
     - normalized disease candidates
     - matched phenotype terms
     - xrefs
     - miss/ambiguous flags

4. **Only then run online evidence retrieval**
   - use BioMCP / web / database_api for latest literature & trial evidence
   - keep ontology output as candidate anchor, avoid drifting synonyms

## Retrieval Pattern (Ontology-First)

When performing evidence or disease retrieval, follow this sequence:

1. **Ontology layer first (offline/local retrieval preferred)**
   - Query the local `rd_ontology` SQLite-backed toolset first.
   - Use this layer for disease alias normalization, phenotype term alignment, and candidate-name standardization.

2. **Literature and public evidence layer**
   - Then retrieve evidence from PubMed/Crossref/Google-Scholar-like sources via `biomcp`, `web`, and `database_api`.
   - Prefer recent and discriminative evidence (case reports, reviews, cohort papers) over generic disease summaries.

3. **Evidence labeling and traceability**
   - Label each support point by source type: `[ontology]`, `[literature]`, `[case]`, `[background]`.
   - Keep claims traceable to retrievable sources (title / DB / identifier / URL-ready reference info).
   - If local ontology retrieval is unavailable, explicitly state this and continue with public-source retrieval.

## Local Data Access (SQLite-first)

Preferred local database path:
- `data/rd_ontology_store/rd_ontology.sqlite`

Recommended helper commands:

```bash
# build / rebuild ontology db
python scripts/rare_disease/build_rd_ontology.py build --reset

# quick stats
python scripts/rare_disease/build_rd_ontology.py stats

# quick lookup
python scripts/rare_disease/build_rd_ontology.py search "Marfan syndrome"

# normalize + query
python scripts/rare_disease/query_rd_ontology.py resolve "your_alias_or_term"

# fetch one candidate in detail
python scripts/rare_disease/query_rd_ontology.py disease "OMIM:154700"
```

Preferred agent-native tool calls (when `rd_ontology` toolset is available):
- `resolve_term(query=...)`
- `search_disease(query=...)`
- `get_disease(disease_uid=...)`
- `find_by_hpo(hpo_ids=[...])`

## Output Contract (Minimum)

Return these fields whenever possible:

- `normalized_terms`: list of normalized phenotype/disease terms
- `canonical_candidates`: list of canonical disease candidates
- `xrefs`: ORPHA/OMIM/MONDO/HPO references
- `ontology_support`: short support notes from ontology layer
- `ontology_miss`: unresolved terms or ambiguity notes

## Guardrails

- Do not skip ontology normalization when terms are ambiguous.
- Do not fabricate xref IDs.
- Do not present online evidence as ontology truth.
- If ontology has no good match, explicitly mark `ontology_miss` and continue with cautious fallback.
