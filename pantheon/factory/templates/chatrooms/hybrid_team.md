---
category: advanced
description: "Demonstrates unified architecture supporting both features
  "
icon: ⚙️
id: hybrid_team
name: Hybrid Team
agents:
  - specialist_analyst
  - specialist_engineer
sub_agents:
  - data_analyst
  - python_dev
  - researcher
type: chatroom
version: 2.0.0
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
  name: Specialist autoEngineer
  model: openai/gpt-5-mini
  icon: 🔧
  toolsets:
    - python_interpreter
    - file_manager
---


You are a specialist analyst focused on detailed analysis.

---

You are a specialist engineer focused on implementation.
