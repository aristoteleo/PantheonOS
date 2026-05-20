---
id: visual_refine
name: Visual Refine Agent Prompt
description: |
  Visual refinement prompt for data_plotter iterative loop. Given the
  rendered PNG + original code + user query, produces step-by-step
  improvement instructions. Adapted from thunlp/MatPlotAgent (MIT).
source: https://github.com/thunlp/MatPlotAgent
license: MIT
---

# Visual Refine

> **Source**: Adapted from `agents/visual_refine_agent/prompt.py` in
> [thunlp/MatPlotAgent](https://github.com/thunlp/MatPlotAgent) (MIT).

## Purpose

Used by `data_plotter` as an alternative to the PaperBanana critic when the
agent has the rendered PNG available for vision-based review. Produces concrete
code-level instructions rather than a revised description.

## System Prompt

```
Given a piece of code, a user query, and an image of the current plot, please
determine whether the plot has faithfully followed the user query. Your task
is to provide instruction to make sure the plot has strictly completed the
requirements of the query. Please output a detailed step by step instruction
on how to use python code to enhance the plot.
```

## User Prompt

```
Here is the code: [Code]:
"""
{code}
"""

Here is the user query: [Query]:
"""
{query}
"""

Carefully read and analyze the user query to understand what kind of plot the
user wants to create. Identify the key requirements and features mentioned in
the user query, such as: the type of plot (e.g., line chart, bar chart,
scatter plot), the data to be plotted, the labels, titles, and annotations
required, any specific colors, styles, or formatting instructions, and any
other special requirements.

Analyze the provided image of the current plot. Compare these elements with
the requirements specified in the user query. Note any differences, missing
elements, or areas for improvement.

Based on your analysis, provide step-by-step instructions on how to modify the
Python code to make the plot better align with the user query. Remember to save
the plot to a png file. The file name should be """{file_name}"""
```

## Error Correction Prompt

```
There are some errors in the code you gave:
{error_message}
please correct the errors.
Then give the complete code and don't omit anything.
```

## Query Expansion System Prompt

```
According to the user query, expand and solidify the query into a step by step
detailed instruction (or comment) on how to write python code to fulfill the
user query's requirements. Import the appropriate libraries. Pinpoint the
correct library functions to call and set each parameter in every function call
accordingly.
```

## Query Expansion User Prompt

```
Here is the user query: [User Query]:
"""
{query}
"""
You should understand what the query's requirements are, and output step by
step, detailed instructions on how to use python code to fulfill these
requirements. Include what libraries to import, what library functions to call,
how to set the parameters in each function correctly, how to prepare the data,
how to manipulate the data, and how to set the figure properties.
```

## Usage in `data_plotter`

MatPlotAgent uses a three-agent loop that `data_plotter` can replicate:

```
1. QueryExpansionAgent → expand raw user query into step-by-step code instructions
2. PlotAgent (initial) → generate Python code from expanded instructions
3. Execute code → render PNG
4. VisualRefineAgent → compare rendered PNG vs query → produce improvement instructions
5. PlotAgent (refine) → apply improvement instructions → new code
6. Repeat steps 3-5 up to 4 attempts or until plot_critic returns null
```

Retry loop: up to 4 attempts; extract Python from ```python...``` blocks;
run code; if error → feed ERROR_PROMPT back to PlotAgent.
