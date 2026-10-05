"""Independent desktop bus joined to the real native acceptance Fleet control.

Only bus-service placement and node inventory are fixtures. Installed package
reads, lifecycle, usage, and Agent RPC use the real Controller and Managers.
"""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from platform_no_agent import install
install()
from pantheon.apps import resolver
from pantheon.apps.builtin.desktop import DesktopToolSet
from pantheon.apps.builtin.file import FileManagerToolSet
from pantheon.apps.builtin.file_transfer import FileTransferToolSet
from pantheon.platform.bootstrap import serve
from pantheon.platform.service import PlatformService
from pantheon.models.manager import ModelServiceManager


async def main():
    root = Path.cwd()
    config = json.loads(Path(os.environ['NATIVE_DESKTOP_CONFIG']).read_text())
    def command(node, body):
        req = Request(config['base']+'/fixture/node/'+node, data=json.dumps(body).encode(),
                      headers={'Authorization':'Bearer '+config['key'], 'Content-Type':'application/json'})
        with urlopen(req, timeout=100) as response: return json.load(response)
    class Control:
        async def lifecycle(self, node, method, **data):
            result = await asyncio.to_thread(command, node, dict(type='app_lifecycle', protocol=1, method=method, **data))
            return result
        async def invoke(self, node, app_id, binding, method, args, timeout):
            return await self.lifecycle(node, 'invoke', app_id=app_id,
                instance_id=binding['instance_id'], revision=binding['revision'], generation=binding['generation'],
                payload={'method':method,'args':args,'timeout_s':timeout}, timeout_seconds=min(600, max(1, int(timeout))))
    services = {
        'desktop': DesktopToolSet(id_hash='native-desktop'),
        'file_manager': FileManagerToolSet('file_manager', root, id_hash='native-desktop-files'),
        'file_transfer': FileTransferToolSet('file_transfer', root, id_hash='native-desktop-transfer'),
    }
    class Placement(resolver.AppInstanceResolver):
        def resolves(self, name): return name in services
        async def ensure_instance(self, name, **kwargs): return services[name].service_id
        async def _ensure_client(self): pass
        async def _list_nodes(self, **kwargs):
            return [dict(node_id=node, name=node, last_seen=datetime.now(timezone.utc).isoformat(),
                state={'status':'online'}, capability={'os':'darwin', 'arch':'arm64', 'caps':['proc'],
                'runtimes':{'app-lifecycle':'1', 'app-services':'1', 'app-rpc-auth':'1'}})
                for node in ('consumer-node','provider-node')]
    placement = Placement(config['owner'],'consumer-node','native-desktop',str(root))
    placement._client = Control()
    resolver._shared = placement
    resolver._shared_built = True
    tasks = [asyncio.create_task(service.run(log_level='WARNING')) for service in services.values()]
    try:
        await asyncio.wait_for(asyncio.gather(*(s._worker_ready.wait() for s in services.values())), 15)
        platform = PlatformService(id_hash='native-desktop-platform',workspace_path=str(root))
        platform._model_services = ModelServiceManager(resolver=placement)
        platform_task = asyncio.create_task(serve(platform,log_level='INFO'))
        await asyncio.wait_for(platform._worker_ready.wait(),15)
        (root/'services.json').write_text(json.dumps({**{name:s.service_id for name,s in services.items()}, 'platform':platform.service_id}))
        await platform_task
    finally:
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        for service in services.values():
            if service._backend: await service._backend.close()


if __name__ == '__main__': asyncio.run(main())
