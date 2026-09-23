"""Opt-in model acceptance inside a Fleet-owned engine lifecycle test.

Uses only a pre-downloaded, checksum-verified public GGUF. The enclosing Go test
owns the engine, port, resources and temporary cache. No user daemon is touched.
"""
import argparse
import http.client
import json
import os
from pathlib import Path
import sys
import threading
import time
from http.server import ThreadingHTTPServer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--cache', required=True)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--recipe', required=True)
    parser.add_argument('--artifact', required=True)
    parser.add_argument('--blob-cache', required=True)
    parser.add_argument('--memory-bytes', type=int, required=True)
    parser.add_argument('--restart', action='store_true')
    parser.add_argument('--load-policy', choices=['manual', 'on_demand', 'warm', 'resident'], default='manual')
    args = parser.parse_args()
    sys.path.insert(0, args.source)
    from server import Connector, handler
    from artifacts import ArtifactCache
    cache = Path(args.cache)
    os.environ.update(PANTHEON_APP_CACHE=str(cache), PANTHEON_APP_SCOPE='model-acceptance')
    artifact = json.loads(Path(args.artifact).read_text())
    original = Path(args.blob_cache) / artifact['sha256']
    ArtifactCache.verify(original, artifact['sha256'], artifact['size'], threading.Event())
    connector = Connector(cache / 'acceptance-connector')
    engine = 'lmstudio' if args.recipe.startswith('llmster-') else 'ollama'
    managed = {
        'recipe_id': args.recipe, 'scope': 'engine-acceptance', 'context_length': 4096,
        'parallel': 1, 'keep_alive_seconds': 300, 'memory_bytes': args.memory_bytes,
    }
    if args.load_policy != 'manual':
        managed.update(load_policy=args.load_policy, keep_alive_seconds=3 if args.load_policy == 'warm' else 0)
    connector.configure({'engine': engine, 'endpoint': args.endpoint}, managed=managed)
    downloads = connector.downloads()
    target = downloads.cache.root / artifact['sha256']
    if not target.exists():
        os.link(original, target)
    # Exercise the ordinary durable download worker's verified-cache path.
    # Any attempted network call is a test failure, including after restart.
    class OfflineOnly:
        def open(self, *args, **kwargs):
            raise AssertionError('Managed restart/import attempted to redownload weights')
    downloads.cache.opener = OfflineOnly()
    downloads.submit('acceptance-weights', artifact)
    deadline = time.monotonic() + 15
    while downloads.list()[0]['state'] != 'ready' and time.monotonic() < deadline:
        time.sleep(.05)
    assert downloads.list()[0]['state'] == 'ready', downloads.list()
    control = connector.model_control()
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler(connector))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    model_id = 'fleet/' + artifact['sha256'] + ':latest'

    def operation(action, suffix=''):
        started = time.monotonic()
        control.submit(action + ('-restart' if args.restart else '-first') + suffix, action,
                       artifact_job_id='acceptance-weights' if action == 'import' else '',
                       model_id='' if action == 'import' else model_id)
        control.worker.join(180)
        assert not control.worker.is_alive(), 'Model operation deadline exceeded'
        jobs = control.status()['jobs']
        if jobs[0]['state'] != 'succeeded' and control.llmster():
            print('owned acceptance model catalog:', json.dumps(control.llmster().catalog()), flush=True)
        assert jobs[0]['state'] == 'succeeded', jobs[0]
        print(json.dumps({'operation': action, 'seconds': round(time.monotonic()-started, 3), 'restart': args.restart}), flush=True)

    def inference(request_id, *, cancel=False):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=60)
        started = time.monotonic()
        content, first, usage = '', None, None
        try:
            connection.request('POST', '/v1/chat/completions', json.dumps({
                'model': model_id, 'messages': [{'role': 'user', 'content': 'Count from 1 to 1000.' if cancel else 'What is 2 + 2? Answer briefly.'}],
                'stream': True, 'stream_options': {'include_usage': True}, 'temperature': 0,
                'max_tokens': 1024 if cancel else 48,
            }), {'Content-Type': 'application/json', 'X-Model-Request': request_id, 'X-Model-Config': connector.revision})
            response = connection.getresponse()
            assert response.status == 200, (response.status, response.read())
            while line := response.readline():
                if not line.startswith(b'data: ') or line.strip() == b'data: [DONE]':
                    continue
                data = json.loads(line[6:])
                choices = data.get('choices') or []
                text = choices[0].get('delta', {}).get('content', '') if choices else ''
                usage = data.get('usage') or usage
                if text:
                    content += text
                    if first is None:
                        first = time.monotonic() - started
                    if cancel:
                        before = time.monotonic()
                        assert connector.cancel(request_id)['cancelled']
                        while connector.calls and time.monotonic() - before < 3:
                            time.sleep(.01)
                        assert not connector.calls
                        print(json.dumps({'connector_cancel_release_ms': round((time.monotonic()-before)*1000, 1)}), flush=True)
                        break
            assert content and first is not None
            if not cancel:
                assert '4' in content or 'four' in content.lower(), content
            print(json.dumps({'request': request_id, 'first_content_ms': round(first*1000, 1), 'total_ms': round((time.monotonic()-started)*1000, 1), 'content': content if not cancel else '(cancelled)', 'usage': usage}), flush=True)
        finally:
            connection.close()
        deadline = time.monotonic() + 15
        while (connector.calls or connector.maintenance) and time.monotonic() < deadline:
            time.sleep(.01)
        assert not connector.calls and not connector.maintenance
        assert not connector.lifetime_error, connector.lifetime_error
    try:
        if not args.restart:
            operation('import')
        else:
            assert control.status()['models'][0]['id'] == model_id
            assert not control.status()['models'][0]['loaded']
        prefix = ('restart' if args.restart else 'first')
        if args.load_policy == 'manual':
            operation('load')
        else:
            assert connector.route_state()['models'][0]['inference_ready']
            assert not control.status()['models'][0]['loaded']
            inference(prefix + '-autoload')
        measurement = control.status()['models'][0]
        assert measurement['cold_load_ms'] > 0 and measurement['load_samples'] == 1
        assert measurement['inference_ready'] is True
        if args.load_policy == 'manual':
            operation('load', '-warm')
        else:
            inference(prefix + '-reuse')
        warm = control.status()['models'][0]
        if args.load_policy == 'on_demand':
            assert not warm['loaded'] and warm['load_samples'] == 2
        else:
            assert warm['load_samples'] == 1 and warm['cold_load_ms'] == measurement['cold_load_ms']
        if args.load_policy in {'warm', 'resident'}:
            time.sleep(4)
            observed = control.status()['models'][0]
            if args.load_policy == 'warm':
                deadline = time.monotonic() + 15
                while observed['loaded'] and time.monotonic() < deadline:
                    time.sleep(.2)
                    observed = control.status()['models'][0]
                assert not observed['loaded'], 'Engine did not release memory after idle TTL'
            else:
                assert observed['loaded'], 'Resident model was unexpectedly evicted'
            inference(prefix + '-after-idle')
            assert control.status()['models'][0]['load_samples'] == (2 if args.load_policy == 'warm' else 1)
        print(json.dumps({'cold_load_ms': measurement['cold_load_ms'], 'load_samples': measurement['load_samples'],
                          'lifetime_policy': args.load_policy, 'restart': args.restart}), flush=True)
        for i in range(3):
            inference(('restart' if args.restart else 'first') + '-' + str(i))
        inference('cancel-' + str(args.restart).lower(), cancel=True)
        measurement = control.status()['models'][0]
        if args.load_policy == 'on_demand':
            assert not measurement['loaded']
        operation('unload')
        unloaded = control.status()['models'][0]
        assert not unloaded['loaded'] and unloaded['cold_load_ms'] == measurement['cold_load_ms']
        assert unloaded['inference_ready'] == (engine == 'ollama' or args.load_policy != 'manual')
        # Probe once after unload so the read-only route snapshot is fresh.
        probe = connector.route_state()['models'][0]
        assert not probe['loaded'] and probe['inference_ready'] == unloaded['inference_ready']
        assert probe['cold_load_ms'] == unloaded['cold_load_ms']
        assert target.exists()
        print('managed model import/load/inference/unload verified; disk weights retained', flush=True)
    finally:
        server.shutdown(); server.server_close(); thread.join(3)
        control.close(); downloads.close()


if __name__ == '__main__':
    main()
