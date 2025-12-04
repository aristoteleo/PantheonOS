Here’s a concise review of the final 500-gene receptor-centric panel, based on the provided files and UMAP:

Coverage of major brain cell classes (and minimal core markers)
- Neurons (excitatory and inhibitory): Well covered and clearly resolved on the Final500 UMAP. Core markers present (Rbfox3/NeuN, Syt1, Snap25), excitatory (Slc17a7/VGLUT1; Slc17a6/VGLUT2), inhibitory (Gad1, Gad2, Slc32a1). Interneuron-enriched Erbb4 included.
- Astrocytes: Strong coverage (Aldh1l1, Aqp4, Slc1a2/GLT-1, Slc1a3/GLAST, Glul).
- Oligodendrocytes/OPCs: Strong (Sox10, Plp1, Mbp, Mog, Mag, Cnp; OPC marker Pdgfra).
- Microglia: Strong (Tmem119, Cx3cr1, Itgam, Csf1r + multiple cytokine/chemokine receptors).
- Endothelial/pericytes: Strong (Pecam1, Kdr/VEGFR2, Flt1/VEGFR1, Tek/Tie2, Pdgfrb, Klf2). Clear vascular cluster.
- Ependymal: Weakest. Ependymal separation is less distinct on the UMAP; canonical Foxj1 is absent. Minimal core markers for this class look insufficient.

Diversity of receptor families relevant to neuropharmacology
- Neurotransmitter GPCRs: Broad coverage (dopamine Drd1/2/3/5; serotonin Htr1a/b/d/f, Htr2a/b/c, Htr3a, Htr4, Htr5a/b, Htr7; adrenergic Adra1a/b, Adra2a/b/c, Adrb1–3; muscarinic Chrm1/2/3/5; cannabinoid Cnr1/Cnr2; adenosine Adora1/Adora2a; neuropeptide receptors Npy1r/2r/5r, Sstr1/2/3/5, Crhr1/2, Vipr1/2, Oxtr, Avpr1a, Ghsr; numerous orphan GPCRs).
- Ion channels: Extensive voltage-gated (NaV, CaV with auxiliary subunits, Kv/Kir/K2P, Hcn1–4), TRP (Trpc/Trpm/Trpv), Piezo1; ligand-gated glutamate (AMPA/NMDA/kainate/delta), GABA-A, nicotinic ACh, P2X.
- RTKs and developmental: Egfr/Erbb3/4; Ntrk2/3; Fgfr1–4; Epha/Ephb families; Ror1/2; Pdgfra/b; Insr, Igf1r; TGF-β/Activin/BMP (Acvr1/1b/1c/2a/2b, Bmpr1a/1b, Bmpr2, Tgfbr1–3, Acvrl1); Notch1/3.
- Cytokine/chemokine/TNFR: Rich (Il1r/Il18r axis; Il2/6/7/10/12/13/20/21/22/31 receptors; chemokine GPCRs Cx3cr1, Cxcr3/4/5, Ccr1/2/5/6/9, Ccrl2; many TNFRSF).
- Purinergic: Comprehensive P2Y (P2ry1/2/6/10/10b/12/13/14) and P2X (P2rx1/3/4/5/6/7); adenosine receptors included.
- Vascular targets: Strong (VEGF axis Kdr/Flt1, Tek, Pdgfrb/a, Pecam1).

Strengths
- Major classes (neurons, astrocytes, oligodendrocytes/OPCs, microglia, endothelial/pericytes) are well-resolved; marker coverage is robust.
- Excellent breadth of ion channels and synaptic receptors; strong neuroimmune and RTK/Eph/TGF-β pathways.

Potential gaps
- Metabotropic glutamate receptors (Grm1–8) absent.
- GABA-B receptors (Gabbr1/2) absent.
- Opioid receptors (Oprm1/Oprd1/Oprk1/Oprl1) absent.
- Histamine receptors (especially Hrh3) absent.
- Glycine receptors (Glra/Glrb) not represented (more critical for hindbrain/spinal contexts).
- Ependymal lineage lacks Foxj1.

3–5 small swap suggestions
1) Add Grm5 (mGluR5) → drop Trpm6 (low CNS relevance).
2) Add Gabbr1 (GABA-B1) → drop Cacng6 (redundant vs other γ subunits already included).
3) Add Oprm1 (mu-opioid receptor) → drop Gpr1 (orphan GPCR; limited CNS pharmacology).
4) Add Hrh3 (histamine H3) → drop Gpr150 (orphan GPCR; limited CNS annotation).
5) Add Foxj1 (ependymal/ciliogenesis marker) → drop Trpv6 (limited CNS expression).
- If strict receptor-only swaps are required, replace Foxj1 with Gabbr2 or Grm2 and drop another low-priority orphan (e.g., Gpr3 or Gpr137b).

Files saved in workdir
- Short note: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert/biologist/biologist_notes.md
- Full report: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert/biologist/report_biologist_receptor_centric_500_review.md