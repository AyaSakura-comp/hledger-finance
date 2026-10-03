import { randomUUID, createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { localDate } from './core.ts';
export const readonlyOperations=['query','stats','visualize','check','audit','classify','native','settings'] as const;
export const mutationOperations=['ingest','historical','installment','loan','delete','undo','rules','backup'] as const;
export type Request={operation:string;data:Record<string,any>};
export type Reply={code:number;stdout:string;stderr:string;snapshot?:string;delete_token?:string;images?:string[]};
export type Bridge=(request:Request,options:{mode:'preview'|'execute';expected_snapshot?:string;delete_token?:string;signal?:AbortSignal})=>Promise<Reply>;
export type WorkflowState='idle'|'collecting'|'preparing'|'awaiting_confirmation'|'executing'|'done'|'failed'|'cancelled'|'clarifying';
export type Receipt={text:string;state:WorkflowState};

export class Workflow {
 state:WorkflowState='idle';
 private pending?:{request:Request;reply:Reply;token:string;expires:number};
 private repairable=false;private input='';private id='';private source:'text'|'image'|'file'|'pdf'='text';
 constructor(private bridge:Bridge,private today:()=>string=localDate,private clock:()=>number=Date.now){}
 get confirmation(){return this.pending?.token;}
 begin(input:string,source:'text'|'image'|'file'|'pdf'='text',stableSourceDigest?:string){
  if(['preparing','executing','awaiting_confirmation'].includes(this.state))throw new Error('Existing pending workflow');
  this.input=input;this.id=stableSourceDigest?`src-${stableSourceDigest}`:`flow-${randomUUID()}`;this.source=source;this.pending=undefined;this.repairable=false;this.state='collecting';
 }
 resumeDraft(){if(this.state!=='failed'||!this.repairable)return false;this.repairable=false;this.state='collecting';return true;}
 cancel(){if(this.state==='executing'||this.state==='preparing')throw new Error('Busy workflow; outcome may be uncertain');this.pending=undefined;this.state='cancelled';}
 clarify(question:string):Receipt{
  if(this.state!=='collecting'||!question.trim()||question.length>500)throw new Error('Invalid clarification');
  this.state='clarifying';return {text:question,state:this.state};
 }
 async inspect(request:Request,signal?:AbortSignal):Promise<Reply>{
  if(this.state!=='collecting'||!['classify','native','settings'].includes(request.operation))throw new Error('Inspection refused');
  if(signal?.aborted)throw new Error('Aborted');
  // Inspect cannot run concurrently with a mutation proposal.
  this.state='preparing';
  // Read-only: a rejected inspection leaves the draft collecting (turn cap bounds loops).
  try{const r=await this.bridge(structuredClone(request),{mode:'preview',signal});if(r.code)throw new Error(r.stderr||r.stdout);this.state='collecting';return r;}
  catch(e){this.state=signal?.aborted?'failed':'collecting';throw e;}
 }
 async propose(input:Request,signal?:AbortSignal):Promise<Receipt>{
  if(this.state!=='collecting')throw new Error('No collecting workflow; concurrent/replayed proposal refused');
  if(signal?.aborted)throw new Error('Aborted');
  if(![...readonlyOperations,...mutationOperations].includes(input.operation as any))throw new Error('Unknown finance operation');
  const request:Request=structuredClone(input);
  if(request.operation==='visualize'&&/每(?:個|種)?幣別|逐幣別|各幣別|every currency|each currency/i.test(this.input))delete request.data.currency;
  if(request.operation==='ingest'){
   if(!Array.isArray(request.data.transactions)||!request.data.transactions.length)throw new Error('Transaction batch required');
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
   // A single explicitly named payment account is a user fact: apply it to
   // expense rows the model left without a credit side.
   const paid=[...new Set([...this.input.normalize('NFKC').matchAll(/付款(?:都用|帳戶|方式)?(?:用|是|為|:|：)?\s*((?:assets|liabilities):[A-Za-z0-9_:.-]+)/g)].map(m=>m[1]))];
   if(paid.length===1)for(const r of request.data.transactions)if((r.kind??request.data.defaults?.kind)==='expense'&&!r.credit&&!request.data.defaults?.credit)r.credit=paid[0];
   if(['image','pdf'].includes(this.source)){
    const seen=new Set<string>();
    for(const r of request.data.transactions){const key=JSON.stringify(Object.entries(r).filter(([k])=>!['import_id','tags'].includes(k)).sort());if(seen.has(key))throw new Error('Identical duplicate rows from one receipt source; record the final paid total once');seen.add(key);}
   }
   const defaults={...(request.data.defaults??{})};
   const inherited=Array.isArray(defaults.tags)?defaults.tags:[];delete defaults.tags;
   request.data.defaults={...defaults,date:defaults.date??this.today(),currency:defaults.currency??'TWD'};
   request.data.transactions=request.data.transactions.map((row:any,index:number)=>{
    const r={...row};
    // kind fixes posting direction; an exactly reversed pair is the same
    // fact written backwards, so canonicalize instead of failing.
    const family=(a:any)=>String(a??'').split(':')[0],side=(a:any)=>['assets','liabilities'].includes(family(a));
    const kind=r.kind??defaults.kind,debit=r.debit??defaults.debit,credit=r.credit??defaults.credit;
    const reversed=kind==='expense'?family(credit)==='expenses'&&side(debit):kind==='income'?family(debit)==='income'&&side(credit):kind==='refund'?family(debit)==='expenses'&&side(credit):false;
    if(reversed){r.debit=credit;r.credit=debit;}
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
   const reply=await this.bridge(request,{mode:'preview',signal});
   const valid=reply.code===0||(request.operation==='delete'&&reply.code===3);
   if(!valid)throw new Error(reply.stderr||reply.stdout||'Backend preview failed');
   if(readonlyOperations.includes(request.operation as any)){
    this.state='done';return {text:this.boundOutput(reply),state:this.state};
   }
   if(!reply.snapshot)throw new Error('No preview snapshot; authorization refused');
   if(request.operation==='delete'&&!reply.delete_token)throw new Error('No backend deletion token');
   const token=randomUUID();this.pending={request,reply:structuredClone(reply),token,expires:this.clock()+600000};this.state='awaiting_confirmation';
   // Full candidate preview is mandatory. Do not offer confirmation for a
   // truncated preview: narrow the batch/filter instead.
   const preview=JSON.stringify(request,null,2)+'\n'+reply.stdout+'\n'+reply.stderr;
   if(Buffer.byteLength(preview)>45000||preview.split('\n').length>1800)throw new Error('Preview too large to show completely; narrow scope');
   return {text:`尚未修改帳本／規則／備份。請核對完整 ${request.operation} 預覽（包含所有候選）。\n${preview}\n\n僅完整同意以上範圍時，在下一則訊息輸入：確認 ${token}\n只同意部分內容請取消後重新提出；取消：取消記帳。授權有效 10 分鐘。`,state:this.state};
  }catch(error){this.pending=undefined;this.repairable=!signal?.aborted;this.state='failed';throw error;}
 }
 async confirm(text:string,signal?:AbortSignal):Promise<Receipt|null>{
  if(this.state!=='awaiting_confirmation'||!this.pending)return null;
  if(this.clock()>this.pending.expires){this.pending=undefined;this.state='cancelled';return null;}
  if(text.trim()!==`確認 ${this.pending.token}`&&text.trim()!==`confirm ${this.pending.token}`)return null;
  if(signal?.aborted)throw new Error('Aborted before mutation');
  const {request,reply}=this.pending;this.pending=undefined;this.repairable=false;this.state='executing';
  try{
   const output=await this.bridge(request,{mode:'execute',expected_snapshot:reply.snapshot,delete_token:reply.delete_token,signal});
   this.state=output.code===0?'done':'failed';
   return {text:(output.code===0?'操作完成：':'未確認成功；已停止，請先查帳，不要自動重試：')+'\n'+this.boundOutput(output),state:this.state};
  }catch(error){this.state='failed';return {text:`結果不確定，已停止且不自動重試：${String(error)}`,state:this.state};}
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
