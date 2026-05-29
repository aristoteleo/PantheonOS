---
id: latex_en
name: English LaTeX Template
description: |
  English academic paper LaTeX template. Compiled with Tectonic.
  Uses standard Computer Modern fonts. Includes theorem environments,
  booktabs tables, natbib citations, microtype.
---

# English LaTeX Template

Academic paper template for English content. Compiled with Tectonic.

## Placeholders

| Placeholder | Value |
|-------------|-------|
| `%%TITLE%%` | Paper title |
| `%%AUTHORS%%` | Author string (e.g., `Author 1 \and Author 2`) |
| `%%DATE%%` | Date string |
| `%%CONTENT%%` | Converted LaTeX body |

## Template

```latex
% English Academic Paper Template
% Compiled with Tectonic (auto-downloads packages)
\documentclass[11pt,a4paper]{article}

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
\usepackage{microtype}

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
\newtheorem{theorem}{Theorem}[section]
\newtheorem{lemma}[theorem]{Lemma}
\newtheorem{proposition}[theorem]{Proposition}
\newtheorem{corollary}[theorem]{Corollary}
\theoremstyle{definition}
\newtheorem{definition}{Definition}[section]
\theoremstyle{remark}
\newtheorem{remark}{Remark}[section]

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
