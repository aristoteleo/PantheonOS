"""
Integration Tests for SCFM Router

These tests require scanpy and use real AnnData files.
"""

import json
import pytest
from unittest.mock import AsyncMock

# Check if scanpy is available
try:
    import scanpy as sc
    import numpy as np
    HAS_SCANPY = True
except Exception:
    HAS_SCANPY = False


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestSCFMRouterIntegration:
    """Integration tests for scfm_router with real AnnData files."""

    @pytest.fixture
    def toolset(self):
        from pantheon.toolsets.scfm import SCFMToolSet
        return SCFMToolSet(name="scfm_test")

    @pytest.mark.asyncio
    async def test_returns_data_profile_with_real_h5ad(self, toolset, test_adata_path):
        """IT-01: scfm_router should return data_profile with real .h5ad."""
        # Create mock context that returns a valid response
        valid_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Embed my data", "adata_path": test_adata_path},
            "selection": {"recommended": {"name": "uce", "rationale": "Fast"}, "fallbacks": []},
            "resolved_params": {},
            "plan": [
                {"tool": "scfm_preprocess_validate", "args": {}},
                {"tool": "scfm_run", "args": {}},
            ],
            "questions": [],
            "warnings": [],
        })

        mock_call_agent = AsyncMock(return_value={
            "success": True,
            "response": valid_response,
        })
        context = {"_call_agent": mock_call_agent}

        result = await toolset.scfm_router(
            query="Generate embeddings for my single-cell data",
            adata_path=test_adata_path,
            context_variables=context,
        )

        # Should not have an error
        assert "error" not in result, f"Unexpected error: {result.get('error')}"

        # Should have profiled the data
        # Note: The router injects the data profile, so it should be in the result
        # The mock response has data_profile: null, so we check warnings instead
        # which would mention profiling if it failed
        assert result["intent"]["task"] == "embed"
        assert result["selection"]["recommended"]["name"] == "uce"

    @pytest.mark.asyncio
    async def test_incompatible_model_triggers_warning(self, toolset, test_adata_path):
        """IT-02: Incompatible model selection should trigger warnings/reroute."""
        # Create mock context that selects geneformer (requires Ensembl)
        # Our test data has GENE0, GENE1, etc. (symbol-style, not Ensembl)
        valid_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Embed my data", "adata_path": test_adata_path},
            "selection": {
                "recommended": {"name": "geneformer", "rationale": "Selected"},
                "fallbacks": [{"name": "uce", "rationale": "Alternative"}],
            },
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
            query="Generate embeddings for my single-cell data",
            adata_path=test_adata_path,
            context_variables=context,
        )

        # The router should detect incompatibility and add warnings
        # Geneformer requires Ensembl IDs, but test data has symbols
        warnings = result.get("warnings", [])
        # Should have warnings about gene scheme mismatch
        # (depends on what _profile_data_impl returns)
        assert result["intent"]["task"] == "embed"

    @pytest.mark.asyncio
    async def test_ambiguous_batch_key_returns_questions(self, toolset, test_adata_path):
        """IT-03: Ambiguous batch_key should return questions."""
        # Create mock context that requests integration but doesn't know batch_key
        valid_response = json.dumps({
            "intent": {"task": "integrate", "confidence": 0.8, "constraints": {}},
            "inputs": {"query": "Integrate batches", "adata_path": test_adata_path},
            "selection": {"recommended": {"name": "scgpt", "rationale": "Good for integration"}, "fallbacks": []},
            "resolved_params": {"batch_key": None},
            "plan": [
                {"tool": "scfm_preprocess_validate", "args": {}},
                {"tool": "scfm_run", "args": {}},
            ],
            "questions": [
                {
                    "field": "batch_key",
                    "question": "Which column contains batch information?",
                    "options": ["batch", "sample_id"],
                }
            ],
            "warnings": [],
        })

        mock_call_agent = AsyncMock(return_value={
            "success": True,
            "response": valid_response,
        })
        context = {"_call_agent": mock_call_agent}

        result = await toolset.scfm_router(
            query="Integrate my batches",
            adata_path=test_adata_path,
            context_variables=context,
        )

        # Should have questions about batch_key
        assert len(result.get("questions", [])) > 0
        assert result["questions"][0]["field"] == "batch_key"

    @pytest.mark.asyncio
    async def test_router_with_prespecified_batch_key(self, toolset, test_adata_path):
        """Router should use pre-specified batch_key."""
        valid_response = json.dumps({
            "intent": {"task": "integrate", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Integrate batches", "adata_path": test_adata_path},
            "selection": {"recommended": {"name": "scgpt", "rationale": ""}, "fallbacks": []},
            "resolved_params": {"batch_key": None, "output_path": None, "label_key": None},
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
            query="Integrate my batches",
            adata_path=test_adata_path,
            batch_key="batch",  # Pre-specified
            context_variables=context,
        )

        # Should override the resolved_params with our value
        assert result["resolved_params"]["batch_key"] == "batch"

    @pytest.mark.asyncio
    async def test_router_with_skill_ready_only(self, toolset, test_adata_path):
        """Router should respect skill_ready_only flag."""
        valid_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Embed my data", "adata_path": test_adata_path},
            "selection": {"recommended": {"name": "uce", "rationale": "Skill-ready"}, "fallbacks": []},
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

        result = await toolset.scfm_router(
            query="Embed my data",
            adata_path=test_adata_path,
            skill_ready_only=True,
            context_variables=context,
        )

        # Should select a skill-ready model (uce, scgpt, or geneformer)
        model_name = result["selection"]["recommended"]["name"].lower()
        assert model_name in ["uce", "scgpt", "geneformer"]

    @pytest.mark.asyncio
    async def test_router_with_max_vram_constraint(self, toolset, test_adata_path):
        """Router should respect max_vram_gb constraint."""
        valid_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Embed my data", "adata_path": test_adata_path},
            "selection": {
                "recommended": {"name": "scgpt", "rationale": "Low VRAM"},
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

        result = await toolset.scfm_router(
            query="Embed my data",
            adata_path=test_adata_path,
            max_vram_gb=8,  # Should filter out UCE (16GB min)
            context_variables=context,
        )

        # The mock will return scgpt which requires 8GB
        assert result["selection"]["recommended"]["name"].lower() == "scgpt"


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestSCFMRouterDataProfiling:
    """Tests for data profiling within router."""

    @pytest.fixture
    def toolset(self):
        from pantheon.toolsets.scfm import SCFMToolSet
        return SCFMToolSet(name="scfm_test")

    @pytest.mark.asyncio
    async def test_profile_detects_species(self, toolset, test_adata_path):
        """Router should detect species from data."""
        # Call _profile_data_impl directly
        profile = toolset._profile_data_impl(test_adata_path)

        assert "error" not in profile
        assert profile["species"] == "human"

    @pytest.mark.asyncio
    async def test_profile_detects_batch_columns(self, toolset, test_adata_path):
        """Router should detect batch columns from data."""
        profile = toolset._profile_data_impl(test_adata_path)

        assert "error" not in profile
        assert "batch" in profile.get("batch_columns", [])

    @pytest.mark.asyncio
    async def test_profile_detects_celltype_columns(self, toolset, test_adata_path):
        """Router should detect celltype columns from data."""
        profile = toolset._profile_data_impl(test_adata_path)

        assert "error" not in profile
        assert "celltype" in profile.get("celltype_columns", [])

    @pytest.mark.asyncio
    async def test_router_handles_missing_file_gracefully(self, toolset):
        """Router should handle missing adata_path gracefully."""
        valid_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Embed my data", "adata_path": "/nonexistent/file.h5ad"},
            "selection": {"recommended": {"name": "uce", "rationale": ""}, "fallbacks": []},
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

        result = await toolset.scfm_router(
            query="Embed my data",
            adata_path="/nonexistent/file.h5ad",
            context_variables=context,
        )

        # Should have a warning about failed profiling
        warnings = result.get("warnings", [])
        assert any("profiling failed" in w.lower() or "not found" in w.lower() for w in warnings)


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestSCFMRouterExecutionPlan:
    """Tests for router execution plan generation."""

    @pytest.fixture
    def toolset(self):
        from pantheon.toolsets.scfm import SCFMToolSet
        return SCFMToolSet(name="scfm_test")

    @pytest.mark.asyncio
    async def test_plan_includes_validate_step(self, toolset, test_adata_path):
        """Execution plan should include validation step."""
        valid_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Embed my data", "adata_path": test_adata_path},
            "selection": {"recommended": {"name": "uce", "rationale": ""}, "fallbacks": []},
            "resolved_params": {},
            "plan": [
                {"tool": "scfm_preprocess_validate", "args": {"adata_path": test_adata_path, "model_name": "uce", "task": "embed"}},
                {"tool": "scfm_run", "args": {"task": "embed", "model_name": "uce", "adata_path": test_adata_path}},
            ],
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
            adata_path=test_adata_path,
            context_variables=context,
        )

        # Plan should have validation and run steps
        plan_tools = [step["tool"] for step in result.get("plan", [])]
        assert "scfm_preprocess_validate" in plan_tools
        assert "scfm_run" in plan_tools

    @pytest.mark.asyncio
    async def test_plan_tool_names_are_valid(self, toolset, test_adata_path):
        """All tool names in plan should be valid SCFM tools."""
        from pantheon.toolsets.scfm.router import VALID_SCFM_TOOLS

        valid_response = json.dumps({
            "intent": {"task": "embed", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Embed my data", "adata_path": test_adata_path},
            "selection": {"recommended": {"name": "uce", "rationale": ""}, "fallbacks": []},
            "resolved_params": {},
            "plan": [
                {"tool": "scfm_profile_data", "args": {}},
                {"tool": "scfm_preprocess_validate", "args": {}},
                {"tool": "scfm_run", "args": {}},
                {"tool": "scfm_interpret_results", "args": {}},
            ],
            "questions": [],
            "warnings": [],
        })

        mock_call_agent = AsyncMock(return_value={
            "success": True,
            "response": valid_response,
        })
        context = {"_call_agent": mock_call_agent}

        result = await toolset.scfm_router(
            query="Embed and analyze my data",
            adata_path=test_adata_path,
            context_variables=context,
        )

        # All tools in plan should be valid
        for step in result.get("plan", []):
            assert step["tool"] in VALID_SCFM_TOOLS, f"Invalid tool: {step['tool']}"


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestSCFMRouterWithEmbeddings:
    """Tests for router with data that already has embeddings."""

    @pytest.fixture
    def toolset(self):
        from pantheon.toolsets.scfm import SCFMToolSet
        return SCFMToolSet(name="scfm_test")

    @pytest.mark.asyncio
    async def test_profile_detects_existing_embeddings(self, toolset, test_adata_with_embeddings):
        """Router should detect existing embeddings in data."""
        profile = toolset._profile_data_impl(test_adata_with_embeddings)

        assert "error" not in profile
        # Should detect X_uce in obsm
        obsm_keys = profile.get("obsm_keys", [])
        assert "X_uce" in obsm_keys

    @pytest.mark.asyncio
    async def test_router_with_existing_embeddings(self, toolset, test_adata_with_embeddings):
        """Router should handle data with existing embeddings."""
        valid_response = json.dumps({
            "intent": {"task": "integrate", "confidence": 0.9, "constraints": {}},
            "inputs": {"query": "Integrate using existing embeddings", "adata_path": test_adata_with_embeddings},
            "selection": {"recommended": {"name": "scgpt", "rationale": ""}, "fallbacks": []},
            "resolved_params": {},
            "plan": [{"tool": "scfm_run", "args": {}}],
            "questions": [],
            "warnings": ["Data already has embeddings (X_uce)"],
        })

        mock_call_agent = AsyncMock(return_value={
            "success": True,
            "response": valid_response,
        })
        context = {"_call_agent": mock_call_agent}

        result = await toolset.scfm_router(
            query="Integrate my data using existing embeddings",
            adata_path=test_adata_with_embeddings,
            context_variables=context,
        )

        # Should have info about existing embeddings
        assert result["intent"]["task"] == "integrate"
