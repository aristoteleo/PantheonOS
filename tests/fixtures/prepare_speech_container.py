"""Prepare the shipped managed recipe/cache for an opt-in Linux Docker test.

Only registry discovery is replaced with the source App directory; the actual
managed.validate/package and speech model preparation implementations run unchanged.
No model SDK or inference dependencies are installed on the runner.
"""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import threading
import types

ROOT = Path(__file__).resolve().parents[2]
APPS = ROOT / 'apps'
sys.modules['pantheon.apps.registry'] = types.SimpleNamespace(BUILTIN_ROOT=APPS)

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

managed = load('managed', ROOT / 'pantheon/models/managed.py')
speech = load('speech_models', APPS / 'model-service/speech_models.py')
artifacts = load('artifacts', APPS / 'model-service/artifacts.py')
model_id, cache, destination = sys.argv[1:]
selected = speech.model(model_id)
config = dict(recipe_id='speaches-0.9.0-rc.3-linux-amd64-cpu', model_recipe_id=model_id,
              context_length=512, parallel=1, keep_alive_seconds=0, load_policy='resident',
              resources=dict(memory_bytes=selected['minimum_memory_bytes'], devices=[]))
with managed.package(config, 'linux-amd64') as package:
    shutil.copytree(package, destination)
prepared = speech.SpeechModelCache(cache, artifacts).fetch(speech.source(selected), threading.Event(), lambda *args: None)
assert speech.verify_directory(prepared, selected)
print(json.dumps(dict(model=selected['model'], sha256=speech.source(selected)['sha256'],
                     memory_bytes=selected['minimum_memory_bytes'])))
