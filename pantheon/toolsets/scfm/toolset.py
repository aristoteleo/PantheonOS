"""
Single-Cell Foundation Model ToolSet

Provides agent-callable tools for foundation model operations.
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from pantheon.toolset import ToolSet, tool

from .registry import (
    GeneIDScheme,
    ModelRegistry,
    ModelSpec,
    Modality,
    SkillReadyStatus,
    TaskType,
    get_registry,
)


class SCFMToolSet(ToolSet):
    """
    ToolSet for Single-Cell Foundation Model operations.

    Provides a unified API for:
    - Model discovery and selection
    - Data profiling and validation
    - Model execution (embedding, annotation, integration)
    - Result interpretation and QA
    """

    description = "Single-cell foundation model tools for embedding, annotation, and integration"

    def __init__(self, name: str = "scfm", checkpoint_dir: str = None, **kwargs):
        super().__init__(name, **kwargs)
        self._registry = get_registry()
        self._checkpoint_dir = checkpoint_dir

    # =========================================================================
    # Model Discovery Tools
    # =========================================================================

    @tool
    def scfm_list_models(
        self,
        task: Optional[str] = None,
        skill_ready_only: bool = True,
    ) -> dict[str, Any]:
        """
        List available single-cell foundation models and their capabilities.

        Args:
            task: Filter by task type ('embed', 'annotate', 'integrate', 'perturb')
            skill_ready_only: If True, only show models with complete adapter specs

        Returns:
            Dictionary with 'models' list containing model summaries
        """
        task_filter = TaskType(task) if task else None
        models = self._registry.list_models(skill_ready_only=skill_ready_only)

        if task_filter:
            models = [m for m in models if m.supports_task(task_filter)]

        result = {
            "count": len(models),
            "models": [],
        }

        for spec in models:
            result["models"].append({
                "name": spec.name,
                "version": spec.version,
                "status": spec.skill_ready.value,
                "tasks": [t.value for t in spec.tasks],
                "modalities": [m.value for m in spec.modalities],
                "species": spec.species,
                "gene_id_scheme": spec.gene_id_scheme.value,
                "zero_shot": spec.zero_shot_embedding,
                "gpu_required": spec.hardware.gpu_required,
                "min_vram_gb": spec.hardware.min_vram_gb,
            })

        return result

    @tool
    def scfm_describe_model(self, model_name: str) -> dict[str, Any]:
        """
        Get detailed specification for a foundation model.

        Args:
            model_name: Name of the model (e.g., 'scgpt', 'geneformer', 'uce')

        Returns:
            Full model specification including I/O contract, requirements, and resources
        """
        spec = self._registry.get(model_name)
        if not spec:
            available = [m.name for m in self._registry.list_models()]
            return {
                "error": f"Model '{model_name}' not found",
                "available_models": available,
            }

        return {
            "model": spec.to_dict(),
            "input_contract": {
                "gene_id_scheme": spec.gene_id_scheme.value,
                "gene_id_notes": self._get_gene_id_notes(spec),
                "required_obs": self._get_required_obs(spec),
                "preprocessing": self._get_preprocessing_notes(spec),
            },
            "output_contract": {
                "embedding_key": f"obsm['{spec.output_keys.embedding_key}']" if spec.output_keys.embedding_key else None,
                "annotation_key": f"obs['{spec.output_keys.annotation_key}']" if spec.output_keys.annotation_key else None,
                "confidence_key": f"obs['{spec.output_keys.confidence_key}']" if spec.output_keys.confidence_key else None,
                "embedding_dim": spec.embedding_dim,
            },
            "resources": {
                "checkpoint": spec.checkpoint_url,
                "documentation": spec.documentation_url,
                "paper": spec.paper_url,
                "license": spec.license_notes,
            },
        }

    def _get_gene_id_notes(self, spec: ModelSpec) -> str:
        """Get gene ID conversion notes for a model"""
        notes = {
            "scgpt": "Uses HGNC gene symbols. Convert Ensembl IDs to symbols if needed.",
            "geneformer": "Requires Ensembl IDs (ENSG...). Strip version suffix (.15) if present.",
            "uce": "Uses gene symbols. Not compatible with Ensembl IDs directly.",
            "scfoundation": "Uses custom 19,264 gene set. Map genes to model vocabulary.",
        }
        return notes.get(spec.name, "Check model documentation for gene ID requirements.")

    def _get_required_obs(self, spec: ModelSpec) -> list[str]:
        """Get required .obs columns for a model"""
        required = []
        if TaskType.INTEGRATE in spec.tasks:
            required.append("batch_id (for integration)")
        if TaskType.ANNOTATE in spec.tasks and spec.requires_finetuning:
            required.append("celltype (for annotation training)")
        return required

    def _get_preprocessing_notes(self, spec: ModelSpec) -> str:
        """Get preprocessing notes for a model"""
        notes = {
            "scgpt": "Normalize to 1e4 via sc.pp.normalize_total, then bin into 51 expression bins.",
            "geneformer": "Rank-value encoding. Use geneformer.preprocess() for proper tokenization.",
            "uce": "Standard log-normalization. Model handles tokenization internally.",
            "scfoundation": "Match genes to model vocabulary. Follow xTrimoGene preprocessing.",
        }
        return notes.get(spec.name, "See model documentation for preprocessing requirements.")

    # =========================================================================
    # Data Profiling Tools
    # =========================================================================

    @tool
    def scfm_profile_data(self, adata_path: str) -> dict[str, Any]:
        """
        Profile an AnnData file to detect species, gene scheme, and modality.

        Args:
            adata_path: Path to .h5ad file

        Returns:
            Data profile including species, gene_scheme, modality, n_cells, n_genes,
            and compatibility notes for each model
        """
        return self._profile_data_impl(adata_path)

    def _profile_data_impl(self, adata_path: str) -> dict[str, Any]:
        """Internal implementation of scfm_profile_data (sync)"""
        path = Path(adata_path)
        if not path.exists():
            return {"error": f"File not found: {adata_path}"}

        if not path.suffix == ".h5ad":
            return {"error": f"Expected .h5ad file, got: {path.suffix}"}

        try:
            import scanpy as sc
            adata = sc.read_h5ad(adata_path, backed="r")
        except ImportError:
            return {
                "error": "scanpy not installed. Install with: pip install scanpy",
                "fallback": "Use remote MCP backend for foundation model operations.",
            }
        except Exception as e:
            return {"error": f"Failed to read AnnData: {str(e)}"}

        # Profile the data
        profile = {
            "file": str(path.name),
            "n_cells": adata.n_obs,
            "n_genes": adata.n_vars,
            "species": self._detect_species(adata),
            "gene_scheme": self._detect_gene_scheme(adata),
            "modality": self._detect_modality(adata),
            "has_raw": adata.raw is not None,
            "layers": list(adata.layers.keys()) if adata.layers else [],
            "obs_columns": list(adata.obs.columns)[:20],  # First 20 columns
            "obsm_keys": list(adata.obsm.keys()),
        }

        # Add batch info if present
        batch_cols = [c for c in adata.obs.columns if "batch" in c.lower()]
        profile["batch_columns"] = batch_cols

        # Add celltype info if present
        celltype_cols = [c for c in adata.obs.columns if any(x in c.lower() for x in ["celltype", "cell_type", "annotation"])]
        profile["celltype_columns"] = celltype_cols

        # Add model compatibility
        profile["model_compatibility"] = self._check_model_compatibility(profile)

        adata.file.close()
        return profile

    def _detect_species(self, adata) -> str:
        """Detect species from gene names or metadata"""
        # Check metadata first
        if "species" in adata.uns:
            return adata.uns["species"]

        # Sample gene names
        gene_names = adata.var_names[:100].tolist()

        # Human genes often start with uppercase (TP53, BRCA1)
        # Mouse genes often have first letter uppercase only (Tp53, Brca1)
        uppercase_count = sum(1 for g in gene_names if g.isupper() or (len(g) > 1 and g[1:].isupper()))
        mixed_count = sum(1 for g in gene_names if g[0].isupper() and g[1:].islower())

        # Check for species-specific genes
        human_markers = {"ACTB", "GAPDH", "CD4", "CD8A", "MS4A1", "CD14"}
        mouse_markers = {"Actb", "Gapdh", "Cd4", "Cd8a", "Ms4a1", "Cd14"}

        gene_set = set(gene_names)
        human_hits = len(human_markers & gene_set)
        mouse_hits = len(mouse_markers & gene_set)

        if human_hits > mouse_hits:
            return "human"
        elif mouse_hits > human_hits:
            return "mouse"
        elif uppercase_count > mixed_count:
            return "human (inferred)"
        elif mixed_count > uppercase_count:
            return "mouse (inferred)"
        return "unknown"

    def _detect_gene_scheme(self, adata) -> str:
        """Detect gene ID scheme from var_names"""
        gene_names = adata.var_names[:50].tolist()

        # Check for Ensembl IDs
        ensembl_count = sum(1 for g in gene_names if g.startswith(("ENSG", "ENSMUSG", "ENS")))
        if ensembl_count > len(gene_names) * 0.5:
            return "ensembl"

        # Check for symbols (alphanumeric, possibly with dashes)
        symbol_pattern_count = sum(1 for g in gene_names if g.isalnum() or "-" in g)
        if symbol_pattern_count > len(gene_names) * 0.8:
            return "symbol"

        return "unknown"

    def _detect_modality(self, adata) -> str:
        """Detect data modality"""
        # Check uns for explicit modality
        if "modality" in adata.uns:
            return adata.uns["modality"]

        # Check for ATAC-specific keys
        if any("peak" in k.lower() or "atac" in k.lower() for k in adata.var.columns):
            return "ATAC"

        # Check for spatial coordinates
        if "spatial" in adata.obsm or "X_spatial" in adata.obsm:
            return "Spatial"

        # Default to RNA
        return "RNA"

    def _check_model_compatibility(self, profile: dict) -> dict[str, dict]:
        """Check compatibility with each model"""
        compatibility = {}

        for spec in self._registry.list_models():
            issues = []
            recommendations = []

            # Check species
            species = profile["species"].replace(" (inferred)", "")
            if species != "unknown" and not spec.supports_species(species):
                issues.append(f"Species '{species}' not supported")

            # Check gene scheme
            gene_scheme = profile["gene_scheme"]
            if gene_scheme != "unknown":
                if spec.gene_id_scheme == GeneIDScheme.ENSEMBL and gene_scheme != "ensembl":
                    issues.append("Model requires Ensembl IDs")
                    recommendations.append("Convert gene symbols to Ensembl IDs")
                elif spec.gene_id_scheme == GeneIDScheme.SYMBOL and gene_scheme == "ensembl":
                    issues.append("Model requires gene symbols")
                    recommendations.append("Convert Ensembl IDs to gene symbols")

            # Check modality
            modality = profile["modality"]
            if modality and not spec.supports_modality(Modality(modality)):
                issues.append(f"Modality '{modality}' not supported")

            compatibility[spec.name] = {
                "compatible": len(issues) == 0,
                "issues": issues,
                "recommendations": recommendations,
            }

        return compatibility

    # =========================================================================
    # Model Selection Tools
    # =========================================================================

    @tool
    def scfm_select_model(
        self,
        adata_path: str,
        task: str,
        prefer_zero_shot: bool = True,
        max_vram_gb: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Select the best foundation model for a task and dataset.

        Args:
            adata_path: Path to .h5ad file
            task: Task type ('embed', 'annotate', 'integrate')
            prefer_zero_shot: Prefer models that don't require fine-tuning
            max_vram_gb: Maximum VRAM constraint (optional)

        Returns:
            Recommended model with rationale and fallback options
        """
        # Profile the data first
        profile = self._profile_data_impl(adata_path)
        if "error" in profile:
            return profile

        task_type = TaskType(task)

        # Determine species
        species = profile["species"].replace(" (inferred)", "")
        if species == "unknown":
            species = None

        # Determine gene scheme
        gene_scheme = profile["gene_scheme"]
        gene_scheme_enum = None
        if gene_scheme == "ensembl":
            gene_scheme_enum = GeneIDScheme.ENSEMBL
        elif gene_scheme == "symbol":
            gene_scheme_enum = GeneIDScheme.SYMBOL

        # Find compatible models
        models = self._registry.find_models(
            task=task_type,
            species=species,
            gene_scheme=gene_scheme_enum,
            max_vram_gb=max_vram_gb,
        )

        if not models:
            # Try without gene scheme filter
            models = self._registry.find_models(
                task=task_type,
                species=species,
                max_vram_gb=max_vram_gb,
            )

        if not models:
            return {
                "error": "No compatible models found",
                "data_profile": profile,
                "suggestion": "Try relaxing constraints or check data format",
            }

        # Score and rank models
        scored_models = []
        for spec in models:
            score = self._score_model(spec, profile, task_type, prefer_zero_shot)
            scored_models.append((spec, score))

        scored_models.sort(key=lambda x: x[1], reverse=True)

        recommended = scored_models[0][0]
        fallbacks = [m[0] for m in scored_models[1:3]]  # Top 2 fallbacks

        return {
            "recommended": {
                "name": recommended.name,
                "version": recommended.version,
                "rationale": self._generate_rationale(recommended, profile, task_type),
            },
            "fallbacks": [
                {"name": f.name, "rationale": self._generate_rationale(f, profile, task_type)}
                for f in fallbacks
            ],
            "preprocessing_notes": self._get_preprocessing_notes(recommended),
            "data_profile": {
                "species": profile["species"],
                "gene_scheme": profile["gene_scheme"],
                "n_cells": profile["n_cells"],
                "n_genes": profile["n_genes"],
            },
        }

    def _score_model(
        self,
        spec: ModelSpec,
        profile: dict,
        task: TaskType,
        prefer_zero_shot: bool,
    ) -> float:
        """Score a model for selection ranking"""
        score = 0.0

        # Skill-ready status (most important)
        if spec.skill_ready == SkillReadyStatus.READY:
            score += 100
        elif spec.skill_ready == SkillReadyStatus.PARTIAL:
            score += 50

        # Zero-shot preference
        if prefer_zero_shot:
            if task == TaskType.EMBED and spec.zero_shot_embedding:
                score += 30
            elif task == TaskType.ANNOTATE and spec.zero_shot_annotation:
                score += 30

        # Gene scheme match (no conversion needed)
        gene_scheme = profile["gene_scheme"]
        if gene_scheme == "ensembl" and spec.gene_id_scheme == GeneIDScheme.ENSEMBL:
            score += 20
        elif gene_scheme == "symbol" and spec.gene_id_scheme == GeneIDScheme.SYMBOL:
            score += 20

        # Hardware accessibility
        if spec.hardware.cpu_fallback:
            score += 10
        if spec.hardware.min_vram_gb <= 8:
            score += 5

        return score

    def _generate_rationale(self, spec: ModelSpec, profile: dict, task: TaskType) -> str:
        """Generate human-readable rationale for model selection"""
        reasons = []

        if spec.skill_ready == SkillReadyStatus.READY:
            reasons.append("fully documented adapter spec")

        gene_scheme = profile["gene_scheme"]
        if gene_scheme == "ensembl" and spec.gene_id_scheme == GeneIDScheme.ENSEMBL:
            reasons.append("matches Ensembl gene IDs")
        elif gene_scheme == "symbol" and spec.gene_id_scheme == GeneIDScheme.SYMBOL:
            reasons.append("matches gene symbols")

        species = profile["species"].replace(" (inferred)", "")
        if species in spec.species:
            reasons.append(f"supports {species}")

        if task == TaskType.EMBED and spec.zero_shot_embedding:
            reasons.append("zero-shot embedding (no fine-tuning needed)")

        if spec.hardware.cpu_fallback:
            reasons.append("CPU fallback available")

        return "; ".join(reasons) if reasons else "general purpose model"

    # =========================================================================
    # Validation Tools
    # =========================================================================

    @tool
    def scfm_preprocess_validate(
        self,
        adata_path: str,
        model_name: str,
        task: str,
    ) -> dict[str, Any]:
        """
        Validate data compatibility with a model and suggest preprocessing steps.

        Args:
            adata_path: Path to .h5ad file
            model_name: Target model name
            task: Task type ('embed', 'annotate', 'integrate')

        Returns:
            Validation result with status, diagnostics, and auto-fix suggestions
        """
        return self._preprocess_validate_impl(adata_path, model_name, task)

    def _preprocess_validate_impl(
        self,
        adata_path: str,
        model_name: str,
        task: str,
    ) -> dict[str, Any]:
        """Internal sync implementation of scfm_preprocess_validate"""
        spec = self._registry.get(model_name)
        if not spec:
            return {"error": f"Model '{model_name}' not found"}

        profile = self._profile_data_impl(adata_path)
        if "error" in profile:
            return profile

        task_type = TaskType(task)

        diagnostics = []
        auto_fixes = []
        status = "ready"

        # Check task support
        if not spec.supports_task(task_type):
            diagnostics.append({
                "severity": "error",
                "message": f"Model '{model_name}' does not support task '{task}'",
            })
            status = "incompatible"

        # Check gene ID scheme
        gene_scheme = profile["gene_scheme"]
        if gene_scheme == "ensembl" and spec.gene_id_scheme == GeneIDScheme.SYMBOL:
            diagnostics.append({
                "severity": "warning",
                "message": "Data has Ensembl IDs but model requires gene symbols",
            })
            auto_fixes.append({
                "action": "convert_gene_ids",
                "from": "ensembl",
                "to": "symbol",
                "code": "# Use biomart or mygene to convert Ensembl to symbols",
            })
            status = "needs_preprocessing"

        elif gene_scheme == "symbol" and spec.gene_id_scheme == GeneIDScheme.ENSEMBL:
            diagnostics.append({
                "severity": "warning",
                "message": "Data has gene symbols but model requires Ensembl IDs",
            })
            auto_fixes.append({
                "action": "convert_gene_ids",
                "from": "symbol",
                "to": "ensembl",
                "code": "# Use biomart or mygene to convert symbols to Ensembl",
            })
            status = "needs_preprocessing"

        # Check species
        species = profile["species"].replace(" (inferred)", "")
        if species != "unknown" and not spec.supports_species(species):
            diagnostics.append({
                "severity": "error",
                "message": f"Species '{species}' not supported by {model_name}",
            })
            status = "incompatible"

        # Check for batch column if integration task
        if task_type == TaskType.INTEGRATE:
            if not profile.get("batch_columns"):
                diagnostics.append({
                    "severity": "warning",
                    "message": "No batch column found for integration task",
                })
                auto_fixes.append({
                    "action": "add_batch_column",
                    "code": "adata.obs['batch_id'] = 'batch_1'  # Add appropriate batch labels",
                })

        # Check for celltype column if annotation training
        if task_type == TaskType.ANNOTATE and spec.requires_finetuning:
            if not profile.get("celltype_columns"):
                diagnostics.append({
                    "severity": "info",
                    "message": "No celltype column found. Fine-tuning requires labeled data.",
                })

        # Check for raw counts
        if not profile.get("has_raw") and "counts" not in profile.get("layers", []):
            diagnostics.append({
                "severity": "info",
                "message": "No raw counts found. Some models require unnormalized counts in .raw or layers['counts'].",
            })

        return {
            "status": status,
            "model": model_name,
            "task": task,
            "diagnostics": diagnostics,
            "auto_fixes": auto_fixes,
            "data_summary": {
                "n_cells": profile["n_cells"],
                "n_genes": profile["n_genes"],
                "species": profile["species"],
                "gene_scheme": profile["gene_scheme"],
            },
        }

    # =========================================================================
    # Execution Tools (Stubs - require model adapters)
    # =========================================================================

    @tool
    def scfm_run(
        self,
        task: str,
        model_name: str,
        adata_path: str,
        output_path: Optional[str] = None,
        batch_key: Optional[str] = None,
        label_key: Optional[str] = None,
        device: str = "auto",
        batch_size: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Execute a foundation model task.

        Args:
            task: Task type ('embed', 'annotate', 'integrate')
            model_name: Model to use
            adata_path: Path to input .h5ad file
            output_path: Path for output .h5ad (default: overwrites input)
            batch_key: Column in .obs for batch information
            label_key: Column in .obs for cell type labels (annotation)
            device: Device to use ('auto', 'cuda', 'cpu')
            batch_size: Batch size for inference

        Returns:
            Execution result with output path, keys, and statistics
        """
        spec = self._registry.get(model_name)
        if not spec:
            return {"error": f"Model '{model_name}' not found"}

        # Validate first (use sync internal method to avoid async issues)
        validation = self._preprocess_validate_impl(adata_path, model_name, task)
        if validation.get("status") == "incompatible":
            return {
                "error": "Data incompatible with model",
                "validation": validation,
            }

        task_type = TaskType(task)
        output_path = output_path or adata_path

        # Check if we have an adapter for this model
        adapter = self._get_model_adapter(model_name)
        if adapter is None:
            return {
                "error": f"No adapter implemented for model '{model_name}'",
                "status": "not_implemented",
                "suggestion": "Use remote MCP backend or implement local adapter",
                "model_spec": spec.to_dict(),
            }

        # Execute via adapter
        try:
            result = adapter.run(
                task=task_type,
                adata_path=adata_path,
                output_path=output_path,
                batch_key=batch_key,
                label_key=label_key,
                device=device,
                batch_size=batch_size or spec.hardware.default_batch_size,
            )
            return result
        except Exception as e:
            return {
                "error": f"Execution failed: {str(e)}",
                "model": model_name,
                "task": task,
            }

    def _get_model_adapter(self, model_name: str):
        """Get the adapter for a specific model"""
        model_name = model_name.lower()

        if model_name == "uce":
            try:
                from .adapters.uce import UCEAdapter
                return UCEAdapter()
            except ImportError:
                return None

        if model_name == "scgpt":
            try:
                from .adapters.scgpt import ScGPTAdapter
                return ScGPTAdapter(self._checkpoint_dir)
            except ImportError:
                return None

        if model_name == "geneformer":
            try:
                from .adapters.geneformer import GeneformerAdapter
                return GeneformerAdapter(self._checkpoint_dir)
            except ImportError:
                return None

        if model_name == "scfoundation":
            try:
                from .adapters.scfoundation import ScFoundationAdapter
                return ScFoundationAdapter(self._checkpoint_dir)
            except ImportError:
                return None

        if model_name == "scbert":
            try:
                from .adapters.scbert import ScBERTAdapter
                return ScBERTAdapter(self._checkpoint_dir)
            except ImportError:
                return None

        if model_name == "genecompass":
            try:
                from .adapters.genecompass import GeneCompassAdapter
                return GeneCompassAdapter(self._checkpoint_dir)
            except ImportError:
                return None

        if model_name == "cellplm":
            try:
                from .adapters.cellplm import CellPLMAdapter
                return CellPLMAdapter(self._checkpoint_dir)
            except ImportError:
                return None

        if model_name == "nicheformer":
            try:
                from .adapters.nicheformer import NicheformerAdapter
                return NicheformerAdapter(self._checkpoint_dir)
            except ImportError:
                return None

        if model_name == "scmulan":
            try:
                from .adapters.scmulan import ScMulanAdapter
                return ScMulanAdapter(self._checkpoint_dir)
            except ImportError:
                return None

        # Specialized & Emerging Models (2024-2025)
        if model_name == "tgpt":
            try:
                from .adapters.tgpt import TGPTAdapter
                return TGPTAdapter(self._checkpoint_dir)
            except ImportError:
                return None

        if model_name == "cellfm":
            try:
                from .adapters.cellfm import CellFMAdapter
                return CellFMAdapter(self._checkpoint_dir)
            except ImportError:
                return None

        if model_name == "sccello":
            try:
                from .adapters.sccello import ScCelloAdapter
                return ScCelloAdapter(self._checkpoint_dir)
            except ImportError:
                return None

        if model_name == "scprint":
            try:
                from .adapters.scprint import ScPRINTAdapter
                return ScPRINTAdapter(self._checkpoint_dir)
            except ImportError:
                return None

        if model_name == "aidocell":
            try:
                from .adapters.aidocell import AIDOCellAdapter
                return AIDOCellAdapter(self._checkpoint_dir)
            except ImportError:
                return None

        if model_name == "pulsar":
            try:
                from .adapters.pulsar import PULSARAdapter
                return PULSARAdapter(self._checkpoint_dir)
            except ImportError:
                return None

        if model_name == "atacformer":
            try:
                from .adapters.atacformer import AtacformerAdapter
                return AtacformerAdapter(self._checkpoint_dir)
            except ImportError:
                return None

        if model_name == "scplantllm":
            try:
                from .adapters.scplantllm import ScPlantLLMAdapter
                return ScPlantLLMAdapter(self._checkpoint_dir)
            except ImportError:
                return None

        if model_name == "langcell":
            try:
                from .adapters.langcell import LangCellAdapter
                return LangCellAdapter(self._checkpoint_dir)
            except ImportError:
                return None

        if model_name == "cell2sentence":
            try:
                from .adapters.cell2sentence import Cell2SentenceAdapter
                return Cell2SentenceAdapter(self._checkpoint_dir)
            except ImportError:
                return None

        if model_name == "genept":
            try:
                from .adapters.genept import GenePTAdapter
                return GenePTAdapter(self._checkpoint_dir)
            except ImportError:
                return None

        if model_name == "chatcell":
            try:
                from .adapters.chatcell import CHATCELLAdapter
                return CHATCELLAdapter(self._checkpoint_dir)
            except ImportError:
                return None

        return None

    # =========================================================================
    # Interpretation Tools
    # =========================================================================

    @tool
    def scfm_interpret_results(
        self,
        adata_path: str,
        task: str,
        output_dir: Optional[str] = None,
        generate_umap: bool = True,
        color_by: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """
        Generate QA metrics and visualizations for model results.

        Args:
            adata_path: Path to .h5ad file with model outputs
            task: Task that was executed
            output_dir: Directory for visualization outputs
            generate_umap: Whether to generate UMAP visualizations (default: True)
            color_by: List of obs columns to color UMAP by (auto-detected if None)

        Returns:
            QA metrics, visualization paths, and warnings
        """
        try:
            import scanpy as sc
            adata = sc.read_h5ad(adata_path)
        except ImportError:
            return {"error": "scanpy not installed"}
        except Exception as e:
            return {"error": f"Failed to read AnnData: {str(e)}"}

        task_type = TaskType(task)
        output_dir = output_dir or os.path.dirname(adata_path)
        os.makedirs(output_dir, exist_ok=True)

        metrics = {}
        visualizations = []
        warnings = []

        # Check for embedding results - use registry to get all model names
        embedding_keys = [k for k in adata.obsm.keys() if k.startswith("X_")]
        model_names = [spec.name for spec in self._registry.list_models()]
        scfm_keys = [k for k in embedding_keys if any(m in k.lower() for m in model_names)]

        if not scfm_keys:
            warnings.append("No foundation model embeddings found in obsm")

        if scfm_keys:
            metrics["embeddings"] = {}
            for key in scfm_keys:
                emb = adata.obsm[key]
                metrics["embeddings"][key] = {
                    "dim": emb.shape[1],
                    "n_cells": emb.shape[0],
                }

                # Compute silhouette score if we have labels
                sil_score = self._compute_silhouette(adata, key)
                if sil_score is not None:
                    metrics["embeddings"][key]["silhouette"] = round(sil_score, 4)

        # Check for annotation results
        annotation_cols = [c for c in adata.obs.columns if any(m in c.lower() for m in ["pred", "annotation"])]
        if annotation_cols:
            metrics["annotations"] = {"columns": annotation_cols}

            # Add confidence stats if available
            conf_cols = [c for c in adata.obs.columns if "confidence" in c.lower() or "score" in c.lower()]
            if conf_cols:
                for col in conf_cols:
                    if adata.obs[col].dtype in ["float64", "float32"]:
                        metrics["annotations"][f"{col}_mean"] = round(float(adata.obs[col].mean()), 4)

        # Check for provenance
        if "scfm" in adata.uns:
            metrics["provenance"] = adata.uns["scfm"]

        # Generate UMAP visualizations if requested
        if generate_umap and scfm_keys:
            umap_results = self._generate_umap_visualizations(
                adata=adata,
                embedding_keys=scfm_keys,
                output_dir=output_dir,
                color_by=color_by,
            )
            visualizations.extend(umap_results["visualizations"])
            warnings.extend(umap_results.get("warnings", []))

        # Add basic stats
        metrics["n_cells"] = adata.n_obs
        metrics["n_genes"] = adata.n_vars

        return {
            "metrics": metrics,
            "visualizations": visualizations,
            "warnings": warnings,
            "embedding_keys": scfm_keys,
            "annotation_columns": annotation_cols,
        }

    def _compute_silhouette(self, adata, embedding_key: str) -> Optional[float]:
        """
        Compute silhouette score for embeddings if labels are available.

        Args:
            adata: AnnData object
            embedding_key: Key in obsm for the embedding

        Returns:
            Silhouette score or None if no labels found
        """
        try:
            from sklearn.metrics import silhouette_score
        except ImportError:
            return None

        # Find a label column to use
        label_cols = [c for c in adata.obs.columns if any(
            x in c.lower() for x in ["celltype", "cell_type", "cluster", "leiden", "louvain"]
        )]

        if not label_cols:
            return None

        # Use the first label column
        label_col = label_cols[0]
        labels = adata.obs[label_col]

        # Need at least 2 unique labels
        if labels.nunique() < 2:
            return None

        try:
            # Subsample if too large (silhouette is expensive)
            max_cells = 10000
            if adata.n_obs > max_cells:
                import numpy as np
                idx = np.random.choice(adata.n_obs, max_cells, replace=False)
                embeddings = adata.obsm[embedding_key][idx]
                sample_labels = labels.iloc[idx]
            else:
                embeddings = adata.obsm[embedding_key]
                sample_labels = labels

            score = silhouette_score(embeddings, sample_labels, metric="euclidean")
            return score
        except Exception:
            return None

    def _generate_umap_visualizations(
        self,
        adata,
        embedding_keys: list[str],
        output_dir: str,
        color_by: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """
        Generate UMAP visualizations for foundation model embeddings.

        Args:
            adata: AnnData object
            embedding_keys: List of embedding keys to visualize
            output_dir: Output directory for plots
            color_by: Columns to color by (auto-detected if None)

        Returns:
            Dictionary with visualization paths and warnings
        """
        import scanpy as sc
        import matplotlib
        matplotlib.use("Agg")  # Non-interactive backend for saving
        import matplotlib.pyplot as plt

        visualizations = []
        warnings = []

        # Auto-detect color columns if not specified
        if color_by is None:
            color_by = []
            # Look for cell type annotations
            celltype_cols = [c for c in adata.obs.columns if any(
                x in c.lower() for x in ["celltype", "cell_type", "annotation"]
            )]
            if celltype_cols:
                color_by.extend(celltype_cols[:2])  # Max 2 celltype columns

            # Look for batch info
            batch_cols = [c for c in adata.obs.columns if "batch" in c.lower()]
            if batch_cols:
                color_by.append(batch_cols[0])

            # Look for clustering
            cluster_cols = [c for c in adata.obs.columns if c in ["leiden", "louvain", "cluster"]]
            if cluster_cols:
                color_by.extend(cluster_cols[:1])

        # Generate UMAP for each embedding
        for emb_key in embedding_keys:
            try:
                # Compute neighbors from this embedding
                sc.pp.neighbors(adata, use_rep=emb_key, n_neighbors=15)

                # Compute UMAP
                umap_key = f"X_umap_{emb_key.replace('X_', '')}"
                sc.tl.umap(adata, key_added=umap_key)

                # Generate plots
                for color_col in color_by:
                    if color_col not in adata.obs.columns:
                        continue

                    # Create figure
                    fig, ax = plt.subplots(figsize=(8, 8))

                    # Plot UMAP
                    sc.pl.embedding(
                        adata,
                        basis=umap_key.replace("X_", ""),
                        color=color_col,
                        ax=ax,
                        show=False,
                        title=f"{emb_key} UMAP - {color_col}",
                    )

                    # Save plot
                    plot_name = f"umap_{emb_key.replace('X_', '')}_{color_col}.png"
                    plot_path = os.path.join(output_dir, plot_name)
                    fig.savefig(plot_path, dpi=150, bbox_inches="tight")
                    plt.close(fig)

                    visualizations.append({
                        "type": "umap",
                        "embedding": emb_key,
                        "color_by": color_col,
                        "path": plot_path,
                    })

                # Also generate a basic UMAP without coloring
                fig, ax = plt.subplots(figsize=(8, 8))
                sc.pl.embedding(
                    adata,
                    basis=umap_key.replace("X_", ""),
                    ax=ax,
                    show=False,
                    title=f"{emb_key} UMAP",
                )
                plot_name = f"umap_{emb_key.replace('X_', '')}_basic.png"
                plot_path = os.path.join(output_dir, plot_name)
                fig.savefig(plot_path, dpi=150, bbox_inches="tight")
                plt.close(fig)

                visualizations.append({
                    "type": "umap",
                    "embedding": emb_key,
                    "color_by": None,
                    "path": plot_path,
                })

            except Exception as e:
                warnings.append(f"Failed to generate UMAP for {emb_key}: {str(e)}")

        return {
            "visualizations": visualizations,
            "warnings": warnings,
        }
