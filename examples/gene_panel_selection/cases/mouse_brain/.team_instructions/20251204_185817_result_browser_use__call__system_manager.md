Done.

Summary:
- Installed adjustText (latest stable) into the active notebooks environment.
- Verified import works and version is 1.3.0 with Python 3.10.19.

Artifacts:
- workdir_install_adjustText/report_system_manager_install_adjustText.md: detailed steps and outputs.
- environment.md updated with the new package info and a short usage snippet.

You can now use in notebooks:
from adjustText import adjust_text
texts = [plt.text(x, y, label) for x, y, label in zip(xs, ys, labels)]
adjust_text(texts, arrowprops=dict(arrowstyle='-', color='gray'))