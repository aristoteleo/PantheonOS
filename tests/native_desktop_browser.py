"""Real ordinary Browser package retained through Agent uninstall/reinstall.

The UI sends all input through the production stream. This helper only stages
and starts the App and observes its native inventory/content over real Fleet RPC.
"""
import asyncio
import base64
import io
from pathlib import Path


class DesktopBrowser:
    def __init__(self, root, wire, deploy, owner, stage, rpc, operation, binding):
        self.root, self.wire, self.deploy, self.owner = root, wire, deploy, owner
        self.stage, self.rpc, self.operation, self.binding = stage, rpc, operation, binding
        self.observed = set()

    async def start(self, platform):
        from pantheon.apps.portable import execution_package
        repo = Path(__file__).resolve().parents[1]
        with execution_package(repo/'apps/browser', platform) as package:
            self.digest = await self.stage('provider-node', package)
        self.scope = 'independent-browser'
        recipe = {'browser': dict(node_id='provider-node', revision=self.digest,
                                  scope=self.scope, generation=0, bindings={}, components={})}
        async with asyncio.timeout(180):
            first = True
            while True:
                result = await self.deploy.advance(owner=self.owner, operation_id=self.scope,
                                                   apps=recipe if first else None)
                first = False
                if result['state'] == 'ready':
                    break
                assert result['state'] == 'pending', result
                await asyncio.sleep(.1)
        state = await self.wire.status('provider-node')
        instance = next(i for i in state['instances'].values() if i['digest'] == self.digest)
        self.bound = self.binding('provider-node', instance)

    async def observe(self, page_id='', capture=False):
        pages = await self.rpc(self.bound, 'browser', 'browser_pages')
        result = {'pages': pages['pages']}
        if page_id:
            try:
                result['selected'] = await self.rpc(self.bound, 'browser', 'browser_read', page_id=page_id)
            except (AssertionError, RuntimeError) as error:
                # A read can overlap native navigation/closure. Expose the
                # observation failure; never repeat input or create a page.
                result['read_error'] = str(error)
        if capture and page_id and 'selected' in result:
            from PIL import Image
            shot = await self.rpc(self.bound, 'browser', 'desktop_native_screenshot',
                                  window_id='joint-observer', page_id=page_id)
            image = Image.open(io.BytesIO(base64.b64decode(shot['data_url'].split(',', 1)[1]))).convert('RGB')
            top = next(y for y in range(image.height) if image.getpixel((10, y)) == (19, 97, 163))
            result['geometry'] = {'width':image.width, 'height':image.height, 'top':top}
        self.observed.update(p['page_id'] for p in result['pages'])
        return result

    async def verify(self):
        state = await self.wire.status('consumer-node')
        assert not any(i['app_id'] == 'agent' and i['state'] == 'ready'
                       for i in state['instances'].values())
        assert len(self.observed) >= 2, 'Browser native close and reopen were not observed'
        assert not (await self.observe())['pages'], 'Atrium close left Browser pages alive'

    async def stop(self):
        state = await self.wire.status('provider-node')
        instance = state['instances'][self.bound['instance_id']]
        stopped = await self.operation('provider-node', 'stop', self.digest, self.scope, instance['generation'])
        assert stopped['state'] == 'stopped'
