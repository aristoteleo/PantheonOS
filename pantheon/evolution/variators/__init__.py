"""Mutation operators: the components that actually produce a child.

Separated from the method because they are orthogonal. "MAP-Elites with a single coding agent" and
"MAP-Elites in a sandbox" are one algorithm with two operators, not two algorithms, and pairing
them off in the method would multiply the implementations instead of adding them.

The three differ in how much the operator is allowed to do before it commits, which matters more
than it looks:

    CompletionVariator   one prompt, k completions, no tools -- it cannot run anything and never
                         sees a score. This is what SimpleTES does.
    AgentVariator        a workspace, a shell, and `run_evaluator`, so it verifies its own edit
                         before submitting
    SandboxVariator      the same agent, in a Modal sandbox that also scores the child, so evolved
                         code never runs on the host

Comparing two search policies is only meaningful with the operator held fixed, and which one it is
held fixed AT changes the answer.
"""
from .agent import MUTATION_AGENT_SYSTEM_PROMPT, AgentVariator
from .adapters import CodeEvaluator, ProgramEvaluatorAdapter
from .completion import CompletionVariator, extract_code
from .idea import IdeaCodeVariator, IdeaJudge, IdeaVariator
from .sandbox import SandboxVariator

__all__ = ["AgentVariator", "CompletionVariator", "SandboxVariator",
           "IdeaVariator", "IdeaCodeVariator", "IdeaJudge",
           "MUTATION_AGENT_SYSTEM_PROMPT", "extract_code",
           "CodeEvaluator", "ProgramEvaluatorAdapter"]
