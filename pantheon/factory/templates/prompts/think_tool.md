---
id: think_tool
name: Think Tool
description: Guidance for using the think tool for structured reasoning during multi-step tool chains.
---

## Think Tool Usage

You have access to a `think` tool. Use it as a scratchpad to pause and reason through complex situations before taking action. The think tool does not execute anything or retrieve information — it simply records your reasoning process.

**When to use think:**
- After receiving tool results, before deciding next steps
- When multiple rules or constraints apply to the current request
- When you need to verify that your planned action is correct before executing
- When analyzing results that may require backtracking or a different approach
- When brainstorming multiple solutions and assessing trade-offs

**Before taking action after receiving tool results, use think to:**

1. **List applicable rules** — Identify the specific constraints, policies, or requirements that apply to the current request
2. **Check information completeness** — Verify you have all required information to proceed; identify gaps
3. **Verify compliance** — Confirm your planned action satisfies all applicable constraints
4. **Iterate on correctness** — Review tool results for errors, edge cases, or unexpected outcomes

**Example — analyzing results before acting:**
```
think("Analyzing the search results before proceeding.

1. Rules that apply:
   - Must use peer-reviewed sources from the last 5 years
   - Need at least 3 independent sources for each claim

2. Information collected:
   - Found 5 papers, but 2 are from 2018 (too old)
   - 3 remaining papers cover the main claim

3. Gaps:
   - No source yet for the secondary hypothesis
   - Need to search with different keywords

4. Plan: Search again with refined terms before synthesizing.")
```

**Example — debugging before fixing:**
```
think("Found the error in the traceback. Let me brainstorm fixes.

1. Root cause: The function assumes input is always a list, but line 42 passes a dict
2. Possible fixes:
   a. Add type check at function entry — simplest, but masks caller bugs
   b. Fix the caller at line 42 to wrap in list — addresses root cause
   c. Make function accept both types — most flexible but adds complexity

3. Assessment: Option (b) is best — it fixes the actual bug without adding defensive code.
   Let me verify no other callers have the same issue before making the change.")
```
