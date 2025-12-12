Environment summary update (2025-12-04)

- Python: 3.10.19 (/home/erwinpi/miniconda3/envs/gps/bin/python3)
- Pip: 25.3

Newly installed:
- adjustText: 1.3.0 (installed and import verified)
- PyMuPDF (pymupdf): 1.26.6 (installed and import verified; MuPDF 1.26.11 backend)

Notes:
- PyMuPDF usage example for rendering PDFs to images:
  import fitz  # PyMuPDF
  with fitz.open("report.pdf") as doc:
      page = doc[0]
      pix = page.get_pixmap(dpi=200)
      pix.save("page1.png")
