import { createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager, SettingsManager } from '@earendil-works/pi-coding-agent';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { join } from 'node:path';
const qa='/tmp/hfin-qwen-full-20261003-1708/visualization';
const zoom=process.env.EVAL_ZOOM?.split(',');
const dir='/tmp/hfin-qwen-full-20261003-final/'+(zoom?'visual-review-zoom':'visual-review');await mkdir(dir,{recursive:true});
const runtime=await ModelRuntime.create({modelsPath:'/home/chihmin/.pi/agent/models.json'});
const model=runtime.getModel('local-llama','qwen3.6-35b-q4');if(!model)throw new Error('Configured Qwen not found');
const manifest=JSON.parse(await readFile(join(qa,'results.json'),'utf8'));
const reviews:any[]=[];
for(let sheet=1;sheet<=(zoom?.length??5);sheet++){
 const batch=zoom?manifest.filter((r:any)=>r.id===zoom[sheet-1]):manifest.slice((sheet-1)*10,sheet*10);
 const cwd=join(dir,'sheet-'+sheet);await mkdir(cwd,{recursive:true});
 const loader=new DefaultResourceLoader({cwd,agentDir:cwd,extensionsOverride:base=>({...base,extensions:[],errors:[]}),agentsFilesOverride:()=>({agentsFiles:[]}),skillsOverride:()=>({skills:[],diagnostics:[]}),systemPromptOverride:()=> 'You are a careful financial-chart visual QA reviewer. Inspect the actual attached image. Never claim details that are illegible. Error evidence cards are expected for error-path tests. Return only JSON: {"cases":[{"id":"V01","status":"PASS|FAIL|REVIEW","reason":"short observable reason"}]}. Include every requested ID exactly once. PASS requires visible evidence. REVIEW means full resolution needed. No tools.'});await loader.reload();
 const {session}=await createAgentSession({cwd,agentDir:cwd,resourceLoader:loader,modelRuntime:runtime,model,thinkingLevel:'off',tools:[],sessionManager:SessionManager.inMemory(cwd),settingsManager:SettingsManager.inMemory({})});
 let timeout=false;const timer=setTimeout(()=>{timeout=true;session.abort();},90000);
 let error='';try{await session.prompt('Review every attached panel; when full resolution is attached, re-check exact visible KPI text and sign. Check chart layout, clipped text, labels, white background, charts suited to income-only/expense-only/mixed data; consider the expected behavior for each scenario. Do not guess numeric precision in thumbnails. Panel manifest: '+JSON.stringify(batch.map((r:any)=>({id:r.id,name:r.name,kind:r.kind,focus:r.focus,expectedExit:r.exit_code}))),{images:[{type:'image',mimeType:'image/png',data:(await readFile(zoom?batch[0].artifact:join(qa,`contact-sheet-${sheet}.png`))).toString('base64')}]});}catch(e){error=String(e);}finally{clearTimeout(timer);}
 const answer=session.messages.filter((m:any)=>m.role==='assistant').flatMap((m:any)=>m.content).filter((c:any)=>c.type==='text').map((c:any)=>c.text).join('');let parsed:any;
 try{parsed=JSON.parse(answer.replace(/^```(?:json)?\s*/,'').replace(/\s*```$/,''));}catch{parsed={cases:batch.map((r:any)=>({id:r.id,status:'REVIEW',reason:'Non-JSON or incomplete model output'}))};}
 const valid=batch.map((r:any)=>{const matches=parsed.cases?.filter((x:any)=>x.id===r.id)??[];return matches.length===1&&['PASS','FAIL','REVIEW'].includes(matches[0].status)?matches[0]:{id:r.id,status:'REVIEW',reason:'Missing/invalid review row'};});
 reviews.push(...valid);await writeFile(join(cwd,'evidence.json'),JSON.stringify({sheet,timeout,error,answer,review:valid,messages:session.messages},null,2));session.dispose();await writeFile(join(dir,'results.json'),JSON.stringify({provider:'local-llama',model:'qwen3.6-35b-q4',reviews},null,2));console.log(JSON.stringify({sheet,timeout,error,review:valid}));
}
console.log(dir);
