"""Explicit model and Files bindings for one plugin composition's auxiliary work.

The task-local snapshot carries no newly minted authority. Child tasks inherit
it when admitted, so another conversation cannot redirect an in-flight request.
Bindings are borrowed from the App composition and closed after plugin drain.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class AuxiliaryCall:
    model: str | None = None
    scope: object = None


class AuxiliaryExecution:
    def __init__(self, *, model_scope=None, tool_bindings=None):
        self._default = AuxiliaryCall(scope=model_scope)
        self.tool_bindings = tool_bindings
        self._call = ContextVar('auxiliary_call', default=None)

    def snapshot(self):
        return self._call.get() or self._default

    @contextmanager
    def use(self, call):
        token = self._call.set(call)
        try:
            yield
        finally:
            self._call.reset(token)

    def for_agent(self, agent):
        current = self.snapshot()
        models = getattr(agent, 'models', None)
        scope = getattr(agent, 'model_scope', None) or current.scope
        return self.use(AuxiliaryCall(models[0] if models else current.model, scope))

    def for_team(self, team, memory):
        if memory is not None:
            agent = team.get_active_agent(memory)
        else:
            agents = getattr(team, 'team_agents', None) or getattr(team, 'agents', ())
            agents = list(agents.values()) if isinstance(agents, dict) else list(agents)
            agent = agents[0] if agents else None
        return self.for_agent(agent)

    def set_model(self, model):
        self._call.set(AuxiliaryCall(model, self.snapshot().scope))

    def resolve_model(self, configured):
        from pantheon.agent import _is_model_tag
        raw = getattr(configured, '_tag', configured)
        call = self.snapshot()
        automatic = raw is None or (isinstance(raw, str) and raw.strip().lower() in ('', 'auto'))
        spec = (call.model or 'normal') if automatic else raw
        if call.scope is not None:
            return call.scope.models(spec)[0] if _is_model_tag(spec) else spec
        return spec if automatic else configured

    async def complete_text(self, model, messages, model_params):
        scope = self.snapshot().scope
        if scope is not None:
            from pantheon.utils.llm_providers import call_llm_provider, detect_provider
            result = await call_llm_provider(detect_provider(model, relaxed_schema=False, settings=scope.settings),
                                             messages, model_params=model_params, scope=scope)
            return result.get('content') or ''
        from pantheon.utils.llm import acompletion
        result = await acompletion(model=model, messages=messages, model_params=model_params)
        return result.choices[0].message.content or ''

    async def run_agent(self, prompt, **kwargs):
        from pantheon.internal.background_agent import run_background_agent
        call = self.snapshot()
        return await run_background_agent(prompt, **kwargs, model_scope=call.scope,
                                          tool_bindings=self.tool_bindings)
