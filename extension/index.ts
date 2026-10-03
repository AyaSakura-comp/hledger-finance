import type { ExtensionAPI, ExtensionFactory } from '@earendil-works/pi-coding-agent';
import { Type } from 'typebox';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { FinanceFlow, parseExpense, localDate, pythonBackend, type Backend } from './core.ts';

export function createFinanceExtension(options:{backend?:Backend;journal?:string;today?:()=>string}={}):ExtensionFactory {
 return (pi:ExtensionAPI)=>{
  let flow:FinanceFlow|undefined;
  let savedTools:string[]|undefined;
  const backend=options.backend??pythonBackend({journal:options.journal??process.env.PI_FINANCE_JOURNAL??join(homedir(),'finance/main.journal')});
  const restore=()=>{if(savedTools){pi.setActiveTools(savedTools);savedTools=undefined;}flow=undefined;};
  const report=(content:string)=>pi.sendMessage({customType:'finance-receipt',content,display:true},{triggerTurn:false});
  pi.on('input',async(event,ctx)=>{
   if(event.source==='extension'||!ctx.isIdle()||event.streamingBehavior==='steer')return;
   if(flow){report('已有記帳流程準備中；本次輸入未寫入，請等待回執後重新提出。');return {action:'handled'};}
   const today=(options.today??localDate)();
   if(event.images?.length||!parseExpense(event.text,today))return;
   restore();savedTools=pi.getActiveTools();
   flow=new FinanceFlow(backend,state=>pi.appendEntry('finance-state',{version:1,state}));
   try{
    await flow.prepare(event.text,today,`pi-${ctx.sessionManager.getSessionId()}-${randomUUID()}`);
    pi.setActiveTools(['finance_finish']);
   }catch(error){report(`記帳準備失敗，未寫入：${String(error)}`);restore();return {action:'handled'};}
  });
  pi.on('before_agent_start',event=>{
   if(flow?.state!=='ready')return;
   return {systemPrompt:event.systemPrompt+'\nFinance FSM owns this expense. Call finance_finish exactly once, without commentary. Only select an account from provided categories; binding rule wins. Never invent or change amount, date or payment source. Clarify only material uncertainty, not routine category selection. Tool sends the final receipt; do not call other tools or emit a final answer.',message:{customType:'finance-ready',content:JSON.stringify({expense:flow.expense,...flow.evidence}),display:false}};
  });
  pi.on('tool_call',event=>{
   if(flow&&event.toolName!=='finance_finish')return {block:true,reason:'Finance state machine permits only finance_finish for this request',terminate:true};
  });
  pi.registerTool({
   name:'finance_finish',label:'Finance',
   description:'Finish the prepared expense: select exactly one category from finance-ready evidence, or ask one clarification. Host owns facts, validation and journal write. Returns final receipt and terminates. Output limited to 12000 characters.',
   parameters:Type.Object({category:Type.Optional(Type.String()),question:Type.Optional(Type.String())},{additionalProperties:false}),
   async execute(_id,params,signal){
    if(signal?.aborted)return {content:[{type:'text',text:'記帳已取消，未寫入。'}],details:{state:flow?.state??'idle',trace:flow?[...flow.trace]:[],error:'aborted'},terminate:true};
    if(!flow||flow.state!=='ready')return {content:[{type:'text',text:'No writable prepared expense; stopped without replay.'}],details:{state:flow?.state??'idle',trace:flow?[...flow.trace]:[],error:''},terminate:true};
    try{
     if(Boolean(params.category)===Boolean(params.question))throw new Error('Provide exactly one category or clarification question');
     const text=params.question?flow.clarify(params.question):await flow.commit(params.category!);
     report(text.split('\n')[0]);
     return {content:[{type:'text',text:text.slice(0,12000)}],details:{state:flow.state,trace:[...flow.trace],error:''},terminate:true};
    }catch(error){
     const text=`記帳未確認成功，已停止；請先查帳，不要自動重試：${String(error)}`;
     report(text);return {content:[{type:'text',text}],details:{state:flow.state,error:String(error),trace:[...flow.trace]},terminate:true};
    }
   },
  });
  pi.on('agent_settled',()=>{if(flow?.state==='ready')report('記帳流程未完成，未寫入；請重新提出完整記帳請求。');restore();});
  pi.on('session_shutdown',restore);
  // Reload/fork never reconstructs an in-flight mutation. Journal import-id is the replay boundary.
  pi.on('session_start',restore);
 };
}
export default createFinanceExtension();
