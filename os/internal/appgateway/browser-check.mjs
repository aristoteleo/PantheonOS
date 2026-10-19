import assert from 'node:assert/strict'
const { chromium } = await import(process.env.FLEET_TEST_PLAYWRIGHT)
const controller = process.env.FLEET_TEST_URL
const port = new URL(controller).port
const browser = await chromium.launch({headless:true,args:['--no-proxy-server',`--host-resolver-rules=MAP *.apps.test 127.0.0.1:${port},MAP atrium.test 127.0.0.1:${port},MAP unrelated.test 127.0.0.1:${port}`]})
try {
 const context = await browser.newContext({ignoreHTTPSErrors:true})
 const page=await context.newPage()
 await page.goto('https://atrium.test')
 const connections=[]
 for(const generation of [1,2]) {
  const response=await context.request.post(controller+'/apps/connect',{headers:{authorization:'Bearer browser-test-controller-service-secret','content-type':'application/json'},data:JSON.stringify({fleet_id:'alice',node_id:'node',instance_id:'instance',revision:'a'.repeat(64),generation,component:'office',port:'http',credential:'scoped-test-credential-not-a-user-login',expires:Math.floor(Date.now()/1000)+3600,ui_origin:'https://atrium.test'})})
  assert.equal(response.status(),200)
  const connection=await response.json();connections.push(connection)
  await page.evaluate(async ({origin,ticket})=>{
   const response=await fetch(origin+'/__fleet/connect',{method:'POST',credentials:'include',headers:{'content-type':'application/json'},body:JSON.stringify({ticket})})
   if(!response.ok)throw Error('connect '+response.status)
   const status=await fetch(origin+'/__fleet/status',{credentials:'include'})
   if(!status.ok)throw Error('cookie probe '+status.status)
  },connection)
 }
 assert.notEqual(connections[0].origin,connections[1].origin)
 for(const [index,connection] of connections.entries()) {
  await page.addScriptTag({url:connection.origin+'/api/documents/api.js'})
  await page.evaluate(index=>{window['editor'+index]=window.DocsAPI.DocEditor},index)
 }
 const sources=await page.evaluate(origins=>{
  const config={documentType:'word',document:{key:'test',title:'test.docx',fileType:'docx',url:origins[0]+'/test.docx'},editorConfig:{mode:'view'}}
  new window.editor0('first',config)
  new window.editor1('second',config)
  new window.editor0('third',config)
  return [...document.querySelectorAll('iframe')].map(frame=>frame.src)
 },connections.map(c=>c.origin))
 assert.equal(sources.length,3)
 assert.deepEqual(sources.map(src=>new URL(src).origin),[connections[0].origin,connections[1].origin,connections[0].origin])
 for(const frame of page.frames().slice(1)) await frame.getByText('Editor reached the selected Fleet service').waitFor()
 const cdp=await context.newCDPSession(page)
 const {cookies}=await cdp.send('Network.getAllCookies')

 assert.equal(cookies.filter(c=>c.domain.endsWith('.apps.test') && c.name==='__Host-fleetapp' && c.httpOnly && c.secure && c.partitionKey).length,2)
 const evil=await context.newPage();await evil.goto('https://unrelated.test')
 const denied=await evil.evaluate(async origin=>{
  try {const r=await fetch(origin+'/__fleet/status',{credentials:'include'});return r.status} catch{return 0}
 },connections[0].origin)
 assert.ok(denied===0||denied===401||denied===403)
 console.log('PASS: Chromium partitioned authentication, native editor frames, multiple generations, cross-site rejection')
} finally {await browser.close()}
