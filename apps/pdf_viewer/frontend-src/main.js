/** A rendered PDF surface on the generic Desktop bridge. */
import { getDocument, GlobalWorkerOptions } from 'pdfjs-dist'
GlobalWorkerOptions.workerSrc = new URL('./pdf.worker.min.mjs', import.meta.url).href
export function setup(app, root) {
  root.innerHTML = `<style>
    .pdf {height:100vh;display:flex;flex-direction:column;font:13px system-ui;color:var(--app-fg,#ccc);background:var(--app-bg,#222)}
    .pdf header {display:flex;align-items:center;gap:10px;padding:8px;flex:none}
    .pdf button {padding:4px 10px;cursor:pointer} .pdf .pages {flex:1;min-height:0;overflow:auto;text-align:center;padding:12px;background:#525659}
    .pdf canvas {display:block;margin:0 auto;box-shadow:0 2px 8px #222;max-width:none} .pdf .error {color:#ffb3af}
  </style><div class="pdf"><header><button data-action="previous">Previous</button><span class="page"></span><button data-action="next">Next</button><button data-action="zoom-out">−</button><span class="zoom"></span><button data-action="zoom-in">+</button><span class="status"></span></header><div class="pages"><canvas hidden></canvas></div></div>`
  const canvas=root.querySelector('canvas'), label=root.querySelector('.page'), zoomLabel=root.querySelector('.zoom'), status=root.querySelector('.status')
  let doc=null, url='', page=1, zoom=1, loading=null
  const describe=()=>({loaded:!!doc,page,pageCount:doc?.numPages??0,zoom})
  const publish=()=>app.setState(describe())
  function checkPage(n){if(!Number.isInteger(n)||n<1||!doc||n>doc.numPages)throw new Error(`page must be between 1 and ${doc?.numPages??0}`)}
  function checkZoom(n){if(typeof n!=='number'||!Number.isFinite(n)||n<.25||n>4)throw new Error('zoom must be between 0.25 and 4')}
  async function render(){
    if(!doc)return
    const pdfPage=await doc.getPage(page);const viewport=pdfPage.getViewport({scale:zoom})
    const next=document.createElement('canvas');next.width=Math.ceil(viewport.width);next.height=Math.ceil(viewport.height)
    await pdfPage.render({canvasContext:next.getContext('2d'),viewport}).promise
    canvas.width=next.width;canvas.height=next.height;canvas.getContext('2d').drawImage(next,0,0);canvas.hidden=false
    label.textContent=`Page ${page} / ${doc.numPages}`;zoomLabel.textContent=`${Math.round(zoom*100)}%`;status.textContent=''
    publish()
  }
  let queue=Promise.resolve()
  const serial=(fn)=>{const result=queue.then(fn);queue=result.catch(()=>{});return result}
  async function apply(s){
    if(!s?.url){status.textContent='No PDF open';return}
    status.classList.remove('error')
    try{
      if(s.url!==url){
        status.textContent='Loading PDF…'
        loading=getDocument({url:s.url,isEvalSupported:false})
        const next=await loading.promise
        if (s.page !== undefined && (!Number.isInteger(s.page) || s.page < 1 || s.page > next.numPages)) { await next.destroy(); throw new Error(`page must be between 1 and ${next.numPages}`) }
        if (s.zoom !== undefined) { try { checkZoom(s.zoom) } catch (e) { await next.destroy(); throw e } }
        await doc?.destroy();doc=next;url=s.url;page=1
        if(s.name)app.window.setTitle(s.name)
      }
      if(s.page!==undefined)checkPage(s.page)
      if(s.zoom!==undefined)checkZoom(s.zoom)
      if(s.page!==undefined)page=s.page
      if(s.zoom!==undefined)zoom=s.zoom
      await render()
    }catch(e){status.textContent=e.message;status.classList.add('error');throw e}
  }
  app.onState((s,info)=>info?.reason==='emit'?undefined:serial(()=>apply(s)))
  const setPage=({page:n}={})=>serial(async()=>{checkPage(n);page=n;await render();return describe()})
  const setZoom=({zoom:n}={})=>serial(async()=>{checkZoom(n);zoom=n;await render();return describe()})
  app.defineAction('setPage',setPage);app.defineAction('setZoom',setZoom)
  app.defineAction('getText',({page:n=page}={})=>serial(async()=>{checkPage(n);const p=await doc.getPage(n);const text=await p.getTextContent();return {page:n,pageCount:doc.numPages,text:text.items.map(x=>x.str+(x.hasEOL?'\n':' ')).join('').slice(0,100000)}}))
  app.onSnapshot(()=>{if(!doc||canvas.hidden)throw new Error('No rendered PDF page');return canvas.toDataURL('image/png')})
  root.querySelector('header').addEventListener('click',e=>{
    const action=e.target.closest('button')?.dataset.action
    const op=action==='previous'?setPage({page:page-1}):action==='next'?setPage({page:page+1}):action==='zoom-in'?setZoom({zoom:Math.min(4,zoom+.25)}):action==='zoom-out'?setZoom({zoom:Math.max(.25,zoom-.25)}):null
    op?.catch(e=>{status.textContent=e.message;status.classList.add('error')})
  })
}
