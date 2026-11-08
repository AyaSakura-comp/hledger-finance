import type {ExtensionAPI, ExtensionFactory} from '@earendil-works/pi-coding-agent';
import {dirname, resolve} from 'node:path';
import {homedir} from 'node:os';
import {createFinanceExtension} from './agent.ts';
import {FinanceFeedback} from './feedback.ts';
import {financeIntent, ledgerWrite} from './managed.ts';
import {parseEntries, userMessage, localDate} from './core.ts';
import {readonlyOperations,mutationOperations} from './workflows.ts';

type Options=Parameters<typeof createFinanceExtension>[0] & {host?:string};
const unavailable='此入口未提供錢包；未讀取帳本，也未執行記帳、刪除或備份。請從 PiWeb 使用。';
const denied=()=>({content:[{type:'text' as const,text:unavailable}],details:{state:'wallet_unavailable'},terminate:true});

// Host policy is captured outside model context. This is an execution gate,
// NOT an OS sandbox against unrestricted shell code running as the same UID.
export function createChannelFinanceExtension(options:Options={}):ExtensionFactory{
 return (pi:ExtensionAPI)=>{
  const host=options.host??process.env.PI_FINANCE_HOST??'unknown';
  const enabled=host==='piweb';
  const feedback=new FinanceFeedback(text=>pi.sendMessage({customType:'finance-result-fallback',content:text,display:true},{triggerTurn:false}));
  let feedbackContextSent=false;
  pi.on('input',(event,ctx)=>{if(event.source!=='extension'&&ctx.isIdle()&&event.streamingBehavior!=='steer'){feedback.reset();feedbackContextSent=false;}});
  pi.on('session_start',()=>feedback.reset());
  pi.on('session_shutdown',()=>feedback.reset());
  // Terminating tool denials skip the final assistant turn; gateways need a
  // displayed receipt rather than only the internal blocked-tool result.
  const reportDenied=()=>pi.sendMessage({customType:'finance-wallet-unavailable',content:unavailable,display:true},{triggerTurn:false});
  const blockDenied=()=>{reportDenied();return {block:true,reason:unavailable,terminate:true};};
  const journal=options.journal??process.env.PI_FINANCE_JOURNAL??resolve(homedir(),'finance/main.journal');
  const ledgerDir=dirname(resolve(journal));
  const definitions=new Map<string,any>();
  const handlers=new Map<string,any>();
  const scopedContext=(ctx:any)=>{
   if(!ctx?.sessionManager)return ctx;
   const manager=ctx.sessionManager;
   const sessionManager=new Proxy(manager,{get(target,key){
    if(key==='getSessionId')return ()=>`${host}-${target.getSessionId()}`;
    const value=Reflect.get(target,key,target);return typeof value==='function'?value.bind(target):value;
   }});
   return new Proxy(ctx,{get(target,key){if(key==='sessionManager')return sessionManager;const value=Reflect.get(target,key,target);return typeof value==='function'?value.bind(target):value;}});
  };
  const stable=new Proxy(pi,{get(target,key){
   // The FSM still enforces tool_call permissions, but never changes public
   // tool availability. Other extensions retain their own API unchanged.
   if(key==='setActiveTools')return ()=>{};
   if(key==='sendMessage')return (message:any,options:any)=>{
    if(enabled&&['finance-receipt','finance-workflow-receipt'].includes(message.customType)&&typeof message.content==='string'&&/^(?:記帳未完成|記帳準備失敗|財務流程停止|沒有再記)/.test(message.content)){
     feedback.begin(message.content,true,undefined,message.content.startsWith('記帳準備失敗'));
     return; // The result goes to the model, not a raw final error dump.
    }
    return target.sendMessage(message,options);
   };
   if(key==='registerTool')return (tool:any)=>{
    handlers.set(tool.name,tool.execute);
    if(definitions.has(tool.name))return; // contextual schema/description stays frozen
    const published=tool.name==='finance_action'?{...tool,parameters:{...tool.parameters,properties:{...tool.parameters.properties,operation:{...tool.parameters.properties.operation,enum:[...readonlyOperations,...mutationOperations]}}}}:tool;
    definitions.set(tool.name,published);
    target.registerTool({...published,async execute(...args:any[]){
     if(!enabled){reportDenied();return denied();}
     args[4]=scopedContext(args[4]);
     const result=await handlers.get(tool.name)(...args);
     if(['finance_finish','finance_action'].includes(tool.name)&&result?.terminate&&['done','failed','cancelled'].includes(result.details?.state)){
      const failure=result.details.state!=='done';
      const text=(result.content??[]).filter((c:any)=>c.type==='text').map((c:any)=>c.text).join('\n');
      return {...result,content:[{type:'text',text:feedback.begin(text,failure,args[0])}],details:{...result.details,financeFeedback:true},terminate:false};
     }
     return result;
    }});
   };
   if(key==='on')return (name:string,handler:any)=>{
    (target.on as any)(name,async(event:any,ctx:any)=>{
     // Per-turn tool_choice/thinking/template overrides can split prefix keys.
     // Keep provider options unchanged; instructions live after the prefix.
     if(name==='before_provider_request')return;
     if(!enabled)return;
     if(feedback.active&&name==='before_agent_start'){
      if(feedbackContextSent)return;
      feedbackContextSent=true;return {message:feedback.message()};
     }
     const result=await handler(event,scopedContext(ctx));
     if(name==='input'&&feedback.active&&result?.action==='handled')return;
     if(name!=='before_agent_start'||!result?.systemPrompt)return result;
     const instructions=result.systemPrompt.startsWith(event.systemPrompt)?result.systemPrompt.slice(event.systemPrompt.length):result.systemPrompt;
     const {systemPrompt:_system,...rest}=result;
     return {...rest,message:{...(result.message??{customType:'finance-execution-context',display:false}),content:instructions+'\n'+(result.message?.content??'')}};
    });
   };
   const value=Reflect.get(target,key,target);return typeof value==='function'?value.bind(target):value;
  }}) as ExtensionAPI;

  // Always register the original guarded full workflow, never the opt-in
  // experimental classifier. Both channels load exactly this same code.
  createFinanceExtension({...options,journal})(stable);
  pi.on('agent_end',event=>{if(enabled)feedback.observe(event.messages);});
  pi.on('agent_settled',()=>feedback.settle());
  pi.on('session_start',()=>{
   const nonFinance=pi.getActiveTools().filter(name=>!definitions.has(name));
   pi.setActiveTools([...nonFinance,...definitions.keys()]);
  });
  pi.on('input',(event,ctx)=>{
   if(enabled||event.source==='extension'||!ctx.isIdle()||event.streamingBehavior==='steer')return;
   const text=userMessage(event.text);
   if(financeIntent(text)||parseEntries(text,(options.today??localDate)())||/^(?:確認|confirm) [0-9a-f-]{36}$/i.test(text.trim())){
    reportDenied();
    return {action:'handled' as const};
   }
  });
  pi.on('tool_call',(event,ctx)=>{
   if(enabled)return;
   if(definitions.has(event.toolName))return blockDenied();
   const input=event.input as Record<string,any>;
   const raw=String(input?.path??input?.file_path??'');
   const path=raw.startsWith('~/')?resolve(homedir(),raw.slice(2)):resolve(ctx.cwd,raw);
   if((raw&&(path===ledgerDir||path.startsWith(ledgerDir+'/')))||ledgerWrite(event.toolName,input,ledgerDir))return blockDenied();
  });
 };
}
export default createChannelFinanceExtension();
