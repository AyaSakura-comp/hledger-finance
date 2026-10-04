import { randomUUID, createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { localDate } from './core.ts';
import type { PendingStore } from './pending-store.ts';
export const readonlyOperations=['query','stats','visualize','check','audit','classify','native','settings'] as const;
export const mutationOperations=['ingest','amend','historical','installment','loan','delete','undo','rules','backup'] as const;
export type Request={operation:string;data:Record<string,any>};
export type Reply={code:number;stdout:string;stderr:string;snapshot?:string;delete_token?:string;images?:string[]};
export type Bridge=(request:Request,options:{mode:'preview'|'execute';expected_snapshot?:string;delete_token?:string;signal?:AbortSignal})=>Promise<Reply>;
export type WorkflowState='idle'|'collecting'|'preparing'|'awaiting_confirmation'|'executing'|'done'|'failed'|'cancelled'|'clarifying';
export type Receipt={text:string;state:WorkflowState};

export class Workflow {
 state:WorkflowState='idle';
 private pending?:{request:Request;reply:Reply;token:string;expires:number};
 private inspecting=0;private repairable=false;private input='';private id='';private source:'text'|'image'|'file'|'pdf'='text';
 constructor(private bridge:Bridge,private today:()=>string=localDate,private clock:()=>number=Date.now,private store?:PendingStore,private journal=''){}
 get confirmation(){return this.pending?.token;}
 begin(input:string,source:'text'|'image'|'file'|'pdf'='text',stableSourceDigest?:string){
  if(['preparing','executing','awaiting_confirmation'].includes(this.state))throw new Error('Existing pending workflow');
  this.input=input;this.id=stableSourceDigest?`src-${stableSourceDigest}`:`flow-${randomUUID()}`;this.source=source;this.pending=undefined;this.repairable=false;this.state='collecting';
 }
 resumeDraft(){if(this.state!=='failed'||!this.repairable)return false;this.repairable=false;this.state='collecting';return true;}
 cancel(){if(this.state==='executing'||this.state==='preparing')throw new Error('Busy workflow; outcome may be uncertain');if(this.pending)this.store?.drop(this.pending.token);this.pending=undefined;this.state='cancelled';}
 clarify(question:string):Receipt{
  if(this.state!=='collecting'||!question.trim()||question.length>500)throw new Error('Invalid clarification');
  this.state='clarifying';return {text:question,state:this.state};
 }
 async inspect(request:Request,signal?:AbortSignal):Promise<Reply>{
  if(this.state!=='collecting'||!['classify','native','settings'].includes(request.operation))throw new Error('Inspection refused');
  if(signal?.aborted)throw new Error('Aborted');
  // Read-only and parallel-safe: lookups never change the draft; a proposal
  // waits for in-flight lookups instead (see propose). Failures keep collecting.
  this.inspecting++;
  try{const r=await this.bridge(structuredClone(request),{mode:'preview',signal});if(r.code)throw new Error(r.stderr||r.stdout);return r;}
  finally{this.inspecting--;}
 }
 async propose(input:Request,signal?:AbortSignal):Promise<Receipt>{
  if(this.state!=='collecting'||this.inspecting)throw new Error('No collecting workflow; concurrent/replayed proposal refused');
  if(signal?.aborted)throw new Error('Aborted');
  if(![...readonlyOperations,...mutationOperations].includes(input.operation as any))throw new Error('Unknown finance operation');
  const request:Request=structuredClone(input);
  // Presentation-only options: map obvious synonyms and drop unsupported
  // formats instead of failing (they never change which records are used).
  if(['query','stats','visualize','delete'].includes(request.operation)&&request.data&&typeof request.data==='object'){
   const interval:Record<string,string>={day:'daily',daily:'daily',week:'weekly',weekly:'weekly',month:'monthly',monthly:'monthly',quarter:'quarterly',quarterly:'quarterly',year:'yearly',yearly:'yearly'};
   if(typeof request.data.interval==='string'){const v=interval[request.data.interval.toLowerCase()];if(v)request.data.interval=v;else delete request.data.interval;}
   if('format' in request.data&&!['txt','csv','json','html'].includes(String(request.data.format)))delete request.data.format;
  }
  if(request.operation==='visualize'&&/每(?:個|種)?幣別|逐幣別|各幣別|every currency|each currency/i.test(this.input))delete request.data.currency;
  if(['ingest','amend'].includes(request.operation)){
   if(!Array.isArray(request.data.transactions)||!request.data.transactions.length)throw new Error('Transaction batch required');
   // Ledger convention is lowercase accounts and ISO currencies. Normalize
   // spellings the user did not type verbatim (Assets:Cash, Expense:Food, NTD).
   const account=(a:any)=>{if(typeof a!=='string'||this.input.includes(a))return a;const l=a.toLowerCase().replace(/^expense:/,'expenses:').replace(/^asset:/,'assets:').replace(/^liability:/,'liabilities:');return l;};
   const currency=(c:any)=>typeof c==='string'&&/^(ntd|nt\$?|twd)$/i.test(c.trim())?'TWD':c;
   for(const target of [request.data.defaults??{},...request.data.transactions]){
    for(const k of ['debit','credit','fee_account'])if(k in target)target[k]=account(target[k]);
    for(const k of ['currency','debit_currency'])if(k in target)target[k]=currency(target[k]);
   }
   const explicit=this.input.normalize('NFKC').match(/從\s*((?:assets|liabilities):[A-Za-z0-9_:.-]+)\s*轉出\s*([0-9,.]+)\s*([A-Z]{3})[\s\S]*?收到\s*([0-9,.]+)\s*([A-Z]{3})\s*到\s*((?:assets|liabilities):[A-Za-z0-9_:.-]+)/i);
   if(explicit){
    const facts={kind:'transfer',credit:explicit[1],amount:explicit[2].replaceAll(',',''),currency:explicit[3].toUpperCase(),debit_amount:explicit[4].replaceAll(',',''),debit_currency:explicit[5].toUpperCase(),debit:explicit[6]};
    // The user's explicit wording is authoritative; the host maps it to
    // canonical sides instead of letting the model guess debit/credit.
    const transfers=request.data.transactions.filter((r:any)=>(r.kind??request.data.defaults?.kind)==='transfer');
    if(transfers.length!==1)throw new Error('Explicit cross-currency transfer requires exactly one transfer row: '+JSON.stringify(facts));
    for(const key of ['credit_amount','credit_currency'])delete transfers[0][key];
    Object.assign(transfers[0],facts);
    const duplicate=request.data.transactions.find((r:any)=>r!==transfers[0]&&String(r.amount)===facts.debit_amount&&String(r.currency??request.data.defaults?.currency)===facts.debit_currency&&(r.debit===facts.debit||r.credit===facts.debit));
    if(duplicate)throw new Error(`The transfer row already records receiving ${facts.debit_amount} ${facts.debit_currency} into ${facts.debit}; remove the separate ${duplicate.kind} row (it would double count)`);
   }
   // Explicit "debit X、credit Y" pairs in the user's words are facts: every
   // pair must appear on some proposed row (stops e.g. a refund being booked
   // as an expense with the sides swapped).
   const pairs=[...this.input.normalize('NFKC').matchAll(/debit\s*[:：]?\s*((?:assets|liabilities|expenses|income|equity):[A-Za-z0-9_:.-]+)\s*[、,，;；]?\s*credit\s*[:：]?\s*((?:assets|liabilities|expenses|income|equity):[A-Za-z0-9_:.-]+)/gi)];
   for(const [,debit,credit] of pairs){
    const rows=request.data.transactions.map((r:any)=>({debit:r.debit??request.data.defaults?.debit,credit:r.credit??request.data.defaults?.credit}));
    if(!rows.some((r:any)=>String(r.debit).toLowerCase()===debit.toLowerCase()&&String(r.credit).toLowerCase()===credit.toLowerCase()))
     throw new Error(`The user explicitly specified debit ${debit} / credit ${credit}; one row must use exactly those sides (and the matching kind, e.g. refund for a refund)`);
   }
   // A single explicitly named payment account is a user fact: apply it to
   // expense rows the model left without a credit side.
   const paid=[...new Set([...this.input.normalize('NFKC').matchAll(/付款(?:都用|帳戶|方式)?(?:用|是|為|:|：)?\s*((?:assets|liabilities):[A-Za-z0-9_:.-]+)/g)].map(m=>m[1]))];
   if(paid.length===1)for(const r of request.data.transactions)if((r.kind??request.data.defaults?.kind)==='expense'&&!r.credit&&!request.data.defaults?.credit)r.credit=paid[0];
   // One receipt photo/PDF is one payment: line items plus the total would
   // double count. Splitting is only allowed when the user asks for it.
   if(['image','pdf'].includes(this.source)&&request.data.transactions.length>1&&!/拆|分開|逐項|每一項|每項|明細|split|itemi[sz]e/i.test(this.input))
    throw new Error('One receipt is one payment: record a single row with the final paid total (after discounts), not line items, subtotal or tax');
   if(['image','pdf'].includes(this.source)){
    const seen=new Set<string>();
    for(const r of request.data.transactions){const key=JSON.stringify(Object.entries(r).filter(([k])=>!['import_id','tags'].includes(k)).sort());if(seen.has(key))throw new Error('Identical duplicate rows from one receipt source; record the final paid total once');seen.add(key);}
   }
   const defaults={...(request.data.defaults??{})};
   const inherited=Array.isArray(defaults.tags)?defaults.tags:[];delete defaults.tags;
   // TWD is only a default when the user named no other currency; a missing
   // currency must never turn "1200 JPY" into TWD.
   const named=[...new Set([...this.input.normalize('NFKC').toUpperCase().matchAll(/\b(USD|JPY|EUR|CNY|HKD|GBP|KRW|SGD|AUD|CAD|THB|TWD)\b|(美元|美金)|(日圓|日幣|日元)|(人民幣)|(港幣)|(歐元)/g)].map(m=>m[1]??(m[2]?'USD':m[3]?'JPY':m[4]?'CNY':m[5]?'HKD':'EUR')))].filter(c=>c!=='TWD');
   const fallback=named.length===1?named[0]:named.length?undefined:'TWD';
   if(!defaults.currency&&!fallback&&request.data.transactions.some((r:any)=>!r.currency))throw new Error(`The user mentioned several currencies (${named.join(', ')}); give every row an explicit currency`);
   request.data.defaults={...defaults,date:defaults.date??this.today(),currency:defaults.currency??fallback};
   request.data.transactions=request.data.transactions.map((row:any,index:number)=>{
    const r={...row};
    // A receipt image/PDF is identified by its content digest, so re-sending
    // the same file is skipped as a duplicate. Model ids would defeat that.
    // Only rows read from a CSV/JSON source keep their own ids; otherwise a
    // model could reuse an id from conversation history and the row would be
    // silently skipped as a duplicate.
    if(this.source!=='file')r.import_id=`${this.id}-${index}`;
    // kind fixes posting direction; an exactly reversed pair is the same
    // fact written backwards, so canonicalize instead of failing.
    const family=(a:any)=>String(a??'').split(':')[0],side=(a:any)=>['assets','liabilities'].includes(family(a));
    const kind=r.kind??defaults.kind,debit=r.debit??defaults.debit,credit=r.credit??defaults.credit;
    const reversed=kind==='expense'?family(credit)==='expenses'&&side(debit):kind==='income'?family(debit)==='income'&&side(credit):kind==='refund'?family(debit)==='expenses'&&side(credit):false;
    // Never flip sides the user stated explicitly; a wrong kind must then be
    // fixed by the model (the backend rejects kind/side mismatches).
    const stated=pairs.some(([,d,c])=>String(debit).toLowerCase()===d.toLowerCase()&&String(credit).toLowerCase()===c.toLowerCase());
    if(reversed&&!stated){r.debit=credit;r.credit=debit;}
    if(!r.import_id)r.import_id=`${this.id}-${index}`;
    // Provenance is host-known: replace any model source tag and drop
    // non-canonical labels (they are metadata, never material facts).
    const sourceTag=this.source==='image'?'source:receipt-image':this.source==='pdf'?'source:pdf':this.source==='file'?'source:messy-csv':'source:fuzzy-text';
    const extra=[...inherited,...(Array.isArray(r.tags)?r.tags:[])].filter((t:any)=>typeof t==='string'&&!t.startsWith('source:')&&/^[A-Za-z][A-Za-z0-9_-]*:[\w.+/-]+$/.test(t));
    r.tags=[sourceTag,...new Set(extra)];
    if(!r.date&&!defaults.date)r.tags=[...(r.tags??[]),'inferred:date'];
    if(!r.currency&&!defaults.currency)r.tags=[...(r.tags??[]),'inferred:currency'];
    return r;
   });
  }
  this.repairable=false;this.state='preparing';
  try{
   if(['ingest','amend'].includes(request.operation))await this.checkExpenseAccounts(request,signal);
   const reply=await this.bridge(request,{mode:'preview',signal});
   const valid=reply.code===0||(request.operation==='delete'&&reply.code===3);
   if(!valid)throw new Error(reply.stderr||reply.stdout||'Backend preview failed');
   if(readonlyOperations.includes(request.operation as any)){
    this.state='done';return {text:(request.operation==='stats'?formatStats(reply.stdout):undefined)??(this.boundOutput(reply)||'沒有符合條件的紀錄。'),state:this.state};
   }
   // Never hand out a token for a change that writes nothing.
   if(['ingest','amend'].includes(request.operation)&&/Records ready: 0\b/.test(reply.stdout+'\n'+reply.stderr)){
    if(request.operation==='amend')throw new Error('Replacement would not be written (duplicate of an existing entry); nothing changed');
    this.state='done';return {text:'沒有新增：這些紀錄與帳本既有紀錄重複，未寫入。\n'+this.boundOutput(reply),state:this.state};
   }
   if(!reply.snapshot)throw new Error('No preview snapshot; authorization refused');
   if(['delete','amend'].includes(request.operation)&&!reply.delete_token)throw new Error('No backend deletion token');
   const token=randomUUID();this.pending={request,reply:structuredClone(reply),token,expires:this.clock()+600000};this.state='awaiting_confirmation';
   // Full candidate preview is mandatory. Do not offer confirmation for a
   // truncated preview: narrow the batch/filter instead.
   const preview=JSON.stringify(request,null,2)+'\n'+reply.stdout+'\n'+reply.stderr;
   if(Buffer.byteLength(preview)>45000||preview.split('\n').length>1800)throw new Error('Preview too large to show completely; narrow scope');
   this.store?.save({token,request,reply:structuredClone(reply),expires:this.pending.expires,journal:this.journal});
   return {text:`尚未修改帳本／規則／備份。請核對完整 ${request.operation} 預覽（包含所有候選）。\n${preview}\n\n僅完整同意以上範圍時，在下一則訊息輸入：確認 ${token}\n只同意部分內容請取消後重新提出；取消：取消記帳。授權有效 10 分鐘。`,state:this.state};
  }catch(error){this.pending=undefined;this.repairable=!signal?.aborted;this.state='failed';throw error;}
 }
 async confirm(text:string,signal?:AbortSignal):Promise<Receipt|null>{
  const token=/^(?:確認|confirm) ([0-9a-f-]{36})$/.exec(text.trim())?.[1];
  if(['preparing','executing'].includes(this.state))return null;
  let authorized:{request:Request;reply:Reply;expires:number}|undefined;
  if(this.state==='awaiting_confirmation'&&this.pending){
   if(this.clock()>this.pending.expires){this.store?.drop(this.pending.token);this.pending=undefined;this.state='cancelled';return null;}
   if(token!==this.pending.token)return null;
   // The persisted copy is the single authority: if another process already
   // consumed it, this in-memory preview must not execute again.
   if(this.store&&!this.store.claim(token)){this.pending=undefined;this.state='cancelled';return null;}
   authorized=this.pending;
  }else if(token&&this.store){
   // Preview issued by another pi process (print mode, recycled RPC worker).
   const p=this.store.claim(token);if(!p)return null;
   if(this.clock()>p.expires){this.state='cancelled';return {text:'授權已過期，未執行；請重新提出。',state:this.state};}
   authorized=p;
  }else return null;
  if(signal?.aborted)throw new Error('Aborted before mutation');
  const {request,reply}=authorized;this.pending=undefined;this.repairable=false;this.state='executing';
  try{
   const output=await this.bridge(request,{mode:'execute',expected_snapshot:reply.snapshot,delete_token:reply.delete_token,signal});
   this.state=output.code===0?'done':'failed';
   return {text:(output.code===0?'操作完成：':'未確認成功；已停止，請先查帳，不要自動重試：')+'\n'+this.boundOutput(output),state:this.state};
  }catch(error){this.state='failed';return {text:`結果不確定，已停止且不自動重試：${String(error)}`,state:this.state};}
 }
 // Expense categories come from the user's catalog (standard, custom rules,
 // journal history) or the user's own words; the model must not invent
 // accounts such as "expenses:Groceries".
 private async checkExpenseAccounts(request:Request,signal?:AbortSignal){
  const debits=[...new Set<string>(request.data.transactions.filter((r:any)=>(r.kind??request.data.defaults?.kind)==='expense').map((r:any)=>String(r.debit??request.data.defaults?.debit??'')).filter(Boolean))];
  const unstated=debits.filter(a=>!this.input.includes(a));
  if(!unstated.length)return;
  const reply=await this.bridge({operation:'classify',data:{description:String(request.data.transactions[0]?.description??'expense')}},{mode:'preview',signal});
  let catalog:string[];
  try{catalog=(JSON.parse(reply.stdout).categories as {account:string}[]).map(c=>c.account);}catch{throw new Error('Cannot verify expense categories; nothing written');}
  const unknown=unstated.filter(a=>!catalog.includes(a));
  if(unknown.length)throw new Error(`Unknown expense account ${unknown.join(', ')}; choose exactly one of: ${catalog.join(', ')}`);
 }
 private boundOutput(reply:Reply):string{
  const text=reply.stdout+'\n'+reply.stderr;
  if(Buffer.byteLength(text)>45000||text.split('\n').length>1800) return text.slice(0,12000)+'\n[結果過長，已截斷；請縮小查詢範圍。]';
  return text.trim();
 }
}

export function pythonBridge(journal:string,env?:NodeJS.ProcessEnv):Bridge{
 const script=fileURLToPath(new URL('../scripts/finance_bridge.py',import.meta.url));
 return async(request,options)=>new Promise((ok,fail)=>{
  if(options.signal?.aborted){fail(new Error('Aborted'));return;}
  const {signal,...envelope}=options;
  const child=spawn('python3',[script,journal],{env:env??process.env,stdio:['pipe','pipe','pipe']});let stdout='',stderr='',settled=false;
  const finish=(error?:Error,value?:Reply)=>{if(settled)return;settled=true;clearTimeout(timer);signal?.removeEventListener('abort',abort);if(error)fail(error);else ok(value!);};
  const abort=()=>{child.kill('SIGTERM');finish(new Error('Aborted; mutation outcome uncertain'));};
  const timer=setTimeout(()=>{child.kill('SIGTERM');finish(new Error('Backend timeout; outcome uncertain'));},120000);
  signal?.addEventListener('abort',abort,{once:true});
  child.stdout.on('data',d=>{stdout+=d;if(stdout.length>2_000_000){child.kill('SIGTERM');finish(new Error('Backend output limit; outcome uncertain'));}});
  child.stderr.on('data',d=>{stderr=(stderr+d).slice(-50000);});child.stdin.on('error',()=>{});
  child.on('error',error=>finish(error));
  child.on('close',()=>{if(settled)return;try{const line=stdout.split('\n').reverse().find(l=>l.startsWith('HF_RESPONSE:'));if(!line)throw new Error(stderr||'Missing bridge receipt; outcome uncertain');const value=JSON.parse(line.slice(12));if(typeof value.code!=='number'||typeof value.stdout!=='string'||typeof value.stderr!=='string')throw new Error('Malformed bridge receipt');finish(undefined,value);}catch(e){finish(e as Error);}});
  child.stdin.end(JSON.stringify({request,...envelope}));
 });
}

// Local models often emit one unmatched '}' / ']' that closes the root early
// (trailing, or before more members). Remove only the closer that ended the
// root and retry; the result is still schema-validated and previewed.
function parseLenient(text:string):unknown{
 let current=text;let first:unknown;
 for(let attempt=0;attempt<=3;attempt++){
  try{return JSON.parse(current);}catch(error){
   first??=error;
   const at=Number(/after JSON at position (\d+)/.exec(String(error))?.[1]);
   if(!Number.isInteger(at)){
    // Truncated at the very end: append the closers still open, nothing else.
    const pos=Number(/at position (\d+)/.exec(String(error))?.[1]);
    if(pos<current.trimEnd().length&&!/Unexpected end of JSON/.test(String(error)))break;
    const open:string[]=[];let quoted=false;
    for(let i=0;i<current.length;i++){const ch=current[i];if(quoted){if(ch==='\\')i++;else if(ch==='"')quoted=false;}else if(ch==='"')quoted=true;else if(ch==='{'||ch==='[')open.push(ch==='{'?'}':']');else if(ch==='}'||ch===']')open.pop();}
    if(quoted||open.length!==1)break; // more missing closers may mean lost rows
    current=current.trimEnd()+open.reverse().join('');continue;
   }
   let end=at-1;while(end>=0&&/\s/.test(current[end]))end--;
   if(!['}',']'].includes(current[end]))break;
   current=current.slice(0,end)+current.slice(end+1);
  }
 }
 throw first;
}
export function parseData(raw:unknown):Record<string,any>{
 if(typeof raw==='string'){
  if(Buffer.byteLength(raw)>200000)throw new Error('Data JSON too large');
  const tokens=raw.match(/"(?:\\.|[^"\\])*"|[{}\[\]:,]|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null/g)??[];
  const stack:({kind:string;keys:Set<string>})[]=[];
  for(let i=0;i<tokens.length;i++){
   const t=tokens[i];if(t==='{'||t==='[')stack.push({kind:t,keys:new Set()});
   else if(t==='}'||t===']')stack.pop();
   else if(t.startsWith('"')&&tokens[i+1]===':'&&stack.at(-1)?.kind==='{'){
    const key=JSON.parse(t),keys=stack.at(-1)!.keys;if(keys.has(key))throw new Error('Duplicate JSON key: '+key);keys.add(key);
   }
  }
  raw=parseLenient(raw);
 }
 if(!raw||typeof raw!=='object'||Array.isArray(raw))throw new Error('Data must be a JSON object');
 const money=new Set(['amount','debit_amount','fee','total','principal','annual_rate','payment','rounding','paid_periods','remaining_periods']);
 // Safe integers convert to decimal strings losslessly; fractional JSON
 // numbers are rejected because binary floats can corrupt money.
 const walk=(node:any)=>{if(!node||typeof node!=='object')return;for(const [key,value]of Object.entries(node)){if(money.has(key)&&typeof value!=='string'){if(!Number.isSafeInteger(value))throw new Error(`${key} must be a decimal string, e.g. "0.1"`);node[key]=String(value);}walk(node[key]);}};
 walk(raw);
 // Installment count is a string term; historical controls.count stays an integer.
 const top=raw as Record<string,any>;if(Number.isSafeInteger(top.count))top.count=String(top.count);
 return top;
}
export function sourceDigest(data:string){return createHash('sha256').update(data).digest('hex');}

// Readable summary of the backend stats JSON (exact figures, no rounding).
export function formatStats(stdout:string):string|undefined{
 let d:any;try{d=JSON.parse(stdout);}catch{return undefined;}
 if(!d||typeof d!=='object'||!d.currencies)return undefined;
 const period=d.period?.begin?`${d.period.begin} ～ ${d.period.end_exclusive?new Date(Date.parse(d.period.end_exclusive)-86400000).toISOString().slice(0,10):''}`:'全部期間';
 const lines=[`收支統計（${period}，共 ${d.transaction_count} 筆）`];
 const cur=Object.entries(d.currencies as Record<string,any>);
 if(!cur.length)lines.push('沒有符合條件的收支紀錄。');
 for(const [c,v] of cur)lines.push(`${c}：收入 ${v.income}｜支出 ${String(v.expense).replace(/^-/,'')}｜淨額 ${v.net}`);
 const cats=(o:any,label:string)=>{const rows=Object.entries(o??{}).flatMap(([acct,m]:any)=>Object.entries(m).map(([c,a]:any)=>({acct,c,a:Number(String(a).replace(/^-/,'')),raw:String(a).replace(/^-/,'')}))).sort((x,y)=>y.a-x.a);if(rows.length){lines.push(label);for(const r of rows)lines.push(`  ${r.acct}  ${r.raw} ${r.c}`);}};
 cats(d.expense_categories,'支出分類：');cats(d.income_categories,'收入分類：');
 return lines.join('\n');
}
