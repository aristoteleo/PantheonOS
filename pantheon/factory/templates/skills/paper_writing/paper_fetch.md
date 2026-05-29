---
id: paper_fetch
name: Paper Fetch
description: |
  Automatically fetch academic papers from DOI, arXiv ID, PMID, or other identifiers.
  Retrieves metadata, PDF, and structured information for citation and evidence gathering.
source: https://github.com/Future-House/paper-qa
license: Apache 2.0
---

# Paper Fetch

Search candidate papers and retrieve full text from various sources using
identifiers (DOI, arXiv ID, PMID, etc.).

## When to Use

- Drafting and a paper needs to be cited but the full reference is missing
- Building a literature review and multiple papers need to be fetched
- The draft lacks literature, citations, or related-work positioning
- Verifying that a citation exists and is accessible

## Search Phase (before fetch)

When the draft lacks specific identifiers and needs candidate papers:

Output table:

| Candidate ID | Query | Title | Authors/year | Source | Why candidate | Next action |
|---|---|---|---|---|---|---|

Search rules:

- A search result is a candidate, not evidence. Validate before citing.
- Record query, source, and reason for inclusion.
- Promising candidates flow into fetch + summarize + grounding (see
  [SKILL.md](./SKILL.md) and [../writing/claim_evidence_check.md](../writing/claim_evidence_check.md)).
- Prefer primary sources for factual claims; balance recent and foundational
  for related work.

## Fetch Input

One or more paper identifiers:
- **DOI**: `10.1038/s41586-024-07487-w`
- **arXiv ID**: `2403.12345` or `arXiv:2403.12345`
- **PMID**: `38123456`
- **Title**: "Accurate structure prediction of biomolecular interactions"
- **URL**: Direct link to publisher page

## Process

1. **Identify source**: Determine if input is DOI, arXiv, PMID, or title
2. **Fetch metadata**: Retrieve title, authors, journal, year, abstract
3. **Fetch PDF** (if available):
   - Open access: Direct download
   - arXiv: Always available
   - Publisher: Check access via institutional login or open access
4. **Structure output**: Return as JSON envelope or failure message

## Output

### Success (JSON envelope)

```json
{
  "status": "success",
  "paper": {
    "doi": "10.1038/s41586-024-07487-w",
    "title": "Accurate structure prediction of biomolecular interactions with AlphaFold 3",
    "authors": ["Abramson, J.", "Adler, J.", "..."],
    "journal": "Nature",
    "year": 2024,
    "volume": "630",
    "pages": "493-500",
    "abstract": "...",
    "pdf_path": "papers/alphafold3_nature2024.pdf",
    "bibtex": "@article{abramson2024alphafold3, ...}"
  }
}
```

### Failure

```json
{
  "status": "failure",
  "reason": "paywall",
  "identifier": "10.1038/...",
  "alternatives": [
    "Try arXiv preprint: arXiv:2405.12345",
    "Check institutional access",
    "Request from author via ResearchGate"
  ]
}
```

## Supported Sources

| Source | Identifier Format | Open Access | Notes |
|--------|-------------------|-------------|-------|
| arXiv | `2403.12345` | ✅ Always | PDF always available |
| bioRxiv | DOI `10.1101/...` | ✅ Always | Preprints |
| medRxiv | DOI `10.1101/...` | ✅ Always | Medical preprints |
| PMC | PMID or PMCID | ✅ Often | PubMed Central |
| Publisher | DOI | ⚠️ Depends | Check institutional access |
| Semantic Scholar | Title search | ⚠️ Metadata only | No PDF |

## Quality Gates

- ✅ **Unpaywall**: Check open access availability first
- ✅ **Semantic Scholar**: Fallback for metadata if DOI fails
- ✅ **arXiv mirror**: Prefer arXiv version if both exist
- ❌ **Sci-Hub**: Do NOT use (legal/ethical issues)
- ❌ **Pirate sources**: Do NOT use

## Error Handling

- **DOI not found**: Try title search on Semantic Scholar
- **Paywall**: Return failure envelope with alternatives
- **PDF corrupt**: Return metadata only, flag PDF issue
- **Timeout**: Retry once, then fail gracefully

## Integration

Call this skill when:
- Drafting and a citation needs a full reference
- Building references.bib and needs BibTeX entries
- Verifying a claim and the cited paper needs to be read

Example call:
```
Read evidence/paper_fetch.md, then fetch paper with DOI 10.1038/s41586-024-07487-w
```

## Constraints

- **Rate limits**: Respect API rate limits (max 1 request/second)
- **Storage**: Store PDFs in `{workdir}/papers/` directory
- **Deduplication**: Check if paper already fetched before re-downloading
- **Idempotency**: Same identifier always returns same result (cache)
