"""Opt-in real Modal tools + independent native Agent + fixture model responses.

PANTHEON_TEST_MODAL_IMAGE must point to metadata from a prepared image of this
exact current App artifact. This creates one bounded CPU container and confirms
termination. It does not claim paid-model, production grants or controller/UI
cutover coverage.
"""
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import urllib.request

import pytest

from pantheon.apps.agent_execution_client import AgentExecutionClient
from pantheon.apps.dependency_client import DependencyClient
from pantheon.apps.lifecycle import build_artifact
from pantheon.apps.modal_app_transport import ModalAppTransport
from pantheon.apps.modal_image import ModalAppImage
from pantheon.apps.modal_sandbox import ModalSandboxOwner
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.evolution.sandbox.agent_execution import SandboxAgentExecution
from pantheon.evolution.sandbox.package import build_package
from test_agent_native_process import native_process, request
from test_evolution_remote_execution import evolution_model
from test_evolution_tools_package import EVALUATOR


@pytest.mark.skipif(not os.environ.get('PANTHEON_TEST_MODAL_IMAGE'), reason='Explicit real Modal image required')
@pytest.mark.asyncio
async def test_real_modal_mutation_with_independent_native_agent(tmp_path, evolution_model, monkeypatch):
    image = ModalAppImage(**json.loads(Path(os.environ['PANTHEON_TEST_MODAL_IMAGE']).read_text()))
    package = build_package(tmp_path / 'tools')
    assert build_artifact(package)[1] == image.artifact_sha256, 'Prepare the current App image before live acceptance'
    def forbid(*args, **kwargs):
        raise AssertionError('Controller constructed an embedded Agent')
    monkeypatch.setattr('pantheon.agent.Agent.__init__', forbid)
    root = tmp_path / 'native'
    root.mkdir()
    with native_process(root, evolution_model.url) as (child, base):
        async with asyncio.timeout(20):
            while True:
                try:
                    if (await request(base, '/health'))['ready']:
                        break
                except OSError:
                    assert child.poll() is None, (root / 'process.log').read_text()[-10000:]
                await asyncio.sleep(.05)
        class LocalGrant(DependencyClient):
            def invoke(self, method, args, **kwargs):
                req = urllib.request.Request(base + '/rpc', json.dumps({'method': method,
                    'args': {'consumer_id': 'evolution-consumer', **args}}).encode(),
                    {'Content-Type': 'application/json', 'X-Fleet-RPC-Token': 'native-test-token'})
                with urllib.request.urlopen(req, timeout=30) as response:
                    return json.load(response)
        sdk = AgentExecutionClient(LocalGrant(RuntimeCredential('https://bound.example/rpc', 'a' * 64)))
        owner = ModalSandboxOwner(tmp_path / 'container', operation_id='agent-mutation-live',
            app_name='pantheon-agent-extraction-acceptance', image_id=image.image_id,
            argv=image.argv(), timeout=180, cpu=1, memory=1024)
        owned = pipe = None
        try:
            sandbox = await owner.start()
            pipe = ModalAppTransport(sandbox)
            await pipe.ready()
            async def terminate():
                receipt = await owner.stop()
                await pipe.disconnect()
                return receipt
            owned = SandboxAgentExecution(sdk, tmp_path / 'receipts', binding_id='sandbox-agent',
                execution_id='mutation-1', backend_id=sandbox.object_id,
                invoke=pipe.invoke, terminate=terminate)
            result = await asyncio.wait_for(owned.run(instructions='Improve the code',
                model='openai/gpt-4o-mini', timeout=90, evaluate_initial=True,
                configuration={'parent_files': {'main.py': 'x=1'}, 'evaluator_code': EVALUATOR,
                               'objective': 'Improve score', 'timeout': 90}), 120)
            assert result['initial']['metrics']['score'] == .1
            assert result['mutation']['child_files'] == {'main.py': 'x = 8'}
            assert result['mutation']['metrics']['score'] == .8
            assert result['mutation']['summary'] == 'Verified eight using the caller evaluator'
            assert len(evolution_model.calls) == 5
            assert '42' in str(evolution_model.calls[2]['messages'])
            assert type(await sandbox.poll.aio()) is int
            evidence = {'image': asdict(image), 'backend_id': sandbox.object_id,
                'model_fixture_turns': len(evolution_model.calls), 'result': result,
                'returncode': await sandbox.poll.aio(), 'receipt_dir': str(tmp_path)}
            (tmp_path / 'acceptance.json').write_text(json.dumps(evidence, indent=2))
            print(json.dumps({'backend_id': sandbox.object_id, 'initial_score': .1, 'final_score': .8,
                              'model_fixture_turns': 5, 'receipt_dir': str(tmp_path)}))
            assert (await request(base, '/_fleet/drain', {}))['safe_to_stop']
        finally:
            try:
                if owned is not None:
                    await owned.close()
                else:
                    await owner.stop()
                    if pipe is not None:
                        await pipe.disconnect()
            finally:
                if pipe is not None:
                    (tmp_path / 'tool-stderr-tail.log').write_bytes(pipe.stderr_tail)
                await sdk.close()
