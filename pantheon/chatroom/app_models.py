"""Model composition for an independently launched Agent App.

Private settings and explicitly paired credentials are the only inputs. Both
local and Fleet launchers can use this assembly; it never discovers Hub/Fleet
credentials or imports the OS user's OAuth login. Fleet inference clients may
be supplied by an owner, but are not synthesized from a Fleet management key.
"""
from pathlib import Path
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
    def __init__(self, root, *, defaults, config, credentials, fleet_client=None):
        if not isinstance(config, dict) or config.keys() - {'providers', 'platform_budget', 'oauth', 'ollama'}:
            raise ValueError('Invalid Agent model configuration')
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
