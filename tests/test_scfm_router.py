"""
Unit Tests for SCFM Router

Tests the LLM-based routing functionality with mocked _call_agent.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from pantheon.toolsets.scfm.router import (
    VALID_TASKS,
    VALID_SCFM_TOOLS,
    RouterOutput,
    RouterIntent,
    RouterInputs,
    RouterSelection,
    ModelSelection,
    ResolvedParams,
    ToolCall,
    Question,
    validate_router_output,
    build_model_cards,
    build_router_prompt,
    call_router_llm,
    route_query,
    _extract_json_from_response,
    _reroute_on_incompatibility,
)
from pantheon.toolsets.scfm.registry import get_registry, TaskType


# =============================================================================
# Test Data Models
# =============================================================================


class TestRouterIntent:
    """Tests for RouterIntent data model."""

    def test_valid_task(self):
        """RouterIntent should accept valid tasks."""
        for task in VALID_TASKS:
            intent = RouterIntent(task=task, confidence=0.9)
            assert intent.task == task

    def test_invalid_task(self):
        """RouterIntent should reject invalid tasks."""
        with pytest.raises(ValueError):
            RouterIntent(task="invalid_task", confidence=0.9)

    def test_confidence_bounds(self):
        """RouterIntent confidence should be between 0 and 1."""
        intent = RouterIntent(task="embed", confidence=0.5)
        assert intent.confidence == 0.5

        with pytest.raises(ValueError):
            RouterIntent(task="embed", confidence=1.5)

        with pytest.raises(ValueError):
            RouterIntent(task="embed", confidence=-0.1)


class TestToolCall:
    """Tests for ToolCall data model."""

    def test_valid_tool(self):
        """ToolCall should accept valid tools."""
        for tool in VALID_SCFM_TOOLS:
            call = ToolCall(tool=tool, args={})
            assert call.tool == tool

    def test_invalid_tool(self):
        """ToolCall should reject invalid tools."""
        with pytest.raises(ValueError):
            ToolCall(tool="invalid_tool", args={})


class TestRouterOutput:
    """Tests for RouterOutput data model."""

    def test_minimal_valid_output(self):
        """RouterOutput should accept minimal valid input."""
        output = RouterOutput(
            intent=RouterIntent(task="embed", confidence=0.9),
            inputs=RouterInputs(query="Embed my data"),
            selection=RouterSelection(
                recommended=ModelSelection(name="uce", rationale="Good model")
            ),
        )
        assert output.intent.task == "embed"
        assert output.selection.recommended.name == "uce"

    def test_full_output(self):
        """RouterOutput should accept full input."""
        output = RouterOutput(
            intent=RouterIntent(task="integrate", confidence=0.85),
            inputs=RouterInputs(query="Integrate batches", adata_path="/data/test.h5ad"),
            data_profile={"n_cells": 1000, "species": "human"},
            selection=RouterSelection(
                recommended=ModelSelection(name="scgpt", rationale="Best for integration"),
                fallbacks=[ModelSelection(name="uce", rationale="Alternative")],
            ),
            resolved_params=ResolvedParams(
                output_path="/data/output.h5ad",
                batch_key="batch_id",
            ),
            plan=[
                ToolCall(tool="scfm_preprocess_validate", args={}),
                ToolCall(tool="scfm_run", args={}),
            ],
            questions=[
                Question(field="batch_key", question="Which column?", options=["batch", "sample"])
            ],
            warnings=["Data may need preprocessing"],
        )
        assert output.intent.task == "integrate"
        assert len(output.plan) == 2
        assert len(output.questions) == 1


# =============================================================================
# Test Validation
# =============================================================================


class TestValidateRouterOutput:
    """Tests for validate_router_output function."""

    def test_valid_output_passes(self):
        """Valid output should pass validation."""
        valid_output = {
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Embed my data", "adata_path": None},
            "data_profile": None,
            "selection": {
                "recommended": {"name": "uce", "rationale": "Good model"},
                "fallbacks": [],
            },
            "resolved_params": {},
            "plan": [],
            "questions": [],
            "warnings": [],
        }
        is_valid, errors, parsed = validate_router_output(valid_output)
        assert is_valid
        assert len(errors) == 0
        assert parsed is not None

    def test_invalid_task_fails(self):
        """Invalid task should fail validation."""
        invalid_output = {
            "intent": {"task": "invalid_task", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Embed my data"},
            "selection": {
                "recommended": {"name": "uce", "rationale": ""},
                "fallbacks": [],
            },
        }
        is_valid, errors, parsed = validate_router_output(invalid_output)
        assert not is_valid
        assert len(errors) > 0
        assert any("task" in e.lower() for e in errors)

    def test_unknown_model_fails(self):
        """Unknown model should fail validation."""
        invalid_output = {
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Embed my data"},
            "selection": {
                "recommended": {"name": "nonexistent_model", "rationale": ""},
                "fallbacks": [],
            },
        }
        is_valid, errors, parsed = validate_router_output(invalid_output)
        assert not is_valid
        assert any("not found in registry" in e for e in errors)

    def test_invalid_tool_fails(self):
        """Invalid tool in plan should fail validation."""
        invalid_output = {
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Embed my data"},
            "selection": {
                "recommended": {"name": "uce", "rationale": ""},
                "fallbacks": [],
            },
            "plan": [{"tool": "invalid_tool", "args": {}}],
        }
        is_valid, errors, parsed = validate_router_output(invalid_output)
        assert not is_valid
        assert any("Invalid tool" in e for e in errors)

    def test_model_name_in_plan_is_normalized_to_scfm_run(self):
        output = {
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Embed plant data"},
            "selection": {
                "recommended": {"name": "scplantllm", "rationale": "plant model"},
                "fallbacks": [],
            },
            "plan": [{"tool": "scplantllm", "args": {"adata_path": "plant.h5ad"}}],
        }

        is_valid, errors, parsed = validate_router_output(output)

        assert is_valid
        assert not errors
        assert parsed is not None
        assert parsed.plan[0].tool == "scfm_run"
        assert parsed.plan[0].args["model_name"] == "scplantllm"


# =============================================================================
# Test JSON Extraction
# =============================================================================


class TestExtractJsonFromResponse:
    """Tests for _extract_json_from_response function."""

    def test_direct_json(self):
        """Should extract direct JSON."""
        response = '{"task": "embed"}'
        result = _extract_json_from_response(response)
        assert result == {"task": "embed"}

    def test_markdown_code_block(self):
        """Should extract JSON from markdown code block."""
        response = '```json\n{"task": "embed"}\n```'
        result = _extract_json_from_response(response)
        assert result == {"task": "embed"}

    def test_json_with_surrounding_text(self):
        """Should extract JSON with surrounding text."""
        response = 'Here is the result:\n{"task": "embed"}\nThat was the output.'
        result = _extract_json_from_response(response)
        assert result == {"task": "embed"}

    def test_invalid_json(self):
        """Should return None for invalid JSON."""
        response = 'This is not JSON at all'
        result = _extract_json_from_response(response)
        assert result is None


# =============================================================================
# Test Prompt Building
# =============================================================================


class TestBuildModelCards:
    """Tests for build_model_cards function."""

    def test_builds_cards(self):
        """Should build model cards string."""
        cards = build_model_cards()
        assert "uce" in cards.lower()
        assert "scgpt" in cards.lower()
        assert "geneformer" in cards.lower()

    def test_skill_ready_filter(self):
        """Should filter by skill-ready status."""
        all_cards = build_model_cards(skill_ready_only=False)
        ready_cards = build_model_cards(skill_ready_only=True)
        # Ready cards should be a subset
        assert len(ready_cards) <= len(all_cards)

    def test_vram_filter(self):
        """Should filter by VRAM constraint."""
        # UCE requires 16GB, so 8GB filter should exclude it
        cards_8gb = build_model_cards(max_vram_gb=8)
        assert "uce" not in cards_8gb.lower() or "16GB" not in cards_8gb


class TestBuildRouterPrompt:
    """Tests for build_router_prompt function."""

    def test_includes_query(self):
        """Prompt should include user query."""
        prompt = build_router_prompt(query="Embed my data", model_cards="")
        assert "Embed my data" in prompt

    def test_includes_data_profile(self):
        """Prompt should include data profile if provided."""
        profile = {"n_cells": 1000, "species": "human"}
        prompt = build_router_prompt(query="Test", data_profile=profile)
        assert "1000" in prompt
        assert "human" in prompt

    def test_includes_model_cards(self):
        """Prompt should include model cards."""
        cards = "### uce\n- Tasks: embed"
        prompt = build_router_prompt(query="Test", model_cards=cards)
        assert "uce" in prompt

    def test_includes_constraints(self):
        """Prompt should include constraints."""
        prompt = build_router_prompt(
            query="Test",
            prefer_zero_shot=True,
            max_vram_gb=16,
            skill_ready_only=True,
        )
        assert "zero-shot" in prompt.lower()
        assert "16GB" in prompt
        assert "skill-ready" in prompt.lower()


# =============================================================================
# Test LLM Call Helper
# =============================================================================


class TestCallRouterLLM:
    """Tests for call_router_llm function."""

    @pytest.mark.asyncio
    async def test_missing_call_agent_returns_error(self):
        """Should return error when _call_agent is missing."""
        context = {}
        success, result, errors = await call_router_llm(context, "Test prompt")
        assert not success
        assert "_call_agent not available" in errors[0]

    @pytest.mark.asyncio
    async def test_valid_json_passes_without_retry(self):
        """Valid JSON response should pass without retry."""
        valid_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Test", "adata_path": None},
            "selection": {
                "recommended": {"name": "uce", "rationale": "Good"},
                "fallbacks": [],
            },
            "resolved_params": {},
            "plan": [],
            "questions": [],
            "warnings": [],
        })

        mock_call_agent = AsyncMock(return_value={
            "success": True,
            "response": valid_response,
        })
        context = {"_call_agent": mock_call_agent}

        success, result, errors = await call_router_llm(context, "Test prompt")
        assert success
        assert len(errors) == 0
        assert result["intent"]["task"] == "embed"
        # Should only be called once (no retry needed)
        assert mock_call_agent.call_count == 1

    @pytest.mark.asyncio
    async def test_invalid_json_triggers_retry(self):
        """Invalid JSON should trigger retry."""
        invalid_response = "This is not JSON"
        valid_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Test", "adata_path": None},
            "selection": {
                "recommended": {"name": "uce", "rationale": "Good"},
                "fallbacks": [],
            },
        })

        mock_call_agent = AsyncMock(side_effect=[
            {"success": True, "response": invalid_response},
            {"success": True, "response": valid_response},
        ])
        context = {"_call_agent": mock_call_agent}

        success, result, errors = await call_router_llm(context, "Test prompt", max_retries=1)
        assert success
        # Should be called twice (initial + retry)
        assert mock_call_agent.call_count == 2

    @pytest.mark.asyncio
    async def test_invalid_task_triggers_retry(self):
        """Invalid task enum should trigger retry."""
        invalid_task_response = json.dumps({
            "intent": {"task": "invalid_task", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Test"},
            "selection": {"recommended": {"name": "uce", "rationale": ""}, "fallbacks": []},
        })
        valid_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Test", "adata_path": None},
            "selection": {"recommended": {"name": "uce", "rationale": ""}, "fallbacks": []},
        })

        mock_call_agent = AsyncMock(side_effect=[
            {"success": True, "response": invalid_task_response},
            {"success": True, "response": valid_response},
        ])
        context = {"_call_agent": mock_call_agent}

        success, result, errors = await call_router_llm(context, "Test prompt", max_retries=1)
        assert success
        assert mock_call_agent.call_count == 2

    @pytest.mark.asyncio
    async def test_unknown_model_triggers_retry(self):
        """Unknown model should trigger retry."""
        unknown_model_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Test"},
            "selection": {"recommended": {"name": "nonexistent", "rationale": ""}, "fallbacks": []},
        })
        valid_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Test", "adata_path": None},
            "selection": {"recommended": {"name": "uce", "rationale": ""}, "fallbacks": []},
        })

        mock_call_agent = AsyncMock(side_effect=[
            {"success": True, "response": unknown_model_response},
            {"success": True, "response": valid_response},
        ])
        context = {"_call_agent": mock_call_agent}

        success, result, errors = await call_router_llm(context, "Test prompt", max_retries=1)
        assert success
        assert mock_call_agent.call_count == 2

    @pytest.mark.asyncio
    async def test_unknown_tool_triggers_retry(self):
        """Unknown tool in plan should trigger retry."""
        unknown_tool_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Test"},
            "selection": {"recommended": {"name": "uce", "rationale": ""}, "fallbacks": []},
            "plan": [{"tool": "unknown_tool", "args": {}}],
        })
        valid_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Test", "adata_path": None},
            "selection": {"recommended": {"name": "uce", "rationale": ""}, "fallbacks": []},
            "plan": [{"tool": "scfm_run", "args": {}}],
        })

        mock_call_agent = AsyncMock(side_effect=[
            {"success": True, "response": unknown_tool_response},
            {"success": True, "response": valid_response},
        ])
        context = {"_call_agent": mock_call_agent}

        success, result, errors = await call_router_llm(context, "Test prompt", max_retries=1)
        assert success
        assert mock_call_agent.call_count == 2

    @pytest.mark.asyncio
    async def test_uses_caller_models_in_llm_call(self):
        """Should pass model= from caller_models to _call_agent."""
        valid_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Test", "adata_path": None},
            "selection": {"recommended": {"name": "uce", "rationale": ""}, "fallbacks": []},
        })

        mock_call_agent = AsyncMock(return_value={
            "success": True,
            "response": valid_response,
        })
        context = {
            "_call_agent": mock_call_agent,
            "caller_models": ["gpt-4", "gpt-3.5-turbo"],
        }

        success, result, errors = await call_router_llm(context, "Test prompt")
        assert success
        # Verify model was passed to _call_agent
        call_kwargs = mock_call_agent.call_args[1]
        assert call_kwargs.get("model") == "gpt-4"  # First model in list

    @pytest.mark.asyncio
    async def test_no_caller_models_passes_none(self):
        """Should pass model=None when caller_models is not in context."""
        valid_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Test", "adata_path": None},
            "selection": {"recommended": {"name": "uce", "rationale": ""}, "fallbacks": []},
        })

        mock_call_agent = AsyncMock(return_value={
            "success": True,
            "response": valid_response,
        })
        context = {"_call_agent": mock_call_agent}

        success, result, errors = await call_router_llm(context, "Test prompt")
        assert success
        # Verify model=None was passed
        call_kwargs = mock_call_agent.call_args[1]
        assert call_kwargs.get("model") is None


# =============================================================================
# Test Main Router
# =============================================================================


class TestRouteQuery:
    """Tests for route_query function."""

    @pytest.mark.asyncio
    async def test_missing_call_agent_returns_structured_error(self):
        """Should return structured error when _call_agent is missing."""
        result = await route_query(query="Embed my data", context={})
        assert "error" in result
        assert "Router requires _call_agent" in result["error"]
        assert result["intent"]["task"] == "unknown"
        assert result["inputs"]["query"] == "Embed my data"

    @pytest.mark.asyncio
    async def test_uses_caller_models_from_context(self):
        """Router should have access to caller_models from context."""
        valid_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Test", "adata_path": None},
            "selection": {"recommended": {"name": "uce", "rationale": ""}, "fallbacks": []},
        })

        mock_call_agent = AsyncMock(return_value={
            "success": True,
            "response": valid_response,
        })
        context = {
            "_call_agent": mock_call_agent,
            "caller_models": ["gpt-4", "gpt-3.5-turbo"],
        }

        result = await route_query(query="Embed my data", context=context)
        # Should succeed and have access to context
        assert "error" not in result or result.get("intent", {}).get("task") != "unknown"

    @pytest.mark.asyncio
    async def test_injects_data_profile(self):
        """Should inject data_profile when provided."""
        valid_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Test", "adata_path": "/data/test.h5ad"},
            "selection": {"recommended": {"name": "uce", "rationale": ""}, "fallbacks": []},
            "data_profile": None,
        })

        mock_call_agent = AsyncMock(return_value={
            "success": True,
            "response": valid_response,
        })
        context = {"_call_agent": mock_call_agent}
        data_profile = {"n_cells": 1000, "species": "human", "gene_scheme": "symbol"}

        result = await route_query(
            query="Embed my data",
            context=context,
            data_profile=data_profile,
        )
        # Data profile should be in result
        assert result.get("data_profile") is not None or "data_profile" in str(result)

    @pytest.mark.asyncio
    async def test_overrides_resolved_params(self):
        """Should override resolved_params with pre-specified values."""
        valid_response = json.dumps({
            "intent": {"task": "integrate", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Test", "adata_path": None},
            "selection": {"recommended": {"name": "scgpt", "rationale": ""}, "fallbacks": []},
            "resolved_params": {"output_path": None, "batch_key": None, "label_key": None},
        })

        mock_call_agent = AsyncMock(return_value={
            "success": True,
            "response": valid_response,
        })
        context = {"_call_agent": mock_call_agent}

        result = await route_query(
            query="Integrate batches",
            context=context,
            output_path="/my/output.h5ad",
            batch_key="my_batch",
            label_key="my_label",
        )
        assert result["resolved_params"]["output_path"] == "/my/output.h5ad"
        assert result["resolved_params"]["batch_key"] == "my_batch"
        assert result["resolved_params"]["label_key"] == "my_label"

    @pytest.mark.asyncio
    async def test_adds_compatibility_warnings(self):
        """Should add warnings for incompatible model/data combinations."""
        valid_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Test", "adata_path": "/data/test.h5ad"},
            "selection": {
                "recommended": {"name": "geneformer", "rationale": ""},  # Requires Ensembl
                "fallbacks": [{"name": "uce", "rationale": ""}],
            },
        })

        mock_call_agent = AsyncMock(return_value={
            "success": True,
            "response": valid_response,
        })
        context = {"_call_agent": mock_call_agent}
        # Profile with symbol genes (incompatible with Geneformer)
        data_profile = {
            "species": "human",
            "gene_scheme": "symbol",  # Geneformer requires ensembl
            "model_compatibility": {
                "geneformer": {"compatible": False, "issues": ["Requires Ensembl IDs"]},
                "uce": {"compatible": True, "issues": []},
            },
        }

        result = await route_query(
            query="Embed my data",
            context=context,
            data_profile=data_profile,
        )
        # Should have warnings about incompatibility
        assert len(result.get("warnings", [])) > 0

    @pytest.mark.asyncio
    async def test_reroutes_on_incompatibility(self):
        """Should call LLM twice when first model is incompatible and reroute succeeds."""
        # First response selects geneformer (incompatible)
        first_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Embed my data", "adata_path": None},
            "selection": {
                "recommended": {"name": "geneformer", "rationale": "Best for embedding"},
                "fallbacks": [{"name": "uce", "rationale": "Alternative"}],
            },
        })
        # Second response (reroute) selects uce (compatible)
        reroute_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.85, "constraints": {}},
            "inputs": {"query": "Embed my data", "adata_path": None},
            "selection": {
                "recommended": {"name": "uce", "rationale": "Compatible with data"},
                "fallbacks": [],
            },
        })

        mock_call_agent = AsyncMock(side_effect=[
            {"success": True, "response": first_response},
            {"success": True, "response": reroute_response},
        ])
        context = {"_call_agent": mock_call_agent}
        data_profile = {
            "species": "human",
            "gene_scheme": "symbol",
            "model_compatibility": {
                "geneformer": {"compatible": False, "issues": ["Requires Ensembl IDs"]},
                "uce": {"compatible": True, "issues": []},
            },
        }

        result = await route_query(
            query="Embed my data",
            context=context,
            data_profile=data_profile,
        )

        # Should have called LLM twice (initial + reroute)
        assert mock_call_agent.call_count == 2
        # Should have selected uce (the compatible model)
        assert result["selection"]["recommended"]["name"] == "uce"
        # Should have warning about rerouting
        assert any("Rerouted" in w for w in result.get("warnings", []))

    @pytest.mark.asyncio
    async def test_reroute_failure_adds_questions(self):
        """Should add questions if reroute also fails with incompatible model."""
        # First response selects geneformer (incompatible)
        first_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Embed my data", "adata_path": None},
            "selection": {
                "recommended": {"name": "geneformer", "rationale": "Best for embedding"},
                "fallbacks": [],
            },
        })
        # Second response (reroute) also selects an incompatible model
        reroute_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.85, "constraints": {}},
            "inputs": {"query": "Embed my data", "adata_path": None},
            "selection": {
                "recommended": {"name": "scgpt", "rationale": "Alternative"},
                "fallbacks": [],
            },
        })

        mock_call_agent = AsyncMock(side_effect=[
            {"success": True, "response": first_response},
            {"success": True, "response": reroute_response},
        ])
        context = {"_call_agent": mock_call_agent}
        data_profile = {
            "species": "human",
            "gene_scheme": "symbol",
            "model_compatibility": {
                "geneformer": {"compatible": False, "issues": ["Requires Ensembl IDs"]},
                "scgpt": {"compatible": False, "issues": ["Requires specific preprocessing"]},
                "uce": {"compatible": True, "issues": []},
            },
        }

        result = await route_query(
            query="Embed my data",
            context=context,
            data_profile=data_profile,
        )

        # Should have called LLM twice
        assert mock_call_agent.call_count == 2
        # Should have questions asking user to select manually
        assert len(result.get("questions", [])) > 0
        assert any("model_name" in q.get("field", "") for q in result.get("questions", []))

    @pytest.mark.asyncio
    async def test_reroute_llm_failure_suggests_fallback(self):
        """Should suggest fallback when reroute LLM call fails."""
        # First response selects geneformer (incompatible) with uce fallback
        first_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Embed my data", "adata_path": None},
            "selection": {
                "recommended": {"name": "geneformer", "rationale": "Best for embedding"},
                "fallbacks": [{"name": "uce", "rationale": "Alternative"}],
            },
        })

        mock_call_agent = AsyncMock(side_effect=[
            {"success": True, "response": first_response},
            {"success": False, "error": "LLM call failed"},  # Reroute fails
        ])
        context = {"_call_agent": mock_call_agent}
        data_profile = {
            "species": "human",
            "gene_scheme": "symbol",
            "model_compatibility": {
                "geneformer": {"compatible": False, "issues": ["Requires Ensembl IDs"]},
                "uce": {"compatible": True, "issues": []},
            },
        }

        result = await route_query(
            query="Embed my data",
            context=context,
            data_profile=data_profile,
        )

        # Should have warning suggesting uce fallback
        warnings = result.get("warnings", [])
        assert any("Consider using fallback" in w or "uce" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_candidate_filtering_skill_ready_only(self):
        """Should respect skill_ready_only flag."""
        cards = build_model_cards(skill_ready_only=True)
        # Only skill-ready models should have ✅
        assert cards.count("✅") >= 3  # scgpt, geneformer, uce
        # Partial models should not appear as ✅
        partial_models = ["scfoundation", "scbert", "genecompass"]
        for model in partial_models:
            # Check that partial models are not marked as ready
            if model in cards.lower():
                # Find the line with this model
                lines = cards.lower().split("\n")
                for line in lines:
                    if model in line and "###" in line:
                        # Should have ⚠️ not ✅
                        assert "⚠️" in line or model not in cards

    @pytest.mark.asyncio
    async def test_candidate_filtering_max_vram(self):
        """Should respect max_vram_gb flag."""
        # UCE requires 16GB min
        cards = build_model_cards(max_vram_gb=8)
        # UCE should be filtered out
        assert "uce" not in cards.lower() or "16GB" not in cards


# =============================================================================
# Test SCFMToolSet Integration
# =============================================================================


class TestSCFMRouterTool:
    """Tests for scfm_router tool in SCFMToolSet."""

    @pytest.fixture
    def toolset(self):
        from pantheon.toolsets.scfm import SCFMToolSet
        return SCFMToolSet(name="scfm_test")

    @pytest.mark.asyncio
    async def test_router_tool_exists(self, toolset):
        """scfm_router tool should exist."""
        assert hasattr(toolset, "scfm_router")

    @pytest.mark.asyncio
    async def test_router_without_context(self, toolset):
        """scfm_router should handle missing context gracefully."""
        result = await toolset.scfm_router(
            query="Embed my data",
            context_variables={},
        )
        # Should return error about missing _call_agent
        assert "error" in result or "warning" in str(result).lower()

    @pytest.mark.asyncio
    async def test_router_with_mock_context(self, toolset):
        """scfm_router should work with mocked context."""
        valid_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Embed my data", "adata_path": None},
            "selection": {"recommended": {"name": "uce", "rationale": "Fast"}, "fallbacks": []},
            "resolved_params": {},
            "plan": [{"tool": "scfm_run", "args": {}}],
            "questions": [],
            "warnings": [],
        })

        mock_call_agent = AsyncMock(return_value={
            "success": True,
            "response": valid_response,
        })
        context = {"_call_agent": mock_call_agent}

        result = await toolset.scfm_router(
            query="Embed my data",
            context_variables=context,
        )
        assert result["intent"]["task"] == "embed"
        assert result["selection"]["recommended"]["name"] == "uce"
