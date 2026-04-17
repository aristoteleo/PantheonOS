---
id: delegation
name: Delegation
description: Universal delegation principles and framework
---

## Delegation Framework

Delegation is an **enhancement tool** for handling tasks that would be inefficient to execute directly. Use it when benefits clearly outweigh costs.

### Cost-Benefit Analysis

**Delegation Costs:**
- Task Brief writing time
- Sub-agent startup overhead
- Result validation and integration work

**Delegation Benefits:**
- Preserves your context for coordination and synthesis
- Leverages specialized expertise and optimized prompts
- Enables parallel processing of independent tasks
- Isolates exploratory work from main conversation flow

**Decision Rule**: Delegate when benefits > costs

### Task Brief Format

When delegating, always provide a clear Task Brief:

```markdown
## Goal
- Describe the objective and why it matters

## Context
- Provide all background the sub-agent needs (files, data, constraints, user intent)
- Assume the sub-agent has zero memory of the conversation; restate everything critical

## Expected Outcome
- Detail deliverables, format, quality bar, file names or schemas, validation requirements
```

### Workflow & Tools

1. Assess task characteristics (complexity, expert match, context needs)
2. Check available agents via `list_agents()`
3. Evaluate delegation criteria (defined by your team configuration)
4. If delegating: Build Task Brief and call `call_agent(agent_name, instruction)`
5. Track outstanding delegations and gather outputs
6. Validate each result against the brief's Expected Outcome
7. Integrate results into cohesive response

### Coordination Patterns

- **Sequential**: For dependent tasks, delegate in order
- **Parallel**: Launch independent tasks concurrently using multiple `call_agent()` calls
- **Try-then-escalate**: Start with direct execution, escalate to delegation if complexity exceeds threshold
- **Synthesis**: After receiving results, reconcile conflicts, highlight trade-offs, and produce cohesive answer

### Anti-Patterns to Avoid

- Don't delegate trivial tasks (single file read, quick search)
- Don't prescribe step-by-step "how-to" instructions; sub-agents own the "How"
- Don't omit context or success criteria in Task Brief
- Don't combine unrelated goals in one delegation
- Don't assume agents share state between calls
- Don't skip validation—always verify outputs meet Expected Outcome

### Example (Good)

```python
call_agent(
  "researcher",
  """
  ## Goal
  Evaluate Q1–Q4 revenue growth to inform the 2025 expansion plan.

  ## Context
  - Revenues (USD): Q1 100K, Q2 120K, Q3 115K, Q4 130K.
  - Need QoQ percentages and commentary on trend shifts ≥5%.
  - No external data access; work strictly from provided numbers.

  ## Expected Outcome
  - Markdown table: Quarter | Revenue | QoQ % | Notes.
  - Highlight anomalies, provide 2-sentence strategic insight tied to expansion feasibility.
  """
)
```

### Example (Bad)

```python
call_agent("researcher", "Do analysis fast.")
```

