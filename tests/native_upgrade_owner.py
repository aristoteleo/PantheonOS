"""Disposable coordinator process for native upgrade crash acceptance.

Called only with an explicitly owned LocalFleet fixture and private journal roots.
It exits immediately after each real submit acknowledgement, before returning that
acknowledgement to the coordinator. The node, broker and Controller stay alive.
"""
import asyncio
import json
import os
from pathlib import Path
import sys

import nats

# The disposable interpreter may have a different checkout installed editable.
# Always test the source tree containing this driver.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pantheon.apps.dependency_assembly import AssemblyError, DependencyStarter
from pantheon.apps.deployment import AppDeployment
from pantheon.apps.deployment_abort import AppDeploymentAbort
from pantheon.apps.deployment_stop import AppDeploymentStop
from pantheon.apps.deployment_upgrade import AppUpgradePreparation
from pantheon.apps.lifecycle import FleetLifecycle
from pantheon.apps.resolver import AppInstanceResolver


async def main(path):
    config = json.loads(Path(path).read_text())
    root = Path(config['root'])
    nc = await nats.connect(config['nats'], user_credentials=config['credentials'],
                           inbox_prefix=('_INBOX_' + config['owner']).encode())
    resolver = AppInstanceResolver(config['owner'], config['node'], config['owner'],
                                   str(root), connection=nc)
    wire = FleetLifecycle(resolver)
    original = wire.submit

    async def interrupted_submit(*args, **kwargs):
        result = await original(*args, **kwargs)
        # Evidence only. App/owner/node state is never changed by the fault.
        with (root/'accepted-before-owner-exit.jsonl').open('a') as stream:
            stream.write(json.dumps(result['request']) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os._exit(73)

    wire.submit = interrupted_submit
    deployment = AppDeployment(DependencyStarter(wire, root/'starts'), root/'deployments')
    operations = dict(deploy=deployment, stop=AppDeploymentStop(deployment, root/'stops'),
                      upgrade=AppUpgradePreparation(deployment, root/'upgrades'),
                      abort=AppDeploymentAbort(deployment))
    try:
        result = await operations[config['kind']].advance(**config['args'])
    except AssemblyError as exc:
        result = {'assembly_error': str(exc)}
    finally:
        await resolver.close()
    Path(config['result']).write_text(json.dumps(result))


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1]))
