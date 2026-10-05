"""Local browser gate host: real Platform, Desktop and Files, no Agent imports.

Default mode fixes placement to local workers. Native mode uses the production
resolver and a real Fleet Runner; neither mode launches Agent implementation.
"""
import asyncio
import json
import os
from pathlib import Path
from platform_no_agent import install

install()

from pantheon.apps.builtin.desktop import DesktopToolSet
from pantheon.apps.builtin.file import FileManagerToolSet
from pantheon.apps.builtin.file_transfer import FileTransferToolSet
from pantheon.apps import resolver
from pantheon.platform.bootstrap import serve
from pantheon.platform.service import PlatformService


async def main():
    root = Path.cwd()
    if os.environ.get('PANTHEON_TEST_DESKTOP_FLEET'):
        # Production resolver and native Runner place each App in a separate
        # process. This host contains only Platform, never the App workers.
        placement = resolver.get_shared_resolver(str(root))
        ids = {name: await placement.ensure_instance(name)
               for name in ('desktop', 'file_manager', 'file_transfer')}
        (root/'services.json').write_text(json.dumps(ids))
        try:
            await serve(PlatformService(id_hash='platform-desktop-gate', workspace_path=str(root)), log_level='WARNING')
        finally:
            await placement.close()
        return
    from pantheon.apps.builtin.desktop.session_binding import DesktopSessionBinding
    from pantheon.apps.builtin.desktop import desktop_session, presence
    from pantheon.remote import RemoteBackendFactory
    from pantheon.remote.streams import NamedStreamPublisher
    desktop_state = root.parent / 'desktop-state'
    desktop_state.mkdir()
    # Composition owns this connection. The service must use the bound state
    # even though ambient settings point to a different workspace directory.
    publisher = NamedStreamPublisher(backend=RemoteBackendFactory.create_backend())
    desktop = DesktopToolSet(id_hash='desktop-gate',
        session_binding=DesktopSessionBinding(desktop_state, publisher))
    def no_global_store():
        raise AssertionError('Bound Desktop used an ambient global store')
    desktop_session.get_store = presence.get_store = no_global_store
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
