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
    connector.configure({'engine': engine, 'endpoint': args.endpoint}, managed={
        'recipe_id': args.recipe, 'scope': 'engine-acceptance', 'context_length': 4096,
        'parallel': 1, 'keep_alive_seconds': 300, 'memory_bytes': args.memory_bytes,
    })
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

    def operation(action):
        started = time.monotonic()
        control.submit(action + ('-restart' if args.restart else '-first'), action,
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
        deadline = time.monotonic() + 3
        while connector.calls and time.monotonic() < deadline:
            time.sleep(.01)
        assert not connector.calls
    try:
        if not args.restart:
            operation('import')
        else:
            assert control.status()['models'][0]['id'] == model_id
            assert not control.status()['models'][0]['loaded']
        operation('load')
        for i in range(3):
            inference(('restart' if args.restart else 'first') + '-' + str(i))
        inference('cancel-' + str(args.restart).lower(), cancel=True)
        operation('unload')
        assert not control.status()['models'][0]['loaded']
        assert target.exists()
        print('managed model import/load/inference/unload verified; disk weights retained', flush=True)
    finally:
        server.shutdown(); server.server_close(); thread.join(3)
        control.close(); downloads.close()


if __name__ == '__main__':
    main()
