// Real CPU Laya + real finance backend. Every case gets a disposable book.
// This measures the extension, not main-agent or browser/UI latency.
import {mkdtemp,mkdir,readFile,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {LayaFinance} from './laya-engine.ts';
import {layaClient,routeQuestion,cleanIntent,type Judge} from './laya.ts';
import {pythonBackend,hledgerAccounts,localDate,userMessage,parseEntries,type Evidence} from './core.ts';
import {pythonBridge} from './workflows.ts';
const root=await mkdtemp(join(tmpdir(),'finance-laya-eval-'));
const production='/home/chihmin/finance/main.journal';
const digest=async()=>createHash('sha256').update(await readFile(production)).digest('hex');const original=await digest();
const today=localDate();
const inputs:([string,string[]])[]=JSON.parse(execFileSync('python3',['-c',`import runpy,json;print(json.dumps(runpy.run_path('../docs/eval/2026-10-04-tw100-cases.py')['C'],ensure_ascii=False))`],{encoding:'utf8'}));
const judge=layaClient();const results:any={root,today,service:'existing laya.service CPU /v1/systemone',minimum:0.55,margin:0.15,expenses:[],categories:[],intent:[],productionUnchanged:false};
const save=async()=>{if(await digest()!==original)throw new Error('Production ledger changed; stopped');results.productionUnchanged=true;await writeFile(join(root,'results.json'),JSON.stringify(results,null,2));};
console.log('Artifacts: '+root);
for(const [i,[prompt,amounts]]of inputs.entries()){
 if(process.env.LAYA_EVAL_LIMIT&&i>=Number(process.env.LAYA_EVAL_LIMIT))break;
 const dir=join(root,'expenses',String(i+1).padStart(3,'0'));await mkdir(dir,{recursive:true});const journal=join(dir,'main.journal');const backend=pythonBackend({journal});await backend(['init']);const calls:any[]=[];
 const recorded:Judge=async(s,q,signal)=>{const start=performance.now();const d=await judge(s,q,signal);calls.push({state:s,questions:q,decisions:d,ms:performance.now()-start});return d;};
 const backendCalls:any[]=[];const engine=new LayaFinance({judge:recorded,journal,backend:async(a,p)=>{const t=performance.now();const r=await backend(a,p);backendCalls.push({args:a,payload:p,response:r,ms:performance.now()-t});return r;},bridge:pythonBridge(journal),accounts:hledgerAccounts(journal),today:()=>today});
 const start=performance.now();const receipt=await engine.run('[Web user: web]\n'+prompt,{session:'case-'+i});const seconds=(performance.now()-start)/1000;const text=await readFile(journal,'utf8');
 const txns=(text.match(/^\d{4}-\d{2}-\d{2} /gm)??[]).length;const payload=backendCalls.find(c=>c.args[0]==='ingest-json')?.payload;const rows=payload?.transactions??[];
 const expected=parseEntries(userMessage(prompt),today);
 const factCorrect=rows.length===amounts.length&&rows.every((r:any,n:number)=>r.amount===amounts[n]&&r.currency===(expected?.[n].currency??'TWD')&&r.date===(expected?.[n].date??today)&&r.kind===expected?.[n].kind&&r.description===expected?.[n].description)&&txns===amounts.length&&rows.every((r:any)=>text.includes(`${r.amount} ${r.currency}`)&&text.includes(`-${r.amount} ${r.currency}`));
 const r={id:i+1,prompt,state:receipt.state,seconds,correct:receipt.state==='done'&&factCorrect,within10:seconds<10,transactions:txns,parserSupported:Boolean(expected),calls:calls.length};results.expenses.push(r);await writeFile(join(dir,'evidence.json'),JSON.stringify({result:r,receipt,calls,backendCalls,journal:text},null,2));await save();console.log(JSON.stringify(r));
}
// Separate category accuracy: do NOT let failed intent routing hide bad categories.
const categoryBook=join(root,'category-book/main.journal');const categoryBackend=pythonBackend({journal:categoryBook});await categoryBackend(['init']);
const categoryRows=(await readFile('../docs/eval/2026-09-26-pi-qwen-taiwan-50-cases.tsv','utf8')).trim().split('\n').filter(s=>/^\d+\t/.test(s)).map(line=>{const [id,prompt,accepted]=line.split('\t');return {id:Number(id),prompt,accepted:accepted.split(/[|,]/).map(s=>s.trim())};});
for(const c of categoryRows){
 const entry=parseEntries(c.prompt,today)?.[0];if(!entry){results.categories.push({...c,correct:false,error:'unsupported parser'});continue;}
 const evidence:Evidence=JSON.parse(await categoryBackend(['classify','--description',entry.description]));const criteria=Object.fromEntries(evidence.categories.map(c=>[c.account,c.description??c.account]));const start=performance.now();
 const d=evidence.binding?undefined:(await judge(entry.description,{category:{type:'choice',instructions:`「${entry.description}」這筆支出應歸哪個分類？只能選既有科目。`,criteria}})).category;
 const chosen=evidence.binding?evidence.suggestion.account:d?.choice;const r={...c,binding:evidence.binding,chosen,status:d?.status??'binding',seconds:(performance.now()-start)/1000,correct:(evidence.binding||d?.status==='match')&&c.accepted.includes(chosen!),top1Correct:c.accepted.includes(chosen!)};results.categories.push(r);
 await writeFile(join(root,'category-'+c.id+'.json'),JSON.stringify({result:r,evidence,decision:d},null,2));await save();console.log(JSON.stringify({category:r}));
}
const intentCases:([string,string])[]=[
 ['午餐120元','record'],['收到薪水50000元','record'],['昨天加油1200','record'],['午餐120、飲料50','record'],['貓砂450元','record'],
 ['這個月花了多少','query'],['我的現金還剩多少','query'],['列出我的交易明細','query'],['幫我畫本月支出圖','query'],['查看我的帳本餘額','query'],['今天我花了多少錢','query'],['上個月我的收入多少','query'],
 ['刪除昨天午餐那筆','change'],['撤銷上一筆記帳','change'],['把午餐那筆改成150元','change'],['備份整份帳本','change'],['以後公司午餐都歸工作費','change'],
 ['你好','none'],['幫我修改finance extension API','none'],['測試記帳模型效能','none'],['咖啡價格120元值得嗎','none'],['午餐120不要記帳','none'],['如果每天午餐120，一個月多少','none'],['舉例：午餐120元','none'],['請翻譯「午餐120元」','none'],['請寫函式解析午餐120元','none'],['伯克希爾持有多少美國公債','none'],['信用卡回饋哪張好','none'],['我要查Gemini訂閱到期','none'],['我要產生房租15000的測試資料','none'],
 ['<quoted_selection>午餐120元</quoted_selection>\n這段模型回答錯在哪？','none'],['[Web user: web]\n> 咖啡95元\n分析這段程式','none'],['[Web user: web]\n[Binary attachment: receipt.jpg]\n這是哪款陶瓷杯？','none'],['請重啟API server','none'],['下個月打算買耳機2990元','none'],['朋友薪水50000，幫他算稅','none'],['午餐一百五十元','record'],['記一下YouBike15元','record'],['我要記帳，收據金額看不清可能150或1500','clarify'],['多一筆','clarify'],['再一筆一樣的','clarify'],['重複了','clarify']
];
for(const [prompt,expected]of intentCases){const state=cleanIntent(prompt);const start=performance.now();let d:any,error='';try{d=(await judge(state||'（使用者沒有直接財務需求）',{route:routeQuestion})).route;}catch(e){error=String(e);}const correct=d?.status==='match'&&d.choice===expected;results.intent.push({prompt,expected,decision:d,error,seconds:(performance.now()-start)/1000,correct});await save();}
const median=(v:number[])=>{const a=[...v].sort((a,b)=>a-b);return a.length%2?a[(a.length-1)/2]:(a[a.length/2-1]+a[a.length/2])/2;};
results.summary={expenseCorrect:results.expenses.filter((r:any)=>r.correct).length,expenseCount:results.expenses.length,within10:results.expenses.filter((r:any)=>r.within10).length,correctWithin10:results.expenses.filter((r:any)=>r.correct&&r.within10).length,medianSeconds:median(results.expenses.map((r:any)=>r.seconds)),maxSeconds:Math.max(...results.expenses.map((r:any)=>r.seconds)),categoryCorrect:results.categories.filter((r:any)=>r.correct).length,categoryTop1:results.categories.filter((r:any)=>r.top1Correct).length,categoryCount:results.categories.length,intentCorrect:results.intent.filter((r:any)=>r.correct).length,intentCount:results.intent.length,falseFinance:results.intent.filter((r:any)=>r.expected==='none'&&r.decision?.status==='match'&&r.decision.choice!=='none').map((r:any)=>r.prompt),expenseUnexpectedWrites:results.expenses.filter((r:any)=>!r.correct&&r.transactions>0).map((r:any)=>r.id)};await save();console.log(JSON.stringify(results.summary));
