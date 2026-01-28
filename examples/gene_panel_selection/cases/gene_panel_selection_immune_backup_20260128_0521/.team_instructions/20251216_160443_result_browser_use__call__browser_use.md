Below are concise, citable references (reviews or authoritative databases) supporting common T/NK lineage and state markers used in scRNA-seq, grouped as requested. Each item has a 1–2 sentence summary and a link.

(a) Naive/central memory T cell markers
- CCR7
  - Foundational: Expression of CCR7 subdivides memory T cells into CCR7+ central memory (lymph node–homing, non-immediate effector) vs CCR7− effector memory (tissue-homing, immediate effector). Nature (Sallusto et al., 1999): https://www.nature.com/articles/35005534
  - Review: CCR7 is expressed on naive, regulatory, and central memory T cells and orchestrates homing and immunity/tolerance balance. Nat Rev Immunol (Förster et al., 2008): https://www.nature.com/articles/nri2297
- TCF7 (TCF-1)
  - Review: TCF-1 (encoded by TCF7) is a key transcription factor for T cell development and maintenance of stem-like/naive and early memory CD8+ T cells; widely used to mark progenitor-like states in scRNA-seq. Nat Rev Immunol (2021): https://www.nature.com/articles/s41577-021-00563-6
- IL7R (CD127)
  - Review: IL-7/IL-7Rα signaling is essential for survival/homeostasis of naive and memory T cells; underpins IL7R as a naive/central memory-associated marker. Nat Rev Immunol (2011): https://www.nature.com/articles/nri2970
  - Database: GeneCards IL7R summary (functions, expression): https://www.genecards.org/cgi-bin/carddisp.pl?gene=IL7R

(b) Cytotoxic effector markers
- PRF1 and GZMB
  - Review: Perforin forms pores enabling granzyme entry; granzymes (including GZMB) induce target-cell death—core cytotoxic effector molecules co-expressed in CTLs and NK cells. Nat Rev Immunol (2015): https://www.nature.com/articles/nri3839
- GNLY (Granulysin)
  - Review: Granulysin is a cytolytic/proinflammatory peptide stored in CTL/NK granules with antimicrobial and tumoricidal activity; commonly co-expressed with perforin/granzymes. Blood (2010): https://ashpublications.org/blood/article/116/18/3379/27910/The-multifaceted-granulysin
- NKG7
  - Primary study: NKG7 encodes an NK/T cell granule protein that regulates cytotoxic granule exocytosis and inflammation; robust cytotoxicity-associated scRNA-seq marker. Nat Immunol (2020): https://www.nature.com/articles/s41590-020-0758-6

(c) Exhaustion/checkpoint markers
- TOX
  - Exhaustion overview: TOX is a central transcriptional regulator programming and maintaining CD8+ T cell exhaustion, used to identify exhausted/progenitor-exhausted states. Review (open access): https://pmc.ncbi.nlm.nih.gov/articles/PMC9388609/
  - Canonical primary reports: Nature (2019) TOX programs exhaustion (Khan et al.): https://www.nature.com/articles/s41586-019-1325-x; Nature (2019) TOX reinforces exhausted T cell phenotype (Alfei et al.): https://www.nature.com/articles/s41586-019-1335-7
- PDCD1 (PD-1)
  - Review: PD-1 is a canonical inhibitory checkpoint on activated/exhausted T cells; high PDCD1 marks exhaustion and relates to response to PD-1 blockade. Review (open access): https://pmc.ncbi.nlm.nih.gov/articles/PMC10228652/
- TIGIT
  - Review: TIGIT is an inhibitory receptor on T and NK cells, often co-expressed with other exhaustion markers; blocking TIGIT restores cytotoxic function. Clin Exp Immunol (open access): https://pmc.ncbi.nlm.nih.gov/articles/PMC7160651/
- LAG3
  - Review: LAG-3 is an inhibitory receptor frequently co-expressed with PD-1 on exhausted T cells and is a validated immunotherapy target. Frontiers in Immunology (open access): https://www.frontiersin.org/journals/immunology/articles/10.3389/fimmu.2021.615317/full
- HAVCR2 (TIM-3)
  - Review: TIM-3 is a negative checkpoint receptor expressed on exhausted/dysfunctional T cells, signaling through distinct complexes to curb T cell responses. Signal Transduct Target Ther (2020): https://www.nature.com/articles/s41423-020-00575-7
- CTLA4
  - Overview review: CTLA-4 (with PD-1, LAG3, TIGIT) is a core inhibitory receptor regulating T cell activation and tolerance; central to checkpoint blockade. Frontiers in Immunology (open access): https://pmc.ncbi.nlm.nih.gov/articles/PMC10019320/
- ICOS
  - Databases: Costimulatory receptor upregulated on activated/Tfh and often present in chronically stimulated progenitor-exhausted contexts; used to annotate activated/exhausted-like states in scRNA-seq. UniProtKB Q9Y6W8 publications: https://www.uniprot.org/uniprotkb/Q9Y6W8/publications; GeneCards: https://www.genecards.org/cgi-bin/carddisp.pl?gene=ICOS

(d) NK markers
- KLRD1 (CD94)
  - Database (curated): CD94 pairs with NKG2 receptors (NKG2A/C/E) to form inhibitory/activating NK receptors; KLRD1 is a standard NK lineage marker in scRNA-seq. NCBI Gene: https://www.ncbi.nlm.nih.gov/gene/3824
- KLRC1/2/3 (NKG2A/C/E)
  - Databases (curated): NKG2 family members on NK cells (with CD94); NKG2A is inhibitory, NKG2C/E are activating—useful to subtype NK populations. GeneCards KLRC1: https://www.genecards.org/cgi-bin/carddisp.pl?gene=KLRC1; KLRC2: https://www.genecards.org/cgi-bin/carddisp.pl?gene=KLRC2; KLRC3: https://www.genecards.org/cgi-bin/carddisp.pl?gene=KLRC3

Notes for scRNA-seq annotation
- Naive/Tcm: CCR7 and IL7R (CD127) with TCF7 (TCF-1) support stem-like/naive-memory programs; often paired with SELL (CD62L).
- Cytotoxic: PRF1, GZMB, GNLY, NKG7 co-expression indicates cytotoxic effector programs (CD8 T or NK).
- Exhaustion: PDCD1 with TIGIT/LAG3/HAVCR2/CTLA4 plus TOX supports exhausted or progenitor-exhausted states; ICOS often marks chronically stimulated/Tfh-like/progenitor-exhausted subsets.
- NK: KLRD1 and KLRC1/2/3 define NK lineage and inhibitory/activating receptor balance.

Files saved for your use
- Full report (process, details, all links): workdir/report_browser_use_T_NK_marker_background.md
- BibTeX references file: workdir/references_1.bib