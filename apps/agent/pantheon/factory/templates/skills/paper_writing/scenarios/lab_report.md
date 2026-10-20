---
id: lab_report_scenario
name: Lab Report Scenario
description: Experiment or lab report route with reproducibility and raw observation separation.
tags: [paper_writing, lab_report, reproducibility]
---

# Lab Report Scenario

Use for lab reports, experiment records, data-processing reports, or practical
course reports.

| Field | Contract |
|---|---|
| Trigger | lab report, experiment report, 实验报告, 实验记录 |
| Inputs | date, operator, samples, batch IDs, protocols, instruments, raw observations, data |
| Read next | [../workflow/SKILL.md](../workflow/SKILL.md) (Material Inventory + Data Analysis Summary sections), [../SKILL.md](../SKILL.md) (Reproducibility Check section) |
| Outputs | lab-report HTML/PDF with raw observation, processed result, abnormal events |
| Gates | reproducibility, raw observation/result separation, anomaly logging |
| Forbidden | deleting abnormal observations or merging interpretation into raw results |

If no abnormal event is known, write "No abnormal event was reported" instead of
silently omitting the field.

Sources: local design PDF, scientific-writing/SKILL.md.
