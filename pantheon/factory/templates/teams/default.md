---
category: general
description: 'The default team with Leader that delegates to specialists for comprehensive task handling.'
icon: 🏠
id: default
name: General Team
type: team
version: 1.0.0
agents:
  - leader
  - data_analyst
  - researcher
  - scientific_illustrator
leader:
  id: leader
  name: Leader
  icon: 🧭
  toolsets:
    - file_manager
    - python_interpreter
    - shell
    - package
    - task
    - code
---

{{agentic_general}}


{{delegation}}


{{packages}}