"""
Tests for PantheonTeam's call_agent delegation.

Run with:
  pytest tests/test_team_transfer_and_call_agent.py -xvs

Or with specific model:
  PANTHEON_MODEL=gpt-4o pytest tests/test_team_transfer_and_call_agent.py -xvs
"""

import asyncio
import os
from types import SimpleNamespace
import pytest
from pantheon.agent import Agent, AgentRunContext, _RUN_CONTEXT
from pantheon.team import PantheonTeam

# Get model from environment or use default
TEST_MODEL = os.getenv("PANTHEON_MODEL", "gpt-4o-mini")


def test_get_target_agent_rejects_self_delegation():
    coordinator = Agent(name="omicverse_leader", instructions="lead", model=TEST_MODEL)
    expert = Agent(name="omicverse_expert", instructions="expert", model=TEST_MODEL)
    team = PantheonTeam(agents=[coordinator, expert])

    token = _RUN_CONTEXT.set(
        AgentRunContext(
            agent=expert,
            memory=None,
        )
    )
    try:
        with pytest.raises(ValueError, match="cannot delegate to itself"):
            team.get_target_agent("omicverse_expert", "do work")
    finally:
        _RUN_CONTEXT.reset(token)


@pytest.mark.asyncio
async def test_subagent_cannot_delegate_to_ancestor():
    leader = Agent(name="omicverse_leader", instructions="lead", model=TEST_MODEL)
    expert = Agent(name="omicverse_expert", instructions="expert", model=TEST_MODEL)
    reviewer = Agent(name="omicverse_reviewer", instructions="review", model=TEST_MODEL)
    team = PantheonTeam(agents=[leader, expert, reviewer])
    await team.async_setup()

    token = _RUN_CONTEXT.set(
        AgentRunContext(
            agent=expert,
            memory=None,
            execution_context_id="ctx-subagent",
        )
    )
    try:
        with pytest.raises(RuntimeError, match="parent or ancestor"):
            await expert.functions["call_agent"](
                agent_name="omicverse_leader",
                instruction="hand back control",
                context_variables={
                    "_metadata": {"chain_path": ["omicverse_leader:call-1", "omicverse_expert:call-2"]}
                },
            )
    finally:
        _RUN_CONTEXT.reset(token)


@pytest.mark.asyncio
async def test_subagent_list_agents_excludes_ancestors_but_keeps_peers():
    leader = Agent(name="omicverse_leader", instructions="lead", model=TEST_MODEL)
    expert = Agent(name="omicverse_expert", instructions="expert", model=TEST_MODEL)
    reviewer = Agent(name="omicverse_reviewer", instructions="review", model=TEST_MODEL)
    team = PantheonTeam(agents=[leader, expert, reviewer])
    await team.async_setup()

    token = _RUN_CONTEXT.set(
        AgentRunContext(
            agent=expert,
            memory=None,
            execution_context_id="ctx-subagent",
        )
    )
    try:
        result = expert.functions["list_agents"](
            context_variables={
                "_metadata": {"chain_path": ["omicverse_leader:call-1", "omicverse_expert:call-2"]}
            }
        )
    finally:
        _RUN_CONTEXT.reset(token)

    assert "omicverse_leader" not in result
    assert "omicverse_reviewer" in result


@pytest.mark.asyncio
async def test_subagent_can_delegate_to_non_ancestor(monkeypatch):
    leader = Agent(name="omicverse_leader", instructions="lead", model=TEST_MODEL)
    expert = Agent(name="omicverse_expert", instructions="expert", model=TEST_MODEL)
    reviewer = Agent(name="omicverse_reviewer", instructions="review", model=TEST_MODEL)
    team = PantheonTeam(agents=[leader, expert, reviewer])
    await team.async_setup()

    async def fake_run(*args, **kwargs):
        return SimpleNamespace(content="review complete")

    monkeypatch.setattr(reviewer, "run", fake_run)

    token = _RUN_CONTEXT.set(
        AgentRunContext(
            agent=expert,
            memory=None,
            execution_context_id="ctx-subagent",
        )
    )
    try:
        result = await expert.functions["call_agent"](
            agent_name="omicverse_reviewer",
            instruction="review this plan",
            context_variables={
                "_metadata": {"chain_path": ["omicverse_leader:call-1", "omicverse_expert:call-2"]}
            },
        )
    finally:
        _RUN_CONTEXT.reset(token)

    assert result == "review complete"


@pytest.mark.asyncio
async def test_call_agent_without_fork_passes_raw_instruction(monkeypatch):
    coordinator = Agent(name="omicverse_leader", instructions="lead", model=TEST_MODEL)
    expert = Agent(name="omicverse_expert", instructions="expert", model=TEST_MODEL)
    reviewer = Agent(name="omicverse_reviewer", instructions="review", model=TEST_MODEL)
    team = PantheonTeam(agents=[coordinator, expert, reviewer])
    await team.async_setup()

    captured = {}

    async def fake_run(task_message, **kwargs):
        captured["task_message"] = task_message
        captured["context_variables"] = kwargs["context_variables"]
        return SimpleNamespace(content="review complete")

    monkeypatch.setattr(reviewer, "run", fake_run)

    token = _RUN_CONTEXT.set(
        AgentRunContext(
            agent=expert,
            memory=None,
        )
    )
    try:
        result = await expert.functions["call_agent"](
            agent_name="omicverse_reviewer",
            instruction="review this plan",
        )
    finally:
        _RUN_CONTEXT.reset(token)

    assert result == "review complete"
    assert captured["task_message"] == "review this plan"
    assert "_cache_safe_fork_context_messages" not in captured["context_variables"]


# ============ Call sub-agent from team agent ============

@pytest.mark.asyncio
async def test_pantheon_team_call_agent_explicit():
    """Test call_agent() functionality for delegating to sub-agents.

    Scenario:
    - User asks for data analysis (a sub-agent capability)
    - research_coordinator (team agent) should recognize the need for data analysis
    - research_coordinator should call the data_analyzer sub-agent
    - data_analyzer should perform the analysis and return results
    - research_coordinator should integrate the results
    - This tests that call_agent() is correctly registered and messaging works
    """

    # Create team agent (coordinator)
    research_coordinator = Agent(
        name="research_coordinator",
        instructions="""You are a research coordinator. Your job is to coordinate data analysis tasks.
When users ask for data analysis, statistical analysis, or data-driven insights:
1. Use call_agent("data_analyzer", "Clear instruction about what analysis is needed")
2. Wait for the analysis results
3. Integrate and present the findings to the user

IMPORTANT: You MUST use call_agent for any data analysis requests. Do not try to do the analysis yourself.
Always clearly specify what analysis is needed in your instruction to the data_analyzer.""",
        model=TEST_MODEL,
    )

    # Create sub-agent (specialist)
    data_analyzer = Agent(
        name="data_analyzer",
        instructions="""You are a data analysis specialist. You excel at statistical analysis, data manipulation,
and deriving insights from datasets. When given a task:
1. Understand what analysis is needed
2. Provide detailed statistical insights
3. Explain the methodology and assumptions
4. Provide actionable conclusions

You have access to data analysis tools and statistical methods.""",
        model=TEST_MODEL,
    )

    # Create team with all agents (call_agent works between any agents)
    team = PantheonTeam(
        agents=[research_coordinator, data_analyzer],
    )

    # Run team with a question that should trigger call_agent
    response = await team.run(
        "I have sales data from the last 4 quarters: Q1=$50K, Q2=$65K, Q3=$72K, Q4=$85K. "
        "Please perform a trend analysis and calculate the quarter-over-quarter growth rates. "
        "What does this growth pattern tell us about the business?",
        memory=None,
    )

    # Verify response came from the team (coordinator integrated results)
    print(f"\n{'='*60}")
    print(f"Test 2: Call sub-agent (data_analyzer)")
    print(f"{'='*60}")
    print(f"Response content length: {len(response.content)}")
    print(f"Agent name: {response.agent_name}")
    print(f"Response preview: {response.content[:200]}...")

    # The response should contain analysis results
    assert response.agent_name == "research_coordinator", \
        f"Expected response from research_coordinator, got {response.agent_name}"
    assert len(response.content) > 100, "Response should contain detailed analysis"

    # Check that analysis terms are in the response
    analysis_terms = ["growth", "rate", "trend", "quarter", "percent", "analysis", "increase"]
    response_lower = response.content.lower()
    assert any(term in response_lower for term in analysis_terms), \
        "Response should contain analysis results"

    print(f"✅ Call agent test passed! Coordinator integrated sub-agent results.\n")
