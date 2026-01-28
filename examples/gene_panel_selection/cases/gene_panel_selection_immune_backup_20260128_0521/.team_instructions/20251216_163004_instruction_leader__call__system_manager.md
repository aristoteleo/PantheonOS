Task: Ensure the final PDF report is generated from the existing LaTeX sources and placed at the project workdir root.

Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Reporter workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/reporter

Actions:
1) Check for a LaTeX installation. If pdflatex/xelatex are missing, install a minimal TeX Live with required packages (e.g., latexmk, xelatex/pdflatex, amsmath, graphicx, xcolor, geometry, hyperref, longtable, booktabs, adjustbox, caption, float, lscape/pdflscape). Log actions in system_manager/setup_log.txt.
2) Compile reporter/report.tex to reporter/report.pdf (use latexmk -pdf -interaction=nonstopmode if available). Run twice to resolve refs.
3) Copy the compiled PDF to: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/report.pdf
4) Update reporter/report_README.txt if needed to reflect the final PDF path.
5) If compilation errors persist, fall back to generating a PDF via pandoc from an auto-built markdown summary using the available inputs; save as report.pdf in the project workdir and note the fallback in setup_log.txt.

Deliverables:
- report.pdf at the project workdir root
- Updated logs in system_manager/setup_log.txt