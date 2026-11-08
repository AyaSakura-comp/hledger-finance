import type { ExtensionAPI, ExtensionFactory } from '@earendil-works/pi-coding-agent';
import { Type } from 'typebox';
import { homedir } from 'node:os';
import { join, dirname } from 'node:path';
import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { FinanceBatch, parseEntries, hasExplicitFinanceTag, hledgerAccounts, localDate, pythonBackend, requireToolCall, userMessage, isRepeatRequest, type Backend } from './core.ts';
import { registerManaged } from './managed.ts';
import { ADOPTED, type Bridge } from './workflows.ts';

export function createFinanceExtension(options:{backend?:Backend;bridge?:Bridge;journal?:string;today?:()=>string;stateDir?:string;accounts?:()=>Promise<string[]>}={}):ExtensionFactory {
 return (pi:ExtensionAPI)=>{
  let flow:FinanceBatch|undefined;let retries=0;
  let savedTools:string[]|undefined;
  const journal=options.journal??process.env.PI_FINANCE_JOURNAL??join(homedir(),'finance/main.journal');
  const backend=options.backend??pythonBackend({journal});
  const recent:{at:number;rows?:any[]}={at:0};
  const accounts=options.accounts??hledgerAccounts(journal);
  const paymentRules=async()=>{try{return (JSON.parse(await readFile(join(dirname(journal),'payment-account-rules.json'),'utf8')).currency_accounts??{}) as Record<string,string>;}catch{return {};}};
  registerManaged(pi,{journal,bridge:options.bridge,today:options.today,busy:()=>Boolean(flow),stateDir:options.stateDir,recent});
  const restore=()=>{if(savedTools){pi.setActiveTools(savedTools);savedTools=undefined;}flow=undefined;};
  const report=(content:string)=>pi.sendMessage({customType:'finance-receipt',content,display:true},{triggerTurn:false});
  pi.on('input',async(event,ctx)=>{
   if(event.source==='extension'||!ctx.isIdle()||event.streamingBehavior==='steer')return;
   // Input is only handled while idle, so a flow still here belongs to a turn
   // that already ended (aborted/interrupted). Drop it instead of refusing.
   if(flow?.state==='writing'){report('上一筆記帳還在寫入中；本次輸入未寫入，請等待回執後重新提出。');return {action:'handled'};}
   if(flow)restore();
   const today=(options.today??localDate)();
   const text=userMessage(event.text);
   // "多一筆" right after a record: the host repeats exactly those entries
   // (today's date, new import ids) instead of asking the model what was meant.
   if(isRepeatRequest(text)&&recent.rows?.length&&Date.now()-recent.at<15*60000){
    const rows=recent.rows.map((r,i)=>({...r,date:today,import_id:`pi-repeat-${randomUUID()}-${i}`,tags:(r.tags??[]).filter((t:string)=>!t.startsWith('inferred:date'))}));
    try{
     const out=await backend(['ingest-json','-'],{transactions:rows});
     if(!/Records ready: [1-9]/.test(out))throw new Error(out.split('\n').pop()??'not written');
     recent.at=Date.now();recent.rows=rows;
     report(`已再記一筆：\n${rows.map(r=>`・${r.date} ${r.description} ${r.amount} ${r.currency??'TWD'}｜${r.kind==='income'?r.credit:r.debit}`).join('\n')}\n要撤銷請說「撤銷」。`);
    }catch(error){report('沒有再記：'+String(error).replace(/^Error:\s*/,'').slice(0,120));}
    return {action:'handled'};
   }
   // An amount with unconverted Zhuyin would otherwise reach the general model.
   if(!event.images?.length&&/\d/.test(text)&&/[\u3100-\u312F\u31A0-\u31BFˊˇˋ˙]/.test(text)){report('看起來有注音沒有選字（例如「ㄒㄧ ㄔㄜˋ」），沒有記帳。請改成國字後重新輸入，例如「300 洗車」。');return {action:'handled'};}
   if(event.images?.length||!hasExplicitFinanceTag(text)||!parseEntries(text,today))return;
   restore();savedTools=pi.getActiveTools();
   flow=new FinanceBatch(backend,accounts,state=>pi.appendEntry('finance-state',{version:1,state}),paymentRules);retries=0;
   try{
    await flow.prepare(text,today,`pi-${ctx.sessionManager.getSessionId()}-${randomUUID()}`);
    pi.setActiveTools(['finance_finish']);
   }catch(error){report(`記帳準備失敗，未寫入：${String(error)}`);restore();return {action:'handled'};}
  });
  pi.on('before_agent_start',event=>{
   if(flow?.state!=='ready')return;
   return {systemPrompt:event.systemPrompt+'\nFinance FSM owns these entries. Call finance_finish exactly once, without commentary, with entries: one {category} per entry in order (expense -> expense_categories, income -> income_categories; a binding suggestion wins). Add payment only for entries with payment_hint and only if an account in payment_accounts clearly matches it; otherwise omit it (ledger rules decide). Amount, date and currency are fixed by the host. Never ask questions: if unsure use the closest category or expenses:uncategorized.',message:{customType:'finance-ready',content:JSON.stringify(flow.context()),display:false}};
  });
  // A fast-path turn is one short tool call; cap the output so an occasional
  // runaway (reasoning written as text) ends in seconds instead of a minute.
  pi.on('before_provider_request',event=>{if(flow?.state!=='ready')return;const p=requireToolCall(event.payload) as any;if(!p)return;const cap=120+60*flow.entries.length;// Thinking is turned off for this turn: choosing a category needs no
  // reasoning, and reasoning tokens would otherwise eat the output cap.
  const out:any={...p,max_tokens:Math.min(p.max_tokens??cap,cap),enable_thinking:false,chat_template_kwargs:{...(p.chat_template_kwargs??{}),enable_thinking:false}};
  for(const k of ['max_completion_tokens','reasoning_effort','reasoning','thinking','thinking_budget','thinking_budget_tokens','thinking_token_budget'])delete out[k];
  return out;});
  pi.on('tool_call',event=>{
   // The model chose another tool: nothing was written yet, so drop the
   // prepared fast-path entries and let that call proceed (the guarded
   // workflow arms itself) instead of blocking it and failing the request.
   if(flow&&event.toolName!=='finance_finish'){
    if(flow.state==='ready'){restore();return;}
    const text='記帳未完成：執行結果尚未確認，請先查帳，不要自動重送。';restore();report(text);
    return {block:true,reason:text,terminate:false};
   }
  });
  pi.registerTool({
   name:'finance_finish',label:'Finance',
   description:'Finish the prepared entries: one {category, payment?} per entry in order. Never ask questions. Host owns amounts, dates, currency, validation and the journal write. Returns the final receipt.',
   parameters:Type.Object({entries:Type.Optional(Type.Array(Type.Object({category:Type.String(),payment:Type.Optional(Type.String())},{additionalProperties:false}))),category:Type.Optional(Type.String())},{additionalProperties:false}),
   async execute(_id,params,signal){
    const details=()=>({state:flow?.state??'idle',trace:flow?[...flow.trace]:[],error:''});
    if(signal?.aborted)return {content:[{type:'text',text:'記帳已取消，未寫入。'}],details:{...details(),error:'aborted'},terminate:true};
    if(!flow||flow.state!=='ready')return {content:[{type:'text',text:'No writable prepared entries; stopped without replay.'}],details:details(),terminate:true};
    const choices=params.entries??(params.category?[{category:params.category}]:undefined);
    try{
     if(!choices)throw new Error('Provide entries: one category per entry, in order');
     const text=await flow.commit(choices!);
     recent.at=Date.now();recent.rows=flow.written;
     const shown=text.split('\n').filter(l=>!/^\[master|^\s+\d+ file|^Appended to|^Record \d|^Records ready/.test(l)).map(l=>/^Adopted external journal edits/.test(l)?ADOPTED:l).join('\n');
     report(shown);
     return {content:[{type:'text',text:shown.slice(0,12000)}],details:details(),terminate:true};
    }catch(error){
     // Validation happens before anything is written: let the model fix its
     // choice (state is still ready) instead of failing the whole record.
     if(flow.state==='ready'&&++retries<=2)return {content:[{type:'text',text:'Not written. Fix and call finance_finish again: '+String(error)}],details:details(),terminate:false};
     const text=`記帳未完成，沒有寫入：${String(error).replace(/^Error:\s*/,'').slice(0,160)}`;
     const stopped={...details(),state:'failed',error:String(error)};restore();
     report(text);return {content:[{type:'text',text}],details:stopped,terminate:true};
    }
   },
  });
  let nudged=false;
  pi.on('agent_settled',()=>{
   // One automatic retry when the model ended without calling the tool.
   if(flow?.state==='ready'&&!nudged){nudged=true;pi.sendMessage({customType:'finance-fast-nudge',display:false,content:'No finance_finish call was made. Call finance_finish now with one category per entry; no other text.'},{triggerTurn:true});return;}
   if(flow?.state==='ready')report('記帳流程未完成，未寫入；請重新提出完整記帳請求。');nudged=false;restore();
  });
  pi.on('session_shutdown',restore);
  // Reload/fork never reconstructs an in-flight mutation. Journal import-id is the replay boundary.
  pi.on('session_start',restore);
 };
}
export default createFinanceExtension();
