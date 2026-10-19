"""Real model calls around an isolated Fleet node's automatic idle cycle.

The Go parent owns the exact connector/engine processes and has already verified
and linked the public weight blob into their private cache. No user daemon or
installed deployment is contacted. The owner token is never printed.
"""
import json
import os
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen


endpoint, phase = sys.argv[1:]
artifact = json.loads(Path(os.environ['FLEET_TEST_MODEL_SOURCE']).read_text())


def rpc(method, args=None):
    request = Request(endpoint + '/rpc', json.dumps({'method': method, 'args': args or {}}).encode(),
                      headers={'Content-Type': 'application/json',
                               'X-Fleet-RPC-Token': os.environ['FLEET_IDLE_TEST_RPC_TOKEN']})
    with urlopen(request, timeout=15) as response:
        return json.load(response)


def wait_job(method, job_id, success):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        row = next(r for r in rpc(method)['jobs'] if r['job_id'] == job_id)
        if row['state'] == success:
            return row
        assert row['state'] in {'queued', 'running', 'downloading'}, row
        time.sleep(.1)
    raise AssertionError('Owned model operation timed out')


if phase == 'before':
    # Use an intentionally unreachable URL with the original verified blob
    # identity: any attempt to redownload weights instead of reuse fails.
    source = {**artifact, 'url': 'https://127.0.0.1:1/never-download.gguf'}
    rpc('artifacts_submit', {'job_id': 'idle-weights', 'source': source})
    wait_job('artifacts_list', 'idle-weights', 'ready')
    rpc('models_submit', {'job_id': 'idle-import', 'action': 'import', 'artifact_job_id': 'idle-weights'})
    wait_job('models_status', 'idle-import', 'succeeded')
else:
    assert phase == 'after'
    assert next(r for r in rpc('artifacts_list')['jobs'] if r['job_id'] == 'idle-weights')['state'] == 'ready'
    assert next(r for r in rpc('models_status')['jobs'] if r['job_id'] == 'idle-import')['state'] == 'succeeded'

model = 'fleet/' + artifact['sha256'] + ':latest'
status = rpc('status')
assert status['accepting'] and not status['engine_idle']['admission_fenced']
payload = {'model': model, 'messages': [{'role': 'user', 'content': 'What is 2 + 2? Answer briefly.'}],
           'stream': True, 'temperature': 0, 'max_tokens': 48}
request = Request(endpoint + '/v1/chat/completions', json.dumps(payload).encode(), headers={
    'Content-Type': 'application/json', 'X-Model-Request': 'coordinator-' + phase,
    'X-Model-Config': status['config_revision']})
text, done = '', False
started = time.monotonic()
with urlopen(request, timeout=60) as response:
    for line in response:
        if line.strip() == b'data: [DONE]':
            done = True
        elif line.startswith(b'data: '):
            choices = json.loads(line[6:]).get('choices') or []
            text += choices[0].get('delta', {}).get('content', '') if choices else ''
assert done and ('4' in text or 'four' in text.lower()), (done, text)
deadline = time.monotonic() + 15
while time.monotonic() < deadline:
    activity = rpc('activity')
    if not activity['maintenance'] and not activity['lifetime_pending']:
        break
    time.sleep(.05)
assert not activity['maintenance'] and not activity['lifetime_pending'] and not activity['lifetime_error']
models = rpc('models_status')['models']
assert len(models) == 1 and models[0]['id'] == model and models[0]['loaded'] is False, models
print(json.dumps({'phase': phase, 'stream_complete': done, 'content': text,
                  'model_unloaded': True, 'weights_retained': True,
                  'elapsed_ms': round((time.monotonic() - started) * 1000)}), flush=True)
