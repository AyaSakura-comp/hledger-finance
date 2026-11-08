import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile} from 'node:fs/promises';
import {join} from 'node:path';import {tmpdir} from 'node:os';
import {createChannelFinanceExtension} from './channel.ts';
import {fileStore} from './pending-store.ts';
import {pythonBackend,hledgerAccounts} from './core.ts';import {pythonBridge} from './workflows.ts';

test('real isolated PiWeb journal writes and queries; Discord cannot use the same backend or confirmation',async()=>{
 const root=await mkdtemp(join(tmpdir(),'finance-channel-real-'));const journal=join(root,'book/main.journal');const backend=pythonBackend({journal});await backend(['init']);
 const bridge=pythonBridge(journal);let backendCalls=0;
 function make(host:string){
  const hooks:Record<string,any[]>={},tools:Record<string,any>={},messages:any[]=[];let active=['read','bash'];
  const pi:any={on:(n:string,f:any)=>(hooks[n]??=[]).push(f),registerTool:(t:any)=>tools[t.name]=t,getActiveTools:()=>active,setActiveTools:(a:string[])=>active=a,appendEntry:()=>{},sendMessage:(m:any)=>messages.push(m)};
  const ctx:any={cwd:root,isIdle:()=>true,sessionManager:{getSessionId:()=> 'shared-session'},model:{input:['text']}};
  createChannelFinanceExtension({host,journal,stateDir:join(root,'state'),today:()=> '2026-10-09',backend:async(a,p)=>{backendCalls++;return backend(a,p);},accounts:hledgerAccounts(journal),bridge:async(r,o)=>{backendCalls++;return bridge(r,o);}})(pi);
  const fire=async(n:string,e:any)=>{for(const f of hooks[n]??[]){const r=await f(e,ctx);if(r?.action==='handled')return r;}};
  return {tools,ctx,fire,messages};
 }
 const web=make('piweb'),discord=make('piscord');await web.fire('session_start',{});await discord.fire('session_start',{});
 await web.fire('input',{text:'【記帳】午餐120元',source:'rpc'});
 const receipt=await web.tools.finance_finish.execute('1',{category:'expenses:food:dining'},undefined,undefined,web.ctx);
 assert.match(receipt.content[0].text,/已記帳 1 筆/);await web.fire('agent_settled',{});
 const before=await readFile(journal,'utf8');assert.match(before,/120 TWD/);assert.match(before,/expenses:food:dining/);assert.equal((before.match(/^2026-10-09 /gm)??[]).length,1);
 await web.fire('input',{text:'【記帳】我的現金還剩多少',source:'rpc'});
 const query=await web.tools.finance_action.execute('2',{operation:'query',data:JSON.stringify({report:'balance',account:['assets:cash']})},undefined,undefined,web.ctx);
 assert.match(query.content[0].text,/120/);await web.fire('agent_settled',{});
 await web.fire('input',{text:'【記帳】刪除午餐那筆',source:'rpc'});
 const preview=await web.tools.finance_action.execute('3',{operation:'delete',data:JSON.stringify({description:['午餐']})},undefined,undefined,web.ctx);
 const token=fileStore(journal,join(root,'state')).latest('piweb-shared-session');assert.ok(token,preview.content[0].text);
 const calls=backendCalls;
 await discord.fire('input',{text:`[Web user: web]\n確認 ${token}`,source:'rpc'});
 await discord.tools.finance_action.execute('4',{operation:'delete',data:'{}'},undefined,undefined,discord.ctx);
 assert.equal(backendCalls,calls);assert.equal(await readFile(journal,'utf8'),before);
 await web.fire('input',{text:`確認 ${token}`,source:'rpc'});assert.equal((await readFile(journal,'utf8')).match(/^2026-10-09 /gm)?.length??0,0);
});
