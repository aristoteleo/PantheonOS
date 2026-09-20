"""Browser + real kernel acceptance for the actual packaged notebook bundle.

Usage: python tests/notebook_widgets_smoke.py /path/to/notebook-app.js [screenshot.png]
Requires notebook dependencies, aiohttp and Playwright Chromium. Uses only a
temporary notebook and a loopback server, never an existing user kernel.
"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile

from aiohttp import web
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pantheon.apps.builtin.notebook.integrated_notebook import IntegratedNotebookToolSet


CODE = """
import ipywidgets as widgets
from ipycanvas import Canvas, hold_canvas
from IPython.display import display
slider = widgets.IntSlider(description='Speed', value=3, min=0, max=50)
button = widgets.Button(description='Step', icon='play')
label = widgets.Label(value='Count: 0')
out = widgets.Output()
canvas = Canvas(width=160, height=100)
count = 0
def draw():
    with hold_canvas():
        canvas.clear()
        canvas.fill_style = '#ff0000'
        canvas.fill_rect(4, 4, 32, 32)
def step(_):
    global count
    count += 1
    label.value = f'Count: {count}'
    with out:
        print(f'clicked {count}')
button.on_click(step)
canvas.on_mouse_down(lambda x,y: step(None))
canvas.on_client_ready(draw)
draw()
display(widgets.VBox([label, widgets.HBox([button, slider]), canvas, out]))
"""

HTML = """<!doctype html><html><head><meta charset="utf-8"></head>
<body style="margin:0"><div id="app" style="height:100vh"></div><script type="module">
import {setup} from '/notebook-app.js';
window.errors = [];
window.addEventListener('unhandledrejection', e => window.errors.push(String(e.reason)));
const app = {theme:'light',onTheme(){},onState(cb){cb({path:'widgets.ipynb'})},
  setState(){},ready(){},window:{},
  async call(method,args){
    const r = await fetch('/rpc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({method,args})});
    return await r.json();
  }};
setup(app, document.getElementById('app'));
</script></body></html>"""


async def main(bundle):
    with tempfile.TemporaryDirectory(prefix='pantheon-widgets-') as workdir:
        notebook = IntegratedNotebookToolSet('widget-smoke', workdir=workdir,
                                              streaming_mode='local', execution_logging=False)
        context = {'client_id': 'desktop', 'workdir': workdir}
        async def call(method, **args):
            return await getattr(notebook, method)(**args, context_variables=context)
        await notebook.run_setup()
        # Register the test interpreter in a TEMPORARY kernelspec directory.
        # This avoids accidentally testing a different system Python.
        import os
        specroot = Path(workdir) / 'jupyter' / 'kernels' / 'widget-smoke'
        specroot.mkdir(parents=True)
        (specroot / 'kernel.json').write_text(json.dumps({
            'argv': [sys.executable, '-m', 'ipykernel_launcher', '-f', '{connection_file}'],
            'display_name': 'Widget smoke', 'language': 'python'}))
        os.environ['JUPYTER_PATH'] = str(Path(workdir) / 'jupyter')
        await call('create_notebook', notebook_path='widgets.ipynb', kernel_spec='widget-smoke')
        result = await call('add_cell', notebook_path='widgets.ipynb', content=CODE, execute=True)
        assert result['success'] and result['execution']['success'], result
        denied = await notebook.widget_channel(notebook_path='widgets.ipynb',
                                               context_variables={'client_id': 'different-session'})
        assert not denied['success'] and denied['code'] == 'kernel_missing', denied

        async def rpc(request):
            body = await request.json()
            method = body['method']
            assert method in notebook.functions, method
            return web.json_response(await call(method, **body.get('args', {})))
        app = web.Application()
        app.router.add_get('/', lambda _: web.Response(text=HTML, content_type='text/html'))
        app.router.add_get('/notebook-app.js', lambda _: web.FileResponse(bundle))
        app.router.add_post('/rpc', rpc)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                errors = []
                def watch(new_page):
                    new_page.on('pageerror', lambda e: errors.append(e.stack))
                page = await browser.new_page(viewport={'width': 1200, 'height': 1000})
                watch(page)
                await page.goto(url)
                await page.locator('.widget-host button').filter(has_text='Step').wait_for(timeout=45000)
                await page.locator('.widget-host button').filter(has_text='Step').click()
                await page.get_by_text('Count: 1', exact=True).wait_for()
                await page.get_by_text('clicked 1', exact=True).wait_for()
                slider = page.locator('.widget-host [role=slider]')
                await slider.focus()
                await slider.press('ArrowRight')
                await asyncio.sleep(.2)
                check = await call('add_cell', notebook_path='widgets.ipynb', content='assert slider.value == 4', execute=True)
                assert check['execution']['success'], check
                canvas = page.locator('.widget-host canvas').first
                assert (await canvas.bounding_box())['width'] == 160
                assert await canvas.evaluate("e => [...e.getContext('2d').getImageData(10,10,1,1).data]") == [255, 0, 0, 255]
                await canvas.click(position={'x': 20, 'y': 20})
                await page.get_by_text('Count: 2', exact=True).wait_for()
                # Kernel-side updates continue after execution completes.
                await call('add_cell', notebook_path='widgets.ipynb', content="import threading; threading.Timer(.2, lambda: setattr(label, 'value', 'Background update')).start()", execute=True)
                await page.get_by_text('Background update', exact=True).wait_for()
                # Reopen the UI; preserve the same live Python models.
                await page.reload()
                await page.get_by_text('Background update', exact=True).wait_for(timeout=30000)
                await page.locator('.widget-host button').filter(has_text='Step').click()
                await page.get_by_text('Count: 3', exact=True).wait_for()
                screenshot = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(tempfile.gettempdir()) / 'pantheon-notebook-widgets.png'
                await page.screenshot(path=str(screenshot))
                assert not await page.evaluate('window.errors'), await page.evaluate('window.errors')
                # A second client shares live state without sharing a cursor.
                second = await browser.new_page()
                watch(second)
                await second.goto(url)
                await second.get_by_text('Count: 3', exact=True).wait_for(timeout=30000)
                await second.locator('.widget-host button').filter(has_text='Step').click()
                await page.get_by_text('Count: 4', exact=True).wait_for()
                await second.close()
                # Simulate a viewer offline for longer than the bounded replay
                # window; recover actual current state through the control comm.
                await page.goto('about:blank')
                await call('add_cell', notebook_path='widgets.ipynb', content="for i in range(600): label.value = f'Updated: {i}'", execute=True)
                await page.goto(url)
                await page.get_by_text('Updated: 599', exact=True).wait_for(timeout=30000)
                await page.wait_for_function("""() => {
                    const e = document.querySelector('.widget-host canvas');
                    return e && e.getContext('2d').getImageData(10,10,1,1).data[0] === 255;
                }""")
                await page.locator('.widget-host button').filter(has_text='Step').click()
                await page.get_by_text('Count: 5', exact=True).wait_for()
                await page.get_by_text('clicked 5', exact=False).wait_for()
                await call('manage_kernel', notebook_path='widgets.ipynb', action='restart')
                await page.get_by_text('This widget is no longer in the kernel. Run its cell again.', exact=False).wait_for(timeout=25000)
                assert not errors, errors
                assert not await page.evaluate('window.errors'), await page.evaluate('window.errors')
                await browser.close()
                print('PASS: packaged UI, button, slider, Canvas pixels/input, Output, background update, reopen, multiple viewers, replay overflow recovery, restart', flush=True)
        finally:
            await runner.cleanup()
            await notebook.cleanup()


if __name__ == '__main__':
    asyncio.run(main(Path(sys.argv[1]).resolve()))
