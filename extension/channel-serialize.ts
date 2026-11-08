// Real Pi SDK serialization, stopped before network inference. No KV/model changes.
import {createAgentSession,DefaultResourceLoader,ModelRuntime,SessionManager,SettingsManager} from '@earendil-works/pi-coding-agent';
import {mkdtemp,readFile,writeFile} from 'node:fs/promises';import {join} from 'node:path';import {tmpdir} from 'node:os';import {createHash} from 'node:crypto';import assert from 'node:assert/strict';
import {createChannelFinanceExtension} from './channel.ts';import {pythonBackend,hledgerAccounts} from './core.ts';import {pythonBridge} from './workflows.ts';
const root=await mkdtemp(join(tmpdir(),'finance-channel-sdk-'));const journal=join(root,'book/main.journal');const backend=pythonBackend({journal});await backend(['init']);
const production='/home/chihmin/finance/main.journal';const hash=(s:string|Buffer)=>createHash('sha256').update(s).digest('hex');const before=hash(await readFile(production));
const runtime=await ModelRuntime.create({modelsPath:'/home/chihmin/.pi/agent/models.json'});const model=runtime.getModel('local-llama','qwen3.6-35b-q4');assert.ok(model,'Existing configured model unavailable');
const snapshots:any[]=[];
for(const host of ['piweb','piscord']){
 let payload:any;
 const audit=(pi:any)=>pi.on('before_provider_request',(e:any)=>{payload=structuredClone(e.payload);throw new Error('SERIALIZATION_ONLY_NO_INFERENCE');});
 const loader=new DefaultResourceLoader({cwd:root,agentDir:root,extensionFactories:[createChannelFinanceExtension({host,journal,stateDir:join(root,'state'),backend,bridge:pythonBridge(journal),accounts:hledgerAccounts(journal),today:()=> '2026-10-09'}),audit],extensionsOverride:base=>({...base,extensions:base.extensions.filter(e=>e.path.startsWith('<inline:')),errors:[]}),agentsFilesOverride:()=>({agentsFiles:[]}),skillsOverride:()=>({skills:[],diagnostics:[]}),systemPromptOverride:()=> 'You are a careful assistant. Financial requests use finance_handoff; success requires an actual receipt.'});await loader.reload();
 const {session,extensionsResult}=await createAgentSession({cwd:root,agentDir:root,resourceLoader:loader,modelRuntime:runtime,model,sessionManager:SessionManager.inMemory(root),settingsManager:SettingsManager.inMemory({retry:{enabled:false}})});assert.equal(extensionsResult.errors.length,0);
 // Discord's financial input is handled without inference; ordinary chat
 // serializes its identical public interface. Web exercises active fast FSM.
 try{await session.prompt(host==='piweb'?'午餐120元':'你好');await session.waitForIdle();}catch{}
 assert.ok(payload,'Provider serialization not reached: '+JSON.stringify(session.messages));
 const prefix={system:payload.messages[0],tools:payload.tools};snapshots.push({host,prefix,hash:hash(JSON.stringify(prefix)),providerOptions:Object.fromEntries(Object.entries(payload).filter(([k])=>!['messages','tools'].includes(k)))});
 session.dispose();
}
assert.deepEqual(snapshots[0].prefix,snapshots[1].prefix);assert.equal(hash(await readFile(production)),before);
await writeFile(join(root,'evidence.json'),JSON.stringify({mode:'actual Pi SDK serialization; inference deliberately stopped; no measured KV hit-rate',productionUnchanged:true,model:model.id,snapshots},null,2));
console.log(JSON.stringify({artifacts:root,prefixHash:snapshots[0].hash,sameSystemAndTools:true,productionUnchanged:true}));
