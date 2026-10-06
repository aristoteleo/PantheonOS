"""Original deployment schemas and lifecycle transitions, without a Hub runtime."""
import json
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .errors import DirectoryError
from .idle import EngineIdlePolicy, validate_policy, validate_transition as validate_idle_transition

DIFFUSION_RECIPES = {
    'sglang-diffusion-0.5.20-linux-amd64': {
        'model': 'sdxl-turbo-71153311', 'operation': 'image', 'memory_bytes': 24 << 30},
    'sglang-wan-0.5.20-linux-amd64': {
        'model': 'wan2-1-t2v-1-3b-0fad780a', 'operation': 'video', 'memory_bytes': 48 << 30},
}


# Node-provided SGLang serving a pinned catalog LLM (Modal GPU nodes). The
# runtime catalog pins files and parsers; the Hub only admits its identities.
LLM_RECIPES = {'sglang-0.5.20-linux-amd64-node': {'qwen3.6-35b-a3b-fp8', 'qwen3-30b-a3b-instruct-2507', 'qwen3-8b'}}
# Custom Hugging Face models pinned by the Agent carry their bounded manifest.
CUSTOM_LLM_ID = re.compile(r'^hf-[a-z0-9][a-z0-9.-]{0,90}$')


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1, max_length=200)
    name: str = Field(default='', max_length=200)
    operations: list[Literal['text', 'embedding', 'rerank', 'speech', 'transcription', 'image', 'video']] = Field(default_factory=lambda: ['text'], max_length=7)
    tools: bool | None = None
    vision: bool | None = None
    reasoning: bool | None = None
    structured_output: bool | None = None
    context: int | None = Field(default=None, ge=1)
    # The owner's cap on the context the service reports; context is the effective value.
    context_limit: int | None = Field(default=None, ge=512)
    compute: Literal['node', 'provider', 'unknown'] = 'unknown'


class Binding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    node_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    instance_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    generation: int = Field(ge=1)
    component: Literal['backend'] = 'backend'
    port: Literal['http'] = 'http'


class DeviceBudget(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')
    backend: Literal['cuda', 'metal']
    memory_bytes: int = Field(ge=256 << 20, le=1 << 50, strict=True)
    exclusive: bool = Field(strict=True)


class MemoryBudget(BaseModel):
    model_config = ConfigDict(extra='forbid')
    memory_bytes: int = Field(ge=256 << 20, le=1 << 50, strict=True)
    devices: list[DeviceBudget] = Field(min_length=0, max_length=8)


class ManagedEngine(BaseModel):
    model_config = ConfigDict(extra='forbid')
    recipe_id: str = Field(pattern=r'^[a-z0-9][a-z0-9_.-]{0,79}$')
    context_length: int = Field(ge=512, le=1048576, strict=True)
    parallel: int = Field(ge=1, le=16, strict=True)
    keep_alive_seconds: int = Field(ge=0, le=86400, strict=True)
    load_policy: Literal['manual', 'on_demand', 'warm', 'resident'] | None = None
    resources: MemoryBudget
    tensor_parallel_size: int | None = Field(default=None, strict=True)
    model_artifact_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    model_recipe_id: str | None = Field(default=None, max_length=96)
    model_manifest: dict | None = None

    @model_validator(mode='after')
    def lifetime(self):
        tp = self.tensor_parallel_size if self.tensor_parallel_size is not None else 1
        if tp not in {1, 2, 4, 8}:
            raise ValueError('Tensor parallel size must be 1, 2, 4 or 8')
        llm = LLM_RECIPES.get(self.recipe_id)
        if self.tensor_parallel_size is not None and self.recipe_id != 'sglang-0.5.20-linux-amd64' and not llm:
            raise ValueError('Tensor parallelism requires the pinned SGLang text recipe')
        if tp > 1 and (len({d.id for d in self.resources.devices}) != tp
                or any(d.backend != 'cuda' or not d.id.startswith('GPU-') or not d.exclusive for d in self.resources.devices)):
            raise ValueError('Each tensor parallel rank requires a distinct exclusive NVIDIA GPU')
        diffusion = DIFFUSION_RECIPES.get(self.recipe_id)
        custom = bool(self.model_recipe_id and CUSTOM_LLM_ID.match(self.model_recipe_id))
        if self.model_manifest is not None or custom:
            files = (self.model_manifest or {}).get('files')
            if (llm is None or not custom or not isinstance(self.model_manifest, dict)
                    or self.model_manifest.get('id') != self.model_recipe_id
                    or not isinstance(files, list) or not 0 < len(files) <= 256
                    or len(json.dumps(self.model_manifest)) > 64 << 10):
                raise ValueError('A custom model manifest (hf-…, at most 256 files and 64 KB) runs on the node-provided SGLang only')
        if self.model_recipe_id and not custom and self.model_recipe_id not in {
                'kokoro-82m-v1', 'whisper-tiny-en', *(d['model'] for d in DIFFUSION_RECIPES.values()),
                *set().union(*LLM_RECIPES.values())}:
            raise ValueError('Unknown pinned model recipe')
        if llm is not None or self.model_recipe_id in set().union(*LLM_RECIPES.values()) or custom:
            if (llm is None or (self.model_recipe_id not in llm and not custom) or self.model_artifact_sha256
                    or len(self.resources.devices) != tp
                    or any(d.backend != 'cuda' or not d.id.startswith('GPU-') or not d.exclusive for d in self.resources.devices)
                    or self.load_policy not in {'manual', 'resident'} or self.keep_alive_seconds != 0):
                raise ValueError('A catalog language model runs resident on exclusive NVIDIA GPUs of a node-provided SGLang')
        elif diffusion:
            if (self.model_recipe_id != diffusion['model'] or self.model_artifact_sha256
                    or self.resources.memory_bytes < diffusion['memory_bytes'] or len(self.resources.devices) != 1
                    or self.resources.devices[0].backend != 'cuda' or not self.resources.devices[0].id.startswith('GPU-')
                    or self.resources.devices[0].memory_bytes < 20 << 30 or not self.resources.devices[0].exclusive
                    or self.parallel != 1 or self.context_length != 512
                    or self.load_policy != 'resident' or self.keep_alive_seconds != 0):
                raise ValueError('Owned diffusion requires its pinned resident model, memory budget and one exclusive CUDA device')
        elif self.model_recipe_id:
            minimum = (4 if self.model_recipe_id == 'kokoro-82m-v1' else 2) << 30
            if (self.model_recipe_id not in {'kokoro-82m-v1', 'whisper-tiny-en'}
                    or self.resources.devices or self.resources.memory_bytes < minimum or self.parallel != 1
                    or self.context_length != 512 or self.load_policy != 'resident'
                    or self.model_artifact_sha256 or self.keep_alive_seconds != 0
                    or self.recipe_id != 'speaches-0.9.0-rc.3-linux-amd64-cpu'):
                raise ValueError('CPU speech requires a pinned resident model and sufficient system memory')
        elif len(self.resources.devices) != tp and not (
                not self.resources.devices and tp == 1 and self.tensor_parallel_size is None
                and self.recipe_id.startswith('ollama-') and '-darwin' not in self.recipe_id):
            # CPU-only Ollama on Linux/Windows holds no accelerator lease.
            raise ValueError('Declare exactly one accelerator per tensor parallel rank')
        if ((self.load_policy == 'warm' and self.keep_alive_seconds < 1)
                or (self.load_policy in {'on_demand', 'resident'} and self.keep_alive_seconds != 0)):
            raise ValueError('Warm models require a positive idle TTL; on-demand and resident use zero')
        return self


class ConnectorUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source: Binding
    target_revision: str = Field(pattern=r'^[a-f0-9]{64}$')


class Recovery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    binding: Binding
    engine_binding: Binding | None = None


class EngineUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    source: Binding
    connector: Binding
    target_revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    target_instance_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    target_config: ManagedEngine
    started_at: float = Field(ge=0, allow_inf_nan=False)
    phase: Literal['draining', 'stopping', 'starting', 'verifying']


class StopBinding(Binding):
    generation: int = Field(ge=0)


class StopTarget(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal['binding', 'engine_binding', 'replacement']
    scope: str = Field(pattern=r'^(model|engine)-[a-z0-9][a-z0-9_-]{0,63}$')
    binding: StopBinding


class OperationStop(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    kind: Literal['recovery', 'connector_update', 'engine_update']
    started_at: float = Field(ge=0, allow_inf_nan=False)
    targets: list[StopTarget] = Field(min_length=1, max_length=3)


class CompletedStop(OperationStop):
    completed_at: float = Field(ge=0, allow_inf_nan=False)


class Deployment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    deployment_id: str = Field(pattern=r'^[a-z0-9][a-z0-9_-]{0,63}$')
    name: str = Field(min_length=1, max_length=120)
    node_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    node_name: str = Field(default='', max_length=120)
    engine: Literal['ollama', 'lmstudio', 'sglang', 'speaches', 'api']
    mode: Literal['attached', 'managed'] = 'attached'
    managed: ManagedEngine | None = None
    engine_binding: Binding | None = None
    state: Literal['draft', 'ready', 'stopping', 'stopped', 'recovering'] = 'draft'
    engine_idle: EngineIdlePolicy | None = None
    binding: Binding | None = None
    connector_update: ConnectorUpdate | None = None
    engine_update: EngineUpdate | None = None
    recovery: Recovery | None = None
    operation_stop: OperationStop | None = None
    last_operation_stop: CompletedStop | None = None
    config_revision: str = Field(default='', max_length=64)
    models: list[Model] = Field(default_factory=list, max_length=1000)
    revision: int = Field(default=0, ge=0)

    @model_validator(mode='after')
    def typed_adapter(self):
        diffusion = DIFFUSION_RECIPES.get(self.managed.recipe_id) if self.managed else None
        if diffusion and (self.engine != 'sglang' or self.mode != 'managed'
                or any(op != diffusion['operation'] for m in self.models for op in m.operations)):
            raise ValueError(f"This managed SGLang Diffusion recipe publishes {diffusion['operation']} generation only")
        attached_diffusion = self.engine == 'sglang' and self.mode == 'attached' and not self.managed
        attached_image_api = self.engine == 'api' and self.mode == 'attached' and not self.managed
        if any('image' in m.operations for m in self.models) and not (attached_diffusion or diffusion or attached_image_api):
            raise ValueError('Image generation requires SGLang Diffusion or an attached Images API')
        if any('video' in m.operations for m in self.models) and not (attached_diffusion or diffusion):
            raise ValueError('Video generation requires an SGLang Diffusion engine')
        if self.engine not in {'sglang', 'api'} and any('rerank' in m.operations for m in self.models):
            raise ValueError('Rerank requires the SGLang or API connector adapter')
        if self.engine not in {'speaches', 'api'} and any(op in {'speech', 'transcription'} for m in self.models for op in m.operations):
            raise ValueError('Speech requires the Speaches or API connector adapter')
        if self.engine == 'speaches' and any(op not in {'speech', 'transcription'} for m in self.models for op in m.operations):
            raise ValueError('The Speaches connector currently publishes speech generation and transcription only')
        return self


def normalize(body):
    """Validate a typed publication and return its canonical persistent payload."""
    if body.binding and body.binding.node_id != body.node_id:
        raise DirectoryError(400, 'Deployment cannot silently move to another node')
    if body.connector_update and (body.connector_update.source.node_id != body.node_id or body.state != 'stopping'):
        raise DirectoryError(400, 'A connector update must stay on this node and block new inference')
    if body.engine_update:
        pending = body.engine_update
        if (body.mode != 'managed' or body.state != 'stopping' or body.recovery or body.connector_update
                or pending.source != body.engine_binding or pending.connector != body.binding
                or pending.source.node_id != body.node_id or pending.connector.node_id != body.node_id
                or pending.target_revision == pending.source.revision
                or pending.target_instance_id == pending.source.instance_id
                or not body.managed or pending.target_config.model_dump(exclude={'recipe_id'}) != body.managed.model_dump(exclude={'recipe_id'})):
            raise DirectoryError(400, 'An engine update must pin this owned service and retain its model and resource configuration')
    if (body.state == 'recovering') != bool(body.recovery):
        raise DirectoryError(400, 'Recovery must retain its intent and block new inference')
    if body.recovery:
        if (body.connector_update or body.recovery.binding.node_id != body.node_id
                or body.binding != body.recovery.binding
                or body.engine_binding != body.recovery.engine_binding):
            raise DirectoryError(400, 'Recovery must retain the exact source bindings until complete')
        if body.mode == 'managed' and not body.recovery.engine_binding:
            raise DirectoryError(400, 'Recover a managed service only after its engine was created')
    if body.engine_binding and body.engine_binding.node_id != body.node_id:
        raise DirectoryError(400, 'Managed engine must run on the deployment node')
    if body.mode == 'attached' and (body.managed is not None or body.engine_binding is not None):
        raise DirectoryError(400, 'Attached services cannot own a managed engine')
    if body.mode == 'managed' and (not body.managed or body.engine not in {'ollama', 'lmstudio', 'sglang', 'speaches'}):
        raise DirectoryError(400, 'A managed service needs a supported engine configuration')
    diffusion = bool(body.managed and body.managed.recipe_id in DIFFUSION_RECIPES)
    llm = bool(body.managed and body.managed.recipe_id in LLM_RECIPES)
    if body.managed and ((body.engine == 'speaches') != bool(body.managed.model_recipe_id and not diffusion and not llm)):
        raise DirectoryError(400, 'Pinned speech configuration must match a Speaches service')
    if body.mode == 'managed' and body.engine == 'sglang' and not diffusion and ((not body.managed.model_artifact_sha256 and not llm)
            or body.managed.keep_alive_seconds != 0 or body.managed.load_policy not in {None, 'manual', 'resident'}):
        raise DirectoryError(400, 'Managed SGLang needs an immutable model snapshot and resident lifetime')
    if body.mode == 'managed' and body.state == 'ready' and not body.engine_binding:
        raise DirectoryError(400, 'A managed service must own an exact engine instance before publication')
    if body.state == 'ready' and (not body.binding or len(body.config_revision) != 64):
        raise DirectoryError(400, 'A ready deployment needs an exact instance and configuration')
    payload = body.model_dump(exclude={'revision'})
    if body.managed:
        payload['managed'] = body.managed.model_dump(exclude_none=True)
    if body.engine_update:
        payload['engine_update']['target_config'] = body.engine_update.target_config.model_dump(exclude_none=True)
    validate_policy(payload)
    if len(json.dumps(payload)) > 512 * 1024:
        raise DirectoryError(413, 'Model directory entry is too large')
    return payload


def validate_create(body):
    if body.engine_idle:
        raise DirectoryError(400, 'Create and verify the service before enabling engine idle')
    if body.engine_update or body.operation_stop or body.last_operation_stop:
        raise DirectoryError(400, 'Create and verify the service before updating its engine')


def validate_update(old, body, payload):
    """Compare under the storage adapter's revision lock before committing."""
    validate_idle_transition(old, payload)
    if any(old.get(k, 'attached' if k == 'mode' else None) != payload[k]
           for k in ('node_id', 'engine', 'mode')):
        raise DirectoryError(409, 'Create a new deployment for a different node, ownership mode or managed engine configuration')
    before, after = old.get('engine_update'), payload.get('engine_update')
    from .operation_stop import validate_transition
    stop_transition = validate_transition(old, payload)
    if stop_transition:
        pass  # Exact stopped bindings/configuration are checked by the stop protocol.
    elif before:
        if after:
            if ({k: v for k, v in before.items() if k != 'phase'} != {k: v for k, v in after.items() if k != 'phase'}
                    or any(old.get(k) != payload.get(k) for k in ('managed', 'config_revision', 'models'))):
                raise DirectoryError(409, 'Resume the exact pinned engine update')
        elif (body.state != 'ready' or body.recovery or body.connector_update
                or payload['managed'] != before['target_config'] or payload['binding'] != before['connector']
                or not body.engine_binding or body.engine_binding.revision != before['target_revision']
                or body.engine_binding.instance_id != before['target_instance_id']
                or body.engine_binding.generation != 1 or payload['models'] != old['models']):
            raise DirectoryError(409, 'Publish only the verified target of the pending engine update')
    elif after:
        if (old['state'] != 'ready' or old.get('recovery') or old.get('connector_update')
                or after['source'] != old.get('engine_binding') or after['connector'] != old.get('binding')
                or any(old.get(k) != payload.get(k) for k in ('managed', 'config_revision', 'models'))):
            raise DirectoryError(409, 'Begin an engine update from this exact ready service')
    elif old.get('managed') != payload['managed']:
        raise DirectoryError(409, 'Use an explicit engine update to change the pinned recipe')
