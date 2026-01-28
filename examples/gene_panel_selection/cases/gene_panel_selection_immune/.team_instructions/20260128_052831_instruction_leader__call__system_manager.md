Project: Human immune oncology gene profiling panel
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Agent workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/system_manager

Task: Gather computational environment information and prepare a suitable environment for the selection_expert to run scRNA-seq gene panel selection workflows with scverse tooling.

Please:
1) Audit hardware and OS (CPU cores, RAM, disk, GPU availability/driver/CUDA), and Python setup. 
2) Audit currently available Python packages.
3) Ensure a working Python environment for scverse and selection pipelines: anndata, scanpy, numpy, scipy, pandas, scikit-learn, numba, pynndescent, igraph/leidenalg, matplotlib, seaborn, statsmodels, celltypist, pytorch (if GPU available). Also ensure packages commonly used by the selection_expert (used in pre-established methods: SpaPROS, scGeneFit, RF-based selection, DE workflows). If anything key is missing, install it.
4) Save a concise environment.md in the project root with the audit summary and key package versions.

Notes:
- Do not change the project structure. Save outputs under your agent workdir and write environment.md to the project root.
- Keep installation logs under your agent workdir.
