Environment summary update (2025-12-04)

- Python: 3.10.19 (/home/erwinpi/miniconda3/envs/gps/bin/python3)
- Pip: 25.3

Newly installed for plotting centroid labels with repel:
- adjustText: 1.3.0 (installed and import verified)

Notes:
- Use in notebooks:
  from adjustText import adjust_text
  texts = [plt.text(x, y, label) for x, y, label in zip(xs, ys, labels)]
  adjust_text(texts, arrowprops=dict(arrowstyle='-', color='gray'))
