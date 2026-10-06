"""Ordinary Notebook deployment in the real desktop/Agent retirement gate.

Only seed source is written here. Execution and widget interaction must happen
through the production desktop UI while the Agent package is uninstalled.
"""
import asyncio
import json
import os
from pathlib import Path

import psutil


class DesktopNotebook:
    def __init__(self, root, wire, deploy, owner, stage, rpc, operation, binding):
        self.root, self.wire, self.deploy, self.owner = root, wire, deploy, owner
        self.stage, self.rpc, self.operation, self.binding = stage, rpc, operation, binding

    async def start(self, platform):
        from pantheon.apps.builtin.notebook.build_managed import build
        from notebook_widgets_smoke import CODE
        package = build(self.root/'notebook', platform,
                        frontend=Path(os.environ['PANTHEON_TEST_NOTEBOOK_FRONTEND']))
        self.digest = await self.stage('provider-node', package)
        self.scope = 'independent-notebook'
        recipe = {'notebook': dict(node_id='provider-node', revision=self.digest,
            scope=self.scope, generation=0, bindings={}, components={'backend': {'values': {
                'notebook': {'execution_timeout': 60, 'execution_logging': True}}}})}
        async with asyncio.timeout(120):
            first = True
            while True:
                result = await self.deploy.advance(owner=self.owner, operation_id='independent-notebook',
                                                   apps=recipe if first else None)
                first = False
                if result['state'] == 'ready':
                    break
                assert result['state'] == 'pending', result
                await asyncio.sleep(.1)
        state = await self.wire.status('provider-node')
        instance = next(i for i in state['instances'].values() if i['digest'] == self.digest)
        self.bound = self.binding('provider-node', instance)
        host = await self.rpc(self.bound, 'integrated-notebook', 'execution_host')
        self.workspace = Path(host['workspace'])
        assert (await self.rpc(self.bound, 'integrated-notebook', 'create_notebook',
                              notebook_path='independent.ipynb'))['success']
        source = ("import os\nfrom pathlib import Path\n"
                  "Path('notebook-kernel.pid').write_text(str(os.getpid()))\n"
                  "print('NOTEBOOK_WITHOUT_AGENT')\n" + CODE)
        seeded = await self.rpc(self.bound, 'integrated-notebook', 'add_cell',
                               notebook_path='independent.ipynb', content=source, execute=False)
        assert seeded['success'], seeded
        assert not (self.workspace/'notebook-kernel.pid').exists()

    async def verify(self):
        state = await self.wire.status('consumer-node')
        assert not any(i['app_id'] == 'agent' and i['state'] == 'ready'
                       for i in state['instances'].values())
        saved = json.loads((self.workspace/'independent.ipynb').read_text())
        outputs = [o for c in saved['cells'] for o in c.get('outputs', [])]
        assert any('NOTEBOOK_WITHOUT_AGENT' in ''.join(o.get('text', '')) for o in outputs), outputs
        assert any('application/vnd.jupyter.widget-view+json' in o.get('data', {}) for o in outputs)
        self.pid = int((self.workspace/'notebook-kernel.pid').read_text())
        assert psutil.pid_exists(self.pid)
        check = await self.rpc(self.bound, 'integrated-notebook', 'add_cell', notebook_path='independent.ipynb',
                              content='assert count == 2\nassert slider.value == 4', execute=True)
        assert check['execution']['success'], check

    async def stop(self):
        state = await self.wire.status('provider-node')
        instance = state['instances'][self.bound['instance_id']]
        if instance['state'] != 'stopped':
            stopped = await self.operation('provider-node', 'stop', self.digest, self.scope, instance['generation'])
            assert stopped['state'] == 'stopped'
        assert not psutil.pid_exists(self.pid), 'Notebook stop left its kernel running'
        assert (self.workspace/'independent.ipynb').is_file()
