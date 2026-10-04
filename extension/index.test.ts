import { test } from 'node:test';
import { mkdtempSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import assert from 'node:assert/strict';
import { createFinanceExtension } from './index.ts';
process.env.PI_FINANCE_STATE_DIR=mkdtempSync(join(tmpdir(),'fin-state-')); // never touch the real pending store
function harness(backend:any){
 const hooks:any={};const tools:any={};const messages:any[]=[];let active=['read','bash'];
 const pi:any={on:(name:string,fn:any)=>hooks[name]=fn,registerTool:(tool:any)=>tools[tool.name]=tool,getActiveTools:()=>active,setActiveTools:(a:string[])=>active=a,appendEntry:()=>{},sendMessage:(msg:any,options:any)=>messages.push({...msg,options})};
 const ctx:any={isIdle:()=>true,sessionManager:{getSessionId:()=> 'test',getBranch:()=>[]}};
 createFinanceExtension({backend,today:()=> '2026-10-03'})(pi);
 return {hooks,tools,messages,ctx,active:()=>active};
}
const evidence={binding:false,suggestion:{account:'expenses:food:dining'},categories:[{account:'expenses:food:dining'}]};
test('route locks tools, blocks escape, terminates once and restores',async()=>{
 let writes=0; const h=harness(async(args:string[])=>{if(args[0]==='classify')return JSON.stringify(evidence);writes++;return 'Records ready: 1; duplicates skipped: 0';});
 assert.equal(await h.hooks.input({text:'你好',source:'interactive'},h.ctx),undefined);
 await h.hooks.input({text:'【記帳】1500 午餐',source:'interactive'},h.ctx);
 assert.deepEqual(h.active(),['finance_finish']);
 assert.equal(h.hooks.tool_call({toolName:'bash'},h.ctx).block,true);
 const response=await h.tools.finance_finish.execute('call',{category:'expenses:food:dining'},undefined,undefined,h.ctx);
 assert.equal(response.terminate,true);assert.equal(writes,1);assert.ok(h.messages[0].content.includes('1500'));assert.equal(h.messages[0].options?.triggerTurn,false);
 const duplicate=await h.tools.finance_finish.execute('call2',{category:'expenses:food:dining'},undefined,undefined,h.ctx);
 assert.equal(duplicate.terminate,true);assert.equal(writes,1);
 h.hooks.agent_settled({},h.ctx);assert.deepEqual(h.active(),['read','bash']);
});
test('aborted tool never writes; plain-text model reply gets a no-write notice',async()=>{
 let writes=0;const h=harness(async(args:string[])=>args[0]==='classify'?JSON.stringify(evidence):(writes++,'Records ready: 1; duplicates skipped: 0'));
 await h.hooks.input({text:'午餐150元',source:'interactive'},h.ctx);
 const controller=new AbortController();controller.abort();
 await h.tools.finance_finish.execute('call',{category:'expenses:food:dining'},controller.signal,undefined,h.ctx);
 assert.equal(writes,0);
 h.hooks.agent_settled({},h.ctx);assert.ok(h.messages.some(m=>m.customType==='finance-fast-nudge'&&m.options?.triggerTurn!==false)); // one automatic retry
 h.hooks.agent_settled({},h.ctx);assert.ok(h.messages.some(m=>m.content.includes('未寫入')));
});
test('prepare errors are handled without falling back to unguarded model',async()=>{
 const h=harness(async()=>{throw new Error('backend missing');});
 const result=await h.hooks.input({text:'午餐1500元',source:'interactive'},h.ctx);
 assert.equal(result.action,'handled');assert.deepEqual(h.active(),['read','bash']);assert.ok(h.messages[0].content.includes('backend missing'));
});
test('concurrent inputs cannot replace a preparing expense',async()=>{
 let release:any;let calls=0;
 const h=harness(async()=>{calls++;return new Promise<string>(ok=>release=ok);});
 const first=h.hooks.input({text:'午餐150元',source:'interactive'},h.ctx);
 const second=await h.hooks.input({text:'咖啡95元',source:'interactive'},h.ctx);
 assert.equal(second.action,'handled');assert.equal(calls,1);
 release(JSON.stringify(evidence));await first;
});
test('busy input does not mutate or hijack another agent run',async()=>{
 let calls=0;const h=harness(async()=>{calls++;return JSON.stringify(evidence)});h.ctx.isIdle=()=>false;
 await h.hooks.input({text:'午餐1500元',source:'interactive',streamingBehavior:'steer'},h.ctx);
 assert.equal(calls,0);assert.deepEqual(h.active(),['read','bash']);
});
test('an amount with unconverted Zhuyin gets a retype notice and no write',async()=>{
 let writes=0;const h=harness(async(args:string[])=>args[0]==='classify'?JSON.stringify(evidence):(writes++,'Records ready: 1; duplicates skipped: 0'));
 const r=await h.hooks.input({text:'[Web user: web]\n【記帳】300 ㄒㄧ ㄔㄜˋ',source:'interactive'},h.ctx);
 assert.equal(r?.action,'handled');assert.equal(writes,0);assert.ok(h.messages.some(m=>m.content.includes('注音')));
});
