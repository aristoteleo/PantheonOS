"""Model composition for an independently launched Agent App.

Private settings and explicitly paired credentials are the only inputs. Both
local and Fleet launchers can use this assembly; it never discovers Hub/Fleet
credentials or imports the OS user's OAuth login. Model Services may be supplied
through an explicit dependency credential, never a Fleet management key.
"""
from pathlib import Path
import asyncio
import time
from urllib.parse import urlsplit

from pantheon.settings import Settings
from pantheon.utils.model_scope import ModelCallScope
from pantheon.utils.model_selector import ModelSelector, PROVIDER_API_KEYS


def model_endpoint(value):
    parts = urlsplit(value)
    if (parts.scheme not in ('https', 'http') or not parts.hostname or parts.username
            or parts.password or parts.query or parts.fragment):
        raise ValueError('Invalid model endpoint')
    parts.port
    return value.rstrip('/')


class AppSettings(Settings):
    """Deployment defaults below App-private saved user/project settings."""
    def __init__(self, root, *, defaults, environment):
        from copy import deepcopy
        if not isinstance(defaults, dict) or 'api_keys' in defaults or 'env_file' in defaults:
            raise ValueError('Deliver model credentials separately from App settings defaults')
        self._app_defaults = deepcopy(defaults)
        super().__init__(Path(root) / 'configuration', user_home=Path(root) / 'user',
                         isolated_env=True, environment=environment)

    def _load_package_defaults(self):
        from copy import deepcopy
        defaults = super()._load_package_defaults()
        self._merge_settings(defaults, deepcopy(self._app_defaults))
        return defaults


class AppModels:
    def __init__(self, root, *, defaults, config, credentials, fleet_client=None, tls_context=None):
        if not isinstance(config, dict) or config.keys() - {'providers', 'platform_budget', 'oauth', 'ollama', 'model_services'}:
            raise ValueError('Invalid Agent model configuration')
        dependency = config.get('model_services')
        if 'model_services' in config:
            if (not isinstance(dependency, str) or dependency not in credentials or fleet_client is not None):
                raise ValueError('Supply one explicit Model Services dependency credential reference')
            from pantheon.apps.dependency_client import DependencyClient
            from pantheon.models.dependency import DependencyModelServices
            fleet_client = DependencyModelServices(DependencyClient(credentials[dependency], tls_context=tls_context))
        providers, oauth = config.get('providers', {}), config.get('oauth', [])
        if (not isinstance(providers, dict) or not isinstance(oauth, list)
                or any(p not in ('codex', 'gemini-cli') for p in oauth)
                or len(set(oauth)) != len(oauth)):
            raise ValueError('Invalid Agent model bindings')
        from pantheon.utils.llm_providers import get_provider_base_env
        from pantheon.utils.provider_registry import get_provider_config
        environment = {}
        for provider, alias in providers.items():
            key_env = PROVIDER_API_KEYS.get(provider)
            if not key_env or not isinstance(alias, str) or alias not in credentials:
                raise ValueError('Invalid Agent provider credential reference')
            credential = credentials[alias]
            environment[key_env] = credential.key
            environment[get_provider_base_env(provider, get_provider_config(provider))] = model_endpoint(credential.endpoint)
        budget = config.get('platform_budget')
        if budget is not None:
            if not isinstance(budget, str) or budget not in credentials:
                raise ValueError('Invalid Agent budget credential reference')
            credential = credentials[budget]
            environment.update(LLM_FORCE_PROXY='true', PLATFORM_MODEL_MODE='openrouter',
                PANTHEON_PLATFORM_PROXY_BASE=model_endpoint(credential.endpoint),
                PANTHEON_PLATFORM_PROXY_KEY=credential.key)
        self._ollama_url = None
        if 'ollama' in config:
            self._ollama_url = model_endpoint(config['ollama'])
            self._ollama_url = self._ollama_url.removesuffix('/v1')
            environment['OLLAMA_API_BASE'] = self._ollama_url + '/v1'
        self.settings = AppSettings(root, defaults=defaults, environment=environment)
        managers = {}
        for provider in oauth:
            from pantheon.utils.oauth import CodexOAuthManager, GeminiCliOAuthManager
            manager = CodexOAuthManager if provider == 'codex' else GeminiCliOAuthManager
            managers[provider] = manager(auth_file=Path(root) / 'oauth' / (provider + '.json'))
        self._ollama_state, self._ollama_checked = (False, []), None
        self.scope = ModelCallScope(self.settings, fleet_client=fleet_client,
            oauth_managers=managers, resolve_models=self.resolve,
            ollama_state=lambda: self._ollama_state)
        self.selector = ModelSelector(self.settings, scope=self.scope)
        self._owned_fleet = fleet_client if dependency is not None else None
        self._refresh_lock = asyncio.Lock()
        self.fleet_options, self.fleet_error = [], ''

    def resolve(self, spec):
        from pantheon.agent import _is_model_tag, _parse_thinking_suffix
        if spec is None:
            spec = 'normal'
        if not isinstance(spec, str) or not spec.strip():
            raise ValueError('Choose a model or quality tag')
        clean, _ = _parse_thinking_suffix(spec)
        return self.selector.resolve_model(clean) if _is_model_tag(clean) else [clean]

    def validate(self, spec):
        try:
            available = self.selector._effective_providers()
            for model in self.resolve(spec):
                if model.startswith(('fleet-model://', 'fleet-route://')):
                    from pantheon.models.client import parse_ref, parse_route_ref
                    (parse_route_ref if model.startswith('fleet-route://') else parse_ref)(model)
                    self.scope.fleet()
                    if self._owned_fleet is not None and not any(
                            item['value'] == model and not item['disabled'] for item in self.fleet_options):
                        raise ValueError('Model is not available in the authorized catalog')
                else:
                    from pantheon.utils.provider_registry import find_provider_for_model
                    provider = (model.split('/', 1)[0] if '/' in model else
                                find_provider_for_model(model, settings=self.settings)[0] or 'openai')
                    provider = {'google': 'gemini', 'vertex_ai': 'gemini'}.get(provider, provider)
                    if provider not in available:
                        raise ValueError('Model provider is not bound to this App')
            return True, ''
        except Exception:
            return False, 'The selected model is unavailable in this Agent App. Check its model bindings.'

    async def refresh(self):
        async with self._refresh_lock:
            await self._refresh_ollama()
            if self._owned_fleet is None:
                return
            try:
                sources, models = await self._owned_fleet.catalog()
                sources = {source['id']: source for source in sources}
                options = []
                for model in models:
                    if 'text' not in model['operations']:
                        continue
                    source = sources[model['source']]
                    reason = ('Model service is unavailable' if not source['available'] else
                              'Publish with Tools supported to use it for an agent'
                              if model['capabilities'].get('tools') is not True else
                              'Publish with a context length to use it for an agent'
                              if type(model.get('context')) is not int or model['context'] <= 0 else '')
                    options.append(dict(value=model['model'], label=model['name'],
                        description=model.get('description', ''), disabled=bool(reason), reason=reason))
                self.fleet_options, self.fleet_error = options, ''
            except asyncio.CancelledError:
                self.fleet_options = []
                self._owned_fleet.metadata.clear()
                raise
            except Exception:
                # A failed/revoked catalog must not leave stale selectable models
                # or prevent this App's independent BYOK models from working.
                self.fleet_options = []
                self._owned_fleet.metadata.clear()
                self.fleet_error = 'Model Services is unavailable or no longer authorized. Refresh its binding.'

    def catalog(self):
        return {**self.selector.list_available_models(), 'fleet_models': list(self.fleet_options),
                'fleet_catalog_ready': not bool(self.fleet_error), 'fleet_catalog_error': self.fleet_error}

    async def aclose(self):
        if self._owned_fleet is not None:
            await self._owned_fleet.aclose()

    async def _refresh_ollama(self):
        # Discovery is outside synchronous Agent construction and tied to this
        # exact endpoint. Failures never reuse another App's localhost catalog.
        if self._ollama_url is None or (self._ollama_checked is not None
                and time.monotonic() - self._ollama_checked < 30):
            return
        import httpx
        try:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=2) as client:
                response = await client.get(self._ollama_url + '/api/tags')
                response.raise_for_status()
                models = [item['name'] for item in response.json()['models']]
                if not all(isinstance(m, str) and m for m in models):
                    raise ValueError
                self._ollama_state = True, models
        except Exception:
            self._ollama_state = False, []
        self._ollama_checked = time.monotonic()
