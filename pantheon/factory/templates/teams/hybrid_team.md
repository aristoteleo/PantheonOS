---
category: advanced
description: Demonstrates unified architecture with mixed inline and referenced agents
icon: ⚙️
id: hybrid_team
name: Hybrid Team
type: team
version: 2.0.0
agents:
  - specialist_analyst
  - specialist_engineer
  - data_analyst
  - python_dev
  - researcher
specialist_analyst:
  id: specialist_analyst
  name: Specialist Analyst
  model: openai/gpt-5-mini
  icon: 📊
  toolsets:
    - python_interpreter
    - file_manager
specialist_engineer:
  id: specialist_engineer
  name: Specialist Engineer
  model: openai/gpt-5-mini
  icon: 🔧
  toolsets:
    - python_interpreter
    - file_manager
---

You are a specialist analyst focused on detailed analysis.

---

You are a specialist engineer focused on implementation.
