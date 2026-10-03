// Controlled screening: restricted skill+CLI baseline vs FSM, NOT historical unconstrained UI latency.
import { createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager, SettingsManager } from '@earendil-works/pi-coding-agent';
import { Type } from 'typebox';
import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { createFinanceExtension } from './index.ts';
import { localDate, pythonBackend } from './core.ts';

const root=resolve(fileURLToPath(new URL('..',import.meta.url)));
const production='/home/chihmin/finance/main.journal';
const checksum=async()=>createHash('sha256').update(await readFile(production)).digest('hex');
const originalHash=await checksum();
const dir=await mkdtemp(join(tmpdir(),'finance-qwen-bench-'));
const runtime=await ModelRuntime.create({modelsPath:'/home/chihmin/.pi/agent/models.json'});
const provider=process.env.BENCH_PROVIDER??'local-llama', modelId=process.env.BENCH_MODEL??'qwen3.6-35b-q4';
const model=runtime.getModel(provider,modelId); if(!model)throw new Error('Configured Qwen model not found');
const skill=await readFile(join(root,'SKILL.md'),'utf8');
const inputs=[['【記帳】1500 午餐','expenses:food:dining'],['YouBike 15 元','expenses:transport:transit'],['威力彩 100 元','expenses:entertainment:lottery']];
const results:any[]=[];
for(let repeat=0;repeat<Number(process.env.BENCH_REPEATS??2);repeat++){
 for(let i=0;i<inputs.length;i++){
  for(const mode of (repeat%2===0?['skill','fsm']:['fsm','skill'])){
   const runDir=join(dir,`${repeat}-${i}-${mode}`);await mkdir(runDir);const journal=join(runDir,'main.journal');
   const backend=pythonBackend({journal});await backend(['init']);
   const backendCalls:any[]=[];
   const wrapped=async(args:string[],payload?:unknown)=>{backendCalls.push({args,payload});return backend(args,payload);};
   const loader=new DefaultResourceLoader({cwd:runDir,agentDir:runDir,
    extensionFactories:mode==='fsm'?[createFinanceExtension({backend:wrapped})]:[],
    extensionsOverride:base=>({...base,extensions:base.extensions.filter(e=>e.path.startsWith('<inline:')),errors:[]}),
    agentsFilesOverride:()=>({agentsFiles:[]}),skillsOverride:()=>({skills:[],diagnostics:[]}),
    systemPromptOverride:()=>`You are a personal-finance assistant. Local date is ${localDate()} (Asia/Taipei). The user gives a completed expense. Record it once and briefly confirm. No unrelated tools.`+(mode==='skill'?'\n'+skill+'\nEVALUATION ADAPTER: hfin/read/bash are unavailable. Skill is already supplied here. Use finance_cli with argument array to call classify, then ingest-json - with payload, then confirm. Journal is fixed by host. Do not include --journal or execute commands.':''),
   });
   await loader.reload();
   const baselineTool:any={name:'finance_cli',label:'Finance CLI',description:'Isolated hfin adapter. Use args=["classify","--description","<user description>"] (exact three-argument syntax) to obtain categories/binding, then args=["ingest-json","-"] with payload={transactions:[{kind:"expense",description:"<user description>",amount:"<user amount>",debit:"<chosen category>",tags:["source:fuzzy-text"]}]}. Angle-bracket strings are placeholders: use actual current user facts. No date/currency in payload unless user supplied them: backend defaults local today/TWD. Only these two command shapes allowed.',parameters:Type.Object({args:Type.Array(Type.String()),payload:Type.Optional(Type.Any())}),async execute(_id:string,p:any){
    if(!((p.args[0]==='classify'&&p.args.length===3&&p.args[1]==='--description')||(p.args.length===2&&p.args[0]==='ingest-json'&&p.args[1]==='-')))throw new Error('Sandbox only accepts classify or ingest-json -');
    return {content:[{type:'text',text:await wrapped(p.args,p.payload)}],details:{}};
   }};
   const settings=SettingsManager.inMemory({});
   const {session,extensionsResult}=await createAgentSession({cwd:runDir,agentDir:runDir,resourceLoader:loader,modelRuntime:runtime,model,thinkingLevel:'off',tools:mode==='fsm'?['finance_finish']:['finance_cli'],customTools:mode==='skill'?[baselineTool]:[],sessionManager:SessionManager.inMemory(runDir),settingsManager:settings});
   if(extensionsResult.errors.length)throw new Error(JSON.stringify(extensionsResult.errors));
   let assistantRounds=0,tools=0,timedOut=false; const events:any[]=[];
   session.subscribe(event=>{if(['message_end','tool_execution_start','tool_execution_end','agent_end','agent_settled'].includes(event.type))events.push(event);if(event.type==='message_end'&&event.message.role==='assistant')assistantRounds++;if(event.type==='tool_execution_start')tools++;});
   const start=performance.now();let error='';
   const timer=setTimeout(()=>{timedOut=true;session.abort();},180000);
   try{await session.prompt(inputs[i][0]);}catch(e){error=String(e);}finally{clearTimeout(timer);}
   const seconds=(performance.now()-start)/1000;
   const text=await readFile(journal,'utf8');
   const transactions=text.match(/^\d{4}-\d{2}-\d{2} /gm)??[];
   const amount=inputs[i][0].match(/\d+/)![0];
   const correct=transactions.length===1&&text.includes(inputs[i][1])&&text.includes(localDate())&&text.includes(`${amount} TWD`)&&text.includes(`-${amount} TWD`);
   const messages=session.messages;
   const usage=messages.filter((m:any)=>m.role==='assistant').reduce((a:any,m:any)=>({input:a.input+(m.usage?.input??0),output:a.output+(m.usage?.output??0),cacheRead:a.cacheRead+(m.usage?.cacheRead??0)}),{input:0,output:0,cacheRead:0});
   const result={repeat,input:inputs[i][0],expected:inputs[i][1],mode,seconds,assistantRounds,tools,backendCalls:backendCalls.length,correct,transactions:transactions.length,usage,error,timedOut};results.push(result);
   await writeFile(join(runDir,'evidence.json'),JSON.stringify({result,events,messages,backendCalls,journal:text},null,2));
   session.dispose();
   if(await checksum()!==originalHash)throw new Error('Production journal changed; stopping');
   await writeFile(join(dir,'results.json'),JSON.stringify({provider,model:modelId,thinking:'off',productionUnchanged:true,results},null,2));
   console.log(JSON.stringify(result));
  }
 }
}
console.log(`Artifacts: ${dir}`);
