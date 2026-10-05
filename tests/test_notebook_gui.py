"""Real packaged Notebook + opaque-origin App SDK + independent kernel process.

Set PANTHEON_TEST_NOTEBOOK_UI to the paired UI checkout (public/app-host.html),
and PANTHEON_TEST_NOTEBOOK_FRONTEND to its freshly built notebook frontend.
The small parent replaces placement only: production SDK and viewer bytes run
unchanged, and RPCs reach the separately authenticated ordinary App process.
This is not a complete Atrium/Fleet placement acceptance test.
"""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import threading
from urllib.parse import urlsplit

import pytest
from notebook_app_fixture import notebook_rpc
from notebook_widgets_smoke import CODE


PARENT = '''<!doctype html><meta charset="utf-8"><title>Notebook App acceptance</title>
<style>html,body,iframe{margin:0;width:100%;height:100%;border:0}</style>
<iframe id="viewer" sandbox="allow-scripts allow-forms allow-downloads" src="/app-host.html?theme=light"></iframe>
<script>
const viewer=document.querySelector('iframe'); window.errors=[]; window.calls=[];
let initialized=false;
window.addEventListener('message',async event=>{
 if(event.source!==viewer.contentWindow || event.data?.__lv!==1) return;
 const m=event.data, post=value=>event.source.postMessage({__lv:1,...value},'*');
 if(m.t==='hello') post({t:'boot',frontendUrl:location.origin+'/frontend/control.js'});
 if(m.t==='ready' && !initialized){initialized=true;post({t:'init',state:{path:'widgets.ipynb'}});}
 if(m.t==='diag' && m.level==='error' || m.t==='error') window.errors.push(m.message);
 if(m.t==='call'){
  window.calls.push(m.method);
  try{
   const response=await fetch('/rpc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({method:m.method,args:m.args})});
   const result=await response.json();
   post({t:'call_result',id:m.id,...result});
  }catch(e){post({t:'call_result',id:m.id,ok:false,error:String(e)});}
 }
});
window.callAction=(name,args)=>new Promise((resolve,reject)=>{
 const id=crypto.randomUUID(), timer=setTimeout(()=>{window.removeEventListener('message',receive);reject(new Error('Action timeout'));},30000);
 function receive(event){if(event.source!==viewer.contentWindow || event.data?.__lv!==1 || event.data.t!=='result' || event.data.id!==id)return;
  clearTimeout(timer);window.removeEventListener('message',receive);
  event.data.ok?resolve(event.data.value):reject(new Error(event.data.error));}
 window.addEventListener('message',receive); viewer.contentWindow.postMessage({__lv:1,t:'action',id,name,args},'*');
});
</script>'''


def test_managed_notebook_rendered_widgets_and_window_actions(notebook_rpc, tmp_path):
    ui = os.environ.get('PANTHEON_TEST_NOTEBOOK_UI')
    if not ui or not os.environ.get('PANTHEON_TEST_NOTEBOOK_FRONTEND'):
        pytest.skip('Supply paired UI and freshly built Notebook frontend for rendered acceptance')
    from playwright.sync_api import sync_playwright, expect
    rpc = notebook_rpc
    root = tmp_path / 'web'; root.mkdir()
    (root / 'index.html').write_text(PARENT)
    for name in ('app-host.html', 'snapshot-colors.js'):
        shutil.copyfile(Path(ui) / 'public' / name, root / name)
    shutil.copytree(rpc.package / 'frontend', root / 'frontend')
    assert rpc('create_notebook', notebook_path='widgets.ipynb')['success']
    created = rpc('add_cell', notebook_path='widgets.ipynb', content=CODE, execute=True)
    assert created['execution']['success'], created
    cell_id = created['cell_id']
    errors, requests = [], []
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(root), **kwargs)
        def log_message(self, *_): pass
        def end_headers(self):
            # Opaque App iframe modules and fonts are fetched from this explicit
            # asset origin. No backend token is ever exposed to either document.
            self.send_header('Access-Control-Allow-Origin', '*')
            super().end_headers()
        def do_POST(self):
            assert self.path == '/rpc'
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body['method'])
            try:
                result = {'ok': True, 'result': rpc(body['method'], **body.get('args', {}))}
            except Exception as e:
                result = {'ok': False, 'error': str(e)}
            raw = json.dumps(result).encode()
            self.send_response(200); self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    unexpected=[]
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                context=browser.new_context(viewport={'width':1200,'height':1000})
                def route(req):
                    if urlsplit(req.request.url).netloc != urlsplit(origin).netloc:
                        unexpected.append(req.request.url);req.abort()
                    else: req.continue_()
                context.route('**/*',route)
                page=context.new_page();page.on('pageerror',lambda e: errors.append(str(e)))
                page.goto(origin)
                frame=page.frame_locator('#viewer')
                button=frame.locator('.widget-host button').filter(has_text='Step')
                expect(button).to_be_visible(timeout=45000)
                # The real SDK must work with opaque storage, not just a mocked app.call.
                assert page.locator('#viewer').evaluate("e => !e.sandbox.contains('allow-same-origin')")
                button.click();expect(frame.get_by_text('Count: 1',exact=True)).to_be_visible()
                expect(frame.get_by_text('clicked 1',exact=True)).to_be_visible()
                slider=frame.locator('.widget-host [role=slider]');slider.focus();slider.press('ArrowRight')
                expect(slider).to_have_attribute('aria-valuenow','4.0')
                # Calls accepted by the ordinary backend also see the widget's state.
                check=rpc('add_cell',notebook_path='widgets.ipynb',content='assert slider.value == 4',execute=True)
                assert check['execution']['success'],check
                canvas=frame.locator('.widget-host canvas').first
                expect(canvas).to_be_visible()
                assert canvas.evaluate("e=>[...e.getContext('2d').getImageData(10,10,1,1).data]")==[255,0,0,255]
                canvas.click(position={'x':20,'y':20})
                expect(frame.get_by_text('Count: 2',exact=True)).to_be_visible()
                update=rpc('add_cell',notebook_path='widgets.ipynb',content="import threading; threading.Timer(.2, lambda: setattr(label, 'value', 'Background update')).start()",execute=True)
                assert update['execution']['success']
                expect(frame.get_by_text('Background update',exact=True)).to_be_visible()
                # The ordinary window action must mutate and acknowledge the visible document.
                action=page.evaluate("() => callAction('add_cell',{content:'print(\"WINDOW_ACTION_OK\")',execute:true})")
                assert action['applied'] and action['visible'] and action['success'],action
                expect(frame.get_by_text('WINDOW_ACTION_OK',exact=True).last).to_be_visible()
                page.reload();expect(frame.get_by_text('Background update',exact=True)).to_be_visible(timeout=30000)
                button.click();expect(frame.get_by_text('Count: 3',exact=True)).to_be_visible()
                assert not page.evaluate('window.errors'),page.evaluate('window.errors')
                second=context.new_page();second.on('pageerror',lambda e: errors.append(str(e)))
                second.goto(origin);other=second.frame_locator('#viewer')
                expect(other.get_by_text('Count: 3',exact=True)).to_be_visible(timeout=30000)
                other.locator('.widget-host button').filter(has_text='Step').click()
                expect(frame.get_by_text('Count: 4',exact=True)).to_be_visible();second.close()
                # Leave enough updates to exceed bounded replay, then restore current models.
                page.goto('about:blank')
                assert rpc('add_cell',notebook_path='widgets.ipynb',content="for i in range(600): label.value=f'Updated: {i}'",execute=True)['execution']['success']
                page.goto(origin);expect(frame.get_by_text('Updated: 599',exact=True)).to_be_visible(timeout=30000)
                button.click();expect(frame.get_by_text('Count: 5',exact=True)).to_be_visible()
                screenshot=Path(os.environ.get('PANTHEON_TEST_NOTEBOOK_SCREENSHOT',str(tmp_path/'widgets.png')))
                page.screenshot(path=str(screenshot))
                assert rpc('manage_kernel',notebook_path='widgets.ipynb',action='restart')['success']
                expect(frame.get_by_text('This widget is no longer in the kernel.',exact=False)).to_be_visible(timeout=30000)
                # Rerun through the window, exercising kernel adoption + visible output refresh.
                rerun=page.evaluate('id=>callAction("execute_cell",{cell_id:id})',cell_id)
                assert rerun['success'] and rerun['visible'],rerun
                expect(frame.get_by_text('Count: 0',exact=True)).to_be_visible(timeout=30000)
                button.click();expect(frame.get_by_text('Count: 1',exact=True)).to_be_visible()
                assert not errors,errors
                assert not page.evaluate('window.errors'),page.evaluate('window.errors')
                assert not unexpected,unexpected
                assert {'widget_channel','read_notebook','add_cell','execute_cell'} <= set(requests)
            finally: browser.close()
    finally:
        server.shutdown();server.server_close();thread.join(5)
