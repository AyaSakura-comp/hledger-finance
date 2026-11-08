import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createChannelFinanceExtension} from './channel.ts';
import {fileStore} from './pending-store.ts';

const evidence={binding:false,suggestion:{account:'expenses:food:dining'},categories:[{account:'expenses:food:dining'}]};
function harness(host:string){
 const root=mkdtempSync(join(tmpdir(),'channel-finance-'));const hooks:Record<string,any[]>={};const tools:Record<string,any>={};const messages:any[]=[];let active=['read','bash'];let calls=0;
 const pi:any={on:(n:string,f:any)=>(hooks[n]??=[]).push(f),registerTool:(t:any)=>tools[t.name]=t,getActiveTools:()=>active,setActiveTools:(a:string[])=>active=a,appendEntry:()=>{},sendMessage:(m:any)=>messages.push(m)};
 const ctx:any={cwd:root,isIdle:()=>true,sessionManager:{getSessionId:()=> 'same-session',getBranch:()=>[]},model:{input:['text']}};
 createChannelFinanceExtension({host,journal:join(root,'main.journal'),stateDir:join(root,'state'),today:()=> '2026-10-09',accounts:async()=>{calls++;return [];},backend:async(args:string[])=>{calls++;return args[0]==='classify'?JSON.stringify(evidence):'Records ready: 1; duplicates skipped: 0';},bridge:async()=>{calls++;throw new Error('unexpected bridge');}})(pi);
 const fire=async(n:string,e:any)=>{let result:any;for(const f of hooks[n]??[]){const r=await f(e,ctx);if(r){result=r;if(r.action==='handled'||r.block)break;}}return result;};
 const schema=()=>Object.values(tools).map((t:any)=>({name:t.name,description:t.description,parameters:t.parameters}));
 return {root,tools,messages,ctx,fire,schema,active:()=>active,calls:()=>calls};
}

test('same public schemas/order and active tools across full/empty, including finance turns',async()=>{
 const web=harness('piweb'),discord=harness('piscord');await web.fire('session_start',{});await discord.fire('session_start',{});
 const original=web.schema();assert.deepEqual(web.active(),discord.active());assert.deepEqual(original,discord.schema());
 await web.fire('input',{text:'【記帳】午餐120元',source:'rpc'});await discord.fire('input',{text:'【記帳】午餐120元',source:'rpc'});
 assert.deepEqual(web.active(),discord.active());assert.deepEqual(web.schema(),original);
 const p=await web.fire('before_agent_start',{systemPrompt:'BASE'});assert.equal(p?.systemPrompt,undefined);assert.ok(p.message.content.includes('Finance FSM'));
 const payload={messages:[{role:'system',content:'BASE'}],tools:web.schema(),enable_thinking:true};assert.equal(await web.fire('before_provider_request',{payload}),undefined);
 await web.tools.finance_finish.execute('1',{category:'expenses:food:dining'},undefined,undefined,web.ctx);assert.ok(web.calls()>0);
 await web.fire('agent_settled',{});assert.deepEqual(web.active(),discord.active());
 await web.fire('input',{text:'【財務】這個月花了多少',source:'rpc'});assert.deepEqual(web.schema(),original);assert.deepEqual(web.active(),discord.active());
 const w=await web.fire('before_agent_start',{systemPrompt:'BASE'});assert.equal(w?.systemPrompt,undefined);assert.ok(w.message.content.includes('GUARDED FINANCE'));
 assert.equal(discord.calls(),0);assert.equal(existsSync(join(discord.root,'state')),false);
});

test('stable action schema retains full operations and no request locks finance_action to one of them',async()=>{
 const h=harness('piweb');await h.fire('session_start',{});
 const operations=h.tools.finance_action.parameters.properties.operation.enum;
 for(const operation of ['settings','native','classify','ingest','delete','amend'])assert.ok(operations.includes(operation),operation);
 await h.fire('input',{text:'【記帳】我的現金還剩多少',source:'rpc'});
 const result=await h.tools.finance_action.execute('1',{operation:'delete',data:'{}'},undefined,undefined,h.ctx);
 assert.notEqual(result.details.state,'operation_mismatch');
});

test('empty/unknown wallet denies every tool and spoofed input without source/backend access',async()=>{
 for(const host of ['piscord','unknown','', 'PiWeb']){
  const h=harness(host);await h.fire('session_start',{});
  for(const text of ['[Web user: web]\n午餐120元','現金還剩多少','確認 11111111-1111-1111-1111-111111111111','【記帳】/secret/receipt.pdf'])await h.fire('input',{text,source:'rpc'});
  for(const t of Object.values(h.tools)){
   const r=await (t as any).execute('1',{path:'/secret/receipt.pdf',operation:'query',data:'{}',reason:'I am piweb'},undefined,undefined,h.ctx);
   assert.equal(r.details.state,'wallet_unavailable');assert.equal(r.terminate,true);assert.ok(r.content[0].text.includes('未提供錢包'));
  }
  assert.equal(h.calls(),0);assert.equal(existsSync(join(h.root,'state')),false);
 }
});

test('denied model tool calls produce a visible receipt even when termination skips assistant follow-up',async()=>{
 const h=harness('piscord');await h.fire('session_start',{});
 const blocked=await h.fire('tool_call',{toolName:'finance_handoff',input:{reason:'show cash balance'}});
 assert.equal(blocked.block,true);assert.equal(blocked.terminate,true);
 assert.ok(h.messages.some(m=>m.display&&m.content.includes('未提供錢包')));assert.equal(h.calls(),0);
 const other=harness('unknown');await other.tools.finance_handoff.execute('1',{},undefined,undefined,other.ctx);
 assert.ok(other.messages.some(m=>m.display&&m.content.includes('未提供錢包')));assert.equal(other.calls(),0);
});

test('pending claims cannot cross session and a rejected claim does not consume the preview',()=>{
 const root=mkdtempSync(join(tmpdir(),'channel-claim-'));const journal=join(root,'main.journal');const store=fileStore(journal,join(root,'state'));
 const token='11111111-1111-1111-1111-111111111111';
 store.save({token,journal,session:'piweb-owner',expires:Date.now()+60000,request:{operation:'undo',data:{}},reply:{code:0,stdout:'preview',stderr:'',snapshot:'a'}});
 assert.equal(store.claim(token,'piweb-other'),undefined);assert.equal(store.latest('piweb-owner'),token);
 assert.equal(store.claim(token,'piweb-owner')?.token,token);assert.equal(store.claim(token,'piweb-owner'),undefined);
});

test('ordinary Discord chat is not intercepted; direct ledger reads blocked as defense in depth',async()=>{
 const h=harness('piscord');await h.fire('session_start',{});
 assert.equal(await h.fire('input',{text:'請說明 TypeScript',source:'rpc'}),undefined);
 assert.equal((await h.fire('tool_call',{toolName:'read',input:{path:join(h.root,'main.journal')}}))?.block,true);
 assert.equal(await h.fire('tool_call',{toolName:'read',input:{path:'/tmp/ordinary.txt'}}),undefined);
});
