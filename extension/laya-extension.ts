import type {ExtensionAPI} from '@earendil-works/pi-coding-agent';
import {Type} from 'typebox';
import {dirname,resolve} from 'node:path';
import {readFile} from 'node:fs/promises';
import {pythonBackend,hledgerAccounts,userMessage,type Backend} from './core.ts';
import {pythonBridge,isShortConfirm,type Bridge} from './workflows.ts';
import {ledgerWrite} from './managed.ts';
import {fileStore} from './pending-store.ts';
import {LayaFinance,type EngineReceipt} from './laya-engine.ts';
import type {Judge} from './laya.ts';
export function registerLaya(pi:ExtensionAPI,options:{journal:string;backend?:Backend;bridge?:Bridge;judge?:Judge;today?:()=>string;stateDir?:string;accounts?:()=>Promise<string[]>}){
 const engine=new LayaFinance({...options,backend:options.backend??pythonBackend({journal:options.journal}),bridge:options.bridge??pythonBridge(options.journal),accounts:options.accounts??hledgerAccounts(options.journal),store:fileStore(options.journal,options.stateDir),paymentRules:async()=>{try{return JSON.parse(await readFile(resolve(dirname(options.journal),'payment-account-rules.json'),'utf8')).currency_accounts??{};}catch{return {};}}});
 let latest:{text:string;images?:unknown[];session:string}|undefined;let consumed=false;let running=false;
 const report=(r:EngineReceipt)=>pi.sendMessage({customType:'finance-laya-receipt',content:r.text,display:true},{triggerTurn:false});
 const run=async(signal?:AbortSignal)=>{
  if(!latest)return {text:'沒有使用者訊息可交付。',state:'failed',trace:[]} as EngineReceipt;
  if(consumed)return {text:'本次訊息已處理，未再次執行。',state:'consumed',trace:[]} as EngineReceipt;
  consumed=true;running=true;
  try{const r=await engine.run(latest.text,{session:latest.session,images:latest.images,signal});if(r.state!=='released')report(r);return r;}
  finally{running=false;}
 };
 pi.on('input',async(event,ctx)=>{
  if(event.source==='extension'||!ctx.isIdle()||event.streamingBehavior==='steer')return;
  if(running){report({text:'財務流程執行中，本次輸入未操作。',state:'busy',trace:[]});return {action:'handled'};}
  latest={text:event.text,images:event.images,session:ctx.sessionManager.getSessionId()};consumed=false;
  // Observe EVERY input, including normal chat with no handoff: an intervening
  // message must invalidate pending authorization in memory AND on disk.
  engine.observeInput(event.text,latest.session);
  // Confirmation is exact user authorization, not a Laya decision or agent tool.
  const text=userMessage(event.text).trim();
  const explicit=/^(?:確認|confirm) [0-9a-f-]{36}$/i.test(text);
  if(explicit||(engine.hasPending(latest.session)&&(isShortConfirm(text)||/^取消(?:記帳)?$/.test(text)||/^(?:只|僅|部分).{0,12}確認/.test(text)))){
   await run();return {action:'handled'};
  }
 });
 pi.registerTool({name:'finance_handoff',label:'Finance (Laya)',description:"Hand the user's latest own-money/ledger request to the extension. Laya chooses record/query/change/category; the host executes or asks for missing facts. Do not classify, query, edit, or prepare transactions yourself. No amount/category argument is accepted. The receipt is authoritative. Not for general financial news or software discussion.",parameters:Type.Object({reason:Type.Optional(Type.String({maxLength:200}))},{additionalProperties:false}),async execute(_id,_params,signal){
  const r=await run(signal);return {content:[{type:'text',text:r.text}],details:r,terminate:r.state!=='released'};
 }});
 pi.on('tool_call',event=>{const reason=ledgerWrite(event.toolName,event.input as any,dirname(resolve(options.journal)));if(reason)return {block:true,reason:reason+'; use finance_handoff.'};});
 const reset=()=>{latest=undefined;consumed=false;engine.reset();};
 pi.on('session_start',reset);pi.on('session_shutdown',reset);
}
