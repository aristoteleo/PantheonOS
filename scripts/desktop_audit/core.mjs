/** Core apps, errors/recovery, exact targets and shell boundaries over real Desktop services. */
import assert from 'node:assert/strict';
import path from 'node:path';
import { existsSync } from 'node:fs';
import { pathToFileURL, fileURLToPath } from 'node:url';
import fs from 'node:fs/promises';
const runtime=path.resolve(process.env.RUNTIME_REPO||fileURLToPath(new URL('../../',import.meta.url)));
const ui=path.resolve(process.env.UI_REPO||path.join(path.dirname(runtime),'pantheon-ui-apps'));
const api=process.env.AUDIT_API||'http://127.0.0.1:48180';
const origin=new URL(process.env.UI_URL||'http://localhost:5173').origin;
const meta=await fetch(api+'/meta').then(r=>r.json());
assert.equal(meta.audit,true,'Use the isolated scripts/desktop_audit/server.py');
assert.equal(meta.workspace,path.join(meta.root,'workspace'));
const root=path.resolve(process.env.AUDIT_OUTPUT||path.join(meta.root,'core-results'));
await fs.mkdir(path.join(root,'evidence'),{recursive:true});
const {chromium}=await import(pathToFileURL(path.join(ui,'node_modules/playwright/index.mjs')).href);
const macChrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const executablePath=process.env.CHROME_EXECUTABLE||(existsSync(macChrome)?macChrome:undefined);
const fixtureURL=origin+'/__all-apps-audit?api='+encodeURIComponent(api);
const browser=await chromium.launch({headless:true,executablePath,args:['--no-first-run','--disable-features=LocalNetworkAccessChecks','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1800,height:1100}});const errors=[];
page.on('pageerror',e=>{errors.push(String(e));console.log('PAGEERR',String(e).slice(0,200))});
page.on('console',m=>{if(m.type()==='error')console.log('ERROR',m.text().slice(0,200))});
await page.route('**/@vite/client',r=>r.fulfill({contentType:'text/javascript',body:'export function createHotContext(){return {data:{},accept(){},dispose(){},on(){},invalidate(){},prune(){},send(){},acceptExports(){}}} export function injectQuery(url){return url} export function updateStyle(id,css){let el=document.querySelector(`style[data-audit-id="${CSS.escape(id)}"]`);if(!el){el=document.createElement("style");el.dataset.auditId=id;document.head.append(el)}el.textContent=css} export function removeStyle(){}'}));
await page.route(fixtureURL,r=>r.fulfill({contentType:'text/html',body:'<!doctype html><div id="fixture"></div><script>window.__BUILD_ID__="audit"</script><script type="module" src="/scripts/desktop/fixtures/all-apps-live.ts"></script>'}));
await page.goto(fixtureURL);
await page.waitForFunction(()=>window.appAudit?.ready,{timeout:180000});
console.log('READY: isolated real Desktop',meta.tag);
const rpc=(method,args={})=>page.evaluate(({method,args})=>window.appAudit.rpc(method,args),{method,args});
await fs.writeFile(`${root}/apps.json`,JSON.stringify(await rpc('desktop_apps'),null,2));
const results=[];
const note=(app,test,ok,detail)=>{results.push({app,test,ok,detail});console.log(ok?'PASS':'FAIL',app,test,JSON.stringify(detail??'').slice(0,300));return ok};
const call=(wid,action,args={})=>rpc('desktop_call',{window_id:wid,action,args});
const read=async wid=>(await rpc('desktop_read',{window_id:wid})).result?.state;
const open=async(app,args={})=>{const r=await rpc('desktop_open',{app,...args});note(app,'open',r.success&&r.result?.status==='ready',r);return r.result?.window_id};
const snapshot=async(app,wid,suffix='after')=>{const r=await rpc('desktop_screenshot',{window_id:wid});if(r.path)await fs.copyFile(r.path,`${root}/evidence/${app}-${suffix}-tool.png`);await page.locator(`[data-window-id="${wid}"]`).screenshot({path:`${root}/evidence/${app}-${suffix}-visible.png`});note(app,'screenshot',r.success,{path:r.path,error:r.error});return r};
const until=async(fn,timeout=15000)=>{const end=Date.now()+timeout;while(Date.now()<end){const v=await fn();if(v)return v;await new Promise(r=>setTimeout(r,150))}return false};
const frame=async wid=>await page.locator(`[data-window-id="${wid}"] iframe`).first().contentFrame();
const work=meta.workspace;
const close=async wid=>{if(wid)await call(wid,'$close')};
for(const w of (await rpc('desktop_windows')).result?.windows??[])await close(w.window_id);
try{
 let wid=await open('files',{path:work});
 note('files','select', (await call(wid,'select',{name:'audit.txt'})).success && (await read(wid)).selected.includes('audit.txt'));
 const bad=await call(wid,'navigate',{path:`${work}/missing-directory`});note('files','invalid navigation rejected',bad.success===false,bad);
 note('files','recover same window',(await call(wid,'navigate',{path:work})).success && (await read(wid)).entries.includes('audit.pdf'));
 await snapshot('files',wid);await close(wid);
 wid=await open('terminal');await until(async()=> (await read(wid))?.connected);
 const session=(await read(wid)).session;const mark='AGENT_AUDIT_'+Date.now();await call(wid,'run',{command:`printf '${mark}\\n'`});
 note('terminal','same PTY output',!!await until(async()=>{const s=await read(wid);return s?.session===session && s.output.split(mark).length>=3}));
 await call(wid,'run',{command:'sleep 30'});await new Promise(r=>setTimeout(r,300));await call(wid,'interrupt');await call(wid,'run',{command:'echo INTERRUPT_CONFIRMED'});
 note('terminal','interrupt and subsequent command',!!await until(async()=> (await read(wid))?.output.split('INTERRUPT_CONFIRMED').length>=3));
 await snapshot('terminal',wid);await close(wid);
 wid=await open('image-viewer',{path:`${work}/audit.png`});note('image-viewer','actual image loaded',(await read(wid))?.width===640);
 note('image-viewer','zoomed',(await rpc('desktop_update',{window_id:wid,patch:{zoomed:true}})).success && await page.locator(`[data-window-id="${wid}"] iframe`).contentFrame().locator('.canvas.zoomed').count()===1);
 await snapshot('image-viewer',wid);const imageBad=await rpc('desktop_update',{window_id:wid,patch:{url:`${api}/missing.png`}});note('image-viewer','broken image rejected',imageBad.success===false,imageBad);await close(wid);
 wid=await open('text-viewer',{path:`${work}/audit.txt`});let old=(await call(wid,'getText')).result?.result;
 console.log('TEXT_BEFORE',JSON.stringify(old));
 const txt='Agent changed the actual editor\nVerified in saved file\n';const edited=await call(wid,'replaceText',{text:txt});note('text-viewer','editor change',edited.success && (await call(wid,'getText')).result?.result?.text===txt,edited);
 const saved=await call(wid,'save');note('text-viewer','actual saved bytes',saved.success && await fs.readFile(`${work}/audit.txt`,'utf8')===txt,saved);
 note('text-viewer','stale edit rejected',(await call(wid,'replaceText',{text:'WRONG',expectedText:'stale'})).success===false);await snapshot('text-viewer',wid);await close(wid);
 wid=await open('pdf-viewer',{path:`${work}/audit.pdf`});note('pdf-viewer','two pages loaded',(await read(wid))?.pageCount===2);
 note('pdf-viewer','navigate actual page',(await call(wid,'setPage',{page:2})).success && (await read(wid)).page===2);
 const text=await call(wid,'getText');note('pdf-viewer','extract visible page text',text.success&&JSON.stringify(text).includes('Agent PDF audit page 2'),text);
 note('pdf-viewer','invalid page rejected',(await call(wid,'setPage',{page:9})).success===false);await snapshot('pdf-viewer',wid);await close(wid);
} catch(e){note('harness','exception',false,String(e))}

try {
 const broken=await rpc('desktop_open',{app:'files',path:work+'/audit.txt'});
 note('files','initial non-directory rejected with recoverable window',broken.success===false&&!!broken.result?.window_id,broken);
 await call(broken.result?.window_id,'navigate',{path:work});
 const repaired=await rpc('desktop_read',{window_id:broken.result?.window_id});note('files','failed open repaired in same window',repaired.success && repaired.result?.status==='ready',repaired);await close(broken.result?.window_id);
 const wid=await open('files',{path:work});
 note('files','ready includes actual directory listing',(await read(wid))?.entries?.includes('audit.pdf'));
 const opened=await call(wid,'open',{name:'audit.pdf'});const target=opened.result?.result?.window_id;
 note('files','open waits for actual target ready',opened.success&&(await read(target))?.pageCount===2,opened);await close(target);
 for(const name of ['delete-A','delete-B']){await fs.mkdir(work+'/'+name,{recursive:true});await fs.writeFile(work+'/'+name+'/same.txt',name)}
 await call(wid,'navigate',{path:work+'/delete-A'});
 let pending=call(wid,'delete',{name:'same.txt'});await page.getByRole('button',{name:'Delete',exact:true}).waitFor();
 await call(wid,'navigate',{path:work+'/delete-B'});await page.getByRole('button',{name:'Delete',exact:true}).click();
 const deleted=await pending;
 note('files','pending deletion retains original directory',deleted.success && deleted.result?.result?.from===work+'/delete-A' && await fs.readFile(work+'/delete-B/same.txt','utf8')==='delete-B' && !(await fs.stat(work+'/delete-A/same.txt').catch(()=>null)),deleted);
 pending=call(wid,'delete',{name:'same.txt'});await page.getByRole('button',{name:'Cancel',exact:true}).click();const cancelled=await pending;
 note('files','cancel does not claim deletion',cancelled.success===false&&!!(await fs.stat(work+'/delete-B/same.txt')),cancelled);
 pending=call(wid,'delete',{name:'same.txt'});await page.getByRole('button',{name:'Delete',exact:true}).waitFor();await fs.unlink(work+'/delete-B/same.txt');await page.getByRole('button',{name:'Delete',exact:true}).click();const failed=await pending;
 note('files','actual delete failure propagated',failed.success===false,failed);await close(wid);
 for(const [id,file,selector] of [['image-viewer','audit.png','.canvas'],['text-viewer','audit.txt','.editor']]){
  const w=await open(id,{path:work+'/'+file});const state=await read(w);
  const bad=await rpc('desktop_set',{window_id:w,state:{...state,url:`${api}/missing-file`}});
  note(id,'bad file rejected',bad.success===false,bad);
  const restored=await rpc('desktop_set',{window_id:w,state});
  note(id,'original file visibly restored after failure',restored.success&&await page.locator(`[data-window-id="${w}"] iframe`).contentFrame().locator(selector).isVisible(),restored);
  await rpc('desktop_set',{window_id:w,state:{}});await rpc('desktop_set',{window_id:w,state});
  note(id,'original file visibly restored after empty state',await page.locator(`[data-window-id="${w}"] iframe`).contentFrame().locator(selector).isVisible());await close(w);
 }
} catch(e){note('harness','exception',false,String(e))}

try{
 const backend=async(method,args)=>page.evaluate(({method,args})=>window.appAudit.rpc(method,args,'integrated_notebook'),{method,args});
 const path=work+'/audit-notebook.ipynb';
 const created=await backend('create_notebook',{notebook_path:path});note('notebook','create',created.success,created);
 const wid=await open('integrated-notebook',{path});
 const add=await call(wid,'add_cell',{cell_type:'code',content:'print("AGENT_NOTEBOOK_OUTPUT", 6 * 7)',execute:true});note('notebook','execute',add.success&&JSON.stringify(add).includes('42'),add);
 const state=await read(wid);note('notebook','state',state?.cellCount>=1,state);
 const f=page.locator(`[data-window-id="${wid}"] iframe`).contentFrame();
 note('notebook','visible output',!!await until(async()=> (await f.locator('body').innerText()).includes('AGENT_NOTEBOOK_OUTPUT 42'),30000));
 await snapshot('notebook',wid);
 const cells=await call(wid,'read_cells',{include_details:true});note('notebook','read output',cells.success&&JSON.stringify(cells).includes('42'),cells);
 await close(wid);
} catch(e){note('harness','exception',false,String(e))}

try {
 const module=`export function setup(app, root) {
  root.innerHTML = '<div style="background:oklch(60% .15 160);padding:40px"><h1>Agent control fixture</h1><output></output></div>';
  app.onState(s=>{root.querySelector('output').textContent=String(s.count??0)});
  app.defineAction('increment',({amount=1}={})=>{if(typeof amount!=='number')throw new Error('amount must be numeric');app.setState({count:(app.state.count??0)+amount});return {count:app.state.count}});
 }`;
 const a=await rpc('desktop_open',{module,state:{count:3},title:'Audit custom A'}), b=await rpc('desktop_open',{module,state:{count:7},title:'Audit custom B'});
 note('agent-view','open exact two custom windows',a.success&&b.success,{a,b});
 const wa=a.result?.window_id,wb=b.result?.window_id;
 const change=await call(wa,'increment',{amount:2});
 note('agent-view','action and visible state',change.success&&(await read(wa)).count===5&&await page.locator(`[data-window-id="${wa}"] iframe`).contentFrame().locator('output').innerText()==='5',change);
 note('agent-view','unrelated custom window unchanged',(await read(wb)).count===7);
 const metadata=(await rpc('desktop_windows')).result?.windows?.find(w=>w.window_id===wa);
 note('agent-view','truthful control discovery',metadata?.controllable===true,metadata);
 note('agent-view','bad action rejected',(await call(wa,'increment',{amount:'bad'})).success===false);
 await snapshot('agent-view',wa);await close(wa);await close(wb);
 for(const app of ['agent','settings','store','interfaces']){
  const w=await open(app);const state=await rpc('desktop_read',{window_id:w});
  const meta=(await rpc('desktop_windows')).result?.windows?.find(x=>x.window_id===w);
  note(app,'shell-only content control explicitly unsupported',state.success===false&&meta?.controllable===false,{state,meta});
  const closed=await call(w,'$close');note(app,'generic window close',closed.success&&!!await until(async()=>!(await rpc('desktop_windows')).result?.windows?.some(x=>x.window_id===w)),closed);
 }
} catch(e){note('harness','exception',false,String(e))}
await fs.writeFile(`${root}/results.json`,JSON.stringify({kind:'real NATS/fleet + actual UI',results,errors},null,2));
await browser.close();
const failures=results.filter(r=>!r.ok);
console.log(`${results.length-failures.length}/${results.length} passed; ${root}/results.json`);
if(failures.length||errors.length)process.exitCode=1;
