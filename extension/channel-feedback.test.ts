import {test} from 'node:test';import assert from 'node:assert/strict';import {mkdtempSync} from 'node:fs';import {tmpdir} from 'node:os';import {join} from 'node:path';import {createChannelFinanceExtension} from './channel.ts';
function harness(error?:string,host='piweb',prepareError=false){
 const root=mkdtempSync(join(tmpdir(),'finance-feedback-'));const hooks:Record<string,any[]>={},tools:Record<string,any>={},messages:any[]=[];let active=['read','bash'];let writes=0,aborts=0;
 const pi:any={on:(n:string,f:any)=>(hooks[n]??=[]).push(f),registerTool:(t:any)=>tools[t.name]=t,getActiveTools:()=>active,setActiveTools:(t:string[])=>active=t,appendEntry:()=>{},sendMessage:(m:any)=>messages.push(m)};
 const ctx:any={cwd:root,isIdle:()=>true,abort:()=>aborts++,sessionManager:{getSessionId:()=> 'feedback-session'},model:{input:['text']}};
 createChannelFinanceExtension({host,journal:join(root,'book/main.journal'),stateDir:join(root,'state'),today:()=> '2026-10-09',accounts:async()=>[],backend:async(args)=>{if(args[0]==='classify'){if(prepareError)throw new Error(error);return JSON.stringify({binding:false,suggestion:{account:'expenses:food:groceries'},categories:[{account:'expenses:food:groceries'}]});}writes++;if(error)throw new Error(error);return 'Records ready: 1; duplicates skipped: 0';},bridge:async()=>{throw new Error('backend timeout; outcome uncertain');}})(pi);
 const fire=async(n:string,e:any={})=>{let result:any;for(const f of hooks[n]??[]){const r=await f(e,ctx);if(r){result=r;if(r.block||r.action==='handled')break;}}return result;};
 return {tools,messages,ctx,fire,writes:()=>writes,aborts:()=>aborts};
}
test('Git rejection feeds the model instead of displaying a raw final error; finance_finish cannot replay',async()=>{
 const h=harness('finance.py exit 2: Refusing to ingest while the journal has staged Git changes');await h.fire('session_start');await h.fire('input',{text:'【記帳】1690 食材',source:'rpc'});
 const result=await h.tools.finance_finish.execute('finish-1',{category:'expenses:food:groceries'},undefined,undefined,h.ctx);
 assert.equal(result.terminate,false);assert.equal(result.details.financeFeedback,true);assert.match(result.content[0].text,/EXPLANATION ONLY/);assert.ok(!h.messages.some(m=>m.display&&m.content.includes('finance.py exit')));
 // Tools are not blocked after a result, but the consumed flow cannot write again.
 const direct=await h.tools.finance_finish.execute('finish-2',{category:'expenses:food:groceries'},undefined,undefined,h.ctx);assert.match(direct.content[0].text,/without replay/);assert.equal(h.writes(),1);
 const payload={enable_thinking:true,max_tokens:1234};assert.equal(await h.fire('before_provider_request',{payload}),undefined);
 await h.fire('agent_end',{messages:[{role:'toolResult',toolCallId:'finish-1',content:result.content},{role:'assistant',content:[{type:'text',text:'帳本的 Git 變更尚未處理，這筆沒有確認完成。我沒有清理變更或重送。'}]}]});await h.fire('agent_settled');assert.equal(h.writes(),1);assert.ok(!h.messages.some(m=>m.display));
 await h.fire('input',{text:'【記帳】20 食材',source:'rpc'});assert.equal(await h.fire('tool_call',{toolName:'finance_finish',input:{}}),undefined);
});
test('another tool during the fast path is not blocked: the unwritten prepared entries are dropped',async()=>{
 const h=harness();await h.fire('input',{text:'【記帳】1690 食材',source:'rpc'});
 assert.equal(await h.fire('tool_call',{toolName:'finance_action',input:{operation:'amend'}}),undefined);assert.equal(h.writes(),0);
 const r=await h.tools.finance_finish.execute('late',{category:'expenses:food:groceries'},undefined,undefined,h.ctx);assert.equal(r.terminate,true);assert.equal(h.writes(),0);
});
test('success still displays authoritative receipt, but permits model follow-up without another write',async()=>{
 const h=harness();await h.fire('input',{text:'【記帳】1690 食材',source:'rpc'});const r=await h.tools.finance_finish.execute('ok',{category:'expenses:food:groceries'},undefined,undefined,h.ctx);
 assert.equal(r.terminate,false);assert.match(r.content[0].text,/已記帳 1 筆/);assert.ok(h.messages.some(m=>m.display&&m.content.includes('1690 TWD')));
 await h.tools.finance_action.execute('bad',{operation:'ingest',data:'{}'},undefined,undefined,h.ctx);assert.equal(h.writes(),1);
});
test('missing narrative gets a readable fallback and cannot loop indefinitely',async()=>{
 const h=harness('Unrecognized write receipt; inspect journal before retrying');await h.fire('input',{text:'【記帳】1690 食材',source:'rpc'});const r=await h.tools.finance_finish.execute('uncertain',{category:'expenses:food:groceries'},undefined,undefined,h.ctx);assert.match(r.content[0].text,/unconfirmed/);
 // Text before the tool result must not count as a final explanation.
 await h.fire('agent_end',{messages:[{role:'assistant',content:[{type:'text',text:'正在記帳'}]},{role:'toolResult',toolCallId:'uncertain',content:r.content}]});
 for(let i=0;i<5;i++)await h.fire('turn_start');await h.fire('agent_settled');assert.equal(h.writes(),1);assert.equal(h.aborts(),0);assert.equal(h.messages.filter(m=>m.display).length,1);assert.match(h.messages.find(m=>m.display).content,/未確認成功|不確定/);
});
test('prepare errors also reach an explanation turn, rather than being swallowed by handled input',async()=>{
 const h=harness('classification unavailable','piweb',true);const r=await h.fire('input',{text:'【記帳】1690 食材',source:'rpc'});assert.notEqual(r?.action,'handled');const context=await h.fire('before_agent_start',{systemPrompt:'BASE'});assert.equal(context.systemPrompt,undefined);assert.match(context.message.content,/EXPLANATION ONLY/);assert.equal(h.writes(),0);assert.ok(!h.messages.some(m=>m.display));
});
test('managed backend errors get narration after bounded proposal repair, but never replay a mutation',async()=>{
 const h=harness();await h.fire('input',{text:'【記帳】我的現金還剩多少',source:'rpc'});let r:any;for(let i=0;i<6;i++)r=await h.tools.finance_action.execute('query-'+i,{operation:'query',data:'{"report":"balance"}'},undefined,undefined,h.ctx);
 assert.equal(r.terminate,false);assert.equal(r.details.financeFeedback,true);assert.match(r.content[0].text,/EXPLANATION ONLY/);assert.equal(h.writes(),0);assert.equal(await h.fire('tool_call',{toolName:'finance_release',input:{reason:'debug'}}),undefined);
});
test('Discord denial remains immediate with zero backend/model follow-up',async()=>{
 const h=harness(undefined,'piscord');const r=await h.tools.finance_finish.execute('no',{category:'expenses:food:groceries'},undefined,undefined,h.ctx);assert.equal(r.terminate,true);assert.equal(r.details.state,'wallet_unavailable');assert.equal(h.writes(),0);
});
