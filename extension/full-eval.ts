// Operator-requested direct pi SDK + Qwen full-skill evaluation. No child agents.
import { createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager, SettingsManager } from '@earendil-works/pi-coding-agent';
import { Type } from 'typebox';
import { readFile, writeFile, mkdir, readdir, stat } from 'node:fs/promises';
import { resolve, join, basename } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { cases, record, type Case } from './full-eval-cases.ts';
import { createFinanceExtension } from './index.ts';
import { pythonBackend, localDate } from './core.ts';
import { execute, validateCLI, scopedPath, checkTransactions, amounts } from './full-eval-support.ts';
const repo=resolve(fileURLToPath(new URL('..',import.meta.url)));
const output=resolve(process.env.EVAL_OUTPUT??'/tmp/hfin-qwen-full-20261003-1708');await mkdir(output,{recursive:true});
const production='/home/chihmin/finance/main.journal';const hash=async()=>createHash('sha256').update(await readFile(production)).digest('hex');const original=await hash();
const runtime=await ModelRuntime.create({modelsPath:'/home/chihmin/.pi/agent/models.json'});
const provider=process.env.EVAL_PROVIDER??'local-llama',modelId=process.env.EVAL_MODEL??'qwen3.6-35b-q4';const model=runtime.getModel(provider,modelId);if(!model)throw new Error('Qwen not configured');
const skill=await readFile(join(repo,'SKILL.md'),'utf8');const helper=join(repo,'scripts/finance.py');
const results:any[]=[];
const classification=(await readFile(join(repo,'docs/eval/2026-09-26-pi-qwen-taiwan-50-cases.tsv'),'utf8')).trim().split('\n').map(line=>{
 const [id,prompt,accounts]=line.split('\t');return {id:'C'+id.padStart(2,'0'),group:'Taiwan classification',prompt,fsm:true,expected:[{date:localDate(),amount:prompt.match(/\d+/)![0],account:accounts.split('|'),currency:'TWD'}]} as Case;
});
const matrix=[...cases,...classification];
for(const c of matrix){
 if(process.env.EVAL_FILTER&&!new RegExp(process.env.EVAL_FILTER).test(c.id))continue;
 const dir=join(output,'agent',c.id);await mkdir(join(dir,'book'),{recursive:true});await mkdir(join(dir,'home'),{recursive:true});
 const journal=join(dir,'book/main.journal');
 if(await stat(journal).catch(()=>null))throw new Error('Refusing reuse of case journal; choose new EVAL_OUTPUT or filter only untouched cases');
 const env={...process.env,PYTHONPATH:[process.env.PYTHONPATH,'/home/chihmin/.local/lib/python3.13/site-packages'].filter(Boolean).join(':'),HOME:join(dir,'home'),RCLONE_CONFIG:join(dir,'rclone.conf'),FINANCE_JOURNAL:journal,PI_FINANCE_JOURNAL:journal};
 await writeFile(env.RCLONE_CONFIG,`[qa]\ntype = alias\nremote = ${join(dir,'backup')}\n`);
 const host=async(args:string[],payload?:unknown)=>execute('python3',[helper,'--journal',journal,...args],{cwd:dir,env,stdin:payload===undefined?'':JSON.stringify(payload)});
 const init=await host(['init']);if(init.code)throw new Error(JSON.stringify(init));
 if(c.seed?.length){const seed=await host(['ingest-json','-'],{transactions:c.seed});if(seed.code)throw new Error(JSON.stringify(seed));}
 for(const [name,text] of Object.entries(c.files??{}))await writeFile(join(name.endsWith('-rules.json')?join(dir,'book'):dir,name),text);
 if(c.image)await execute('python3',['-c',`from PIL import Image,ImageDraw,ImageFont\nim=Image.new('RGB',(900,650),'white');d=ImageDraw.Draw(im);f=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',32)\nfor i,line in enumerate(['QA TEST RESTAURANT','Date: 2026-09-10','Invoice: RECEIPTQA01','Subtotal: TWD 180','Tax: TWD 9','TOTAL PAID: TWD 189','Payment: CASH']):d.text((45,40+i*75),line,fill='black',font=f)\nim.save('${join(dir,c.image)}')`],{cwd:dir,env});
 const before=await readFile(journal,'utf8');const calls:any[]=[];let forbidden=0,toolCount=0,timedOut=false,abortedBudget=false;const stages:any[]=[];
 const wrapped=async(args:string[],payload?:unknown)=>{const result=await host(args,payload);calls.push({program:'hfin',args,payload,...result,hostFSM:true});if(result.code)throw new Error(result.stderr||result.stdout);return result.stdout+'\n'+result.stderr;};
 const loader=new DefaultResourceLoader({cwd:dir,agentDir:join(dir,'home'),extensionFactories:[createFinanceExtension({backend:wrapped,journal})],extensionsOverride:base=>({...base,extensions:base.extensions.filter(e=>e.path.startsWith('<inline:')),errors:[]}),agentsFilesOverride:()=>({agentsFiles:[]}),skillsOverride:()=>({skills:[],diagnostics:[]}),systemPromptOverride:()=>`You are a personal-finance assistant. Authoritative local date ${localDate()}, Asia/Taipei. All data here are synthetic tests in an isolated journal. Follow the skill and user, don't invent facts or quote example facts as user facts. Do not ask routine category confirmation.\n${skill}\nTEST TRANSPORT: hfin tool invokes the SAME real finance.py with an argument array, e.g. {args:["classify","--description","<description>"]}, then {args:["ingest-json","-"],payload:{transactions:[...]}}. Provide user values, not placeholders. Relative paths refer to this fixture directory; journal is fixed by host, never pass --journal. read_artifact can read skill references by references/NAME.md or fixture files. write_artifact can write normalized JSON or classification-rules.json / payment-account-rules.json, never a journal. native_hledger runs read-only native reports with fixed -f. book_info reads journal/history. hfin --help or subcommand --help is available. Backend defaults are safe but date host above is authoritative. Default visualize output is inside fixture book/reports; for explicit output use a simple filename. Images must be inspected via read_artifact (PNG) then delivered using exact absolute [[image: ...]] markers. qa: backup remote is a configured local alias with no network. No bash/file-system escape tool is available.\n`});
 await loader.reload();
 const toolResult=(result:any)=>({content:[{type:'text' as const,text:JSON.stringify(result).slice(0,20000)}],details:{},isError:result.code!==undefined&&result.code!==0&&result.code!==3});
 const tools:any[]=[
  {name:'hfin',label:'hfin',description:'Real finance.py isolated CLI. args is command argument array excluding executable and --journal; use ["classify","--description","<user description>"] or ["ingest-json","-"] plus payload. Other skill subcommands and --help supported. No shell syntax. Return actual exit code, stdout/stderr. Exit 3 delete is confirmation search, not mutation.',parameters:Type.Object({args:Type.Array(Type.String()),payload:Type.Optional(Type.Any())}),async execute(_id:string,p:any){try{const args=await validateCLI(dir,'hfin',p.args);const result=await host(args,p.payload);calls.push({program:'hfin',args,payload:p.payload,...result});return toolResult(result);}catch(e){forbidden++;calls.push({program:'hfin',args:p.args,refused:String(e)});return toolResult({code:2,error:String(e)});}}},
  {name:'native_hledger',label:'hledger',description:'Read-only native hledger reports; fixed journal, no -f or output-file allowed. args begins with report command.',parameters:Type.Object({args:Type.Array(Type.String())}),async execute(_id:string,p:any){try{const args=await validateCLI(dir,'hledger',p.args);const result=await execute('hledger',['-f',journal,...args],{cwd:dir,env});calls.push({program:'hledger',args,...result});return toolResult(result);}catch(e){forbidden++;calls.push({program:'hledger',args:p.args,refused:String(e)});return toolResult({code:2,error:String(e)});}}},
  {name:'read_artifact',label:'Read fixture',description:'Read references/*.md, a fixture file, or inspect PNG artifact image. Use exact paths from tools. For generated book/reports PNG only, readonly access permitted.',parameters:Type.Object({path:Type.String()}),async execute(_id:string,p:any){try{let path:string;if(/^references\/[A-Z_]+\.md$/.test(p.path))path=join(repo,p.path);else path=await scopedPath(dir,p.path,true);const data=await readFile(path);if(path.endsWith('.png'))return {content:[{type:'text',text:path},{type:'image',mimeType:'image/png',data:data.toString('base64')}],details:{}};return {content:[{type:'text',text:data.toString('utf8').slice(0,50000)}],details:{}};}catch(e){return toolResult({code:2,error:String(e)});}}},
  {name:'write_artifact',label:'Write normalized fixture',description:'Write a normalized JSON/CSV/text artifact. For future preference configs use exact names classification-rules.json or payment-account-rules.json, automatically mapped beside journal. Cannot edit journal or .git.',parameters:Type.Object({path:Type.String(),content:Type.String()}),async execute(_id:string,p:any){try{let path:string;if(['classification-rules.json','payment-account-rules.json'].includes(p.path)){JSON.parse(p.content);path=join(dir,'book',p.path);}else{path=await scopedPath(dir,p.path);if(!/\.(json|csv|txt)$/.test(path)||path.includes('/.git/'))throw new Error('Artifact type refused');}await writeFile(path,p.content);calls.push({program:'write_artifact',path,content:p.content});return toolResult({path});}catch(e){forbidden++;return toolResult({code:2,error:String(e)});}}},
  {name:'book_info',label:'Inspect journal/history',description:'Read synthetic fixed journal plus Git history, never writes.',parameters:Type.Object({}),async execute(){const log=await execute('git',['-C',join(dir,'book'),'log','--format=%s'],{cwd:dir,env});return toolResult({journal:await readFile(journal,'utf8'),history:log.stdout});}},
 ];
 const {session,extensionsResult}=await createAgentSession({cwd:dir,agentDir:join(dir,'home'),resourceLoader:loader,modelRuntime:runtime,model,thinkingLevel:'off',tools:['hfin','native_hledger','read_artifact','write_artifact','book_info','finance_finish'],customTools:tools,sessionManager:SessionManager.inMemory(dir),settingsManager:SettingsManager.inMemory({})});
 if(extensionsResult.errors.length)throw new Error(JSON.stringify(extensionsResult.errors));
 const events:any[]=[];session.subscribe(e=>{if(['message_end','tool_execution_start','tool_execution_end'].includes(e.type))events.push(e);if(e.type==='tool_execution_start'&&++toolCount>32){abortedBudget=true;session.abort();}});
 const failures:string[]=[];const start=performance.now();
 const prompt=async(text:string,image?:string)=>{const timer=setTimeout(()=>{timedOut=true;session.abort();},Number(process.env.EVAL_TIMEOUT_MS??90000));try{await session.prompt(text,image?{images:[{type:'image',mimeType:'image/png',data:(await readFile(join(dir,image))).toString('base64')}]}:undefined);}catch(e){failures.push('Agent error: '+String(e));}finally{clearTimeout(timer);}const result=await execute('hledger',['-f',journal,'print','-O','json'],{cwd:dir,env});const transactions=result.code?[]:JSON.parse(result.stdout);stages.push({text,transactions,journal:await readFile(journal,'utf8')});};
 await prompt(c.prompt,c.image);
 if(c.id==='D1'||c.id==='D2'){if(stages[0].transactions.length!==4||stages[0].journal!==before)failures.push('Delete mutated before explicit user confirmation');const preview=calls.find(x=>x.args?.[0]==='delete'&&x.code===3);if(!preview)failures.push('Missing unconfirmed candidate search');}
 if(c.stale){await host(['ingest-json','-'],{transactions:[record('expense',localDate(),'New transaction','7','expenses:food:dining','assets:cash')]});}
 if(c.followUp)await prompt(c.followUp);
 if(c.id==='D1'){
  if(stages.at(-1).transactions.length!==3)failures.push('Confirmed delete did not remove exactly one candidate');
  await prompt('請用undo還原剛才刪除的一筆，保留Git歷史。');if(stages.at(-1).transactions.length!==4)failures.push('Undo did not restore deleted entry');
 }
 const last=stages.at(-1),text=last.journal;const tx=last.transactions;
 if(c.expected)failures.push(...checkTransactions(tx,c.expected));
 if(c.count!==undefined&&tx.length!==(c.stale?c.count+1:c.count))failures.push(`Transaction count ${tx.length}, expected ${c.stale?c.count+1:c.count}`);
 if(c.unchanged&&text!==before)failures.push('Read-only/preview mutated journal');
 if(timedOut)failures.push('Agent timeout');if(abortedBudget)failures.push('Agent exceeded 32-call budget');if(forbidden)failures.push(`${forbidden} forbidden command/path attempts`);
 for(const required of c.required??[])if(!calls.some(x=>x.program===(required.startsWith('native:')?'hledger':'hfin')&&x.args?.[0]===(required.replace('native:',''))&&(x.code===0||x.code===3)))failures.push('Missing successful command '+required);
 const answers=session.messages.filter((m:any)=>m.role==='assistant'||(m.role==='custom'&&m.display)).map((m:any)=>typeof m.content==='string'?m.content:m.content.filter((x:any)=>x.type==='text').map((x:any)=>x.text).join('')).join('\n');
 for(const n of c.answerNumbers??[])if(!answers.replaceAll(',','').includes(n))failures.push('Missing answer figure '+n);
 const allFiles=await readdir(dir,{recursive:true});
 const pngs=allFiles.filter(x=>x.endsWith('.png')&&x!==c.image);
 if(c.pngs){if(pngs.length<c.pngs)failures.push('Missing chart files');for(const name of pngs){const data=await readFile(join(dir,name));if(data.length<1000||data.readUInt32BE(16)!==1800||data.readUInt32BE(20)!==1200)failures.push('Invalid chart dimensions '+name);if(!answers.includes(`[[image: ${join(dir,name)}]]`))failures.push('Missing image delivery marker '+name);}if(!calls.some(x=>x.program==='hfin'&&x.args[0]==='visualize'&&x.code===0))failures.push('No rendered visualization');}
 if(c.inspect==='rules'){const rule=JSON.parse(await readFile(join(dir,'book/classification-rules.json'),'utf8').catch(()=>'{}'));if(!rule.rules?.some((r:any)=>r.pattern==='公司午餐'&&r.account==='expenses:work:meals'))failures.push('Future rule not persisted');}
 if(c.inspect==='backup'){const backed=await readFile(join(dir,'backup/main.journal'),'utf8').catch(()=>'');if(backed!==text)failures.push('Backup content mismatch');}
 if(c.inspect==='loan'){if(!text.includes('loan-period:3')||!text.includes('loan-period:5')||!text.includes('kind:loan-payment'))failures.push('Loan metadata/absolute periods wrong');const total=tx.flatMap((t:any)=>t.tpostings).filter((p:any)=>p.paccount==='liabilities:loans:personal').flatMap(amounts).reduce((sum:number,a:any)=>sum+Number(a.amount),0);if(total!==1000)failures.push('Loan principal sum incorrect');}
 if(c.inspect==='history'&&(!text.includes('source:historical-import')||!text.includes('import-id:h1')||!text.includes('import-id:h3')))failures.push('Historical metadata/IDs missing');
 if(c.inspect==='cross'&&(!text.includes('14368 JPY @@ 3000 TWD')||!text.includes('kind:transfer')))failures.push('Cross-currency direction/price incorrect');
 if(c.id==='F17'){const total=tx.flatMap((t:any)=>t.tpostings).filter((p:any)=>p.paccount==='expenses:shopping').flatMap(amounts).reduce((s:number,a:any)=>s+Number(a.amount),0);if(Math.abs(total-100.01)>1e-9||!text.includes('2026-11-30')||!text.includes('2026-12-31'))failures.push('Installment sum/end-month incorrect');}
 if(c.id==='D2'&&!calls.some(x=>x.args?.[0]==='delete'&&x.args.includes('--confirm')&&x.code!==0))failures.push('Stale token was not checked/rejected');
 const check=await host(['check']);if(check.code)failures.push('Final journal check failed');
 const log=await execute('git',['-C',join(dir,'book'),'log','--format=%s'],{cwd:dir,env});
 if(c.expected&&!c.seed&&['F08','F22','F23'].includes(c.id)&&log.stdout.split('\n').filter(x=>x.startsWith('Ingest')).length!==1)failures.push('Batch was not single commit');
 if(c.id==='F27'&&log.stdout.split('\n').filter(x=>x.startsWith('Ingest')).length!==1)failures.push('Duplicate import added extra commit');
 const result={id:c.id,group:c.group,status:failures.length?'FAIL':'PASS',seconds:(performance.now()-start)/1000,tools:toolCount,modelResponses:session.messages.filter((m:any)=>m.role==='assistant').length,transactions:tx.length,fsm:events.some((e:any)=>e.type==='tool_execution_start'&&e.toolName==='finance_finish'),failures,images:pngs.map(name=>join(dir,name))};
 await writeFile(join(dir,'evidence.json'),JSON.stringify({result,prompt:c.prompt,stages,calls,messages:session.messages,history:log.stdout},null,2));session.dispose();
 if(await hash()!==original)throw new Error('Formal journal changed; abort all testing');
 results.push(result);await writeFile(join(output,'agent-results.json'),JSON.stringify({provider,model:modelId,thinking:'off',productionUnchanged:true,date:localDate(),adapter:'fixed-journal CLI + scoped artifacts, no shell/network tools',results},null,2));console.log(JSON.stringify(result));
}
console.log('Artifacts: '+output);
