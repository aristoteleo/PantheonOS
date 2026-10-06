"""Second real Runner process on a private LocalFleet test control plane.

Separate state/workspace, ordinary TLS enrollment and authenticated node commands.
Both Runners still share the test host; this is not a physical cross-host gate.
"""
import asyncio
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace

import nats

from pantheon.apps.client import AppClient
from pantheon.platform.local_fleet import _local_environment


@asynccontextmanager
async def fleet_peer(runtime, root, workspace):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    (root/'runtime.json').unlink(missing_ok=True)
    info = runtime.coordinates
    command = [runtime.binaries.runner, 'up', '--controller', info.controller,
               '--controller-ca', info.ca_certificate, '--local-dependency-rpc',
               '--key-file', runtime.root/'owner.key', '--state-dir', root,
               '--workdir', workspace, '--share-dir', workspace,
               '--name', 'Isolated model provider', '--no-auto-update', '--no-capture-setup']
    with (root/'test-runner.log').open('ab') as log:
        process = await asyncio.create_subprocess_exec(*map(str, command), cwd=workspace,
            env=_local_environment(), stdin=asyncio.subprocess.DEVNULL, stdout=log, stderr=log)
        nc = None
        try:
            nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
                                    inbox_prefix=('_INBOX_'+info.fleet_id).encode())
            client = AppClient(nc, info.fleet_id)
            async with asyncio.timeout(45):
                while True:
                    assert process.returncode is None, (root/'test-runner.log').read_text(errors='replace')
                    try:
                        coordinates = json.loads((root/'runtime.json').read_text())
                    except (FileNotFoundError, json.JSONDecodeError):
                        await asyncio.sleep(.05)
                        continue
                    assert coordinates['fleet_id'] == info.fleet_id
                    assert coordinates['nats_url'] == info.nats
                    node = coordinates['node_id']
                    assert node != info.node_id
                    if await client.ping(node, timeout=.5):
                        state = await client.lifecycle(node, 'status')
                        assert state['node_id'] == node and state['owner'] == info.fleet_id
                        break
                    await asyncio.sleep(.05)
            yield SimpleNamespace(node_id=node, process=process, root=root)
        finally:
            if nc is not None:
                await nc.close()
            if process.returncode is None:
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 10)
            except TimeoutError:
                process.kill()
                await process.wait()
                raise AssertionError('Second Runner failed to terminate')
