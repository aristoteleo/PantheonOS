"""Real Pantheon Agent, real model tool calls, isolated real Desktop services."""
import asyncio,json,os
from pathlib import Path
import aiohttp
ROOT=Path(os.environ['AUDIT_OUTPUT']).resolve()
API=os.environ.get('AUDIT_API','http://127.0.0.1:48180')
MODEL=os.environ['AUDIT_MODEL']
from pantheon.agent import Agent
calls=[]
async def rpc(method,args):
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as session:
  async with session.post(API+'/rpc',json={'method':method,'args':args,'toolset':'desktop'}) as response:
   result=await response.json();calls.append({'method':method,'args':args,'result':result});return result
async def desktop_windows()->dict:
 """List existing desktop windows with window_id, app identity and available actions. Operate the user-specified exact window ID."""
 return await rpc('desktop_windows',{})
async def desktop_read(window_id:str)->dict:
 """Read the specified desktop window's current UI state; Terminal includes its actual visible output, PDF includes current page."""
 return await rpc('desktop_read',{'window_id':window_id})
async def desktop_call(window_id:str,action:str,args:dict={ })->dict:
 """Call a named action on an existing exact window. Terminal: run {command}, input {data}, interrupt, clear. Text Viewer: getText {}, replaceText {text,expectedText?}, save {}. PDF: setPage {page}, getText {page?}, setZoom {zoom}. Discover actions via desktop_windows. Failure must not be reported as success."""
 return await rpc('desktop_call',{'window_id':window_id,'action':action,'args':args})
async def main():
 async with aiohttp.ClientSession() as check:
  async with check.get(API+'/meta') as response:
   meta=await response.json()
   if meta.get('audit') is not True: raise RuntimeError('An isolated audit server is required')
   os.chdir(meta['workspace'])
 for _ in range(120):
  if (ROOT/'llm-windows.json').exists():break
  await asyncio.sleep(1)
 w=json.loads((ROOT/'llm-windows.json').read_text())
 agent=Agent(name='Desktop acceptance agent',model=MODEL,instructions='You are testing genuine desktop control on an isolated test workspace. Use only the supplied public Desktop tools. Do not claim success without reading back results. Never operate the untouched window. Do not create or close windows.',tools=[desktop_windows,desktop_read,desktop_call],use_memory=False,tool_timeout=90)
 prompt=f'''Inspect desktop_windows and control these existing windows: Terminal {w['terminal']}, Text Viewer {w['text']}, PDF {w['pdf']}. In the Terminal run printf 'AGENT_LLM_OK 42\\n' and verify the output in the same terminal. In the Text Viewer replace the document with exactly "Agent operated this exact editor.\\n", save it, and read it back. In the PDF go to page 2 and read the page text. Window {w['untouched']} must remain unchanged. End with the verified IDs and results. If a tool fails, diagnose instead of pretending it worked.'''
 try:
  result=await agent.run(prompt,max_turns=32)
  (ROOT/'llm-agent-result.json').write_text(json.dumps({'response':str(result),'calls':calls},indent=2))
  print('LLM_AGENT_FINISHED',len(calls),flush=True)
 finally:
  (ROOT/'llm-done.json').write_text('{}')
asyncio.run(main())
