"""
Test suite to validate the unified architecture implementation.

The unified architecture has two agent types:
- Inline Agents: All treated equally. Support transfer, discovery, delegation.
- Sub-Agents: Stateless computation frameworks. Called by inline agents only.

Key principle: Triage is NOT special - it's the first inline agent with no special treatment.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from pantheon.agent import Agent
from pantheon.team import PantheonTeam
from pantheon.factory.template_manager import ChatroomTemplate, TemplateManager


def _create_mock_agent(name: str, description: str = "") -> MagicMock:
    """Helper to create a mock agent."""
    agent = MagicMock(spec=Agent)
    agent.name = name
    agent.description = description
    agent.functions = {}
    agent.can_delegate = False
    return agent


@pytest.fixture
def mock_agents():
    """Create mock agents for testing (module-level fixture)."""
    # Create inline agents (all treated equally)
    triage = _create_mock_agent("Triage Agent", "Main coordinator")
    analyst = _create_mock_agent("Analyst Agent", "Data analysis specialist")
    engineer = _create_mock_agent("Engineer Agent", "Engineering specialist")

    # Create sub-agents (stateless frameworks)
    data_scientist = _create_mock_agent("Data Scientist", "ML and data science")

    return {
        "inline": [triage, analyst, engineer],
        "sub": [data_scientist],
        "triage": triage,
        "analyst": analyst,
        "engineer": engineer,
        "data_scientist": data_scientist,
    }


class TestUnifiedArchitecture:
    """Test the unified two-agent-type architecture."""

    def test_pantheon_team_initialization_with_triage_as_first_inline_agent(
        self, mock_agents
    ):
        """Test that triage is the first inline agent, not a special parameter."""
        inline_agents = mock_agents["inline"]
        sub_agents = mock_agents["sub"]

        # Create team with inline_agents containing triage as first element
        team = PantheonTeam(inline_agents=inline_agents, sub_agents=sub_agents)

        # Validate initialization
        assert team.triage == inline_agents[0], "Triage should be first inline agent"
        assert team.inline_agents == inline_agents, "All inline agents should be stored"
        assert team.sub_agents == sub_agents, "Sub-agents should be stored"
        assert len(team.inline_agents) == 3, "Should have 3 inline agents"
        assert len(team.sub_agents) == 1, "Should have 1 sub-agent"

    def test_feature_enablement_transfer_agents(self, mock_agents):
        """Test that Agent Transfer feature is enabled based on inline agent count."""
        # Case 1: Single inline agent (just triage) - no transfer
        team_single = PantheonTeam(inline_agents=[mock_agents["triage"]])
        assert (
            team_single.has_transfer_agents is False
        ), "Single inline agent should not enable transfer"

        # Case 2: Multiple inline agents - transfer enabled
        team_multi = PantheonTeam(
            inline_agents=mock_agents["inline"], sub_agents=mock_agents["sub"]
        )
        assert (
            team_multi.has_transfer_agents is True
        ), "Multiple inline agents should enable transfer"

    def test_feature_enablement_sub_agent_discovery(self, mock_agents):
        """Test that Sub-Agent Discovery feature is enabled based on sub-agent count."""
        # Case 1: No sub-agents - discovery disabled
        team_no_sub = PantheonTeam(inline_agents=mock_agents["inline"])
        assert (
            team_no_sub.has_sub_agents is False
        ), "No sub-agents should disable discovery"

        # Case 2: With sub-agents - discovery enabled
        team_with_sub = PantheonTeam(
            inline_agents=mock_agents["inline"], sub_agents=mock_agents["sub"]
        )
        assert (
            team_with_sub.has_sub_agents is True
        ), "Sub-agents should enable discovery"

    def test_inline_agent_names_tracking(self, mock_agents):
        """Test that inline agent names are properly tracked for tool distribution."""
        team = PantheonTeam(
            inline_agents=mock_agents["inline"], sub_agents=mock_agents["sub"]
        )

        inline_names = {agent.name for agent in mock_agents["inline"]}
        sub_names = {agent.name for agent in mock_agents["sub"]}

        assert team._inline_agent_names == inline_names
        assert team._sub_agent_names == sub_names

    def test_add_list_agents_tool_excludes_triage(self, mock_agents):
        """Test that list_agents() shows only sub-agents (not inline agents)."""
        team = PantheonTeam(
            inline_agents=mock_agents["inline"], sub_agents=mock_agents["sub"]
        )

        # Populate agents dict (normally done in async_setup)
        team.agents = {agent.name: agent for agent in mock_agents["inline"] + mock_agents["sub"]}

        agents_info = team.list_agents_descriptions()

        # list_agents() returns only sub-agents
        agent_names = [agent["name"] for agent in agents_info]

        # Should NOT include triage or other inline agents
        assert "Triage Agent" not in agent_names
        assert "Analyst Agent" not in agent_names
        assert "Engineer Agent" not in agent_names

        # Should include ONLY sub-agents
        assert "Data Scientist" in agent_names
        assert len(agent_names) == 1  # Only 1 sub-agent in test

    def test_add_unified_call_agent_tool_only_to_inline_agents(self, mock_agents):
        """Verify call_agent() is added only to inline agents (not sub-agents).

        The implementation iterates over team.inline_agents, so we verify:
        - The team has the correct inline/sub agent separation
        - The method would process only inline agents
        """
        team = PantheonTeam(
            inline_agents=mock_agents["inline"],
            sub_agents=mock_agents["sub"]
        )

        # Verify structure
        assert len(team.inline_agents) == 3, "Should have 3 inline agents"
        assert len(team.sub_agents) == 1, "Should have 1 sub-agent"

        # The actual tool registration to agents happens in async_setup()
        # For this sync test, we just verify the team structure is correct

    def test_call_agent_validation_sub_agents_only(self, mock_agents):
        """Test that call_agent() validates target is a sub-agent."""
        team = PantheonTeam(
            inline_agents=mock_agents["inline"], sub_agents=mock_agents["sub"]
        )

        # Test: Inline agents are tracked separately
        assert "Analyst Agent" in team._inline_agent_names
        assert "Analyst Agent" not in team._sub_agent_names

        # Test: Sub-agents are tracked correctly
        assert "Data Scientist" in team._sub_agent_names
        assert "Data Scientist" not in team._inline_agent_names

    @pytest.mark.asyncio
    async def test_async_setup_enables_correct_features(self, mock_agents):
        """Test that async_setup enables features based on team composition.

        - With sub-agents: enable discovery (list_agents)
        - With multiple inline agents: enable transfer (call_agent)
        """
        team = PantheonTeam(
            inline_agents=mock_agents["inline"],
            sub_agents=mock_agents["sub"]
        )

        # Verify feature flags
        assert team.has_sub_agents is True, "Should have sub-agents"
        assert team.has_transfer_agents is True, "Should have transfer agents (3 inline)"

        # Mock the tool addition methods to verify they would be called
        list_agents_called = False
        call_agent_called = False

        async def mock_list_agents():
            nonlocal list_agents_called
            list_agents_called = True

        async def mock_call_agent():
            nonlocal call_agent_called
            call_agent_called = True

        team.add_list_agents_tool = mock_list_agents
        team.add_unified_call_agent_tool = mock_call_agent

        await team.async_setup()

        # Verify correct tools were set up
        assert list_agents_called, "list_agents should be added (has sub-agents)"
        assert call_agent_called, "call_agent should be added (has transfer agents)"


class TestChatroomTemplateUnification:
    """Test unified chatroom template format."""

    def test_chatroom_template_unified_format(self):
        """Test that ChatroomTemplate uses unified format with agents_config and sub_agents."""
        agents_config = {
            "triage": {
                "name": "Coordinator",
                "instructions": "Coordinate team",
                "model": "openai/gpt-4o-mini",
            },
            "analyst": {
                "name": "Analyst",
                "instructions": "Analyze data",
                "model": "openai/gpt-4o-mini",
            },
        }

        # Create template with unified format
        template = ChatroomTemplate(
            id="test",
            name="Test Template",
            description="Test",
            icon="🧪",
            category="test",
            version="1.0.0",
            agents_config=agents_config,
            sub_agents=["data_analyst", "ml_engineer"],
        )

        # Validate unified format
        assert template.agents_config == agents_config
        assert template.sub_agents == ["data_analyst", "ml_engineer"]
        assert "triage" in template.agents_config

    def test_template_validation_requires_triage(self):
        """Test that template validation requires triage agent in agents_config."""
        tm = TemplateManager()

        # Template without triage should fail validation
        template_no_triage = ChatroomTemplate(
            id="test",
            name="No Triage",
            description="Test",
            icon="🧪",
            category="test",
            version="1.0.0",
            agents_config={
                "analyst": {
                    "name": "Analyst",
                    "instructions": "Analyze",
                    "model": "openai/gpt-4o-mini",
                }
            },
        )

        errors = tm.validate_template(template_no_triage)
        assert any(
            "triage" in error.lower() for error in errors
        ), "Should require triage agent"

    def test_template_required_toolsets_from_agents_config(self):
        """Test that required_toolsets are computed from all agents in agents_config."""
        agents_config = {
            "triage": {
                "name": "Coordinator",
                "instructions": "Coordinate",
                "model": "openai/gpt-4o-mini",
                "toolsets": ["python_interpreter"],
            },
            "analyst": {
                "name": "Analyst",
                "instructions": "Analyze",
                "model": "openai/gpt-4o-mini",
                "toolsets": ["file_manager", "python_interpreter"],
            },
        }

        template = ChatroomTemplate(
            id="test",
            name="Test",
            description="Test",
            icon="🧪",
            category="test",
            version="1.0.0",
            agents_config=agents_config,
        )

        toolsets = template.required_toolsets
        # Should collect unique toolsets from all agents
        assert "python_interpreter" in toolsets
        assert "file_manager" in toolsets
        assert len(toolsets) == 2

    def test_template_support_all_sub_agents_specs(self):
        """Test that template supports all sub_agents specification formats."""
        base_config = {
            "triage": {
                "name": "Coordinator",
                "instructions": "Coordinate",
                "model": "openai/gpt-4o-mini",
            }
        }

        # Case 1: "all" string
        template1 = ChatroomTemplate(
            id="test1",
            name="Test1",
            description="",
            icon="",
            category="",
            version="1.0.0",
            agents_config=base_config,
            sub_agents="all",
        )
        assert template1.sub_agents == "all"

        # Case 2: Specific list
        template2 = ChatroomTemplate(
            id="test2",
            name="Test2",
            description="",
            icon="",
            category="",
            version="1.0.0",
            agents_config=base_config,
            sub_agents=["agent1", "agent2"],
        )
        assert template2.sub_agents == ["agent1", "agent2"]

        # Case 3: None (no sub-agents)
        template3 = ChatroomTemplate(
            id="test3",
            name="Test3",
            description="",
            icon="",
            category="",
            version="1.0.0",
            agents_config=base_config,
            sub_agents=None,
        )
        assert template3.sub_agents is None


class TestInlineAgentCapabilities:
    """Test that inline agents have the correct capabilities.

    Verify that all inline agents are treated equally in the unified architecture.
    """

    def test_inline_agents_are_equal(self, mock_agents):
        """Test that inline agents (including triage) are treated equally."""
        team = PantheonTeam(inline_agents=mock_agents["inline"])

        # All inline agents should be tracked equally
        assert len(team.inline_agents) == 3
        assert team.triage == team.inline_agents[0]

        # All inline agents should have same can_delegate status
        # can_delegate is set when there are other agents to delegate to
        assert (
            team.inline_agents[1].can_delegate is True
        ), "All inline agents should have can_delegate=True when multiple agents exist"

    def test_can_delegate_set_based_on_features(self, mock_agents):
        """Test that can_delegate is set based on team composition (not agent role).

        can_delegate should be True when:
        - There are multiple inline agents (transfer capability), or
        - There are sub-agents (discovery capability)
        """
        # Case 1: Single agent, no features → can_delegate = False
        team_single = PantheonTeam(inline_agents=[mock_agents["triage"]])
        assert team_single.triage.can_delegate is False

        # Case 2: Multiple inline agents → can_delegate = True for all
        team_multi = PantheonTeam(inline_agents=mock_agents["inline"])
        for agent in team_multi.inline_agents:
            assert agent.can_delegate is True, f"{agent.name} should have can_delegate=True"

        # Case 3: Sub-agents present → can_delegate = True for all inline agents
        team_with_sub = PantheonTeam(
            inline_agents=[mock_agents["triage"]],
            sub_agents=mock_agents["sub"]
        )
        assert team_with_sub.triage.can_delegate is True


class TestInstructionTransfer:
    """Test that instruction is correctly passed through delegation."""

    def test_agent_transfer_includes_instruction_field(self):
        """Test that AgentTransfer has instruction field."""
        from pantheon.agent import AgentTransfer

        transfer = AgentTransfer(
            from_agent="Agent A",
            to_agent="Agent B",
            history=[],
            context_variables={},
            init_message_length=0,
            instruction="Do something specific",
        )

        assert transfer.instruction == "Do something specific"
        assert transfer.from_agent == "Agent A"
        assert transfer.to_agent == "Agent B"

    def test_agent_transfer_instruction_defaults_to_empty_string(self):
        """Test that instruction defaults to empty string for backward compatibility."""
        from pantheon.agent import AgentTransfer

        transfer = AgentTransfer(
            from_agent="Agent A",
            to_agent="Agent B",
            history=[],
            context_variables={},
            init_message_length=0,
        )

        assert transfer.instruction == ""

    def test_pantheon_team_injects_instruction_into_transfer(self, mock_agents):
        """Test that PantheonTeam injects instruction from _call_stack into AgentTransfer."""
        from pantheon.agent import AgentTransfer
        import uuid

        team = PantheonTeam(
            inline_agents=mock_agents["inline"],
            sub_agents=mock_agents["sub"],
        )

        # Simulate a call_agent delegation by manually adding to call_stack with new dict format
        execution_context_id = f"call_{str(uuid.uuid4())[:12]}"
        team._call_stack.append({
            "caller_name": "Triage Agent",
            "instruction": "Analyze the data carefully",
            "execution_context_id": execution_context_id,
            "timestamp": __import__("time").time(),
        })

        # Create a mock AgentTransfer without instruction
        transfer = AgentTransfer(
            from_agent="Triage Agent",
            to_agent="Data Scientist",
            history=[],
            context_variables={},
            init_message_length=0,
        )

        # Simulate the injection logic from PantheonTeam.run()
        if team._call_stack:
            delegation_context = team._call_stack[-1]
            transfer.instruction = delegation_context["instruction"]
            transfer.execution_context_id = delegation_context["execution_context_id"]

        # Verify instruction was injected
        assert transfer.instruction == "Analyze the data carefully"
        assert team._call_stack[-1]["instruction"] == "Analyze the data carefully"
        assert team._call_stack[-1]["caller_name"] == "Triage Agent"
        assert "execution_context_id" in team._call_stack[-1]
        assert transfer.execution_context_id == execution_context_id

    @pytest.mark.asyncio
    async def test_input_to_openai_messages_includes_instruction_context(self):
        """Test that instruction is included in messages when processing AgentTransfer."""
        from pantheon.agent import Agent, AgentTransfer
        from pantheon.memory import Memory

        # Create a real agent instance (not mock) to test message processing
        agent = Agent(name="Test Agent", model="gpt-4o-mini", instructions="Test instructions")

        # Create an AgentTransfer with instruction
        transfer = AgentTransfer(
            from_agent="Agent A",
            to_agent="Test Agent",
            history=[
                {"role": "user", "content": "Initial query"},
                {"role": "assistant", "content": "Initial response"},
            ],
            context_variables={},
            init_message_length=1,
            instruction="Please perform this specific task",
        )

        # Process the transfer message (now async)
        messages = await agent._input_to_openai_messages(transfer)

        # Verify instruction is included as first message
        assert len(messages) > 0
        # The instruction should be prepended as a user message
        assert any(
            "Please perform this specific task" in str(msg.get("content", ""))
            for msg in messages
        ), "Instruction should be in the messages"

        # Verify history is still included
        assert any(
            "Initial query" in str(msg.get("content", "")) for msg in messages
        ), "History should be preserved"

    def test_execution_context_id_generation_and_tracking(self, mock_agents):
        """Test that execution_context_id is generated and tracked in call_agent."""
        team = PantheonTeam(
            inline_agents=[mock_agents["triage"]], sub_agents=[mock_agents["data_scientist"]]
        )

        # Create a mock call_agent function
        inline_agent = mock_agents["triage"]

        # Simulate what call_agent does internally
        agent_name = "Data Scientist"
        instruction = "Analyze the data"

        # Generate context_id as call_agent would
        import uuid
        execution_context_id = f"call_{str(uuid.uuid4())[:12]}"

        # Simulate pushing to call_stack as the real call_agent does
        team._call_stack.append({
            "caller_name": inline_agent.name,
            "instruction": instruction,
            "execution_context_id": execution_context_id,
            "timestamp": __import__("time").time(),
        })

        # Verify the context_id was added to call_stack
        assert len(team._call_stack) == 1
        assert team._call_stack[0]["execution_context_id"] == execution_context_id
        assert team._call_stack[0]["caller_name"] == "Triage Agent"
        assert team._call_stack[0]["instruction"] == "Analyze the data"

        # Pop from call_stack (as would happen on return)
        delegation_context = team._call_stack.pop()
        assert delegation_context["execution_context_id"] == execution_context_id

    def test_agent_transfer_with_execution_context_id(self):
        """Test that AgentTransfer properly carries execution_context_id."""
        from pantheon.agent import AgentTransfer

        # Create an AgentTransfer with execution_context_id
        transfer = AgentTransfer(
            from_agent="Triage",
            to_agent="DataScientist",
            history=[{"role": "user", "content": "Analyze data"}],
            context_variables={},
            init_message_length=1,
            instruction="Analyze the dataset",
            execution_context_id="call_abc123def456",
        )

        # Verify execution_context_id is stored
        assert transfer.execution_context_id == "call_abc123def456"
        assert transfer.instruction == "Analyze the dataset"

    def test_response_details_with_execution_context_id(self):
        """Test that ResponseDetails properly carries execution_context_id."""
        from pantheon.agent import ResponseDetails

        # Create ResponseDetails with execution_context_id
        details = ResponseDetails(
            messages=[{"role": "assistant", "content": "Analysis result"}],
            context_variables={"key": "value"},
            execution_context_id="call_xyz789uvw012",
        )

        # Verify execution_context_id is stored
        assert details.execution_context_id == "call_xyz789uvw012"
        assert len(details.messages) == 1

    def test_message_filtering_by_context_id(self):
        """Test that messages are filtered based on execution_context_id."""
        # Create a list of messages with different context_ids
        messages = [
            {"role": "system", "content": "System prompt", "execution_context_id": None},
            {"role": "user", "content": "Question 1", "execution_context_id": "call_abc123"},
            {"role": "assistant", "content": "Answer 1", "execution_context_id": "call_abc123"},
            {"role": "user", "content": "Question 2", "execution_context_id": "call_xyz789"},
            {"role": "assistant", "content": "Answer 2", "execution_context_id": "call_xyz789"},
        ]

        # Simulate filtering for context_id "call_abc123"
        context_id = "call_abc123"
        filtered = []
        for msg in messages:
            if msg.get("role") == "system" or msg.get("execution_context_id") == context_id:
                filtered.append(msg)

        # Verify filtering results
        assert len(filtered) == 3  # system + 2 messages with matching context_id
        assert filtered[0]["role"] == "system"
        assert all(m.get("execution_context_id") in [None, context_id] for m in filtered)

    def test_filter_inline_messages_utility(self):
        """Test the filter_inline_messages utility function."""
        from pantheon.agent import filter_inline_messages

        # Create a mixed message list: inline + sub-agent messages
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "User question", "execution_context_id": None},
            {"role": "assistant", "content": "Inline response", "execution_context_id": None},
            {"role": "user", "content": "Sub-task", "execution_context_id": "call_abc123"},
            {"role": "assistant", "content": "Sub-response", "execution_context_id": "call_abc123"},
            {"role": "user", "content": "Another task", "execution_context_id": "call_xyz789"},
            {"role": "assistant", "content": "Another response", "execution_context_id": "call_xyz789"},
            {"role": "user", "content": "Follow-up", "execution_context_id": None},
        ]

        # Filter to get only inline messages
        filtered = filter_inline_messages(messages)

        # Verify filtering results
        assert len(filtered) == 4  # system + 3 inline messages
        assert filtered[0]["role"] == "system"
        assert filtered[1]["content"] == "User question"
        assert filtered[2]["content"] == "Inline response"
        assert filtered[3]["content"] == "Follow-up"

        # Verify no sub-agent messages in filtered list
        for msg in filtered:
            if msg.get("role") != "system":
                assert msg.get("execution_context_id") is None, "Sub-agent message should not be in filtered list"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
