"""Pinned llmster foreground launch with a deployment-owned HOME and runtime.

The generated settings are for the pinned recipe only. No user's GUI instance,
global daemon, shell profile, runtime directory or model directory is modified.
"""
import json
import os
from pathlib import Path
from urllib.request import urlopen


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.tmp')
    with open(temporary, 'w') as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def home_for(cache, scope):
    return Path(cache) / 'models' / 'lmstudio' / scope


def configure(home, config, port, recipe_id):
    root = Path(home) / '.lmstudio'
    path = root / 'settings.json'
    settings = json.loads(path.read_text()) if path.exists() else {}
    settings.update(autoLoadBundledLLM=False, enableLocalService=False,
                    defaultContextLength={'type': 'custom', 'value': config['context_length']})
    settings.setdefault('developer', {}).update(
        autoUpdateExtensionPacks=False, autoDeleteExtensionPacks=False,
        allowDevelopmentPlugins=False, attemptedInstallLmsCliOnStartup=True,
        unloadPreviousJITModelOnLoad=True,
        jitModelTTL={'enabled': True, 'ttlSeconds': config['keep_alive_seconds']})
    write_json(path, settings)
    # Explicit loads must use the deployment context, TTL and budget. Keep JIT
    # off: startup migrations can replace the vendor's default context setting.
    # This also prevents bundled models from loading outside Fleet admission.
    write_json(root / '.internal/http-server-config.json', dict(
        autoStartOnLaunch=True, port=port, networkInterface='127.0.0.1', cors=False,
        justInTimeModelLoading=False, logSensitiveData=False,
        logIncomingTokens=False, verbose=False, logLinesLimit=100, fileLoggingMode='succinct'))
    write_json(root / 'fleet-engine.json', dict(pid=os.getpid(), port=port, recipe_id=recipe_id))


def ready(home, port, recipe_id):
    root = Path(home) / '.lmstudio'
    identity = json.loads((root / 'fleet-engine.json').read_text())
    daemon_pid = int((root / '.internal/llmster-pid.lock').read_text())
    if identity != {'pid': daemon_pid, 'port': port, 'recipe_id': recipe_id}:
        raise ValueError('Owned llmster instance is not ready')
    os.kill(daemon_pid, 0)
    with urlopen(f'http://127.0.0.1:{port}/v1/models', timeout=2) as response:
        if not isinstance(json.loads(response.read(2 << 20)).get('data'), list):
            raise ValueError('Owned llmster API is not ready')


def start(binary, cache, scope, config, selected, port):
    home = home_for(cache, scope)
    configure(home, config, port, selected['id'])
    env = {k: v for k, v in os.environ.items() if not k.startswith(('LMS_', 'LMSTUDIO_'))
           and k != 'PANTHEON_APP_RPC_TOKEN'}
    env.update(HOME=str(home), LMS_DAEMON_DISABLE_UPDATES='1', LMS_SERVER_HOST='127.0.0.1')
    os.chdir(binary.parent)
    # Do not use `lms daemon up`: Fleet must own the actual foreground daemon,
    # including the process group containing its inference workers.
    os.execve(binary, [str(binary)], env)
