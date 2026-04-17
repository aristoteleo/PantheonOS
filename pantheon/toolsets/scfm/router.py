"""
SCFM Router - LLM-based routing for single-cell foundation model tasks.

Takes a natural language query and returns:
- Inferred scFM task (embed/integrate/annotate/spatial/perturb/drug_response)
- Selected best-fit model from registry
- Resolved parameters (or clarifying questions)
- Executable tool-call plan
"""

import json
import re
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from .registry import (
    GeneIDScheme,
    ModelSpec,
    SkillReadyStatus,
    TaskType,
    get_registry,
)


# =============================================================================
# Constants
# =============================================================================

VALID_TASKS = [t.value for t in TaskType]

VALID_SCFM_TOOLS = [
    "scfm_profile_data",
    "scfm_preprocess_validate",
    "scfm_run",
    "scfm_interpret_results",
    "scfm_list_models",
    "scfm_describe_model",
    "scfm_select_model",
]


def _normalize_router_output_dict(output_dict: dict[str, Any]) -> dict[str, Any]:
    """Normalize common LLM plan mistakes into valid SCFM router output.

    In practice the router sometimes emits a model name such as ``scplantllm``
    as the plan tool. Convert those steps into ``scfm_run`` with
    ``model_name=<that model>`` while preserving the original args.
    """
    if not isinstance(output_dict, dict):
        return output_dict

    normalized = json.loads(json.dumps(output_dict))
    registry = get_registry()
    plan = normalized.get("plan")
    if not isinstance(plan, list):
        return normalized

    for step in plan:
        if not isinstance(step, dict):
            continue
        tool_name = step.get("tool")
        if not isinstance(tool_name, str) or tool_name in VALID_SCFM_TOOLS:
            continue
        if registry.get(tool_name.lower()) is None:
            continue

        args = step.get("args")
        if not isinstance(args, dict):
            args = {}
            step["args"] = args
        args.setdefault("model_name", tool_name.lower())
        step["tool"] = "scfm_run"

    return normalized

ROUTER_SYSTEM_PROMPT = """You are an expert scFM (single-cell foundation model) router.
Your job is to analyze user queries about single-cell data analysis and determine:
1. Which task they want to perform (embed, integrate, annotate, spatial, perturb, drug_response)
2. Which model best fits their needs from the available registry
3. What parameters are needed for execution
4. If any clarification is required

IMPORTANT: You MUST respond with valid JSON only. No markdown, no explanation text outside JSON.

## Available Tasks
- embed: Generate cell embeddings using a foundation model
- integrate: Batch integration / correction using foundation model embeddings
- annotate: Cell type annotation (may require fine-tuning depending on model)
- spatial: Spatial transcriptomics analysis (requires spatial coordinates)
- perturb: Perturbation prediction / analysis
- drug_response: Drug response prediction

## Output Format
Return a JSON object with this exact structure:
{
  "intent": {
    "task": "<task_name>",
    "confidence": <0.0-1.0>,
    "constraints": {}
  },
  "inputs": {
    "query": "<original_query>",
    "adata_path": "<path_if_provided>"
  },
  "data_profile": <null_or_profile_object>,
  "selection": {
    "recommended": {"name": "<model_name>", "rationale": "<why>"},
    "fallbacks": [{"name": "<model_name>", "rationale": "<why>"}]
  },
  "resolved_params": {
    "output_path": "<path_or_null>",
    "batch_key": "<key_or_null>",
    "label_key": "<key_or_null>"
  },
  "plan": [
    {"tool": "<tool_name>", "args": {}}
  ],
  "questions": [
    {"field": "<param_name>", "question": "<clarification_question>", "options": []}
  ],
  "warnings": []
}

## CRITICAL: Model Selection Rules

**Match the user's specific requirements to each model's unique differentiator and "Use when" guidance.**
Do NOT default to any single model. Each model has a distinct strength — select based on what the user actually needs.

### Disambiguation Table (confusable models)
| User mentions...                          | Select         | NOT              |
|-------------------------------------------|---------------|------------------|
| multi-omics, CITE-seq, RNA+ATAC+Protein   | scmulan       | scgpt            |
| spatial transcriptomics, niche, Visium    | nicheformer   | scgpt            |
| ATAC-seq only, chromatin accessibility    | atacformer    | scgpt/scmulan    |
| denoising, ambient RNA, protein-coding    | scprint       | scgpt            |
| unsupervised clustering, label-free       | aidocell      | scgpt            |
| cell-cell communication, multicellular    | pulsar        | scgpt            |
| fast inference, high throughput, million+  | cellplm       | scgpt            |
| next-token, autoregressive, generative    | tgpt          | scgpt/geneformer |
| MLP architecture, largest scale           | cellfm        | scgpt            |
| compact 200-dim, lightweight              | scbert        | scgpt            |
| ontology, hierarchical cell types         | sccello       | scgpt            |
| plant, polyploidy, Arabidopsis            | scplantllm    | scgpt/uce        |
| text+cell alignment, NL cell queries      | langcell      | scgpt            |
| LLM fine-tuning, cells-as-text            | cell2sentence | scgpt            |
| gene-level (not cell), no GPU, API-based  | genept        | scgpt/geneformer |
| chat-based, conversational annotation     | chatcell      | scgpt            |
| Ensembl IDs, network biology, CPU-only    | geneformer    | scgpt            |
| cross-species, zebrafish/frog/pig/macaque | uce           | scgpt/geneformer |
| prior knowledge, gene regulatory networks | genecompass   | scgpt            |
| perturbation prediction (gene KO/KD)      | tabula        | scfoundation/scgpt |
| general RNA embed/integrate, no special needs | scgpt     | -                |

### Selection Priority
1. **Unique requirement match**: If the query mentions a specific capability listed in a model's "Use when" field, select that model — even if it is ⚠️ partial-spec.
2. **Modality/species match**: ATAC-only → atacformer. Plant → scplantllm. Multi-omics → scmulan. Non-standard species → uce.
3. **Task-specific match**: Zero-shot annotation → sccello or chatcell. Perturbation → tabula. Spatial → nicheformer.
4. **General fallback**: Only select scgpt or geneformer when no specific differentiating requirement is present.

### Rules
1. Always select models from the provided model cards
2. If uncertain about parameters (like batch_key), add a question
3. If data profile shows incompatibility, select alternative model or add warning
4. Generate a complete execution plan with tool calls
5. Set confidence based on how clear the user's intent is
6. Skill-ready status (✅ vs ⚠️) is about adapter documentation, NOT model quality — do not prefer ✅ models over ⚠️ models based on status alone
"""


# =============================================================================
# Data Models (Pydantic)
# =============================================================================


class RouterIntent(BaseModel):
    """Inferred task intent from user query."""
    task: str = Field(..., description="The inferred task type")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Confidence in task inference")
    constraints: dict[str, Any] = Field(default_factory=dict, description="Additional constraints from query")

    @field_validator("task")
    @classmethod
    def validate_task(cls, v: str) -> str:
        if v not in VALID_TASKS:
            raise ValueError(f"Invalid task: {v}. Must be one of {VALID_TASKS}")
        return v


class RouterInputs(BaseModel):
    """Input information from user query."""
    query: str = Field(..., description="Original user query")
    adata_path: Optional[str] = Field(default=None, description="Path to AnnData file if provided")


class ModelSelection(BaseModel):
    """Model selection with rationale."""
    name: str = Field(..., description="Model name")
    rationale: str = Field(default="", description="Why this model was selected")


class RouterSelection(BaseModel):
    """Model selection output."""
    recommended: ModelSelection = Field(..., description="Recommended model")
    fallbacks: list[ModelSelection] = Field(default_factory=list, description="Fallback options")


class ResolvedParams(BaseModel):
    """Resolved parameters for execution."""
    output_path: Optional[str] = Field(default=None, description="Output file path")
    batch_key: Optional[str] = Field(default=None, description="Batch key in .obs")
    label_key: Optional[str] = Field(default=None, description="Label key in .obs")


class ToolCall(BaseModel):
    """A single tool call in the execution plan."""
    tool: str = Field(..., description="Tool name to call")
    args: dict[str, Any] = Field(default_factory=dict, description="Arguments for the tool")

    @field_validator("tool")
    @classmethod
    def validate_tool(cls, v: str) -> str:
        if v not in VALID_SCFM_TOOLS:
            raise ValueError(f"Invalid tool: {v}. Must be one of {VALID_SCFM_TOOLS}")
        return v


class Question(BaseModel):
    """A clarifying question for the user."""
    field: str = Field(..., description="Parameter field this question is about")
    question: str = Field(..., description="The question to ask")
    options: list[str] = Field(default_factory=list, description="Suggested options if applicable")


class RouterOutput(BaseModel):
    """Complete router output."""
    intent: RouterIntent = Field(..., description="Inferred task intent")
    inputs: RouterInputs = Field(..., description="Input information")
    data_profile: Optional[dict[str, Any]] = Field(default=None, description="Data profile if adata was provided")
    selection: RouterSelection = Field(..., description="Model selection")
    resolved_params: ResolvedParams = Field(default_factory=ResolvedParams, description="Resolved parameters")
    plan: list[ToolCall] = Field(default_factory=list, description="Execution plan")
    questions: list[Question] = Field(default_factory=list, description="Clarifying questions")
    warnings: list[str] = Field(default_factory=list, description="Warnings about the request")


# =============================================================================
# Validation
# =============================================================================


def validate_router_output(output_dict: dict[str, Any]) -> tuple[bool, list[str], Optional[RouterOutput]]:
    """
    Validate router output against schema and registry.

    Args:
        output_dict: Parsed JSON output from LLM

    Returns:
        Tuple of (is_valid, error_messages, parsed_output)
    """
    errors = []
    output_dict = _normalize_router_output_dict(output_dict)
    registry = get_registry()

    # Validate against Pydantic schema
    try:
        parsed = RouterOutput.model_validate(output_dict)
    except Exception as e:
        errors.append(f"Schema validation error: {str(e)}")
        return False, errors, None

    # Validate model exists in registry
    recommended_model = parsed.selection.recommended.name.lower()
    if registry.get(recommended_model) is None:
        available = [m.name for m in registry.list_models()]
        errors.append(f"Model '{recommended_model}' not found in registry. Available: {available[:10]}")

    # Validate fallback models exist
    for fallback in parsed.selection.fallbacks:
        if registry.get(fallback.name.lower()) is None:
            errors.append(f"Fallback model '{fallback.name}' not found in registry")

    # Validate tool names in plan
    for tool_call in parsed.plan:
        if tool_call.tool not in VALID_SCFM_TOOLS:
            errors.append(f"Invalid tool '{tool_call.tool}' in plan. Valid: {VALID_SCFM_TOOLS}")

    if errors:
        return False, errors, parsed

    return True, [], parsed


# =============================================================================
# Prompt Builder
# =============================================================================


def build_model_cards(
    skill_ready_only: bool = False,
    max_vram_gb: Optional[int] = None,
    prefer_zero_shot: bool = True,
) -> str:
    """
    Build formatted model cards for LLM prompt.

    Args:
        skill_ready_only: Only include skill-ready models
        max_vram_gb: Filter by max VRAM constraint
        prefer_zero_shot: Highlight zero-shot capable models

    Returns:
        Formatted string of model cards
    """
    registry = get_registry()
    models = registry.list_models(skill_ready_only=skill_ready_only)

    if max_vram_gb:
        models = [m for m in models if m.hardware.min_vram_gb <= max_vram_gb]

    cards = []
    for spec in models:
        status_icon = "✅" if spec.skill_ready == SkillReadyStatus.READY else "⚠️"
        zero_shot_note = " [zero-shot]" if spec.zero_shot_embedding else ""

        card = f"""### {status_icon} {spec.name}{zero_shot_note}
- **Version**: {spec.version}
- **Tasks**: {', '.join(t.value for t in spec.tasks)}
- **Species**: {', '.join(spec.species)}
- **Gene IDs**: {spec.gene_id_scheme.value}
- **VRAM**: {spec.hardware.min_vram_gb}GB min
- **CPU fallback**: {"Yes" if spec.hardware.cpu_fallback else "No"}
- **Differentiator**: {spec.differentiator or "General-purpose"}
- **Use when**: {spec.prefer_when or "No specific preference"}
"""
        cards.append(card)

    return "\n".join(cards)


def build_router_prompt(
    query: str,
    data_profile: Optional[dict[str, Any]] = None,
    model_cards: str = "",
    prefer_zero_shot: bool = True,
    max_vram_gb: Optional[int] = None,
    skill_ready_only: bool = False,
    allow_partial: bool = True,
    allow_reference: bool = False,
) -> str:
    """
    Build the user prompt for the router LLM.

    Args:
        query: User's natural language query
        data_profile: Optional data profile from scfm_profile_data
        model_cards: Formatted model cards string
        prefer_zero_shot: Prefer zero-shot capable models
        max_vram_gb: Max VRAM constraint
        skill_ready_only: Only select skill-ready models
        allow_partial: Allow partial-spec models
        allow_reference: Allow reference-only models

    Returns:
        Formatted prompt string
    """
    prompt_parts = [f"## User Query\n{query}"]

    if data_profile:
        profile_str = json.dumps(data_profile, indent=2, default=str)
        prompt_parts.append(f"## Data Profile\n```json\n{profile_str}\n```")

    if model_cards:
        prompt_parts.append(f"## Available Models\n{model_cards}")

    constraints = []
    if prefer_zero_shot:
        constraints.append("Zero-shot capability is available — but only prefer it if the user has no labeled reference data")
    if max_vram_gb:
        constraints.append(f"Max VRAM available: {max_vram_gb}GB")
    if skill_ready_only:
        constraints.append("Only select fully skill-ready (✅) models")
    elif not allow_partial:
        constraints.append("Avoid partial-spec (⚠️) models")
    if not allow_reference:
        constraints.append("Do not select reference-only models")

    if constraints:
        prompt_parts.append(f"## Constraints\n- " + "\n- ".join(constraints))

    prompt_parts.append("## Instructions\nAnalyze the query and provide your response in JSON format only.")

    return "\n\n".join(prompt_parts)


# =============================================================================
# LLM Call Helper
# =============================================================================


async def call_router_llm(
    context: dict[str, Any],
    prompt: str,
    system_prompt: str = ROUTER_SYSTEM_PROMPT,
    max_retries: int = 1,
) -> tuple[bool, dict[str, Any], list[str]]:
    """
    Call the router LLM via context._call_agent.

    Args:
        context: Context variables containing _call_agent callback
        prompt: User prompt to send
        system_prompt: System prompt for the LLM
        max_retries: Number of retries on validation failure

    Returns:
        Tuple of (success, result_dict, errors)
    """
    _call_agent = context.get("_call_agent")
    if _call_agent is None:
        return False, {}, ["_call_agent not available in context"]

    # Get model from caller_models if available
    model = None
    caller_models = context.get("caller_models")
    if caller_models and len(caller_models) > 0:
        model = caller_models[0]  # Use first available model

    errors_accumulated = []
    last_response = None

    for attempt in range(max_retries + 1):
        # Build messages
        messages = [{"role": "user", "content": prompt}]

        # Add retry context if this is a retry
        if attempt > 0 and errors_accumulated:
            retry_prompt = f"""Your previous response had validation errors:
{chr(10).join('- ' + e for e in errors_accumulated)}

Please fix these issues and return valid JSON only."""
            messages.append({"role": "user", "content": retry_prompt})

        # Call LLM
        try:
            result = await _call_agent(
                messages=messages,
                system_prompt=system_prompt,
                model=model,
            )
        except Exception as e:
            errors_accumulated.append(f"LLM call failed: {str(e)}")
            continue

        if not result.get("success"):
            errors_accumulated.append(f"LLM call unsuccessful: {result.get('error', 'Unknown error')}")
            continue

        response_text = result.get("response", "")
        last_response = response_text

        # Parse JSON from response
        parsed_json = _extract_json_from_response(response_text)
        if parsed_json is None:
            errors_accumulated.append(f"Failed to parse JSON from response: {response_text[:200]}...")
            continue

        # Validate the output
        is_valid, validation_errors, parsed_output = validate_router_output(parsed_json)

        if is_valid and parsed_output:
            return True, parsed_output.model_dump(), []

        errors_accumulated.extend(validation_errors)

    # All retries failed - return best effort
    if last_response:
        parsed_json = _extract_json_from_response(last_response)
        if parsed_json:
            return False, parsed_json, errors_accumulated

    return False, {}, errors_accumulated


def _extract_json_from_response(response: str) -> Optional[dict[str, Any]]:
    """Extract JSON from LLM response, handling markdown code blocks."""
    # Try direct parse first
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code block
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", response, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try finding JSON object boundaries
    start_idx = response.find("{")
    end_idx = response.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        try:
            return json.loads(response[start_idx:end_idx + 1])
        except json.JSONDecodeError:
            pass

    return None


async def _reroute_on_incompatibility(
    context: dict[str, Any],
    original_result: dict[str, Any],
    incompatible_model: str,
    data_profile: dict[str, Any],
    model_cards: str,
    query: str,
) -> Optional[dict[str, Any]]:
    """
    Re-call LLM to select an alternative model after incompatibility detected.

    Args:
        context: Context with _call_agent
        original_result: Original router result
        incompatible_model: Name of the incompatible model
        data_profile: Data profile showing compatibility
        model_cards: Available model cards
        query: Original user query

    Returns:
        New router result or None if reroute fails
    """
    reroute_prompt = f"""## Reroute Request

The previously selected model '{incompatible_model}' is INCOMPATIBLE with the user's data.

### Incompatibility Issues:
{chr(10).join('- ' + issue for issue in data_profile.get('model_compatibility', {}).get(incompatible_model, {}).get('issues', ['Unknown incompatibility']))}

### Original Query:
{query}

### Data Profile:
```json
{json.dumps(data_profile, indent=2, default=str)}
```

### Available Models (excluding {incompatible_model}):
{model_cards}

Please select a DIFFERENT model that is compatible with this data. Do NOT select '{incompatible_model}'.
Return valid JSON only with the same schema as before."""

    success, result, errors = await call_router_llm(
        context=context,
        prompt=reroute_prompt,
    )

    if success:
        return result
    return None


# =============================================================================
# Main Implementation
# =============================================================================


async def route_query(
    query: str,
    context: dict[str, Any],
    adata_path: Optional[str] = None,
    data_profile: Optional[dict[str, Any]] = None,
    prefer_zero_shot: bool = True,
    max_vram_gb: Optional[int] = None,
    skill_ready_only: bool = False,
    allow_partial: bool = True,
    allow_reference: bool = False,
    output_path: Optional[str] = None,
    batch_key: Optional[str] = None,
    label_key: Optional[str] = None,
) -> dict[str, Any]:
    """
    Main router function - orchestrates profiling, LLM call, validation, and rerouting.

    Args:
        query: User's natural language query
        context: Context variables containing _call_agent and other execution context
        adata_path: Optional path to AnnData file
        data_profile: Pre-computed data profile (skips profiling if provided)
        prefer_zero_shot: Prefer zero-shot capable models
        max_vram_gb: Maximum VRAM constraint
        skill_ready_only: Only select skill-ready models
        allow_partial: Allow partial-spec models
        allow_reference: Allow reference-only models
        output_path: Pre-specified output path
        batch_key: Pre-specified batch key
        label_key: Pre-specified label key

    Returns:
        RouterOutput dict with intent, selection, plan, questions, warnings
    """
    registry = get_registry()

    # Check for _call_agent availability
    if context.get("_call_agent") is None:
        return {
            "error": "Router requires _call_agent in context",
            "intent": {"task": "unknown", "confidence": 0.0, "constraints": {}},
            "inputs": {"query": query, "adata_path": adata_path},
            "data_profile": data_profile,
            "selection": {"recommended": {"name": "", "rationale": ""}, "fallbacks": []},
            "resolved_params": {"output_path": output_path, "batch_key": batch_key, "label_key": label_key},
            "plan": [],
            "questions": [],
            "warnings": ["Router cannot function without _call_agent callback"],
        }

    # Build model cards
    model_cards = build_model_cards(
        skill_ready_only=skill_ready_only,
        max_vram_gb=max_vram_gb,
        prefer_zero_shot=prefer_zero_shot,
    )

    # Build router prompt
    prompt = build_router_prompt(
        query=query,
        data_profile=data_profile,
        model_cards=model_cards,
        prefer_zero_shot=prefer_zero_shot,
        max_vram_gb=max_vram_gb,
        skill_ready_only=skill_ready_only,
        allow_partial=allow_partial,
        allow_reference=allow_reference,
    )

    # Call router LLM
    success, result, errors = await call_router_llm(
        context=context,
        prompt=prompt,
    )

    if not success:
        # Return error result with best-effort data
        return {
            "error": f"Router LLM failed: {'; '.join(errors)}",
            "intent": result.get("intent", {"task": "unknown", "confidence": 0.0, "constraints": {}}),
            "inputs": {"query": query, "adata_path": adata_path},
            "data_profile": data_profile,
            "selection": result.get("selection", {"recommended": {"name": "", "rationale": ""}, "fallbacks": []}),
            "resolved_params": {"output_path": output_path, "batch_key": batch_key, "label_key": label_key},
            "plan": result.get("plan", []),
            "questions": result.get("questions", []),
            "warnings": errors,
        }

    # Inject data_profile if we have it
    if data_profile and result.get("data_profile") is None:
        result["data_profile"] = data_profile

    # Override resolved_params with any pre-specified values
    if result.get("resolved_params"):
        if output_path:
            result["resolved_params"]["output_path"] = output_path
        if batch_key:
            result["resolved_params"]["batch_key"] = batch_key
        if label_key:
            result["resolved_params"]["label_key"] = label_key

    # Check compatibility if we have data profile and a model selection
    if data_profile and result.get("selection", {}).get("recommended", {}).get("name"):
        model_name = result["selection"]["recommended"]["name"].lower()
        spec = registry.get(model_name)

        if spec:
            # Check species compatibility
            data_species = data_profile.get("species", "").replace(" (inferred)", "").lower()
            if data_species and data_species != "unknown":
                if not spec.supports_species(data_species):
                    result.setdefault("warnings", []).append(
                        f"Selected model '{model_name}' may not support species '{data_species}'"
                    )

            # Check gene scheme compatibility
            data_gene_scheme = data_profile.get("gene_scheme", "")
            if data_gene_scheme and data_gene_scheme != "unknown":
                model_scheme = spec.gene_id_scheme.value
                if data_gene_scheme != model_scheme:
                    result.setdefault("warnings", []).append(
                        f"Data uses {data_gene_scheme} gene IDs but model '{model_name}' expects {model_scheme}"
                    )

            # Check model compatibility from profile
            model_compat = data_profile.get("model_compatibility", {}).get(model_name, {})
            if model_compat and not model_compat.get("compatible", True):
                issues = model_compat.get("issues", [])
                result.setdefault("warnings", []).extend(issues)

                # REROUTE: Call LLM again to select alternative model
                reroute_result = await _reroute_on_incompatibility(
                    context=context,
                    original_result=result,
                    incompatible_model=model_name,
                    data_profile=data_profile,
                    model_cards=model_cards,
                    query=query,
                )

                if reroute_result:
                    # Check if rerouted model is compatible
                    new_model = reroute_result.get("selection", {}).get("recommended", {}).get("name", "").lower()
                    new_compat = data_profile.get("model_compatibility", {}).get(new_model, {})

                    if new_compat.get("compatible", True):
                        # Merge reroute result, keeping original warnings
                        reroute_result.setdefault("warnings", []).extend(result.get("warnings", []))
                        reroute_result["warnings"].insert(0, f"Rerouted from '{model_name}' to '{new_model}' due to incompatibility")
                        return reroute_result
                    else:
                        # Still incompatible after reroute - add questions
                        result.setdefault("questions", []).append({
                            "field": "model_name",
                            "question": f"Both '{model_name}' and '{new_model}' are incompatible with your data. Please select a model manually.",
                            "options": [m.name for m in registry.find_models() if data_profile.get("model_compatibility", {}).get(m.name.lower(), {}).get("compatible", True)][:5]
                        })
                else:
                    # Reroute failed - suggest fallback in warnings
                    if result.get("selection", {}).get("fallbacks"):
                        for fallback in result["selection"]["fallbacks"]:
                            fallback_name = fallback.get("name", "").lower()
                            fallback_compat = data_profile.get("model_compatibility", {}).get(fallback_name, {})
                            if fallback_compat.get("compatible", True):
                                result.setdefault("warnings", []).append(
                                    f"Recommended model '{model_name}' is incompatible. Consider using fallback '{fallback_name}' instead."
                                )
                                break

    return result
