"""Original Model Services idle intent and transition contract."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .errors import DirectoryError

MAX_REVISION = 9007199254740991


class NodeBinding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    instance_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    generation: int = Field(ge=1, le=MAX_REVISION, strict=True)


class IdleRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['status', 'wake']
    revision: int = Field(ge=1, strict=True)


class IdleRegistration(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-z0-9][a-z0-9_-]{0,63}$')
    revision: int = Field(ge=0, le=MAX_REVISION-2, strict=True)
    connector: NodeBinding
    engine: NodeBinding
    config_revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    idle_seconds: int = Field(ge=1, le=86400, strict=True)


class EngineIdlePolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    idle_seconds: int = Field(ge=1, le=86400, strict=True)
    policy_revision: int = Field(ge=0, le=MAX_REVISION, strict=True)
    phase: Literal['registering', 'enabled', 'disabling', 'disabled']
    registration: IdleRegistration


class IdleSnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-z0-9][a-z0-9_-]{0,63}$')
    revision: int = Field(ge=1, le=MAX_REVISION, strict=True)
    enabled: bool = Field(strict=True)
    state: Literal['active', 'fencing', 'stopping', 'sleeping', 'waking', 'rebinding', 'recovery_required']
    connector: NodeBinding
    engine: NodeBinding
    config_revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    idle_seconds: int = Field(ge=1, le=86400, strict=True)
    cycle: int = Field(ge=0, le=MAX_REVISION, strict=True)
    wake_requested: bool = Field(strict=True)


def pending(row):
    return any(row.get(k) for k in ('recovery', 'connector_update', 'engine_update', 'operation_stop'))


def validate_policy(row):
    policy = row.get('engine_idle')
    if not policy:
        return
    source = policy['registration']
    if source['id'] != row['deployment_id'] or source['idle_seconds'] != policy['idle_seconds']:
        raise DirectoryError(400, 'Idle policy must retain its original registration identity')
    if (row['mode'] != 'managed' or row['engine'] not in {'ollama', 'lmstudio'}
            or not row.get('binding') or not row.get('engine_binding')
            or row['managed'].get('load_policy') not in {'on_demand', 'warm'}
            or policy['idle_seconds'] < row['managed']['keep_alive_seconds']):
        raise DirectoryError(400, 'Engine idle requires an owned on-demand or warm engine and cannot shorten its model TTL')
    if policy['phase'] != 'disabled' and (row['state'] != 'ready' or pending(row)):
        raise DirectoryError(409, 'Disable and reconcile engine idle before changing service lifecycle')
    revisions = {'registering': {source['revision']}, 'enabled': {source['revision']+1},
                 'disabling': {source['revision'], source['revision']+1},
                 'disabled': {source['revision']+1, source['revision']+2}}
    if policy['policy_revision'] not in revisions[policy['phase']]:
        raise DirectoryError(400, 'Idle policy must match the acknowledged registration or cancellation')


def validate_transition(old, new):
    before, after = old.get('engine_idle'), new.get('engine_idle')
    if before == after and (not before or before['phase'] == 'disabled'):
        return
    if before and after is None:
        raise DirectoryError(409, 'Retain the last idle policy revision for reconciliation')
    if not before or before['phase'] == 'disabled':
        expected = (before or {}).get('policy_revision', 0)
        if (not after or after['phase'] != 'registering' or after['policy_revision'] != expected
                or old['state'] != 'ready' or pending(old)):
            raise DirectoryError(409, 'Record registration intent before enabling engine idle')
        source = after['registration']
        if (source['revision'] != expected or source['config_revision'] != old['config_revision']
                or source['connector'] != {k: old['binding'][k] for k in ('instance_id', 'revision', 'generation')}
                or source['engine'] != {k: old['engine_binding'][k] for k in ('instance_id', 'revision', 'generation')}):
            raise DirectoryError(409, 'Register this exact ready connector and engine')
    else:
        transitions = {'registering': {'registering', 'enabled', 'disabling'},
                       'enabled': {'enabled', 'disabling'}, 'disabling': {'disabling', 'disabled'}}
        if (not after or after['phase'] not in transitions[before['phase']]
                or after['idle_seconds'] != before['idle_seconds'] or after['registration'] != before['registration']):
            raise DirectoryError(409, 'Resume the exact pending idle policy change')
        expected = {before['policy_revision'] + int((before['phase'], after['phase']) == ('registering', 'enabled'))}
        if before['phase'] == 'disabling' and after['phase'] == 'disabled':
            expected = {before['registration']['revision']+1, before['registration']['revision']+2}
            if before['policy_revision'] > before['registration']['revision']:
                expected = {before['policy_revision']+1}
        if after['policy_revision'] not in expected:
            raise DirectoryError(409, 'Idle policy revision does not match its acknowledgement')
    # Node observations publish newer engine generations through the dedicated
    # endpoint below. A general save cannot turn a stale wake into a new service.
    if before and before['phase'] == 'disabling' and after['phase'] == 'disabled':
        a, b = old['engine_binding'], new['engine_binding']
        if (any(old.get(k) != new.get(k) for k in ('binding', 'managed', 'config_revision'))
                or {k: v for k, v in a.items() if k != 'generation'} != {k: v for k, v in b.items() if k != 'generation'}
                or b['generation'] < a['generation']):
            raise DirectoryError(409, 'Cancellation may reconcile only this exact owned engine generation')
    elif any(old.get(k) != new.get(k) for k in ('binding', 'engine_binding', 'config_revision', 'managed')):
        raise DirectoryError(409, 'Reconcile the exact idle bindings before changing the service')


