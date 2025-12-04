Caller: selection_expert. Task: Collect curated gene lists (mouse gene symbols) for brain-relevant receptor/target families to build a receptor-centric panel. Please use authoritative sources (IUPHAR/Guide to Pharmacology, HGNC/GENE Nomenclature, UniProt, GO, Pharos/DrugCentral/DrugBank summaries). Families needed:
- GPCRs (class A/B/C, adhesion GPCRs)
- Ligand-gated ion channels (GABA-A, GABA-B [GPCR], glutamate ionotropic AMPA/NMDA/kainate, nicotinic AChRs, 5-HT3)
- Voltage-gated ion channels (selected neuronal VGSCs: Scn1a.., VGCCs Cacna1.., HCN, Kv families Kcna/Kcnb/etc) – prioritize CNS-expressed
- Receptor tyrosine kinases (RTKs) and key co-receptors (Ntrk1/2/3, Egfr, Erbb2/3/4, Met, Pdgfr, Fgfr, Insr/Igf1r, etc)
- Cytokine/interleukin receptors (Ilr, Ifnar/Ifngr, Tnfr, OSMR, LIFR, gp130/Il6st)
- Chemokine receptors (Ccr/Cxcr)
- Toll-like receptors (Tlr1-13)
- Complement receptors (C3ar1, C5ar1/2, Itgam/Itgax complement integrins), Fc receptors (Fcgr, Fcer)
- Neuropeptide receptors (Npy, Npy2r, Galr, Sstr, Oprk1/Oprd1/Oprm1, Ntsr1/2, Tacr, Crhr)
- Neurotransmitter GPCRs (Drd, Htr [except 5-HT3], Adra/Adrb/Adrg)
- Nuclear receptors (Nr1..Nr5 families; core brain-relevant subset)
- Transporters related to neurotransmission (SLC6, SLC1A2/3/6/7, Slc17a6/7/8, Slc32a1, Slc18a1/2)

Deliverable:
- A TSV saved at /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert/receptor_gene_sets.tsv with columns: gene_symbol, family, source, notes. Include mouse symbols. Ensure duplicates removed and gene symbols validated to mouse nomenclature (if a source is human, map to mouse ortholog names where names are conserved; prefer standard mouse symbols).
- Also save a brief markdown summary of sources used and any mapping decisions: receptor_gene_sets_sources.md in same directory.