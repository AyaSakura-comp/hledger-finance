// Real Pi SDK + configured local Qwen. Only synthetic, fixed temporary journals.
import { createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager, SettingsManager } from '@earendil-works/pi-coding-agent';
import { mkdtemp, mkdir, readFile, writeFile, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { createFinanceExtension } from './index.ts';
import { pythonBackend, localDate } from './core.ts';
import { pythonBridge, type Request } from './workflows.ts';
import { execute } from './full-eval-support.ts';
const root=await mkdtemp(join(tmpdir(),'finance-workflow-qwen-'));
const production='/home/chihmin/finance/main.journal';
const hash=async()=>createHash('sha256').update(await readFile(production)).digest('hex');
const before=await hash();
const runtime=await ModelRuntime.create({modelsPath:'/home/chihmin/.pi/agent/models.json'});
const model=runtime.getModel('local-llama','qwen3.6-35b-q4');if(!model)throw new Error('Configured Qwen not found');
const today=localDate();
type Case={id:string;prompt:string;operation?:string;rows?:{kind:string;amount:string;debit:string;credit:string;currency?:string;date?:string;debit_amount?:string;debit_currency?:string}[];seed?:any[];partial?:boolean;chart?:boolean;source?:boolean;backup?:boolean;image?:boolean;pdf?:boolean;contains?:string};
const cases:Case[]=[
 {id:'receipt-image',image:true,prompt:'【財務】把附上的收據圖片之已付款總額記帳，付款assets:cash，支出expenses:food:dining。不可把小計、稅額、總額重複記入。',operation:'ingest',rows:[{kind:'expense',amount:'189',currency:'TWD',date:'2026-09-10',debit:'expenses:food:dining',credit:'assets:cash'}]},
 {id:'receipt-pdf',pdf:true,prompt:'【財務】擷取PDF收據 SOURCEPATH 的文字，記錄最終已付款總額一次，付款assets:cash，支出expenses:food:dining。',operation:'ingest',rows:[{kind:'expense',amount:'189',currency:'TWD',date:'2026-09-10',debit:'expenses:food:dining',credit:'assets:cash'}]},
 {id:'batch',prompt:'【財務】記帳三筆：午餐150元、咖啡95元、YouBike15元。日期今天，台幣，付款都用assets:cash。午餐與咖啡分類expenses:food:dining；YouBike分類expenses:transport:transit。',operation:'ingest',rows:[{kind:'expense',amount:'150',debit:'expenses:food:dining',credit:'assets:cash'},{kind:'expense',amount:'95',debit:'expenses:food:dining',credit:'assets:cash'},{kind:'expense',amount:'15',debit:'expenses:transport:transit',credit:'assets:cash'}]},
 {id:'income',prompt:'【財務】今天收到薪水50000 TWD，存入assets:cash，收入科目income:salary。記帳。',operation:'ingest',rows:[{kind:'income',amount:'50000',debit:'assets:cash',credit:'income:salary'}]},
 {id:'exchange',prompt:'【財務】今天從assets:bank:twd轉出3200 TWD，收到100 USD到assets:bank:usd。不是支出，記錄跨幣轉帳。',operation:'ingest',rows:[{kind:'transfer',amount:'3200',currency:'TWD',debit:'assets:bank:usd',credit:'assets:bank:twd',debit_amount:'100',debit_currency:'USD'}]},
 {id:'foreign',prompt:'【財務】今天午餐1200 JPY，從assets:bank:jpy付款，支出科目expenses:food:dining。記帳，不得改成台幣或現金。',operation:'ingest',rows:[{kind:'expense',amount:'1200',currency:'JPY',debit:'expenses:food:dining',credit:'assets:bank:jpy'}]},
 {id:'refund-opening',prompt:'【財務】今天記兩筆TWD：午餐退款20元，debit assets:cash、credit expenses:food:dining；期初現金1000元，debit assets:cash、credit equity:opening-balances。',operation:'ingest',rows:[{kind:'refund',amount:'20',debit:'assets:cash',credit:'expenses:food:dining'},{kind:'opening-balance',amount:'1000',debit:'assets:cash',credit:'equity:opening-balances'}]},
 {id:'installment',prompt:'【財務】手機分期：2026-10-31開始，共900 TWD，3期，debit expenses:shopping，credit liabilities:credit-card，每期費用0元。記錄完整分期。',operation:'installment'},
 {id:'loan',prompt:'【財務】記錄貸款排程：loan_id qa-loan，start 2026-10-03，description Loan payment，principal 500，annual_rate 0，payment 250，paid_periods 0，remaining_periods 2，currency TWD，liability liabilities:loans:personal，interest_account expenses:interest:loans，payment_account assets:bank，rounding 1。所有資料已核對。',operation:'loan'},
 {id:'historical-csv',source:true,prompt:'【財務】歷史匯入 SOURCEPATH。銀行原始支出負號只代表流出，請轉成正數 magnitude；明確kind/account/currency/ID全在CSV。已獨立核對來源2筆：expense:TWD總額150，income:TWD總額50000。',operation:'historical'},
 {id:'query-balance',prompt:'【財務】查詢所有帳戶餘額，直接提供帳本報表。',operation:'query',contains:'850',seed:[{kind:'opening-balance',description:'Initial',amount:'1000',debit:'assets:cash',credit:'equity:opening-balances'},{kind:'expense',description:'Lunch',amount:'150',debit:'expenses:food:dining',credit:'assets:cash'}]},
 {id:'undo',prompt:'【財務】撤銷最近的一次記帳操作，用undo保留歷史，不要刪Git歷史。',operation:'undo',seed:[{kind:'expense',description:'Lunch',amount:'150',debit:'expenses:food:dining',credit:'assets:cash'}]},
 {id:'stats',prompt:'【財務】統計全部帳本收支，逐幣別列出income/expense/net。',operation:'stats',seed:[{kind:'expense',description:'Lunch',amount:'150',debit:'expenses:food:dining',credit:'assets:cash'},{kind:'income',description:'Salary',amount:'50000',debit:'assets:cash',credit:'income:salary'}]},
 {id:'chart',prompt:'【財務】畫全部帳本財務儀表板，每個幣別各一張，傳送圖片。',operation:'visualize',chart:true,seed:[{kind:'expense',description:'Lunch',amount:'150',debit:'expenses:food:dining',credit:'assets:cash',currency:'TWD'},{kind:'expense',description:'Coffee',amount:'10',debit:'expenses:food:dining',credit:'assets:usd',currency:'USD'}]},
 {id:'delete-subset',prompt:'【財務】先搜尋刪除描述含Lunch的交易，列出所有候選給我確認。',operation:'delete',partial:true,seed:[{kind:'expense',description:'Lunch',amount:'800',debit:'expenses:food:dining',credit:'assets:cash'},{kind:'refund',description:'Lunch refund',amount:'50',debit:'assets:cash',credit:'expenses:food:dining'}]},
 {id:'rules',prompt:'【財務】新增未來分類規則：「毛孩市集」一律expenses:pets:supplies。保留原有其他規則，不要改歷史帳。',operation:'rules'},
 {id:'backup',prompt:'【財務】把整份測試帳本含Git歷史及規則備份到qa:，這是已設定的本機測試alias，不是雲端。',operation:'backup',backup:true},
];
const results:any[]=[];
for(const c of cases.filter(c=>!process.env.WORKFLOW_CASES||process.env.WORKFLOW_CASES.split(',').includes(c.id))){
 const cwd=join(root,c.id);await mkdir(join(cwd,'book'),{recursive:true});const journal=join(cwd,'book/main.journal');const backend=pythonBackend({journal});await backend(['init']);
 if(c.seed)await backend(['ingest-json','-'],{defaults:{date:today,currency:'TWD',tags:['source:fuzzy-text']},transactions:c.seed.map((r,n)=>({...r,import_id:'seed-'+n}))});
 if(c.id==='rules')await writeFile(join(cwd,'book/classification-rules.json'),JSON.stringify({rules:[{pattern:'原規則',account:'expenses:shopping'}]}));
 const source=join(cwd,c.pdf?'receipt.pdf':'source.csv');
 if(c.pdf){
  const lines=['QA TEST RESTAURANT','Date: 2026-09-10','Invoice: RECEIPTQA-PDF','Subtotal: TWD 180','Tax: TWD 9','TOTAL PAID: TWD 189','Payment: CASH'];
  const stream='BT /F1 14 Tf 50 750 Td 24 TL\n'+lines.map(l=>'('+l+') Tj T*\n').join('')+'ET\n';
  const objects=['<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Kids [3 0 R] /Count 1 >>','<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>','<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',`<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}endstream`];
  let pdf='%PDF-1.4\n';const offsets=[0];for(let i=0;i<objects.length;i++){offsets.push(Buffer.byteLength(pdf));pdf+=`${i+1} 0 obj\n${objects[i]}\nendobj\n`;}
  const xref=Buffer.byteLength(pdf);pdf+='xref\n0 6\n0000000000 65535 f \n'+offsets.slice(1).map(o=>String(o).padStart(10,'0')+' 00000 n \n').join('')+`trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;await writeFile(source,pdf);
 }
 const imagePath=join(cwd,'receipt.png');if(c.image){const image=await execute('python3',['-c',`from PIL import Image,ImageDraw,ImageFont\nim=Image.new('RGB',(1000,720),'white');d=ImageDraw.Draw(im);f=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',32)\nfor i,line in enumerate(['QA TEST RESTAURANT','Date: 2026-09-10','Invoice: RECEIPTQAVISION01','Subtotal: TWD 180','Tax: TWD 9','TOTAL PAID: TWD 189','Payment: CASH']):d.text((40,40+i*85),line,fill='black',font=f)\nim.save('${imagePath}')`],{cwd});if(image.code)throw new Error(image.stderr);}
 if(c.source)await writeFile(source,`id,date,description,kind,amount,currency,debit,credit\nsource-1,${today},Lunch,expense,-150,TWD,expenses:food:dining,assets:cash\nsource-2,${today},Salary,income,50000,TWD,assets:cash,income:salary\n`);
 const config=join(cwd,'rclone.conf');await writeFile(config,`[qa]\ntype = alias\nremote = ${join(cwd,'backup')}\n`);
 const bridge=pythonBridge(journal,{...process.env,RCLONE_CONFIG:config});const calls:any[]=[];
 const wrapped=async(r:any,o:any)=>{const reply=await bridge(r,o);calls.push({request:structuredClone(r),mode:o.mode,reply:structuredClone(reply)});return reply;};
 const loader=new DefaultResourceLoader({cwd,agentDir:cwd,extensionFactories:[createFinanceExtension({journal,bridge:wrapped}),pi=>{pi.on('before_provider_request',async event=>{const p=event.payload as any;await writeFile(join(cwd,'request-tools.json'),JSON.stringify({tools:p.tools,tool_choice:p.tool_choice},null,2));});}],extensionsOverride:base=>({...base,extensions:base.extensions.filter(e=>e.path.startsWith('<inline:')),errors:[]}),agentsFilesOverride:()=>({agentsFiles:[]}),skillsOverride:()=>({skills:[],diagnostics:[]}),systemPromptOverride:()=>`You are a careful finance assistant. The current date is ${today} Asia/Taipei. Use real tool calls. Data are synthetic. Never invent user facts. Use only the active finance tools. Do not execute mutations without host user confirmation.`});
 await loader.reload();const {session,extensionsResult}=await createAgentSession({cwd,agentDir:cwd,resourceLoader:loader,modelRuntime:runtime,model,thinkingLevel:'off',tools:['finance_finish','finance_inspect','finance_source','finance_action','finance_question'],sessionManager:SessionManager.inMemory(cwd),settingsManager:SettingsManager.inMemory({})});
 if(extensionsResult.errors.length)throw new Error(JSON.stringify(extensionsResult.errors));
 let error='',timeout=false;const timer=setTimeout(()=>{timeout=true;session.abort();},90000);const start=performance.now();
 try{
  await session.prompt(c.prompt.replace('SOURCEPATH',source),c.image?{images:[{type:'image',mimeType:'image/png',data:(await readFile(imagePath)).toString('base64')}]}:undefined);
  // agent_settled may trigger one follow-up turn (pseudo tool-call nudge).
  await new Promise(r=>setTimeout(r,200));await session.waitForIdle();
  const custom=session.messages.filter((m:any)=>m.role==='custom'&&m.customType==='finance-workflow-receipt');
  const text=custom.map((m:any)=>typeof m.content==='string'?m.content:'').join('\n');
  const token=text.match(/確認 ([a-f0-9-]{36})/i)?.[1]??(/回覆「確認」/.test(text)?'short':undefined);
  const proposed=[...calls].reverse().find(x=>x.mode==='preview'&&x.request.operation===c.operation);
  if(!proposed)throw new Error('Missing actual requested-operation preview/tool call');
  if(proposed.reply.code!==0&&!(c.operation==='delete'&&proposed.reply.code===3))throw new Error('Backend preview failed: '+proposed.reply.stderr);
  if(c.rows){const data=proposed.request.data;const rows=data.transactions;if(rows?.length!==c.rows.length)throw new Error('Wrong proposed row count');const norm=(k:string,v:any)=>['amount','debit_amount'].includes(k)?String(v).replace(/(\.\d*?)0+$/,'$1').replace(/\.$/,''):v;const unused=rows.map((r:any)=>({...data.defaults,...r}));
   // Row order is not a fact; match each expected row to a distinct proposed row.
   for(let i=0;i<c.rows.length;i++){const want={date:today,currency:'TWD',...c.rows[i]};const at=unused.findIndex((a:any)=>Object.entries(want).every(([k,v])=>norm(k,a[k])===v));if(at<0)throw new Error(`Wrong proposed immutable facts for expected row ${i}: ${JSON.stringify(want)} not in ${JSON.stringify(rows)}`);unused.splice(at,1);}}
  if(c.partial){const old=await readFile(journal,'utf8');await session.prompt('只確認刪除Lunch800元，Lunch refund不要刪');if(await readFile(journal,'utf8')!==old)throw new Error('Partial confirmation changed journal');if(calls.some(x=>x.mode==='execute'))throw new Error('Partial confirmation executed deletion');}
  else if(!token&&calls.some(x=>x.mode==='execute'&&x.reply.code===0)){} // plain typed records are written at once
  else if(token){await session.prompt('確認');const executed=[...calls].reverse().find(x=>x.mode==='execute');if(!executed||executed.reply.code!==0)throw new Error('Host confirmation did not successfully execute');}
  else if(!['stats','visualize','query','native','settings','audit','check'].includes(c.operation!))throw new Error('Missing host authorization preview token');
  if(c.contains&&!proposed.reply.stdout.includes(c.contains))throw new Error('Missing expected report value '+c.contains);
  if(c.id==='undo'&&(await readFile(journal,'utf8')).includes('Lunch'))throw new Error('Undo did not remove last action');
  if(c.chart){if(proposed.reply.images?.length!==2)throw new Error('Expected two currency PNGs');for(const path of proposed.reply.images)if((await stat(path)).size===0)throw new Error('Empty PNG');}
  if(c.backup&&await readFile(join(cwd,'backup/main.journal'),'utf8')!==await readFile(journal,'utf8'))throw new Error('Backup bytes differ');
  if(c.id==='rules'){const v=JSON.parse(await readFile(join(cwd,'book/classification-rules.json'),'utf8'));if(v.rules.length!==2||!v.rules.some((r:any)=>r.pattern==='原規則')||!v.rules.some((r:any)=>r.pattern==='毛孩市集'&&r.account==='expenses:pets:supplies'))throw new Error('Rules lost existing preference or wrong binding');}
 }catch(e){error=String(e);}finally{clearTimeout(timer);}
 const result={id:c.id,pass:!error&&!timeout,error,timeout,seconds:(performance.now()-start)/1000,calls:calls.length};results.push(result);
 await writeFile(join(cwd,'evidence.json'),JSON.stringify({result,messages:session.messages,calls,journal:await readFile(journal,'utf8')},null,2));session.dispose();
 if(await hash()!==before)throw new Error('Production journal changed; stopped');
 await writeFile(join(root,'results.json'),JSON.stringify({provider:model.provider,model:model.id,productionUnchanged:true,results},null,2));console.log(JSON.stringify(result));
}
console.log('Artifacts: '+root);
