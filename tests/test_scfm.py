"""
Tests for Single-Cell Foundation Model (SCFM) ToolSet

Tests cover:
- Model registry functionality
- Tool function behavior (without actual model inference)
- Data profiling and validation logic
"""

import pytest
import tempfile
from pathlib import Path

from pantheon.toolsets.scfm import (
    SCFMToolSet,
    ModelRegistry,
    ModelSpec,
    TaskType,
    Modality,
    GeneIDScheme,
    get_registry,
)


class TestModelRegistry:
    """Tests for ModelRegistry"""

    def test_registry_initialization(self):
        """Registry should initialize with default models"""
        registry = get_registry()
        models = registry.list_models()
        assert len(models) >= 3  # scgpt, geneformer, uce at minimum

    def test_list_models(self):
        """list_models should return registered models"""
        registry = get_registry()
        models = registry.list_models()
        model_names = [m.name for m in models]
        assert "scgpt" in model_names
        assert "geneformer" in model_names
        assert "uce" in model_names

    def test_list_models_skill_ready_only(self):
        """list_models with skill_ready_only should filter"""
        registry = get_registry()
        all_models = registry.list_models(skill_ready_only=False)
        ready_models = registry.list_models(skill_ready_only=True)
        assert len(ready_models) <= len(all_models)

    def test_get_model(self):
        """get should return model spec by name"""
        registry = get_registry()
        spec = registry.get("uce")
        assert spec is not None
        assert spec.name == "uce"
        assert TaskType.EMBED in spec.tasks

    def test_get_model_case_insensitive(self):
        """get should be case-insensitive"""
        registry = get_registry()
        spec1 = registry.get("UCE")
        spec2 = registry.get("uce")
        assert spec1 == spec2

    def test_get_nonexistent_model(self):
        """get should return None for unknown model"""
        registry = get_registry()
        spec = registry.get("nonexistent_model")
        assert spec is None

    def test_find_models_by_task(self):
        """find_models should filter by task"""
        registry = get_registry()
        embed_models = registry.find_models(task=TaskType.EMBED)
        assert len(embed_models) >= 3  # All skill-ready models support embed

        annotate_models = registry.find_models(task=TaskType.ANNOTATE)
        # UCE doesn't support annotate
        uce_in_annotate = any(m.name == "uce" for m in annotate_models)
        assert not uce_in_annotate

    def test_find_models_by_species(self):
        """find_models should filter by species"""
        registry = get_registry()
        human_models = registry.find_models(species="human")
        assert len(human_models) >= 3

        # Geneformer only supports human
        mouse_models = registry.find_models(species="mouse")
        geneformer_in_mouse = any(m.name == "geneformer" for m in mouse_models)
        assert not geneformer_in_mouse

    def test_find_models_by_gene_scheme(self):
        """find_models should filter by gene ID scheme"""
        registry = get_registry()
        ensembl_models = registry.find_models(gene_scheme=GeneIDScheme.ENSEMBL)
        # Only Geneformer requires Ensembl
        assert any(m.name == "geneformer" for m in ensembl_models)

        symbol_models = registry.find_models(gene_scheme=GeneIDScheme.SYMBOL)
        assert any(m.name == "uce" for m in symbol_models)
        assert any(m.name == "scgpt" for m in symbol_models)


class TestModelSpec:
    """Tests for ModelSpec"""

    def test_supports_task(self):
        """supports_task should check task list"""
        registry = get_registry()
        uce = registry.get("uce")
        assert uce.supports_task(TaskType.EMBED)
        assert uce.supports_task(TaskType.INTEGRATE)
        assert not uce.supports_task(TaskType.ANNOTATE)

    def test_supports_species(self):
        """supports_species should be case-insensitive"""
        registry = get_registry()
        uce = registry.get("uce")
        assert uce.supports_species("human")
        assert uce.supports_species("Human")
        assert uce.supports_species("HUMAN")
        assert uce.supports_species("mouse")

    def test_to_dict(self):
        """to_dict should serialize model spec"""
        registry = get_registry()
        spec = registry.get("scgpt")
        d = spec.to_dict()
        assert d["name"] == "scgpt"
        assert "embed" in d["tasks"]
        assert "human" in d["species"]


class TestSCFMToolSet:
    """Tests for SCFMToolSet"""

    @pytest.fixture
    def toolset(self):
        return SCFMToolSet(name="scfm_test")

    @pytest.mark.asyncio
    async def test_scfm_list_models(self, toolset):
        """scfm_list_models should return model list"""
        result = await toolset.scfm_list_models()
        assert "count" in result
        assert "models" in result
        assert result["count"] >= 3

    @pytest.mark.asyncio
    async def test_scfm_list_models_with_task_filter(self, toolset):
        """scfm_list_models should filter by task"""
        result = await toolset.scfm_list_models(task="embed")
        assert result["count"] >= 3

        result = await toolset.scfm_list_models(task="annotate")
        # UCE doesn't support annotate, so fewer models
        model_names = [m["name"] for m in result["models"]]
        assert "uce" not in model_names

    @pytest.mark.asyncio
    async def test_scfm_describe_model(self, toolset):
        """scfm_describe_model should return full spec"""
        result = await toolset.scfm_describe_model("uce")
        assert "model" in result
        assert "input_contract" in result
        assert "output_contract" in result
        assert "resources" in result
        assert result["model"]["name"] == "uce"

    @pytest.mark.asyncio
    async def test_scfm_describe_model_not_found(self, toolset):
        """scfm_describe_model should handle unknown model"""
        result = await toolset.scfm_describe_model("nonexistent")
        assert "error" in result
        assert "available_models" in result

    @pytest.mark.asyncio
    async def test_scfm_profile_data_file_not_found(self, toolset):
        """scfm_profile_data should handle missing file"""
        result = await toolset.scfm_profile_data("/nonexistent/path.h5ad")
        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_scfm_profile_data_wrong_extension(self, toolset):
        """scfm_profile_data should check file extension"""
        with tempfile.NamedTemporaryFile(suffix=".csv") as f:
            result = await toolset.scfm_profile_data(f.name)
            assert "error" in result
            assert ".h5ad" in result["error"]

    @pytest.mark.asyncio
    async def test_scfm_preprocess_validate_unknown_model(self, toolset):
        """scfm_preprocess_validate should handle unknown model"""
        result = await toolset.scfm_preprocess_validate(
            "/some/path.h5ad", "nonexistent", "embed"
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_scfm_select_model_file_not_found(self, toolset):
        """scfm_select_model should handle missing file"""
        result = await toolset.scfm_select_model("/nonexistent/path.h5ad", "embed")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_scfm_run_unknown_model(self, toolset):
        """scfm_run should handle unknown model"""
        result = await toolset.scfm_run(
            task="embed",
            model_name="nonexistent",
            adata_path="/some/path.h5ad",
        )
        assert "error" in result


class TestTaskTypeEnum:
    """Tests for TaskType enum"""

    def test_task_values(self):
        """TaskType should have expected values"""
        assert TaskType.EMBED.value == "embed"
        assert TaskType.ANNOTATE.value == "annotate"
        assert TaskType.INTEGRATE.value == "integrate"

    def test_task_from_string(self):
        """TaskType should be creatable from string"""
        task = TaskType("embed")
        assert task == TaskType.EMBED


class TestModalityEnum:
    """Tests for Modality enum"""

    def test_modality_values(self):
        """Modality should have expected values"""
        assert Modality.RNA.value == "RNA"
        assert Modality.ATAC.value == "ATAC"
        assert Modality.SPATIAL.value == "Spatial"


class TestGeneIDSchemeEnum:
    """Tests for GeneIDScheme enum"""

    def test_gene_scheme_values(self):
        """GeneIDScheme should have expected values"""
        assert GeneIDScheme.SYMBOL.value == "symbol"
        assert GeneIDScheme.ENSEMBL.value == "ensembl"
        assert GeneIDScheme.CUSTOM.value == "custom"


# =============================================================================
# Integration Tests (require scanpy)
# =============================================================================

# Check if scanpy is available
try:
    import scanpy as sc
    import numpy as np
    HAS_SCANPY = True
except Exception:
    HAS_SCANPY = False

# Check if sklearn is available
try:
    from sklearn.metrics import silhouette_score
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

# Opt-in flag for heavy smoke tests (require real checkpoints)
import os
SCFM_RUN_HEAVY = os.environ.get("SCFM_RUN_HEAVY") == "1"


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestSCFMIntegration:
    """Integration tests for SCFM toolset with real AnnData files"""

    @pytest.mark.asyncio
    async def test_profile_real_data(self, scfm_toolset, test_adata_path):
        """scfm_profile_data should profile actual h5ad file"""
        result = await scfm_toolset.scfm_profile_data(test_adata_path)

        assert "error" not in result
        assert result["n_cells"] == 100
        assert result["n_genes"] == 200
        assert result["species"] == "human"
        assert result["gene_scheme"] == "symbol"
        assert "celltype" in result["celltype_columns"]
        assert "batch" in result["batch_columns"]

    @pytest.mark.asyncio
    async def test_select_model_for_embed(self, scfm_toolset, test_adata_path):
        """scfm_select_model should recommend a model for embedding task"""
        result = await scfm_toolset.scfm_select_model(test_adata_path, "embed")

        assert "error" not in result
        assert "recommended" in result
        assert "name" in result["recommended"]
        assert "rationale" in result["recommended"]
        assert "data_profile" in result

    @pytest.mark.asyncio
    async def test_preprocess_validate_compatible(self, scfm_toolset, test_adata_path):
        """scfm_preprocess_validate should validate compatible data"""
        result = await scfm_toolset.scfm_preprocess_validate(
            test_adata_path, "uce", "embed"
        )

        assert "error" not in result
        assert result["status"] in ["ready", "needs_preprocessing"]
        assert "diagnostics" in result
        assert "data_summary" in result

    @pytest.mark.asyncio
    async def test_preprocess_validate_incompatible_task(self, scfm_toolset, test_adata_path):
        """scfm_preprocess_validate should detect incompatible task"""
        result = await scfm_toolset.scfm_preprocess_validate(
            test_adata_path, "uce", "annotate"
        )

        # UCE doesn't support annotation
        assert result["status"] == "incompatible"
        assert any("does not support" in d["message"].lower() for d in result["diagnostics"])


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestSCFMInterpretResults:
    """Integration tests for scfm_interpret_results with UMAP generation"""

    @pytest.mark.asyncio
    async def test_interpret_with_embeddings(self, scfm_toolset, test_adata_with_embeddings, tmp_path):
        """scfm_interpret_results should analyze embeddings and generate metrics"""
        output_dir = str(tmp_path / "output")

        result = await scfm_toolset.scfm_interpret_results(
            test_adata_with_embeddings,
            task="embed",
            output_dir=output_dir,
            generate_umap=False,  # Skip UMAP to make test faster
        )

        assert "error" not in result
        assert "metrics" in result
        assert "embeddings" in result["metrics"]
        assert "X_uce" in result["metrics"]["embeddings"]
        assert result["metrics"]["embeddings"]["X_uce"]["dim"] == 1280
        assert result["metrics"]["n_cells"] == 100

        # Check provenance
        assert "provenance" in result["metrics"]

    @pytest.mark.asyncio
    @pytest.mark.skipif(not HAS_SKLEARN, reason="sklearn not installed")
    async def test_interpret_computes_silhouette(self, scfm_toolset, test_adata_with_embeddings, tmp_path):
        """scfm_interpret_results should compute silhouette score when labels exist"""
        output_dir = str(tmp_path / "output")

        result = await scfm_toolset.scfm_interpret_results(
            test_adata_with_embeddings,
            task="embed",
            output_dir=output_dir,
            generate_umap=False,
        )

        assert "error" not in result
        assert "silhouette" in result["metrics"]["embeddings"]["X_uce"]
        # Our test data has clustered embeddings, so silhouette should be positive
        assert result["metrics"]["embeddings"]["X_uce"]["silhouette"] > 0

    @pytest.mark.asyncio
    async def test_interpret_generates_umap(self, scfm_toolset, test_adata_with_embeddings, tmp_path):
        """scfm_interpret_results should generate UMAP visualizations"""
        output_dir = str(tmp_path / "output")

        result = await scfm_toolset.scfm_interpret_results(
            test_adata_with_embeddings,
            task="embed",
            output_dir=output_dir,
            generate_umap=True,
            color_by=["celltype"],
        )

        assert "error" not in result
        assert "visualizations" in result
        assert len(result["visualizations"]) > 0

        # Check that visualization files were created
        for viz in result["visualizations"]:
            assert viz["type"] == "umap"
            assert "path" in viz
            assert Path(viz["path"]).exists()

    @pytest.mark.asyncio
    async def test_interpret_no_embeddings(self, scfm_toolset, test_adata_path, tmp_path):
        """scfm_interpret_results should handle data without FM embeddings"""
        output_dir = str(tmp_path / "output")

        result = await scfm_toolset.scfm_interpret_results(
            test_adata_path,
            task="embed",
            output_dir=output_dir,
            generate_umap=False,
        )

        assert "error" not in result
        assert "warnings" in result
        assert any("no foundation model embeddings" in w.lower() for w in result["warnings"])


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestUCEAdapter:
    """Tests for UCE adapter (without actual model execution)"""

    def test_uce_adapter_init(self):
        """UCE adapter should initialize correctly"""
        from pantheon.toolsets.scfm.adapters.uce import UCEAdapter

        adapter = UCEAdapter()
        assert adapter.name == "uce"
        assert adapter.spec is not None

    def test_uce_adapter_species_mapping(self):
        """UCE adapter should have species mappings"""
        from pantheon.toolsets.scfm.adapters.uce import UCEAdapter

        adapter = UCEAdapter()
        assert "human" in adapter._species_to_name
        assert adapter._species_to_name["human"] == "Homo sapiens"
        assert "mouse" in adapter._species_to_name

    def test_uce_adapter_detect_species(self, test_adata_path):
        """UCE adapter should detect species from AnnData"""
        from pantheon.toolsets.scfm.adapters.uce import UCEAdapter

        adapter = UCEAdapter()
        adata = sc.read_h5ad(test_adata_path)
        species = adapter._detect_species(adata)
        assert species == "human"

    def test_uce_adapter_preprocess(self, test_adata_path):
        """UCE adapter should preprocess AnnData correctly"""
        from pantheon.toolsets.scfm.adapters.uce import UCEAdapter

        adapter = UCEAdapter()
        adata = sc.read_h5ad(test_adata_path)
        processed = adapter._preprocess(adata, TaskType.EMBED)

        # Should be log-normalized
        assert "log1p" in processed.uns

    @pytest.mark.asyncio
    async def test_uce_run_without_gpu(self, scfm_toolset, test_adata_path, tmp_path):
        """scfm_run with UCE should fail gracefully without GPU"""
        output_path = str(tmp_path / "output.h5ad")

        result = await scfm_toolset.scfm_run(
            task="embed",
            model_name="uce",
            adata_path=test_adata_path,
            output_path=output_path,
            device="cpu",  # Force CPU to trigger error
        )

        # Should get an error about GPU requirement
        assert "error" in result
        assert "gpu" in result["error"].lower() or "cpu" in result["error"].lower()


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestSCFMRunHappyPath:
    """Integration tests for scfm_run happy path with mock adapter"""

    @pytest.mark.asyncio
    async def test_scfm_run_success_with_mock_adapter(self, scfm_toolset, test_adata_path, tmp_path):
        """scfm_run should succeed with a mock adapter that returns embeddings"""
        from pantheon.toolsets.scfm.adapters.base import BaseAdapter

        class MockUCEAdapter(BaseAdapter):
            """Mock adapter that generates deterministic embeddings without GPU"""

            def _load_model(self, device: str):
                pass  # No-op for mock

            def _preprocess(self, adata, task):
                return adata  # No-op for mock

            def _postprocess(self, adata, result, task):
                return []  # No-op for mock

            def run(self, task, adata_path, output_path, **kwargs):
                adata = sc.read_h5ad(adata_path)
                # Generate deterministic embeddings
                np.random.seed(42)
                embedding_dim = 1280
                embeddings = np.random.randn(adata.n_obs, embedding_dim).astype(np.float32)
                adata.obsm["X_uce"] = embeddings

                # Add provenance
                adata.uns["scfm"] = {
                    "latest": {
                        "model_name": "uce",
                        "task": task.value,
                        "output_keys": ["obsm['X_uce']"],
                    }
                }

                adata.write(output_path)
                return {
                    "status": "success",
                    "output_path": output_path,
                    "output_keys": ["obsm['X_uce']"],
                    "stats": {
                        "n_cells": adata.n_obs,
                        "embedding_dim": embedding_dim,
                    },
                }

        # Store original method
        original_get_adapter = scfm_toolset._get_model_adapter

        def mock_get_adapter(model_name):
            if model_name == "uce":
                from pantheon.toolsets.scfm import get_registry
                spec = get_registry().get("uce")
                return MockUCEAdapter(spec)
            return original_get_adapter(model_name)

        # Monkey-patch the adapter getter
        scfm_toolset._get_model_adapter = mock_get_adapter

        try:
            output_path = str(tmp_path / "output.h5ad")
            result = await scfm_toolset.scfm_run(
                task="embed",
                model_name="uce",
                adata_path=test_adata_path,
                output_path=output_path,
            )

            # Verify success
            assert "error" not in result, f"Unexpected error: {result.get('error')}"
            assert result.get("status") == "success"
            assert "output_keys" in result
            assert "obsm['X_uce']" in result["output_keys"]

            # Verify output file
            assert Path(output_path).exists()
            result_adata = sc.read_h5ad(output_path)
            assert "X_uce" in result_adata.obsm
            assert result_adata.obsm["X_uce"].shape == (100, 1280)

        finally:
            # Restore original method
            scfm_toolset._get_model_adapter = original_get_adapter

    @pytest.mark.asyncio
    async def test_scfm_run_followed_by_interpret(self, scfm_toolset, test_adata_path, tmp_path):
        """scfm_run followed by scfm_interpret_results should work end-to-end"""
        from pantheon.toolsets.scfm.adapters.base import BaseAdapter

        class MockUCEAdapter(BaseAdapter):
            def _load_model(self, device: str):
                pass  # No-op for mock

            def _preprocess(self, adata, task):
                return adata  # No-op for mock

            def _postprocess(self, adata, result, task):
                return []  # No-op for mock

            def run(self, task, adata_path, output_path, **kwargs):
                adata = sc.read_h5ad(adata_path)
                np.random.seed(42)

                # Create embeddings with cluster structure for meaningful metrics
                cell_types = adata.obs["celltype"].values
                embeddings = np.random.randn(adata.n_obs, 1280).astype(np.float32) * 0.1

                for i, ct in enumerate(cell_types):
                    if ct == "TypeA":
                        embeddings[i, 0] += 1.0
                    elif ct == "TypeB":
                        embeddings[i, 1] += 1.0
                    else:
                        embeddings[i, 2] += 1.0

                adata.obsm["X_uce"] = embeddings
                adata.uns["scfm"] = {
                    "latest": {
                        "model_name": "uce",
                        "task": task.value,
                        "output_keys": ["obsm['X_uce']"],
                    }
                }
                adata.write(output_path)
                return {
                    "status": "success",
                    "output_path": output_path,
                    "output_keys": ["obsm['X_uce']"],
                }

        original_get_adapter = scfm_toolset._get_model_adapter

        def mock_get_adapter(model_name):
            if model_name == "uce":
                from pantheon.toolsets.scfm import get_registry
                return MockUCEAdapter(get_registry().get("uce"))
            return original_get_adapter(model_name)

        scfm_toolset._get_model_adapter = mock_get_adapter

        try:
            # Step 1: Run embedding
            output_path = str(tmp_path / "embedded.h5ad")
            run_result = await scfm_toolset.scfm_run(
                task="embed",
                model_name="uce",
                adata_path=test_adata_path,
                output_path=output_path,
            )
            assert run_result.get("status") == "success"

            # Step 2: Interpret results
            interpret_output = str(tmp_path / "interpret_output")
            interpret_result = await scfm_toolset.scfm_interpret_results(
                adata_path=output_path,
                task="embed",
                output_dir=interpret_output,
                generate_umap=False,  # Skip UMAP for faster test
            )

            assert "error" not in interpret_result
            assert "metrics" in interpret_result
            assert "embeddings" in interpret_result["metrics"]
            assert "X_uce" in interpret_result["metrics"]["embeddings"]

        finally:
            scfm_toolset._get_model_adapter = original_get_adapter


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestScGPTAdapter:
    """Tests for scGPT adapter (without actual model execution)"""

    def test_scgpt_adapter_init(self):
        """ScGPT adapter should initialize correctly"""
        from pantheon.toolsets.scfm.adapters.scgpt import ScGPTAdapter

        adapter = ScGPTAdapter()
        assert adapter.name == "scgpt"
        assert adapter.spec is not None

    def test_scgpt_adapter_species_detection(self, test_adata_path):
        """ScGPT adapter should detect species from AnnData"""
        from pantheon.toolsets.scfm.adapters.scgpt import ScGPTAdapter

        adapter = ScGPTAdapter()
        adata = sc.read_h5ad(test_adata_path)
        species = adapter._detect_species(adata)
        assert species == "human"

    def test_scgpt_adapter_preprocess(self, test_adata_path):
        """ScGPT adapter should preprocess AnnData correctly"""
        from pantheon.toolsets.scfm.adapters.scgpt import ScGPTAdapter

        adapter = ScGPTAdapter()
        adata = sc.read_h5ad(test_adata_path)
        processed = adapter._preprocess(adata, TaskType.EMBED)

        # Should be normalized (no log1p for scGPT)
        assert processed is not None
        assert processed.n_obs == adata.n_obs

    @pytest.mark.asyncio
    async def test_scgpt_run_without_checkpoint(self, scfm_toolset, test_adata_path, tmp_path):
        """scfm_run with scGPT should fail gracefully without checkpoint"""
        output_path = str(tmp_path / "output.h5ad")

        result = await scfm_toolset.scfm_run(
            task="embed",
            model_name="scgpt",
            adata_path=test_adata_path,
            output_path=output_path,
        )

        # Should get an error about checkpoint or package not found
        assert "error" in result

    @pytest.mark.asyncio
    async def test_scgpt_run_success_with_mock_adapter(self, scfm_toolset, test_adata_path, tmp_path):
        """scfm_run with scGPT should succeed with a mock adapter"""
        from pantheon.toolsets.scfm.adapters.base import BaseAdapter

        class MockScGPTAdapter(BaseAdapter):
            """Mock adapter that generates deterministic scGPT-style embeddings"""

            def _load_model(self, device: str):
                pass  # No-op for mock

            def _preprocess(self, adata, task):
                return adata  # No-op for mock

            def _postprocess(self, adata, result, task):
                return []  # No-op for mock

            def run(self, task, adata_path, output_path, **kwargs):
                adata = sc.read_h5ad(adata_path)
                # Generate deterministic embeddings (512-dim for scGPT)
                np.random.seed(42)
                embedding_dim = 512
                embeddings = np.random.randn(adata.n_obs, embedding_dim).astype(np.float32)
                adata.obsm["X_scGPT"] = embeddings

                # Add provenance
                adata.uns["scfm"] = {
                    "latest": {
                        "model_name": "scgpt",
                        "task": task.value,
                        "output_keys": ["obsm['X_scGPT']"],
                    }
                }

                adata.write(output_path)
                return {
                    "status": "success",
                    "output_path": output_path,
                    "output_keys": ["obsm['X_scGPT']"],
                    "stats": {
                        "n_cells": adata.n_obs,
                        "embedding_dim": embedding_dim,
                    },
                }

        # Store original method
        original_get_adapter = scfm_toolset._get_model_adapter

        def mock_get_adapter(model_name):
            if model_name == "scgpt":
                from pantheon.toolsets.scfm import get_registry
                spec = get_registry().get("scgpt")
                return MockScGPTAdapter(spec)
            return original_get_adapter(model_name)

        # Monkey-patch the adapter getter
        scfm_toolset._get_model_adapter = mock_get_adapter

        try:
            output_path = str(tmp_path / "output.h5ad")
            result = await scfm_toolset.scfm_run(
                task="embed",
                model_name="scgpt",
                adata_path=test_adata_path,
                output_path=output_path,
            )

            # Verify success
            assert "error" not in result, f"Unexpected error: {result.get('error')}"
            assert result.get("status") == "success"
            assert "output_keys" in result
            assert "obsm['X_scGPT']" in result["output_keys"]

            # Verify output file
            assert Path(output_path).exists()
            result_adata = sc.read_h5ad(output_path)
            assert "X_scGPT" in result_adata.obsm
            assert result_adata.obsm["X_scGPT"].shape == (100, 512)  # 512-dim for scGPT

        finally:
            # Restore original method
            scfm_toolset._get_model_adapter = original_get_adapter


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestGeneformerAdapter:
    """Tests for Geneformer adapter (without actual model execution)"""

    @pytest.fixture
    def ensembl_adata_path(self, tmp_path):
        """Create a test AnnData file with Ensembl gene IDs for Geneformer"""
        n_cells = 100
        n_genes = 200

        np.random.seed(42)
        X = np.random.poisson(lam=2, size=(n_cells, n_genes)).astype(np.float32)

        # Use Ensembl-style gene IDs (required for Geneformer)
        gene_names = [f"ENSG{i:011d}" for i in range(n_genes)]
        cell_names = [f"CELL_{i:04d}" for i in range(n_cells)]

        adata = sc.AnnData(X=X)
        adata.var_names = gene_names
        adata.obs_names = cell_names
        adata.obs["celltype"] = np.random.choice(["TypeA", "TypeB", "TypeC"], size=n_cells)
        adata.obs["batch"] = np.random.choice(["batch1", "batch2"], size=n_cells)
        adata.uns["species"] = "human"

        adata_path = tmp_path / "ensembl_test_data.h5ad"
        adata.write(str(adata_path))
        return str(adata_path)

    def test_geneformer_adapter_init(self):
        """Geneformer adapter should initialize correctly"""
        from pantheon.toolsets.scfm.adapters.geneformer import GeneformerAdapter

        adapter = GeneformerAdapter()
        assert adapter.name == "geneformer"
        assert adapter.spec is not None

    def test_geneformer_adapter_species_detection(self, ensembl_adata_path):
        """Geneformer adapter should detect species from AnnData"""
        from pantheon.toolsets.scfm.adapters.geneformer import GeneformerAdapter

        adapter = GeneformerAdapter()
        adata = sc.read_h5ad(ensembl_adata_path)
        species = adapter._detect_species(adata)
        assert species == "human"

    def test_geneformer_adapter_gene_scheme_detection(self, ensembl_adata_path):
        """Geneformer adapter should detect Ensembl gene IDs"""
        from pantheon.toolsets.scfm.adapters.geneformer import GeneformerAdapter
        from pantheon.toolsets.scfm import GeneIDScheme

        adapter = GeneformerAdapter()
        adata = sc.read_h5ad(ensembl_adata_path)
        scheme = adapter._detect_gene_scheme(adata)
        assert scheme == GeneIDScheme.ENSEMBL

    def test_geneformer_rejects_symbol_genes(self, test_adata_path):
        """Geneformer should detect non-Ensembl genes"""
        from pantheon.toolsets.scfm.adapters.geneformer import GeneformerAdapter
        from pantheon.toolsets.scfm import GeneIDScheme

        adapter = GeneformerAdapter()
        # test_adata_path has gene symbols (GENE0, GENE1, etc.)
        adata = sc.read_h5ad(test_adata_path)
        scheme = adapter._detect_gene_scheme(adata)

        # Should NOT be Ensembl
        assert scheme != GeneIDScheme.ENSEMBL

    def test_geneformer_adapter_preprocess(self, ensembl_adata_path):
        """Geneformer adapter should preprocess AnnData correctly"""
        from pantheon.toolsets.scfm.adapters.geneformer import GeneformerAdapter

        adapter = GeneformerAdapter()
        adata = sc.read_h5ad(ensembl_adata_path)
        processed = adapter._preprocess(adata, TaskType.EMBED)

        # Should have n_counts calculated
        assert "n_counts" in processed.obs
        assert processed.n_obs == adata.n_obs

    @pytest.mark.asyncio
    async def test_geneformer_run_without_checkpoint(self, scfm_toolset, ensembl_adata_path, tmp_path):
        """scfm_run with Geneformer should fail gracefully without checkpoint"""
        output_path = str(tmp_path / "output.h5ad")

        result = await scfm_toolset.scfm_run(
            task="embed",
            model_name="geneformer",
            adata_path=ensembl_adata_path,
            output_path=output_path,
        )

        # Should get an error about checkpoint or package not found
        assert "error" in result

    @pytest.mark.asyncio
    async def test_geneformer_run_success_with_mock_adapter(self, scfm_toolset, ensembl_adata_path, tmp_path):
        """scfm_run with Geneformer should succeed with a mock adapter"""
        from pantheon.toolsets.scfm.adapters.base import BaseAdapter

        class MockGeneformerAdapter(BaseAdapter):
            """Mock adapter that generates deterministic Geneformer-style embeddings"""

            def _load_model(self, device: str):
                pass  # No-op for mock

            def _preprocess(self, adata, task):
                return adata  # No-op for mock

            def _postprocess(self, adata, result, task):
                return []  # No-op for mock

            def run(self, task, adata_path, output_path, **kwargs):
                adata = sc.read_h5ad(adata_path)
                # Generate deterministic embeddings (512-dim for Geneformer)
                np.random.seed(42)
                embedding_dim = 512
                embeddings = np.random.randn(adata.n_obs, embedding_dim).astype(np.float32)
                adata.obsm["X_geneformer"] = embeddings

                # Add provenance
                adata.uns["scfm"] = {
                    "latest": {
                        "model_name": "geneformer",
                        "task": task.value,
                        "output_keys": ["obsm['X_geneformer']"],
                    }
                }

                adata.write(output_path)
                return {
                    "status": "success",
                    "output_path": output_path,
                    "output_keys": ["obsm['X_geneformer']"],
                    "stats": {
                        "n_cells": adata.n_obs,
                        "embedding_dim": embedding_dim,
                    },
                }

        # Store original method
        original_get_adapter = scfm_toolset._get_model_adapter

        def mock_get_adapter(model_name):
            if model_name == "geneformer":
                from pantheon.toolsets.scfm import get_registry
                spec = get_registry().get("geneformer")
                return MockGeneformerAdapter(spec)
            return original_get_adapter(model_name)

        # Monkey-patch the adapter getter
        scfm_toolset._get_model_adapter = mock_get_adapter

        try:
            output_path = str(tmp_path / "output.h5ad")
            result = await scfm_toolset.scfm_run(
                task="embed",
                model_name="geneformer",
                adata_path=ensembl_adata_path,
                output_path=output_path,
            )

            # Verify success
            assert "error" not in result, f"Unexpected error: {result.get('error')}"
            assert result.get("status") == "success"
            assert "output_keys" in result
            assert "obsm['X_geneformer']" in result["output_keys"]

            # Verify output file
            assert Path(output_path).exists()
            result_adata = sc.read_h5ad(output_path)
            assert "X_geneformer" in result_adata.obsm
            assert result_adata.obsm["X_geneformer"].shape == (100, 512)  # 512-dim for Geneformer

        finally:
            # Restore original method
            scfm_toolset._get_model_adapter = original_get_adapter


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestScFoundationAdapter:
    """Tests for scFoundation adapter (without actual model execution)"""

    def test_scfoundation_adapter_init(self):
        """scFoundation adapter should initialize correctly"""
        from pantheon.toolsets.scfm.adapters.scfoundation import ScFoundationAdapter

        adapter = ScFoundationAdapter()
        assert adapter.name == "scfoundation"
        assert adapter.spec is not None

    def test_scfoundation_adapter_species_detection(self, test_adata_path):
        """scFoundation adapter should detect species from AnnData"""
        from pantheon.toolsets.scfm.adapters.scfoundation import ScFoundationAdapter

        adapter = ScFoundationAdapter()
        adata = sc.read_h5ad(test_adata_path)
        species = adapter._detect_species(adata)
        # scFoundation defaults to human
        assert species == "human"

    def test_scfoundation_adapter_preprocess(self, test_adata_path):
        """scFoundation adapter should preprocess AnnData correctly"""
        from pantheon.toolsets.scfm.adapters.scfoundation import ScFoundationAdapter

        adapter = ScFoundationAdapter()
        adata = sc.read_h5ad(test_adata_path)
        processed = adapter._preprocess(adata, TaskType.EMBED)

        # Should have n_counts calculated
        assert "n_counts" in processed.obs
        assert processed.n_obs == adata.n_obs

    def test_scfoundation_requires_gpu(self, test_adata_path, tmp_path):
        """scFoundation should fail gracefully without GPU"""
        from pantheon.toolsets.scfm.adapters.scfoundation import ScFoundationAdapter

        adapter = ScFoundationAdapter()
        output_path = str(tmp_path / "output.h5ad")

        result = adapter.run(
            task=TaskType.EMBED,
            adata_path=test_adata_path,
            output_path=output_path,
            device="cpu",  # Force CPU to trigger error
        )

        # Should get an error about GPU requirement
        assert "error" in result
        assert "gpu" in result["error"].lower() or "cpu" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_scfoundation_run_without_checkpoint(self, scfm_toolset, test_adata_path, tmp_path):
        """scfm_run with scFoundation should fail gracefully without checkpoint"""
        output_path = str(tmp_path / "output.h5ad")

        result = await scfm_toolset.scfm_run(
            task="embed",
            model_name="scfoundation",
            adata_path=test_adata_path,
            output_path=output_path,
        )

        # Should get an error about GPU, package, or checkpoint not found
        assert "error" in result

    @pytest.mark.asyncio
    async def test_scfoundation_run_success_with_mock_adapter(self, scfm_toolset, test_adata_path, tmp_path):
        """scfm_run with scFoundation should succeed with a mock adapter"""
        from pantheon.toolsets.scfm.adapters.base import BaseAdapter

        class MockScFoundationAdapter(BaseAdapter):
            """Mock adapter that generates deterministic scFoundation-style embeddings"""

            def _load_model(self, device: str):
                pass  # No-op for mock

            def _preprocess(self, adata, task):
                return adata  # No-op for mock

            def _postprocess(self, adata, result, task):
                return []  # No-op for mock

            def run(self, task, adata_path, output_path, **kwargs):
                adata = sc.read_h5ad(adata_path)
                # Generate deterministic embeddings (512-dim for scFoundation)
                np.random.seed(42)
                embedding_dim = 512
                embeddings = np.random.randn(adata.n_obs, embedding_dim).astype(np.float32)
                adata.obsm["X_scfoundation"] = embeddings

                # Add provenance
                adata.uns["scfm"] = {
                    "latest": {
                        "model_name": "scfoundation",
                        "task": task.value,
                        "output_keys": ["obsm['X_scfoundation']"],
                    }
                }

                adata.write(output_path)
                return {
                    "status": "success",
                    "output_path": output_path,
                    "output_keys": ["obsm['X_scfoundation']"],
                    "stats": {
                        "n_cells": adata.n_obs,
                        "embedding_dim": embedding_dim,
                    },
                }

        # Store original method
        original_get_adapter = scfm_toolset._get_model_adapter

        def mock_get_adapter(model_name):
            if model_name == "scfoundation":
                from pantheon.toolsets.scfm import get_registry
                spec = get_registry().get("scfoundation")
                return MockScFoundationAdapter(spec)
            return original_get_adapter(model_name)

        # Monkey-patch the adapter getter
        scfm_toolset._get_model_adapter = mock_get_adapter

        try:
            output_path = str(tmp_path / "output.h5ad")
            result = await scfm_toolset.scfm_run(
                task="embed",
                model_name="scfoundation",
                adata_path=test_adata_path,
                output_path=output_path,
            )

            # Verify success
            assert "error" not in result, f"Unexpected error: {result.get('error')}"
            assert result.get("status") == "success"
            assert "output_keys" in result
            assert "obsm['X_scfoundation']" in result["output_keys"]

            # Verify output file
            assert Path(output_path).exists()
            result_adata = sc.read_h5ad(output_path)
            assert "X_scfoundation" in result_adata.obsm
            assert result_adata.obsm["X_scfoundation"].shape == (100, 512)  # 512-dim for scFoundation

        finally:
            # Restore original method
            scfm_toolset._get_model_adapter = original_get_adapter


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestScBERTAdapter:
    """Tests for scBERT adapter (without actual model execution)"""

    def test_scbert_adapter_init(self):
        """scBERT adapter should initialize correctly"""
        from pantheon.toolsets.scfm.adapters.scbert import ScBERTAdapter

        adapter = ScBERTAdapter()
        assert adapter.name == "scbert"
        assert adapter.spec is not None

    def test_scbert_adapter_species_detection(self, test_adata_path):
        """scBERT adapter should detect species from AnnData"""
        from pantheon.toolsets.scfm.adapters.scbert import ScBERTAdapter

        adapter = ScBERTAdapter()
        adata = sc.read_h5ad(test_adata_path)
        species = adapter._detect_species(adata)
        assert species == "human"

    def test_scbert_adapter_preprocess(self, test_adata_path):
        """scBERT adapter should preprocess AnnData correctly"""
        from pantheon.toolsets.scfm.adapters.scbert import ScBERTAdapter

        adapter = ScBERTAdapter()
        adata = sc.read_h5ad(test_adata_path)
        processed = adapter._preprocess(adata, TaskType.EMBED)

        assert "log1p" in processed.uns
        assert processed.n_obs == adata.n_obs


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestGeneCompassAdapter:
    """Tests for GeneCompass adapter (without actual model execution)"""

    def test_genecompass_adapter_init(self):
        """GeneCompass adapter should initialize correctly"""
        from pantheon.toolsets.scfm.adapters.genecompass import GeneCompassAdapter

        adapter = GeneCompassAdapter()
        assert adapter.name == "genecompass"
        assert adapter.spec is not None

    def test_genecompass_adapter_species_detection(self, test_adata_path):
        """GeneCompass adapter should detect species from AnnData"""
        from pantheon.toolsets.scfm.adapters.genecompass import GeneCompassAdapter

        adapter = GeneCompassAdapter()
        adata = sc.read_h5ad(test_adata_path)
        species = adapter._detect_species(adata)
        assert species == "human"

    def test_genecompass_requires_gpu(self, test_adata_path, tmp_path):
        """GeneCompass should fail gracefully without GPU"""
        from pantheon.toolsets.scfm.adapters.genecompass import GeneCompassAdapter

        adapter = GeneCompassAdapter()
        output_path = str(tmp_path / "output.h5ad")

        result = adapter.run(
            task=TaskType.EMBED,
            adata_path=test_adata_path,
            output_path=output_path,
            device="cpu",
        )

        assert "error" in result
        assert "gpu" in result["error"].lower() or "cpu" in result["error"].lower()


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestCellPLMAdapter:
    """Tests for CellPLM adapter (without actual model execution)"""

    def test_cellplm_adapter_init(self):
        """CellPLM adapter should initialize correctly"""
        from pantheon.toolsets.scfm.adapters.cellplm import CellPLMAdapter

        adapter = CellPLMAdapter()
        assert adapter.name == "cellplm"
        assert adapter.spec is not None

    def test_cellplm_adapter_species_detection(self, test_adata_path):
        """CellPLM adapter should detect species from AnnData"""
        from pantheon.toolsets.scfm.adapters.cellplm import CellPLMAdapter

        adapter = CellPLMAdapter()
        adata = sc.read_h5ad(test_adata_path)
        species = adapter._detect_species(adata)
        assert species == "human"


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestNicheformerAdapter:
    """Tests for Nicheformer adapter (without actual model execution)"""

    def test_nicheformer_adapter_init(self):
        """Nicheformer adapter should initialize correctly"""
        from pantheon.toolsets.scfm.adapters.nicheformer import NicheformerAdapter

        adapter = NicheformerAdapter()
        assert adapter.name == "nicheformer"
        assert adapter.spec is not None

    def test_nicheformer_adapter_species_detection(self, test_adata_path):
        """Nicheformer adapter should detect species from AnnData"""
        from pantheon.toolsets.scfm.adapters.nicheformer import NicheformerAdapter

        adapter = NicheformerAdapter()
        adata = sc.read_h5ad(test_adata_path)
        species = adapter._detect_species(adata)
        assert species == "human"

    def test_nicheformer_requires_gpu(self, test_adata_path, tmp_path):
        """Nicheformer should fail gracefully without GPU"""
        from pantheon.toolsets.scfm.adapters.nicheformer import NicheformerAdapter

        adapter = NicheformerAdapter()
        output_path = str(tmp_path / "output.h5ad")

        result = adapter.run(
            task=TaskType.EMBED,
            adata_path=test_adata_path,
            output_path=output_path,
            device="cpu",
        )

        assert "error" in result
        assert "gpu" in result["error"].lower() or "cpu" in result["error"].lower()


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestScMulanAdapter:
    """Tests for scMulan adapter (without actual model execution)"""

    def test_scmulan_adapter_init(self):
        """scMulan adapter should initialize correctly"""
        from pantheon.toolsets.scfm.adapters.scmulan import ScMulanAdapter

        adapter = ScMulanAdapter()
        assert adapter.name == "scmulan"
        assert adapter.spec is not None

    def test_scmulan_adapter_species_detection(self, test_adata_path):
        """scMulan adapter should detect species from AnnData"""
        from pantheon.toolsets.scfm.adapters.scmulan import ScMulanAdapter

        adapter = ScMulanAdapter()
        adata = sc.read_h5ad(test_adata_path)
        species = adapter._detect_species(adata)
        assert species == "human"

    def test_scmulan_adapter_modality_detection(self, test_adata_path):
        """scMulan adapter should detect modality from AnnData"""
        from pantheon.toolsets.scfm.adapters.scmulan import ScMulanAdapter

        adapter = ScMulanAdapter()
        adata = sc.read_h5ad(test_adata_path)
        modality = adapter._detect_modality(adata)
        assert modality == "RNA"  # Default for test data

    def test_scmulan_requires_gpu(self, test_adata_path, tmp_path):
        """scMulan should fail gracefully without GPU"""
        from pantheon.toolsets.scfm.adapters.scmulan import ScMulanAdapter

        adapter = ScMulanAdapter()
        output_path = str(tmp_path / "output.h5ad")

        result = adapter.run(
            task=TaskType.EMBED,
            adata_path=test_adata_path,
            output_path=output_path,
            device="cpu",
        )

        assert "error" in result
        assert "gpu" in result["error"].lower() or "cpu" in result["error"].lower()


class TestNewModelsInRegistry:
    """Tests for new model specs in registry"""

    def test_scbert_in_registry(self):
        """scBERT should be registered"""
        registry = get_registry()
        spec = registry.get("scbert")
        assert spec is not None
        assert spec.name == "scbert"
        assert TaskType.EMBED in spec.tasks

    def test_genecompass_in_registry(self):
        """GeneCompass should be registered"""
        registry = get_registry()
        spec = registry.get("genecompass")
        assert spec is not None
        assert spec.name == "genecompass"
        assert "human" in spec.species
        assert "mouse" in spec.species

    def test_cellplm_in_registry(self):
        """CellPLM should be registered"""
        registry = get_registry()
        spec = registry.get("cellplm")
        assert spec is not None
        assert spec.name == "cellplm"
        assert spec.hardware.cpu_fallback is True

    def test_nicheformer_in_registry(self):
        """Nicheformer should be registered"""
        registry = get_registry()
        spec = registry.get("nicheformer")
        assert spec is not None
        assert spec.name == "nicheformer"
        assert TaskType.SPATIAL in spec.tasks

    def test_scmulan_in_registry(self):
        """scMulan should be registered"""
        registry = get_registry()
        spec = registry.get("scmulan")
        assert spec is not None
        assert spec.name == "scmulan"
        from pantheon.toolsets.scfm import Modality
        assert Modality.MULTIOMICS in spec.modalities

    def test_registry_model_count(self):
        """Registry should have 21 models total (9 core + 12 specialized)"""
        registry = get_registry()
        models = registry.list_models(skill_ready_only=False)
        # 9 core models + 12 Specialized & Emerging (2024-2025)
        assert len(models) == 21


# =============================================================================
# Tests for Specialized & Emerging Models (2024-2025)
# =============================================================================


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestTGPTAdapter:
    """Tests for tGPT adapter"""

    def test_tgpt_adapter_init(self):
        """tGPT adapter should initialize correctly"""
        from pantheon.toolsets.scfm.adapters.tgpt import TGPTAdapter

        adapter = TGPTAdapter()
        assert adapter.name == "tgpt"
        assert adapter.spec is not None

    def test_tgpt_adapter_species_detection(self, test_adata_path):
        """tGPT adapter should detect species from AnnData"""
        from pantheon.toolsets.scfm.adapters.tgpt import TGPTAdapter

        adapter = TGPTAdapter()
        adata = sc.read_h5ad(test_adata_path)
        species = adapter._detect_species(adata)
        assert species == "human"

    def test_tgpt_in_registry(self):
        """tGPT should be registered"""
        registry = get_registry()
        spec = registry.get("tgpt")
        assert spec is not None
        assert spec.name == "tgpt"
        assert TaskType.EMBED in spec.tasks


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestCellFMAdapter:
    """Tests for CellFM adapter"""

    def test_cellfm_adapter_init(self):
        """CellFM adapter should initialize correctly"""
        from pantheon.toolsets.scfm.adapters.cellfm import CellFMAdapter

        adapter = CellFMAdapter()
        assert adapter.name == "cellfm"
        assert adapter.spec is not None

    def test_cellfm_adapter_species_detection(self, test_adata_path):
        """CellFM adapter should detect species from AnnData"""
        from pantheon.toolsets.scfm.adapters.cellfm import CellFMAdapter

        adapter = CellFMAdapter()
        adata = sc.read_h5ad(test_adata_path)
        species = adapter._detect_species(adata)
        assert species == "human"

    def test_cellfm_in_registry(self):
        """CellFM should be registered"""
        registry = get_registry()
        spec = registry.get("cellfm")
        assert spec is not None
        assert spec.name == "cellfm"
        assert TaskType.EMBED in spec.tasks


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestScCelloAdapter:
    """Tests for scCello adapter"""

    def test_sccello_adapter_init(self):
        """scCello adapter should initialize correctly"""
        from pantheon.toolsets.scfm.adapters.sccello import ScCelloAdapter

        adapter = ScCelloAdapter()
        assert adapter.name == "sccello"
        assert adapter.spec is not None

    def test_sccello_adapter_species_detection(self, test_adata_path):
        """scCello adapter should detect species from AnnData"""
        from pantheon.toolsets.scfm.adapters.sccello import ScCelloAdapter

        adapter = ScCelloAdapter()
        adata = sc.read_h5ad(test_adata_path)
        species = adapter._detect_species(adata)
        assert species == "human"

    def test_sccello_in_registry(self):
        """scCello should be registered"""
        registry = get_registry()
        spec = registry.get("sccello")
        assert spec is not None
        assert spec.name == "sccello"
        assert TaskType.ANNOTATE in spec.tasks  # scCello supports zero-shot annotation
        assert spec.zero_shot_annotation is True


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestScPRINTAdapter:
    """Tests for scPRINT adapter"""

    def test_scprint_adapter_init(self):
        """scPRINT adapter should initialize correctly"""
        from pantheon.toolsets.scfm.adapters.scprint import ScPRINTAdapter

        adapter = ScPRINTAdapter()
        assert adapter.name == "scprint"
        assert adapter.spec is not None

    def test_scprint_adapter_species_detection(self, test_adata_path):
        """scPRINT adapter should detect species from AnnData"""
        from pantheon.toolsets.scfm.adapters.scprint import ScPRINTAdapter

        adapter = ScPRINTAdapter()
        adata = sc.read_h5ad(test_adata_path)
        species = adapter._detect_species(adata)
        assert species == "human"

    def test_scprint_in_registry(self):
        """scPRINT should be registered"""
        registry = get_registry()
        spec = registry.get("scprint")
        assert spec is not None
        assert spec.name == "scprint"
        assert TaskType.EMBED in spec.tasks


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestAIDOCellAdapter:
    """Tests for AIDO.Cell adapter"""

    def test_aidocell_adapter_init(self):
        """AIDO.Cell adapter should initialize correctly"""
        from pantheon.toolsets.scfm.adapters.aidocell import AIDOCellAdapter

        adapter = AIDOCellAdapter()
        assert adapter.name == "aidocell"
        assert adapter.spec is not None

    def test_aidocell_adapter_species_detection(self, test_adata_path):
        """AIDO.Cell adapter should detect species from AnnData"""
        from pantheon.toolsets.scfm.adapters.aidocell import AIDOCellAdapter

        adapter = AIDOCellAdapter()
        adata = sc.read_h5ad(test_adata_path)
        species = adapter._detect_species(adata)
        assert species == "human"

    def test_aidocell_in_registry(self):
        """AIDO.Cell should be registered"""
        registry = get_registry()
        spec = registry.get("aidocell")
        assert spec is not None
        assert spec.name == "aidocell"
        assert TaskType.EMBED in spec.tasks


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestPULSARAdapter:
    """Tests for PULSAR adapter"""

    def test_pulsar_adapter_init(self):
        """PULSAR adapter should initialize correctly"""
        from pantheon.toolsets.scfm.adapters.pulsar import PULSARAdapter

        adapter = PULSARAdapter()
        assert adapter.name == "pulsar"
        assert adapter.spec is not None

    def test_pulsar_adapter_species_detection(self, test_adata_path):
        """PULSAR adapter should detect species from AnnData"""
        from pantheon.toolsets.scfm.adapters.pulsar import PULSARAdapter

        adapter = PULSARAdapter()
        adata = sc.read_h5ad(test_adata_path)
        species = adapter._detect_species(adata)
        assert species == "human"

    def test_pulsar_in_registry(self):
        """PULSAR should be registered"""
        registry = get_registry()
        spec = registry.get("pulsar")
        assert spec is not None
        assert spec.name == "pulsar"
        assert TaskType.EMBED in spec.tasks


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestAtacformerAdapter:
    """Tests for Atacformer adapter"""

    def test_atacformer_adapter_init(self):
        """Atacformer adapter should initialize correctly"""
        from pantheon.toolsets.scfm.adapters.atacformer import AtacformerAdapter

        adapter = AtacformerAdapter()
        assert adapter.name == "atacformer"
        assert adapter.spec is not None

    def test_atacformer_adapter_species_detection(self, test_adata_path):
        """Atacformer adapter should detect species from AnnData"""
        from pantheon.toolsets.scfm.adapters.atacformer import AtacformerAdapter

        adapter = AtacformerAdapter()
        adata = sc.read_h5ad(test_adata_path)
        species = adapter._detect_species(adata)
        assert species == "human"

    def test_atacformer_in_registry(self):
        """Atacformer should be registered"""
        registry = get_registry()
        spec = registry.get("atacformer")
        assert spec is not None
        assert spec.name == "atacformer"
        from pantheon.toolsets.scfm import Modality
        assert Modality.ATAC in spec.modalities


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestScPlantLLMAdapter:
    """Tests for scPlantLLM adapter"""

    def test_scplantllm_adapter_init(self):
        """scPlantLLM adapter should initialize correctly"""
        from pantheon.toolsets.scfm.adapters.scplantllm import ScPlantLLMAdapter

        adapter = ScPlantLLMAdapter()
        assert adapter.name == "scplantllm"
        assert adapter.spec is not None

    def test_scplantllm_in_registry(self):
        """scPlantLLM should be registered"""
        registry = get_registry()
        spec = registry.get("scplantllm")
        assert spec is not None
        assert spec.name == "scplantllm"
        assert "plant" in spec.species


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestLangCellAdapter:
    """Tests for LangCell adapter"""

    def test_langcell_adapter_init(self):
        """LangCell adapter should initialize correctly"""
        from pantheon.toolsets.scfm.adapters.langcell import LangCellAdapter

        adapter = LangCellAdapter()
        assert adapter.name == "langcell"
        assert adapter.spec is not None

    def test_langcell_adapter_species_detection(self, test_adata_path):
        """LangCell adapter should detect species from AnnData"""
        from pantheon.toolsets.scfm.adapters.langcell import LangCellAdapter

        adapter = LangCellAdapter()
        adata = sc.read_h5ad(test_adata_path)
        species = adapter._detect_species(adata)
        assert species == "human"

    def test_langcell_in_registry(self):
        """LangCell should be registered"""
        registry = get_registry()
        spec = registry.get("langcell")
        assert spec is not None
        assert spec.name == "langcell"
        assert TaskType.EMBED in spec.tasks


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestCell2SentenceAdapter:
    """Tests for Cell2Sentence adapter"""

    def test_cell2sentence_adapter_init(self):
        """Cell2Sentence adapter should initialize correctly"""
        from pantheon.toolsets.scfm.adapters.cell2sentence import Cell2SentenceAdapter

        adapter = Cell2SentenceAdapter()
        assert adapter.name == "cell2sentence"
        assert adapter.spec is not None

    def test_cell2sentence_adapter_species_detection(self, test_adata_path):
        """Cell2Sentence adapter should detect species from AnnData"""
        from pantheon.toolsets.scfm.adapters.cell2sentence import Cell2SentenceAdapter

        adapter = Cell2SentenceAdapter()
        adata = sc.read_h5ad(test_adata_path)
        species = adapter._detect_species(adata)
        assert species == "human"

    def test_cell2sentence_in_registry(self):
        """Cell2Sentence should be registered"""
        registry = get_registry()
        spec = registry.get("cell2sentence")
        assert spec is not None
        assert spec.name == "cell2sentence"
        assert spec.embedding_dim == 768  # LLM dimension


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestGenePTAdapter:
    """Tests for GenePT adapter"""

    def test_genept_adapter_init(self):
        """GenePT adapter should initialize correctly"""
        from pantheon.toolsets.scfm.adapters.genept import GenePTAdapter

        adapter = GenePTAdapter()
        assert adapter.name == "genept"
        assert adapter.spec is not None

    def test_genept_adapter_species_detection(self, test_adata_path):
        """GenePT adapter should detect species from AnnData"""
        from pantheon.toolsets.scfm.adapters.genept import GenePTAdapter

        adapter = GenePTAdapter()
        adata = sc.read_h5ad(test_adata_path)
        species = adapter._detect_species(adata)
        assert species == "human"

    def test_genept_in_registry(self):
        """GenePT should be registered"""
        registry = get_registry()
        spec = registry.get("genept")
        assert spec is not None
        assert spec.name == "genept"
        assert spec.embedding_dim == 1536  # GPT-3.5 dimension
        assert spec.hardware.gpu_required is False  # API-based


@pytest.mark.integration
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestCHATCELLAdapter:
    """Tests for CHATCELL adapter"""

    def test_chatcell_adapter_init(self):
        """CHATCELL adapter should initialize correctly"""
        from pantheon.toolsets.scfm.adapters.chatcell import CHATCELLAdapter

        adapter = CHATCELLAdapter()
        assert adapter.name == "chatcell"
        assert adapter.spec is not None

    def test_chatcell_adapter_species_detection(self, test_adata_path):
        """CHATCELL adapter should detect species from AnnData"""
        from pantheon.toolsets.scfm.adapters.chatcell import CHATCELLAdapter

        adapter = CHATCELLAdapter()
        adata = sc.read_h5ad(test_adata_path)
        species = adapter._detect_species(adata)
        assert species == "human"

    def test_chatcell_in_registry(self):
        """CHATCELL should be registered"""
        registry = get_registry()
        spec = registry.get("chatcell")
        assert spec is not None
        assert spec.name == "chatcell"
        assert TaskType.ANNOTATE in spec.tasks
        assert spec.zero_shot_annotation is True


class TestSpecializedModelsInRegistry:
    """Tests for all 12 Specialized & Emerging model specs in registry"""

    def test_tgpt_in_registry(self):
        """tGPT should be registered"""
        spec = get_registry().get("tgpt")
        assert spec is not None
        assert TaskType.EMBED in spec.tasks

    def test_cellfm_in_registry(self):
        """CellFM should be registered"""
        spec = get_registry().get("cellfm")
        assert spec is not None
        assert TaskType.EMBED in spec.tasks

    def test_sccello_in_registry(self):
        """scCello should be registered"""
        spec = get_registry().get("sccello")
        assert spec is not None
        assert spec.zero_shot_annotation is True

    def test_scprint_in_registry(self):
        """scPRINT should be registered"""
        spec = get_registry().get("scprint")
        assert spec is not None

    def test_aidocell_in_registry(self):
        """AIDO.Cell should be registered"""
        spec = get_registry().get("aidocell")
        assert spec is not None

    def test_pulsar_in_registry(self):
        """PULSAR should be registered"""
        spec = get_registry().get("pulsar")
        assert spec is not None

    def test_atacformer_in_registry(self):
        """Atacformer should be registered"""
        spec = get_registry().get("atacformer")
        assert spec is not None
        from pantheon.toolsets.scfm import Modality, GeneIDScheme
        assert Modality.ATAC in spec.modalities
        assert spec.gene_id_scheme == GeneIDScheme.CUSTOM

    def test_scplantllm_in_registry(self):
        """scPlantLLM should be registered"""
        spec = get_registry().get("scplantllm")
        assert spec is not None
        assert "plant" in spec.species

    def test_langcell_in_registry(self):
        """LangCell should be registered"""
        spec = get_registry().get("langcell")
        assert spec is not None

    def test_cell2sentence_in_registry(self):
        """Cell2Sentence should be registered"""
        spec = get_registry().get("cell2sentence")
        assert spec is not None
        assert spec.requires_finetuning is True

    def test_genept_in_registry(self):
        """GenePT should be registered"""
        spec = get_registry().get("genept")
        assert spec is not None
        assert spec.hardware.gpu_required is False
        assert spec.embedding_dim == 1536

    def test_chatcell_in_registry(self):
        """CHATCELL should be registered"""
        spec = get_registry().get("chatcell")
        assert spec is not None
        assert spec.zero_shot_annotation is True


# =============================================================================
# Heavy Smoke Tests (require real checkpoints, opt-in via SCFM_RUN_HEAVY=1)
# =============================================================================


@pytest.mark.integration
@pytest.mark.skipif(not SCFM_RUN_HEAVY, reason="SCFM_RUN_HEAVY not set (opt-in smoke tests)")
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestSCFMHeavySmoke:
    """
    Smoke tests for SCFM with real checkpoints.

    These tests are opt-in and require:
    1. SCFM_RUN_HEAVY=1 environment variable
    2. Real model checkpoints (set via SCFM_CHECKPOINT_DIR_<MODEL> or SCFM_CHECKPOINT_DIR)
    3. GPU (for most models)

    Run with:
        SCFM_RUN_HEAVY=1 pytest tests/test_scfm.py -k Heavy -v

    Or run specific model test:
        SCFM_RUN_HEAVY=1 SCFM_CHECKPOINT_DIR_SCBERT=/path/to/scbert pytest tests/test_scfm.py -k "test_scbert_embed" -v
    """

    @pytest.mark.asyncio
    async def test_scbert_embed(self, scfm_toolset, test_adata_path, tmp_path):
        """scBERT embedding with real checkpoint."""
        if not os.environ.get("SCFM_CHECKPOINT_DIR_SCBERT") and not os.environ.get("SCFM_CHECKPOINT_DIR"):
            pytest.skip("SCFM_CHECKPOINT_DIR_SCBERT or SCFM_CHECKPOINT_DIR not set")

        output_path = str(tmp_path / "scbert_output.h5ad")
        result = await scfm_toolset.scfm_run(
            task="embed",
            model_name="scbert",
            adata_path=test_adata_path,
            output_path=output_path,
        )

        if "error" in result and "checkpoint" in result.get("error", "").lower():
            pytest.skip(f"scBERT checkpoint not found: {result['error']}")

        assert "error" not in result, f"scBERT embed failed: {result}"
        assert result["status"] == "success"
        assert "X_scBERT" in str(result.get("output_keys", []))

    @pytest.mark.asyncio
    async def test_genecompass_embed(self, scfm_toolset, test_adata_path, tmp_path):
        """GeneCompass embedding with real checkpoint."""
        if not os.environ.get("SCFM_CHECKPOINT_DIR_GENECOMPASS") and not os.environ.get("SCFM_CHECKPOINT_DIR"):
            pytest.skip("SCFM_CHECKPOINT_DIR_GENECOMPASS or SCFM_CHECKPOINT_DIR not set")

        output_path = str(tmp_path / "genecompass_output.h5ad")
        result = await scfm_toolset.scfm_run(
            task="embed",
            model_name="genecompass",
            adata_path=test_adata_path,
            output_path=output_path,
        )

        if "error" in result and "checkpoint" in result.get("error", "").lower():
            pytest.skip(f"GeneCompass checkpoint not found: {result['error']}")

        assert "error" not in result, f"GeneCompass embed failed: {result}"
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_cellplm_embed(self, scfm_toolset, test_adata_path, tmp_path):
        """CellPLM embedding with real checkpoint."""
        if not os.environ.get("SCFM_CHECKPOINT_DIR_CELLPLM") and not os.environ.get("SCFM_CHECKPOINT_DIR"):
            pytest.skip("SCFM_CHECKPOINT_DIR_CELLPLM or SCFM_CHECKPOINT_DIR not set")

        output_path = str(tmp_path / "cellplm_output.h5ad")
        result = await scfm_toolset.scfm_run(
            task="embed",
            model_name="cellplm",
            adata_path=test_adata_path,
            output_path=output_path,
        )

        if "error" in result and "checkpoint" in result.get("error", "").lower():
            pytest.skip(f"CellPLM checkpoint not found: {result['error']}")

        assert "error" not in result, f"CellPLM embed failed: {result}"
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_scprint_embed(self, scfm_toolset, test_adata_path, tmp_path):
        """scPRINT embedding with real checkpoint (supports HuggingFace fallback)."""
        output_path = str(tmp_path / "scprint_output.h5ad")
        result = await scfm_toolset.scfm_run(
            task="embed",
            model_name="scprint",
            adata_path=test_adata_path,
            output_path=output_path,
        )

        # scPRINT can load from HuggingFace, so may succeed without local checkpoint
        if "error" in result:
            if "scprint" in result.get("error", "").lower() and "not installed" in result.get("error", "").lower():
                pytest.skip(f"scPRINT package not installed: {result['error']}")
            if "gpu" in result.get("error", "").lower():
                pytest.skip(f"GPU required for scPRINT: {result['error']}")

        assert "error" not in result, f"scPRINT embed failed: {result}"
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_tgpt_embed(self, scfm_toolset, test_adata_path, tmp_path):
        """tGPT embedding with HuggingFace model."""
        output_path = str(tmp_path / "tgpt_output.h5ad")
        result = await scfm_toolset.scfm_run(
            task="embed",
            model_name="tgpt",
            adata_path=test_adata_path,
            output_path=output_path,
        )

        if "error" in result:
            if "transformers" in result.get("error", "").lower():
                pytest.skip(f"transformers package not installed: {result['error']}")
            if "gpu" in result.get("error", "").lower():
                pytest.skip(f"GPU required for tGPT: {result['error']}")

        assert "error" not in result, f"tGPT embed failed: {result}"
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_nicheformer_embed(self, scfm_toolset, test_adata_path, tmp_path):
        """Nicheformer embedding with real checkpoint."""
        if not os.environ.get("SCFM_CHECKPOINT_DIR_NICHEFORMER") and not os.environ.get("SCFM_CHECKPOINT_DIR"):
            pytest.skip("SCFM_CHECKPOINT_DIR_NICHEFORMER or SCFM_CHECKPOINT_DIR not set")

        output_path = str(tmp_path / "nicheformer_output.h5ad")
        result = await scfm_toolset.scfm_run(
            task="embed",
            model_name="nicheformer",
            adata_path=test_adata_path,
            output_path=output_path,
        )

        if "error" in result and "checkpoint" in result.get("error", "").lower():
            pytest.skip(f"Nicheformer checkpoint not found: {result['error']}")

        assert "error" not in result, f"Nicheformer embed failed: {result}"
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_genept_embed(self, scfm_toolset, test_adata_path, tmp_path):
        """GenePT embedding with pre-computed gene embeddings."""
        if not os.environ.get("SCFM_CHECKPOINT_DIR_GENEPT") and not os.environ.get("SCFM_CHECKPOINT_DIR"):
            pytest.skip("SCFM_CHECKPOINT_DIR_GENEPT or SCFM_CHECKPOINT_DIR not set")

        output_path = str(tmp_path / "genept_output.h5ad")
        result = await scfm_toolset.scfm_run(
            task="embed",
            model_name="genept",
            adata_path=test_adata_path,
            output_path=output_path,
        )

        if "error" in result and "checkpoint" in result.get("error", "").lower():
            pytest.skip(f"GenePT embeddings not found: {result['error']}")

        assert "error" not in result, f"GenePT embed failed: {result}"
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_cell2sentence_embed(self, scfm_toolset, test_adata_path, tmp_path):
        """Cell2Sentence embedding with HuggingFace model."""
        output_path = str(tmp_path / "cell2sentence_output.h5ad")
        result = await scfm_toolset.scfm_run(
            task="embed",
            model_name="cell2sentence",
            adata_path=test_adata_path,
            output_path=output_path,
        )

        if "error" in result:
            if "transformers" in result.get("error", "").lower():
                pytest.skip(f"transformers package not installed: {result['error']}")
            if "gpu" in result.get("error", "").lower():
                pytest.skip(f"GPU required for Cell2Sentence: {result['error']}")

        assert "error" not in result, f"Cell2Sentence embed failed: {result}"
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_langcell_embed(self, scfm_toolset, test_adata_path, tmp_path):
        """LangCell embedding with real checkpoint."""
        if not os.environ.get("SCFM_CHECKPOINT_DIR_LANGCELL") and not os.environ.get("SCFM_CHECKPOINT_DIR"):
            pytest.skip("SCFM_CHECKPOINT_DIR_LANGCELL or SCFM_CHECKPOINT_DIR not set")

        output_path = str(tmp_path / "langcell_output.h5ad")
        result = await scfm_toolset.scfm_run(
            task="embed",
            model_name="langcell",
            adata_path=test_adata_path,
            output_path=output_path,
        )

        if "error" in result and "checkpoint" in result.get("error", "").lower():
            pytest.skip(f"LangCell checkpoint not found: {result['error']}")

        assert "error" not in result, f"LangCell embed failed: {result}"
        assert result["status"] == "success"


@pytest.mark.integration
@pytest.mark.skipif(not SCFM_RUN_HEAVY, reason="SCFM_RUN_HEAVY not set (opt-in smoke tests)")
@pytest.mark.skipif(not HAS_SCANPY, reason="scanpy not installed")
class TestSCFMHeavySmokeRemaining:
    """
    Additional heavy smoke tests for remaining conditional models.
    """

    @pytest.mark.asyncio
    async def test_scmulan_embed(self, scfm_toolset, test_adata_path, tmp_path):
        """scMulan embedding with real checkpoint."""
        if not os.environ.get("SCFM_CHECKPOINT_DIR_SCMULAN") and not os.environ.get("SCFM_CHECKPOINT_DIR"):
            pytest.skip("SCFM_CHECKPOINT_DIR_SCMULAN or SCFM_CHECKPOINT_DIR not set")

        output_path = str(tmp_path / "scmulan_output.h5ad")
        result = await scfm_toolset.scfm_run(
            task="embed",
            model_name="scmulan",
            adata_path=test_adata_path,
            output_path=output_path,
        )

        if "error" in result and "checkpoint" in result.get("error", "").lower():
            pytest.skip(f"scMulan checkpoint not found: {result['error']}")

        assert "error" not in result, f"scMulan embed failed: {result}"
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_cellfm_embed(self, scfm_toolset, test_adata_path, tmp_path):
        """CellFM embedding with real checkpoint."""
        if not os.environ.get("SCFM_CHECKPOINT_DIR_CELLFM") and not os.environ.get("SCFM_CHECKPOINT_DIR"):
            pytest.skip("SCFM_CHECKPOINT_DIR_CELLFM or SCFM_CHECKPOINT_DIR not set")

        output_path = str(tmp_path / "cellfm_output.h5ad")
        result = await scfm_toolset.scfm_run(
            task="embed",
            model_name="cellfm",
            adata_path=test_adata_path,
            output_path=output_path,
        )

        if "error" in result and "checkpoint" in result.get("error", "").lower():
            pytest.skip(f"CellFM checkpoint not found: {result['error']}")

        assert "error" not in result, f"CellFM embed failed: {result}"
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_sccello_embed(self, scfm_toolset, test_adata_path, tmp_path):
        """scCello embedding with real checkpoint."""
        if not os.environ.get("SCFM_CHECKPOINT_DIR_SCCELLO") and not os.environ.get("SCFM_CHECKPOINT_DIR"):
            pytest.skip("SCFM_CHECKPOINT_DIR_SCCELLO or SCFM_CHECKPOINT_DIR not set")

        output_path = str(tmp_path / "sccello_output.h5ad")
        result = await scfm_toolset.scfm_run(
            task="embed",
            model_name="sccello",
            adata_path=test_adata_path,
            output_path=output_path,
        )

        if "error" in result and "checkpoint" in result.get("error", "").lower():
            pytest.skip(f"scCello checkpoint not found: {result['error']}")

        assert "error" not in result, f"scCello embed failed: {result}"
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_aidocell_embed(self, scfm_toolset, test_adata_path, tmp_path):
        """AIDO.Cell embedding with real checkpoint."""
        if not os.environ.get("SCFM_CHECKPOINT_DIR_AIDOCELL") and not os.environ.get("SCFM_CHECKPOINT_DIR"):
            pytest.skip("SCFM_CHECKPOINT_DIR_AIDOCELL or SCFM_CHECKPOINT_DIR not set")

        output_path = str(tmp_path / "aidocell_output.h5ad")
        result = await scfm_toolset.scfm_run(
            task="embed",
            model_name="aidocell",
            adata_path=test_adata_path,
            output_path=output_path,
        )

        if "error" in result and "checkpoint" in result.get("error", "").lower():
            pytest.skip(f"AIDO.Cell checkpoint not found: {result['error']}")

        assert "error" not in result, f"AIDO.Cell embed failed: {result}"
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_pulsar_embed(self, scfm_toolset, test_adata_path, tmp_path):
        """PULSAR embedding with real checkpoint."""
        if not os.environ.get("SCFM_CHECKPOINT_DIR_PULSAR") and not os.environ.get("SCFM_CHECKPOINT_DIR"):
            pytest.skip("SCFM_CHECKPOINT_DIR_PULSAR or SCFM_CHECKPOINT_DIR not set")

        output_path = str(tmp_path / "pulsar_output.h5ad")
        result = await scfm_toolset.scfm_run(
            task="embed",
            model_name="pulsar",
            adata_path=test_adata_path,
            output_path=output_path,
        )

        if "error" in result and "checkpoint" in result.get("error", "").lower():
            pytest.skip(f"PULSAR checkpoint not found: {result['error']}")

        assert "error" not in result, f"PULSAR embed failed: {result}"
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_chatcell_embed(self, scfm_toolset, test_adata_path, tmp_path):
        """CHATCELL embedding with real checkpoint."""
        if not os.environ.get("SCFM_CHECKPOINT_DIR_CHATCELL") and not os.environ.get("SCFM_CHECKPOINT_DIR"):
            pytest.skip("SCFM_CHECKPOINT_DIR_CHATCELL or SCFM_CHECKPOINT_DIR not set")

        output_path = str(tmp_path / "chatcell_output.h5ad")
        result = await scfm_toolset.scfm_run(
            task="embed",
            model_name="chatcell",
            adata_path=test_adata_path,
            output_path=output_path,
        )

        if "error" in result and "checkpoint" in result.get("error", "").lower():
            pytest.skip(f"CHATCELL checkpoint not found: {result['error']}")

        assert "error" not in result, f"CHATCELL embed failed: {result}"
        assert result["status"] == "success"