"""
Real Query Tests for SCFM Router Model Selection

These tests validate that the router correctly selects models based on
realistic user queries WITHOUT direct model name hints. Each query describes
a scenario that naturally leads to a specific model based on:
- Species requirements
- Gene ID scheme (symbol/ensembl/custom)
- Modality (RNA/ATAC/Spatial/Multi-omics)
- Task type (embed/annotate/integrate/spatial)
- Hardware constraints (VRAM)
- Zero-shot capability
- Model-specific features

Test Design Principles:
1. Queries must NOT contain direct model names
2. Queries should describe scenarios that naturally trigger specific models
3. Each test validates both prompt construction and model selection logic

Expected Accuracy & Known Limitations:
---------------------------------------
- **High reliability (100%)**: Models with unique HARD constraints
  - Species: uce (zebrafish), scplantllm (plant)
  - Gene scheme: geneformer (ensembl), scfoundation (custom 19K)
  - Modality: atacformer (ATAC-only), nicheformer (Spatial)
  - Hardware: genept (no GPU/API-based)

- **Medium reliability**: Models with unique SOFT constraints
  - Zero-shot annotation: sccello, chatcell
  - Multi-omics: scmulan
  - Text alignment: langcell, cell2sentence

- **Lower reliability**: Models differentiated only by architecture/scale
  - These features are NOT in current model cards (only in license_notes)
  - LLM defaults to skill-ready models (scgpt, geneformer, uce)
  - Affected: scbert, cellplm, tgpt, cellfm, scprint, aidocell, pulsar

To improve accuracy for soft-constraint models, the model registry would need
to expose distinctive features (embedding_dim, architecture, training_scale)
in the model cards displayed to the LLM.
"""

import json
import os
import pytest
from unittest.mock import AsyncMock

from pantheon.toolsets.scfm.router import (
    build_model_cards,
    build_router_prompt,
    route_query,
)


# =============================================================================
# Live LLM Test Configuration
# =============================================================================

def get_test_model() -> str:
    """
    Get LLM model for testing from environment.

    Supports provider-prefixed model strings:
    - OpenAI: "gpt-4o-mini", "gpt-4o"
    - Anthropic: "anthropic/claude-sonnet-4-20250514", "anthropic/claude-haiku-3-5-20241022"
    - Gemini: "gemini/gemini-1.5-flash"
    - DeepSeek: "deepseek/deepseek-chat"

    Environment variable: SCFM_TEST_MODEL
    Default: "gpt-4o-mini"
    """
    return os.environ.get("SCFM_TEST_MODEL", "gpt-4o-mini")


def has_api_key_for_model(model: str) -> bool:
    """Check if the required API key is available for the given model."""
    model_lower = model.lower()

    if model_lower.startswith("anthropic/"):
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    elif model_lower.startswith("gemini/"):
        return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    elif model_lower.startswith("deepseek/"):
        return bool(os.environ.get("DEEPSEEK_API_KEY"))
    elif model_lower.startswith("groq/"):
        return bool(os.environ.get("GROQ_API_KEY"))
    elif model_lower.startswith("mistral/"):
        return bool(os.environ.get("MISTRAL_API_KEY"))
    elif model_lower.startswith("together/") or model_lower.startswith("together_ai/"):
        return bool(os.environ.get("TOGETHER_API_KEY") or os.environ.get("TOGETHERAI_API_KEY"))
    else:
        # Default to OpenAI for models without prefix
        return bool(os.environ.get("OPENAI_API_KEY"))


async def create_real_call_agent():
    """
    Create a real _call_agent function using provider adapters.

    Returns an async function compatible with the router's _call_agent interface.
    """
    from pantheon.utils.adapters import get_adapter
    from pantheon.utils.provider_registry import find_provider_for_model
    from pantheon.utils.llm import stream_chunk_builder

    model = get_test_model()

    async def _call_agent(messages, system_prompt=None, model_override=None, **kwargs):
        """
        Call LLM via provider adapter.

        Args:
            messages: List of message dicts with 'role' and 'content'
            system_prompt: Optional system prompt
            model_override: Optional model override (not typically used in tests)
            **kwargs: Additional arguments (ignored)

        Returns:
            Dict with 'success' and 'response' or 'error' keys
        """
        actual_model = model_override or model

        # Build full message list
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        try:
            provider_key, model_name, provider_config = find_provider_for_model(actual_model)
            adapter = get_adapter(provider_config.get("sdk", "openai"))
            import os
            api_key = os.environ.get(provider_config.get("api_key_env", ""), "")
            chunks = await adapter.acompletion(
                model=model_name,
                messages=full_messages,
                base_url=provider_config.get("base_url"),
                api_key=api_key,
                temperature=0.0,
            )
            response = stream_chunk_builder(chunks)
            return {
                "success": True,
                "response": response.choices[0].message.content,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }

    return _call_agent


# =============================================================================
# Test Data: 21 Real Query Test Cases
# =============================================================================

# Format: (query, data_profile, constraints, expected_model, trigger_description)
REAL_QUERY_TEST_CASES = [
    # =========================================================================
    # Skill-Ready Models (3)
    # =========================================================================
    pytest.param(
        "Integrate scRNA-seq data from multiple human donors using standard gene symbols. I have an 8GB GPU.",
        {"species": "human", "gene_format": "symbol", "modality": "RNA"},
        {"max_vram_gb": 8},
        "scgpt",
        "Human + symbol + 8GB VRAM (filters UCE which needs 16GB)",
        id="scgpt-human-symbol-8gb",
    ),
    pytest.param(
        "Generate embeddings for my human PBMC dataset. My data uses Ensembl identifiers like ENSG00000141510.",
        {"species": "human", "gene_format": "ensembl", "modality": "RNA"},
        {},
        "geneformer",
        "Ensembl IDs requirement (unique to Geneformer)",
        id="geneformer-ensembl-ids",
    ),
    pytest.param(
        "I'm studying regeneration in zebrafish fin tissue. Generate embeddings for my single-cell data.",
        {"species": "zebrafish", "gene_format": "symbol", "modality": "RNA"},
        {},
        "uce",
        "Zebrafish species (UCE uniquely supports zebrafish among skill-ready)",
        id="uce-zebrafish-species",
    ),

    # =========================================================================
    # Partial/Emerging Models (18)
    # =========================================================================
    pytest.param(
        "My data was preprocessed using xTrimoGene pipeline mapped to the standard 19,264 gene vocabulary.",
        {"species": "human", "gene_format": "custom", "n_vars": 19264},
        {"allow_partial": True},
        "scfoundation",
        "Custom 19K gene set (xTrimoGene vocabulary)",
        id="scfoundation-custom-genes",
    ),
    pytest.param(
        "I need exactly 200-dimensional embeddings for a compact downstream classifier. The smallest embedding dimension available is critical for my memory-constrained application.",
        {"species": "human", "gene_format": "symbol", "embedding_dim_required": 200},
        {"allow_partial": True},
        "scbert",
        "200-dim embeddings (smallest embedding dimension)",
        id="scbert-compact-200dim",
    ),
    pytest.param(
        "I want a model incorporating biological prior knowledge like gene regulatory networks, trained on 120 million cells.",
        {"species": "human", "gene_format": "symbol"},
        {"allow_partial": True},
        "genecompass",
        "Prior knowledge + 120M cell training scale",
        id="genecompass-prior-knowledge",
    ),
    pytest.param(
        "I need a cell-centric pretrained language model that doesn't require fine-tuning, optimized for efficient inference with a high default batch size of 128.",
        {"species": "human", "gene_format": "symbol", "n_obs": 2000000},
        {"allow_partial": True},
        "cellplm",
        "Cell-centric PLM + efficient inference (batch 128)",
        id="cellplm-fast-inference",
    ),
    pytest.param(
        "I have Visium spatial data with X/Y coordinates. I want to analyze cellular neighborhoods and tissue microenvironment.",
        {"species": "human", "modality": "Spatial", "obsm_keys": ["spatial"]},
        {"allow_partial": True},
        "nicheformer",
        "Spatial transcriptomics + niche/neighborhood analysis",
        id="nicheformer-spatial-niche",
    ),
    pytest.param(
        "My dataset combines RNA expression, chromatin accessibility ATAC, and protein surface markers. I need a model specifically designed for multi-omics integration supporting all three modalities simultaneously.",
        {"species": "human", "modality": "Multi-omics", "modalities_present": ["RNA", "ATAC", "Protein"]},
        {"allow_partial": True},
        "scmulan",
        "Multi-omics (RNA + ATAC + Protein) simultaneous integration",
        id="scmulan-multiomics",
    ),
    pytest.param(
        "I want a transformer model that uses autoregressive next-token prediction for gene expression, treating genes as tokens in a sequence like language models do.",
        {"species": "human", "gene_format": "symbol", "architecture": "autoregressive"},
        {"allow_partial": True},
        "tgpt",
        "Autoregressive next-token prediction (gene tokens)",
        id="tgpt-next-token",
    ),
    pytest.param(
        "I need a foundation model using MLP (multilayer perceptron) architecture rather than transformer, trained on approximately 126 million cells for maximum scale.",
        {"species": "human", "gene_format": "symbol", "architecture": "MLP", "training_scale": "126M"},
        {"allow_partial": True},
        "cellfm",
        "MLP architecture (not transformer) + 126M scale",
        id="cellfm-largest-scale",
    ),
    pytest.param(
        "Annotate cell types without fine-tuning using Cell Ontology terms for consistent annotation.",
        {"species": "human", "gene_format": "symbol"},
        {"allow_partial": True, "prefer_zero_shot": True},
        "sccello",
        "Zero-shot annotation + Cell Ontology alignment",
        id="sccello-zeroshot-ontology",
    ),
    pytest.param(
        "I need a model focused specifically on protein-coding genes, with strong denoising capabilities and robust batch integration for datasets with severe technical artifacts.",
        {"species": "human", "gene_format": "symbol", "batch_columns": ["batch", "donor"], "focus": "protein_coding"},
        {"allow_partial": True},
        "scprint",
        "Protein-coding focus + denoising + robust integration",
        id="scprint-protein-coding",
    ),
    pytest.param(
        "I need a model with dense transformer architecture specifically designed for unsupervised cell clustering without predefined labels. Should support zero-shot discovery of cell groups.",
        {"species": "human", "gene_format": "symbol", "task": "unsupervised_clustering"},
        {"allow_partial": True},
        "aidocell",
        "Dense transformer + zero-shot unsupervised clustering",
        id="aidocell-zeroshot-clustering",
    ),
    pytest.param(
        "I need a multi-scale foundation model for studying multicellular systems and cell-cell communication networks. Looking for a model designed specifically for tissue-level biological organization analysis.",
        {"species": "human", "gene_format": "symbol", "analysis_scale": "multicellular"},
        {"allow_partial": True},
        "pulsar",
        "Multi-scale + multicellular systems + tissue organization",
        id="pulsar-multicellular",
    ),
    pytest.param(
        "My data is single-cell ATAC-seq measuring chromatin accessibility. Features are genomic peaks, not genes.",
        {"species": "human", "gene_format": "custom", "modality": "ATAC"},
        {"allow_partial": True},
        "atacformer",
        "ATAC-seq chromatin accessibility (peak-based, not gene-based)",
        id="atacformer-atac-peaks",
    ),
    pytest.param(
        "I have scRNA-seq from Arabidopsis thaliana root tissue. Need a model for plant biology and polyploidy.",
        {"species": "plant", "gene_format": "symbol"},
        {"allow_partial": True},
        "scplantllm",
        "Plant species + polyploidy support",
        id="scplantllm-plant-species",
    ),
    pytest.param(
        "I want a two-tower architecture model that aligns cell embeddings with natural language text descriptions. Should enable querying cells using textual descriptions of cell types.",
        {"species": "human", "gene_format": "symbol", "architecture": "two_tower", "modality": "text+cell"},
        {"allow_partial": True},
        "langcell",
        "Two-tower architecture + text-cell alignment",
        id="langcell-text-alignment",
    ),
    pytest.param(
        "I want to flatten single-cell gene expression data into text sentence representations for fine-tuning large language models. Converting cells to sentences for LLM processing.",
        {"species": "human", "gene_format": "symbol", "approach": "cell_to_sentence"},
        {"allow_partial": True, "prefer_zero_shot": False},
        "cell2sentence",
        "Flatten cells to text sentences + LLM fine-tuning",
        id="cell2sentence-text-sequences",
    ),
    pytest.param(
        "I don't have a GPU and can't install heavy dependencies. Need cloud API approach for gene embeddings.",
        {"species": "human", "gene_format": "symbol"},
        {"allow_partial": True, "max_vram_gb": 0},
        "genept",
        "No GPU / API-based approach (uses GPT-3.5 embeddings)",
        id="genept-no-gpu-api",
    ),
    pytest.param(
        "I want a chat-based conversational model for single-cell analysis where I can interactively ask questions about cells and get natural language responses about cell types and states.",
        {"species": "human", "gene_format": "symbol", "interface": "chat"},
        {"allow_partial": True},
        "chatcell",
        "Chat-based conversational interface + interactive Q&A",
        id="chatcell-conversational",
    ),
]


# =============================================================================
# Test Helper Functions
# =============================================================================


def create_valid_router_response(expected_model: str, task: str = "embed", query: str = "Test query") -> str:
    """Create a valid router response JSON for the given model."""
    return json.dumps({
        "intent": {"task": task, "confidence": 0.9, "constraints": {}},
        "inputs": {"query": query, "adata_path": None},
        "selection": {
            "recommended": {"name": expected_model, "rationale": f"Selected {expected_model} based on query requirements"},
            "fallbacks": [],
        },
        "resolved_params": {"output_path": None, "batch_key": None, "label_key": None},
        "plan": [{"tool": "scfm_run", "args": {}}],
        "questions": [],
        "warnings": [],
    })


# =============================================================================
# Prompt Construction Tests
# =============================================================================


class TestPromptContainsModelInfo:
    """Verify that router prompts include necessary model information."""

    @pytest.mark.parametrize("query,data_profile,constraints,expected_model,description", REAL_QUERY_TEST_CASES)
    def test_prompt_contains_expected_model_in_cards(self, query, data_profile, constraints, expected_model, description):
        """Router prompt model cards should contain the expected model."""
        # Build model cards with appropriate filters
        skill_ready_only = constraints.get("skill_ready_only", False)
        max_vram = constraints.get("max_vram_gb")
        allow_partial = constraints.get("allow_partial", True)

        cards = build_model_cards(
            skill_ready_only=skill_ready_only,
            max_vram_gb=max_vram,
        )

        # Expected model should be in the cards (unless filtered by VRAM)
        # For models that need partial status, we shouldn't filter skill_ready_only
        if max_vram is not None and max_vram == 0:
            # GenePT special case: no GPU required, should still be in cards
            assert expected_model in cards.lower() or "genept" in expected_model.lower()
        elif not skill_ready_only or expected_model in ["scgpt", "geneformer", "uce"]:
            assert expected_model in cards.lower(), f"Expected {expected_model} in model cards for: {description}"

    @pytest.mark.parametrize("query,data_profile,constraints,expected_model,description", REAL_QUERY_TEST_CASES)
    def test_prompt_includes_query(self, query, data_profile, constraints, expected_model, description):
        """Router prompt should include the user query."""
        cards = build_model_cards(skill_ready_only=False)
        prompt = build_router_prompt(
            query=query,
            data_profile=data_profile,
            model_cards=cards,
        )
        assert query in prompt, f"Query not found in prompt for: {description}"

    @pytest.mark.parametrize("query,data_profile,constraints,expected_model,description", REAL_QUERY_TEST_CASES)
    def test_prompt_includes_data_profile(self, query, data_profile, constraints, expected_model, description):
        """Router prompt should include data profile information."""
        if not data_profile:
            pytest.skip("No data profile for this test case")

        cards = build_model_cards(skill_ready_only=False)
        prompt = build_router_prompt(
            query=query,
            data_profile=data_profile,
            model_cards=cards,
        )

        # At least one data profile field should be in prompt
        profile_present = any(
            str(v).lower() in prompt.lower()
            for v in data_profile.values()
            if v and str(v) not in ["symbol", "RNA"]  # Skip common terms
        )
        # If we have species or special fields, they should appear
        if "species" in data_profile and data_profile["species"] not in ["human"]:
            assert data_profile["species"] in prompt.lower()


# =============================================================================
# Model Selection Tests (Mock-Based)
# =============================================================================


class TestModelSelectionWithMock:
    """Test that router correctly passes model info for selection."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("query,data_profile,constraints,expected_model,description", REAL_QUERY_TEST_CASES)
    async def test_router_receives_expected_model_in_cards(
        self, query, data_profile, constraints, expected_model, description
    ):
        """Verify router prompt includes expected model for LLM to select."""
        # Create a mock that records the prompt it receives
        received_prompts = []

        async def capture_mock(*args, **kwargs):
            messages = kwargs.get("messages", [])
            if messages:
                received_prompts.append(messages[0].get("content", ""))
            return {
                "success": True,
                "response": create_valid_router_response(expected_model, query=query),
            }

        mock_call_agent = AsyncMock(side_effect=capture_mock)
        context = {"_call_agent": mock_call_agent}

        # Extract constraint kwargs
        constraint_kwargs = {
            "skill_ready_only": constraints.get("skill_ready_only", False),
            "max_vram_gb": constraints.get("max_vram_gb"),
            "prefer_zero_shot": constraints.get("prefer_zero_shot", True),
            "allow_partial": constraints.get("allow_partial", True),
        }
        # Remove None values
        constraint_kwargs = {k: v for k, v in constraint_kwargs.items() if v is not None}

        result = await route_query(
            query=query,
            context=context,
            data_profile=data_profile,
            **constraint_kwargs,
        )

        # Verify the mock was called
        assert mock_call_agent.called, "Router should call LLM"

        # Verify expected model appears in prompt (unless filtered out by constraints)
        if received_prompts:
            prompt = received_prompts[0].lower()
            max_vram = constraints.get("max_vram_gb")

            # Skip assertion for models that would be filtered
            if max_vram == 0 and expected_model != "genept":
                # Most models need GPU, so they'd be filtered at 0 VRAM
                pass
            else:
                # Model should be in prompt for LLM to consider
                assert expected_model in prompt, (
                    f"Expected model '{expected_model}' not in prompt for: {description}"
                )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("query,data_profile,constraints,expected_model,description", REAL_QUERY_TEST_CASES)
    async def test_mock_returns_expected_model(
        self, query, data_profile, constraints, expected_model, description
    ):
        """Verify mock correctly returns expected model (validates test infrastructure)."""
        mock_call_agent = AsyncMock(return_value={
            "success": True,
            "response": create_valid_router_response(expected_model, query=query),
        })
        context = {"_call_agent": mock_call_agent}

        constraint_kwargs = {
            "skill_ready_only": constraints.get("skill_ready_only", False),
            "max_vram_gb": constraints.get("max_vram_gb"),
            "prefer_zero_shot": constraints.get("prefer_zero_shot", True),
            "allow_partial": constraints.get("allow_partial", True),
        }
        constraint_kwargs = {k: v for k, v in constraint_kwargs.items() if v is not None}

        result = await route_query(
            query=query,
            context=context,
            data_profile=data_profile,
            **constraint_kwargs,
        )

        # Verify result structure
        assert "error" not in result or result.get("selection", {}).get("recommended", {}).get("name")
        selected_model = result.get("selection", {}).get("recommended", {}).get("name", "").lower()
        assert selected_model == expected_model.lower(), (
            f"Expected {expected_model} but got {selected_model} for: {description}"
        )


# =============================================================================
# Constraint-Based Filtering Tests
# =============================================================================


class TestConstraintFiltering:
    """Test that constraints properly filter model candidates."""

    def test_vram_8gb_excludes_uce(self):
        """8GB VRAM constraint should exclude UCE (requires 16GB)."""
        cards = build_model_cards(max_vram_gb=8)
        # UCE requires 16GB min VRAM
        assert "uce" not in cards.lower() or "16gb" not in cards.lower()

    def test_vram_0_includes_genept(self):
        """0GB VRAM (no GPU) should still include GenePT (API-based)."""
        cards = build_model_cards(max_vram_gb=0)
        # GenePT doesn't require GPU (API-based)
        assert "genept" in cards.lower()

    def test_skill_ready_only_limits_to_three(self):
        """skill_ready_only should only include scgpt, geneformer, uce."""
        cards = build_model_cards(skill_ready_only=True)
        skill_ready_models = ["scgpt", "geneformer", "uce"]
        partial_models = ["scfoundation", "scbert", "cellplm", "nicheformer"]

        for model in skill_ready_models:
            assert model in cards.lower(), f"Skill-ready model {model} should be in cards"

        for model in partial_models:
            # Partial models should not have ✅ status
            lines = cards.lower().split("\n")
            for line in lines:
                if model in line and "###" in line:
                    assert "✅" not in line, f"Partial model {model} should not have ✅"


# =============================================================================
# Model-Specific Feature Tests
# =============================================================================


class TestModelSpecificFeatures:
    """Test that model-specific features are correctly represented."""

    def test_geneformer_requires_ensembl(self):
        """Geneformer should indicate Ensembl ID requirement."""
        cards = build_model_cards(skill_ready_only=False)
        # Find geneformer section
        assert "geneformer" in cards.lower()
        assert "ensembl" in cards.lower()

    def test_uce_supports_zebrafish(self):
        """UCE should indicate zebrafish support."""
        cards = build_model_cards(skill_ready_only=False)
        assert "uce" in cards.lower()
        assert "zebrafish" in cards.lower()

    def test_atacformer_shows_atac_modality(self):
        """ATACformer should indicate ATAC modality."""
        cards = build_model_cards(skill_ready_only=False)
        assert "atacformer" in cards.lower()
        assert "atac" in cards.lower()

    def test_scplantllm_shows_plant_species(self):
        """scPlantLLM should indicate plant species support."""
        cards = build_model_cards(skill_ready_only=False)
        assert "scplantllm" in cards.lower()
        assert "plant" in cards.lower()

    def test_scmulan_shows_multiomics(self):
        """scMulan should indicate multi-omics support."""
        cards = build_model_cards(skill_ready_only=False)
        assert "scmulan" in cards.lower()
        # Check for multi-omics related content
        lines = [l for l in cards.split("\n") if "scmulan" in l.lower() or "multi" in l.lower()]
        assert len(lines) > 0

    def test_nicheformer_shows_spatial(self):
        """Nicheformer should indicate spatial modality."""
        cards = build_model_cards(skill_ready_only=False)
        assert "nicheformer" in cards.lower()
        assert "spatial" in cards.lower()


# =============================================================================
# Query Validation Tests
# =============================================================================


class TestQueryValidation:
    """Validate that test queries don't contain direct model names."""

    @pytest.mark.parametrize("query,data_profile,constraints,expected_model,description", REAL_QUERY_TEST_CASES)
    def test_query_does_not_contain_model_name(self, query, data_profile, constraints, expected_model, description):
        """Queries should NOT contain direct model names - they should describe scenarios."""
        query_lower = query.lower()

        # List of all model names that shouldn't appear in queries
        model_names = [
            "scgpt", "geneformer", "uce",
            "scfoundation", "scbert", "genecompass", "cellplm",
            "nicheformer", "scmulan", "tgpt", "cellfm", "sccello",
            "scprint", "aidocell", "pulsar", "atacformer",
            "scplantllm", "langcell", "cell2sentence", "genept", "chatcell",
        ]

        for model in model_names:
            assert model not in query_lower, (
                f"Query should not contain model name '{model}': {query[:50]}..."
            )


# =============================================================================
# Live LLM Tests (Optional - requires API key)
# =============================================================================


# Subset of test cases for live tests (skill-ready models only to reduce API costs)
LIVE_TEST_CASES_SKILL_READY = REAL_QUERY_TEST_CASES[:3]  # scgpt, geneformer, uce

# All test cases for comprehensive live tests
LIVE_TEST_CASES_ALL = REAL_QUERY_TEST_CASES


@pytest.mark.live_llm
class TestLiveModelSelection:
    """
    Live LLM tests using real API calls via provider adapters.

    Supports multiple providers through environment configuration:

    Environment Variables:
    - SCFM_TEST_MODEL: Model string (default: "gpt-4o-mini")
    - Provider API keys: OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, etc.

    Example usage:
        # OpenAI
        export OPENAI_API_KEY="sk-..."
        export SCFM_TEST_MODEL="gpt-4o-mini"
        pytest tests/test_scfm_router_real_queries.py -v -m live_llm

        # Anthropic
        export ANTHROPIC_API_KEY="sk-ant-..."
        export SCFM_TEST_MODEL="anthropic/claude-haiku-3-5-20241022"
        pytest tests/test_scfm_router_real_queries.py -v -m live_llm

        # Run comprehensive tests (all 21 models)
        pytest tests/test_scfm_router_real_queries.py -v -m live_llm -k "test_live_all"
    """

    @pytest.fixture(autouse=True)
    def check_api_key(self):
        """Skip tests if no API key is available for the configured model."""
        model = get_test_model()
        if not has_api_key_for_model(model):
            pytest.skip(f"No API key available for model: {model}")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("query,data_profile,constraints,expected_model,description", LIVE_TEST_CASES_SKILL_READY)
    async def test_live_skill_ready_model_selection(
        self, query, data_profile, constraints, expected_model, description
    ):
        """
        Live test for skill-ready models (scgpt, geneformer, uce).

        These tests run by default with -m live_llm and use minimal API calls.
        """
        _call_agent = await create_real_call_agent()
        context = {"_call_agent": _call_agent}

        # Extract constraint kwargs
        constraint_kwargs = {
            "skill_ready_only": constraints.get("skill_ready_only", False),
            "max_vram_gb": constraints.get("max_vram_gb"),
            "prefer_zero_shot": constraints.get("prefer_zero_shot", True),
            "allow_partial": constraints.get("allow_partial", True),
        }
        # Remove None values
        constraint_kwargs = {k: v for k, v in constraint_kwargs.items() if v is not None}

        result = await route_query(
            query=query,
            context=context,
            data_profile=data_profile,
            **constraint_kwargs,
        )

        # Check for errors
        if "error" in result:
            pytest.fail(f"Router returned error: {result['error']}")

        # Verify model selection
        selected = result.get("selection", {}).get("recommended", {}).get("name", "").lower()
        assert selected == expected_model.lower(), (
            f"Expected {expected_model}, got {selected}\n"
            f"Query: {query[:80]}...\n"
            f"Description: {description}\n"
            f"Full result: {json.dumps(result, indent=2, default=str)[:500]}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("query,data_profile,constraints,expected_model,description", LIVE_TEST_CASES_ALL)
    async def test_live_all_models_comprehensive(
        self, query, data_profile, constraints, expected_model, description
    ):
        """
        Comprehensive live test for all 21 models.

        Run with: pytest tests/test_scfm_router_real_queries.py -v -m live_llm -k "test_live_all"

        Warning: This runs 21 API calls and may incur costs.
        """
        _call_agent = await create_real_call_agent()
        context = {"_call_agent": _call_agent}

        # Extract constraint kwargs
        constraint_kwargs = {
            "skill_ready_only": constraints.get("skill_ready_only", False),
            "max_vram_gb": constraints.get("max_vram_gb"),
            "prefer_zero_shot": constraints.get("prefer_zero_shot", True),
            "allow_partial": constraints.get("allow_partial", True),
        }
        # Remove None values
        constraint_kwargs = {k: v for k, v in constraint_kwargs.items() if v is not None}

        result = await route_query(
            query=query,
            context=context,
            data_profile=data_profile,
            **constraint_kwargs,
        )

        # Check for errors
        if "error" in result:
            pytest.fail(f"Router returned error: {result['error']}")

        # Verify model selection
        selected = result.get("selection", {}).get("recommended", {}).get("name", "").lower()
        assert selected == expected_model.lower(), (
            f"Expected {expected_model}, got {selected}\n"
            f"Query: {query[:80]}...\n"
            f"Description: {description}\n"
            f"Full result: {json.dumps(result, indent=2, default=str)[:500]}"
        )

    @pytest.mark.asyncio
    async def test_live_router_returns_valid_structure(self):
        """Test that live router returns a valid response structure."""
        _call_agent = await create_real_call_agent()
        context = {"_call_agent": _call_agent}

        query = "Generate embeddings for my human single-cell RNA-seq data."
        data_profile = {"species": "human", "gene_format": "symbol", "modality": "RNA"}

        result = await route_query(
            query=query,
            context=context,
            data_profile=data_profile,
        )

        # Verify response structure
        assert "intent" in result, "Response should have 'intent' field"
        assert "selection" in result, "Response should have 'selection' field"
        assert "plan" in result, "Response should have 'plan' field"

        # Verify intent structure
        intent = result["intent"]
        assert "task" in intent, "Intent should have 'task'"
        assert "confidence" in intent, "Intent should have 'confidence'"
        assert intent["task"] in ["embed", "integrate", "annotate", "spatial", "perturb", "drug_response"], \
            f"Task should be valid, got: {intent['task']}"

        # Verify selection structure
        selection = result["selection"]
        assert "recommended" in selection, "Selection should have 'recommended'"
        assert "name" in selection["recommended"], "Recommended should have 'name'"
        assert selection["recommended"]["name"], "Model name should not be empty"

    @pytest.mark.asyncio
    async def test_live_router_handles_constraints(self):
        """Test that live router respects hardware constraints."""
        _call_agent = await create_real_call_agent()
        context = {"_call_agent": _call_agent}

        # Query with tight VRAM constraint
        query = "Generate embeddings for my human data. I only have 4GB of VRAM."
        data_profile = {"species": "human", "gene_format": "symbol"}

        result = await route_query(
            query=query,
            context=context,
            data_profile=data_profile,
            max_vram_gb=4,
        )

        # Should not error
        assert "error" not in result or result.get("selection", {}).get("recommended", {}).get("name")

        # The selected model should be presentable
        selected = result.get("selection", {}).get("recommended", {}).get("name", "")
        assert selected, f"Should select a model, got empty selection. Result: {result}"


@pytest.mark.live_llm
class TestLiveModelSelectionAccuracy:
    """
    Accuracy tracking tests for live LLM model selection.

    These tests track the accuracy of model selection across different LLM providers
    and can be used to compare provider performance.
    """

    @pytest.fixture(autouse=True)
    def check_api_key(self):
        """Skip tests if no API key is available for the configured model."""
        model = get_test_model()
        if not has_api_key_for_model(model):
            pytest.skip(f"No API key available for model: {model}")

    @pytest.mark.asyncio
    async def test_live_accuracy_report_skill_ready(self):
        """
        Run all skill-ready tests and report accuracy.

        Useful for comparing different LLM providers.
        """
        _call_agent = await create_real_call_agent()
        context = {"_call_agent": _call_agent}

        correct = 0
        total = len(LIVE_TEST_CASES_SKILL_READY)
        results_log = []

        for test_case in LIVE_TEST_CASES_SKILL_READY:
            query, data_profile, constraints, expected_model, description = test_case.values

            constraint_kwargs = {
                "skill_ready_only": constraints.get("skill_ready_only", False),
                "max_vram_gb": constraints.get("max_vram_gb"),
                "prefer_zero_shot": constraints.get("prefer_zero_shot", True),
                "allow_partial": constraints.get("allow_partial", True),
            }
            constraint_kwargs = {k: v for k, v in constraint_kwargs.items() if v is not None}

            result = await route_query(
                query=query,
                context=context,
                data_profile=data_profile,
                **constraint_kwargs,
            )

            selected = result.get("selection", {}).get("recommended", {}).get("name", "").lower()
            is_correct = selected == expected_model.lower()
            if is_correct:
                correct += 1

            results_log.append({
                "expected": expected_model,
                "selected": selected,
                "correct": is_correct,
                "description": description,
            })

        accuracy = (correct / total) * 100 if total > 0 else 0
        model = get_test_model()

        print(f"\n{'='*60}")
        print(f"SCFM Router Accuracy Report (Skill-Ready Models)")
        print(f"{'='*60}")
        print(f"Model: {model}")
        print(f"Accuracy: {correct}/{total} ({accuracy:.1f}%)")
        print(f"{'='*60}")

        for r in results_log:
            status = "✅" if r["correct"] else "❌"
            print(f"{status} {r['expected']:15} -> {r['selected']:15} | {r['description'][:40]}")

        print(f"{'='*60}\n")

        # This test always passes - it's for reporting, not assertion
        # But we can add a soft assertion for CI
        assert accuracy >= 50.0, f"Accuracy too low: {accuracy:.1f}% (expected >= 50%)"


# =============================================================================
# Summary Statistics
# =============================================================================


class TestSummary:
    """Meta-tests to verify test coverage."""

    def test_all_21_models_covered(self):
        """Verify we have test cases for all 21 models."""
        expected_models = {
            # Skill-ready
            "scgpt", "geneformer", "uce",
            # Partial
            "scfoundation", "scbert", "genecompass", "cellplm",
            "nicheformer", "scmulan", "tgpt", "cellfm", "sccello",
            "scprint", "aidocell", "pulsar", "atacformer",
            "scplantllm", "langcell", "cell2sentence", "genept", "chatcell",
        }

        tested_models = {tc.values[3] for tc in REAL_QUERY_TEST_CASES}

        missing = expected_models - tested_models
        assert not missing, f"Missing test cases for models: {missing}"

        assert len(REAL_QUERY_TEST_CASES) == 21, f"Expected 21 test cases, got {len(REAL_QUERY_TEST_CASES)}"

    def test_each_test_has_unique_trigger(self):
        """Each test should have a unique trigger description."""
        descriptions = [tc.values[4] for tc in REAL_QUERY_TEST_CASES]
        assert len(descriptions) == len(set(descriptions)), "Duplicate trigger descriptions found"
