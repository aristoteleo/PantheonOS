"""CLI uses the original Connector with consumer grants, including revocation.

Real subprocess, TLS RPC and Connector HTTP/SSE; Hub directory and engine replies
are fixtures. This is not live provider or automatic local composition acceptance.
"""
import asyncio

import pytest

from test_agent_launch import prepared
from test_agent_release import release
from test_model_dependency import model_endpoint, model_dependency, tls_material
from test_prepared_cli import launch


@pytest.mark.asyncio
@pytest.mark.parametrize('ref', ['fleet-model://mac/example%3A8b', 'fleet-route://local'])
async def test_packaged_cli_model_services_resume_and_revocation(
        tmp_path, release, model_dependency, model_endpoint, monkeypatch, ref):
    config = prepared(tmp_path, model_endpoint.url)
    config['credentials'].pop('model')
    config['credentials']['model_services'] = model_dependency.credential
    config['values']['agent']['models'] = {
        'model_services': 'model_services', 'fleet_tiers': {'normal': ref}}
    monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'cert.pem'))
    source = tmp_path / 'agent.md'
    source.write_text('---\nid: cli-models\nname: CLI Models\nmodel: normal\n'
                      'toolsets: []\n---\nReply to the user.\n')

    async def run(*args):
        return await asyncio.to_thread(launch, tmp_path, model_endpoint.url, 'package',
            *args, bundle=release, configuration=config)

    first = await run('--template', str(source), '-i', 'first model services prompt')
    assert first.returncode == 0, first.stdout + first.stderr
    assert 'scoped reply' in first.stdout
    second = await run('-r', '-i', 'second model services prompt', '--model', ref)
    assert second.returncode == 0, second.stdout + second.stderr
    assert 'scoped reply' in second.stdout
    inference = [r for r in model_endpoint.requests if r[0] == '/v1/chat/completions']
    assert len(inference) == 2
    assert inference[-1][2]['model'] == 'example:8b'
    assert any('first model services prompt' in str(m.get('content'))
               for m in inference[-1][2]['messages'])
    assert model_dependency.grants
    assert all(g['consumer']['instance_id'] == 'agent-app' for g in model_dependency.grants)
    # Same published CLI must fail closed; ambient keys cannot rescue a revoked
    # route, and opening the conversation must not replay prior inference.
    model_dependency.revoked = True
    denied = await run('-r', '-i', 'must never reach a model', '--model', ref)
    assert denied.returncode != 0, denied.stdout + denied.stderr
    assert len([r for r in model_endpoint.requests if r[0] == '/v1/chat/completions']) == 2
    assert 'ambient-do-not-use' not in denied.stdout + denied.stderr
