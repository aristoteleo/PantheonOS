"""Local browser gate host: real Platform, Desktop and Files, no Agent imports.

Only placement is a fixture: existing App services have fixed local NATS IDs.
This is not a Fleet controller/runner or managed App lifecycle acceptance test.
"""
import asyncio
import importlib.abc
import json
from pathlib import Path
import sys


class NoAgent(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if any(fullname == name or fullname.startswith(name + '.') for name in (
                'pantheon.agent', 'pantheon.chatroom', 'pantheon.team',
                'pantheon.factory', 'pantheon.internal.learning_system', 'pantheon.internal.memory')):
            raise AssertionError('Desktop platform imported Agent: ' + fullname)


sys.meta_path.insert(0, NoAgent())

from pantheon.apps.builtin.desktop import DesktopToolSet
from pantheon.apps.builtin.file import FileManagerToolSet
from pantheon.apps.builtin.file_transfer import FileTransferToolSet
from pantheon.apps import resolver
from pantheon.platform.bootstrap import serve
from pantheon.platform.service import PlatformService


async def main():
    root = Path.cwd()
    desktop = DesktopToolSet(id_hash='desktop-gate')
    files = FileManagerToolSet('file_manager', root, id_hash='files-gate')
    transfer = FileTransferToolSet('file_transfer', root, id_hash='transfer-gate')
    services = {'desktop': desktop, 'file_manager': files, 'file_transfer': transfer}

    class LocalPlacement(resolver.AppInstanceResolver):
        def resolves(self, name):
            return name in services

        async def ensure_instance(self, name, **kwargs):
            assert name in services, name
            return services[name].service_id

        async def _list_nodes(self, **kwargs):
            return []

        async def _ensure_client(self):
            raise RuntimeError('Fleet controller is outside this desktop fixture')

    resolver._shared = LocalPlacement('fixture', 'fixture', 'desktop-gate', str(root))
    resolver._shared_built = True
    tasks = [asyncio.create_task(service.run(log_level='WARNING')) for service in services.values()]
    try:
        await asyncio.wait_for(asyncio.gather(*(service._worker_ready.wait() for service in services.values())), 15)
        (root/'services.json').write_text(json.dumps({name: service.service_id for name, service in services.items()}))
        await serve(PlatformService(id_hash='platform-desktop-gate', workspace_path=str(root)), log_level='WARNING')
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for service in services.values():
            if service._backend:
                await service._backend.close()


if __name__ == '__main__':
    asyncio.run(main())
