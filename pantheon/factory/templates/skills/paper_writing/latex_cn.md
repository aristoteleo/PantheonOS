---
id: latex_cn
name: Chinese LaTeX Template
description: |
  Chinese academic paper LaTeX template. Compiled with Tectonic (XeTeX engine).
  Uses xeCJK + Fandol fonts for Chinese support. Includes theorem environments,
  booktabs tables, natbib citations.
---

# Chinese LaTeX Template

Academic paper template for Chinese content. Compiled with Tectonic.

## Placeholders

| Placeholder | Value |
|-------------|-------|
| `%%TITLE%%` | Paper title |
| `%%AUTHORS%%` | Author string (e.g., `Author 1 \and Author 2`) |
| `%%DATE%%` | Date string |
| `%%CONTENT%%` | Converted LaTeX body |

## Template

```latex
% Chinese Academic Paper Template
% Compiled with Tectonic (XeTeX engine, auto-downloads packages)
\documentclass[11pt,a4paper]{article}

% Chinese support (Tectonic bundles Fandol fonts)
\usepackage{xeCJK}
\setCJKmainfont{FandolSong}
\setCJKsansfont{FandolHei}
\setCJKmonofont{FandolFang}

% Page layout
\usepackage{geometry}
\geometry{margin=2.5cm}

% Core packages
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{amsmath,amssymb,amsthm}
\usepackage{hyperref}
\usepackage[numbers,sort&compress]{natbib}
\usepackage{enumitem}
\usepackage{caption}
\usepackage{float}
\usepackage{xcolor}
\usepackage{listings}
\usepackage{url}

% Hyperref setup
\hypersetup{
    colorlinks=true,
    linkcolor=black,
    citecolor=blue!60!black,
    urlcolor=blue!60!black
}

% Code listing style
\lstset{
    basicstyle=\ttfamily\small,
    breaklines=true,
    frame=single,
    backgroundcolor=\color{gray!5},
    numbers=left,
    numberstyle=\tiny\color{gray},
    tabsize=4
}

% Theorem environments
\newtheorem{theorem}{定理}[section]
\newtheorem{lemma}[theorem]{引理}
\newtheorem{proposition}[theorem]{命题}
\newtheorem{corollary}[theorem]{推论}
\theoremstyle{definition}
\newtheorem{definition}{定义}[section]
\theoremstyle{remark}
\newtheorem{remark}{注}[section]

\title{%%TITLE%%}
\author{%%AUTHORS%%}
\date{%%DATE%%}

\begin{document}
\maketitle

%%CONTENT%%

\bibliographystyle{unsrtnat}
\bibliography{references}

\end{document}
```
