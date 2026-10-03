import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

export type Expense = {description:string; amount:string; date:string; inferredDate:boolean; currency:string; inferredCurrency:boolean};
export type Backend = (args:string[], payload?:unknown) => Promise<string>;
export type State = 'idle'|'preparing'|'ready'|'writing'|'done'|'failed'|'clarifying';
const transitions: Record<State,State[]> = {idle:['preparing'],preparing:['ready','failed'],ready:['writing','clarifying'],writing:['done','failed'],done:[],failed:[],clarifying:[]};
export function localDate(now=new Date(), timezone='Asia/Taipei'):string {
 const parts = new Intl.DateTimeFormat('en-CA',{timeZone:timezone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(now);
 return ['year','month','day'].map(k=>parts.find(p=>p.type===k)!.value).join('-');
}
function validDate(value:string):boolean { const date=new Date(value+'T12:00:00Z'); return !Number.isNaN(date.getTime()) && date.toISOString().slice(0,10)===value; }
export function parseExpense(raw:string,today:string):Expense|null {
 if(!validDate(today)) throw new Error('Invalid host date');
 let text=raw.normalize('NFKC').trim();
 text=text.replace(/^(?:【記帳】|\[記帳\]|記帳[:：]|\/finance\s+)\s*/, '');
 // Spoken lead-ins from voice notes ("幫我記帳 午餐…", "記一下…").
 text=text.replace(/^(?:請)?(?:幫我)?(?:記帳|記一下|記一筆|記個帳)[，,、:：\s]*/, '');
 // Fail closed for non-expenses, hypotheticals, questions, examples, amounts embedded in instructions.
 if(/\b(?:example|sample|budget|hypothetical|estimated?|approximately|if|income|salary|refund|transfer|tomorrow|cash|credit card)\b/i.test(text))return null;
 if(/如果|假設|範例|例如|預算|多少|是否|可以|嗎|如何|約|大概|估計|預計|打算|想買|價格|價錢|售價|定價|每人|每份|各自|昨天以前|明天|後天|上週|上星期|上個月|去年|下週|下星期|下個月|週[一二三四五六日天]|星期[一二三四五六日天]|收到|薪水|收入|退款|退費|轉帳|提款|還款|借|貸|分期|信用卡|現金|銀行|美元|日圓|人民幣|USD|JPY|CNY|執行|忽略|不要|取消|刪除|改成|更正|[?？;；\n"'`<>\\]/i.test(text)) return null;
 let date=today, inferredDate=true;
 const explicit=text.match(/^(\d{4}-\d{2}-\d{2})\s+/);
 if(explicit){date=explicit[1];text=text.slice(explicit[0].length);inferredDate=false;if(!validDate(date))return null;}
 else {
  const relative=text.match(/^(今天|昨天|前天)\s*/);
  if(relative){const day=new Date(today+'T12:00:00Z');day.setUTCDate(day.getUTCDate()-(relative[1]==='昨天'?1:relative[1]==='前天'?2:0));date=day.toISOString().slice(0,10);inferredDate=false;text=text.slice(relative[0].length);}
 }
 if(/今天|昨天|前天/.test(text))return null;
 const number='(?:[1-9]\\d{0,2}(?:,\\d{3})+|[1-9]\\d*)(?:\\.\\d{1,2})?';
 const tail=new RegExp(`^(.+?)\\s*(${number})\\s*(元|TWD|NT\\$)?$`,'i');
 const head=new RegExp(`^(${number})\\s*(元|TWD|NT\\$)?\\s+(.+)$`,'i');
 const a=text.match(tail), b=text.match(head);
 // Trailing spending verbs and a leading 剛剛/剛才 are speech filler, not the item.
 const description=(a?.[1] ?? b?.[3] ?? '').trim().replace(/\s*(?:總共|一共)?(?:花了|用了|付了)$|\s+花$/,'').replace(/^(?:剛剛|剛才)\s*/,'').trim();
 const amount=(a?.[2] ?? b?.[1] ?? '').replaceAll(',','');
 const unit=a?.[3] ?? b?.[2];
 if(!description || !amount || description.length>80 || !/^[\p{L}\p{M} ]+$/u.test(description))return null;
 return {description,amount,date,inferredDate,currency:'TWD',inferredCurrency:!unit};
}
export type Evidence = {binding:boolean; suggestion:{account:string|null;source?:string;reason?:string};categories:{account:string;journal_count?:number;description?:string}[]};
export class FinanceFlow {
 state:State='idle'; expense?:Expense; evidence?:Evidence; importId?:string;
 readonly trace:State[]=['idle'];
 constructor(private backend:Backend,private onState:(state:State)=>void=()=>{}){}
 private move(next:State){if(!transitions[this.state].includes(next))throw new Error(`Illegal state ${this.state} -> ${next}`);this.state=next;this.trace.push(next);this.onState(next);}
 async prepare(raw:string,today:string,importId:string){
  if(this.state!=='idle')throw new Error(`Invalid state ${this.state}`);
  const expense=parseExpense(raw,today);if(!expense)throw new Error('Unsupported/ambiguous expense');
  if(!/^[A-Za-z0-9._-]+$/.test(importId))throw new Error('Invalid import ID');
  this.expense=expense;this.importId=importId;this.move('preparing');
  try{
   const evidence=JSON.parse(await this.backend(['classify','--description',expense.description])) as Evidence;
   if(!Array.isArray(evidence.categories)||!evidence.categories.length||typeof evidence.binding!=='boolean'||!evidence.suggestion)throw new Error('Malformed classification evidence');
   if(evidence.binding){
    const account=evidence.suggestion.account;
    if(typeof account!=='string'||!/^expenses:[^\s\x00-\x1f]+$/.test(account))throw new Error('Invalid binding account');
    if(!evidence.categories.some(c=>c.account===account))evidence.categories.push({account,description:'Persisted custom binding'});
   }
   this.evidence=evidence;this.move('ready');return evidence;
  }catch(error){this.move('failed');throw error;}
 }
 clarify(question:string){if(this.state!=='ready')throw new Error(`Invalid state ${this.state}`);if(!question.trim()||question.length>240)throw new Error('Invalid clarification');this.move('clarifying');return question;}
 async commit(category:string){
  if(this.state!=='ready')throw new Error(`Invalid state ${this.state}; no retry/replay allowed`);
  if(!this.evidence!.categories.some(c=>c.account===category))throw new Error('Invalid category');
  if(this.evidence!.binding&&category!==this.evidence!.suggestion.account)throw new Error('Must respect binding category');
  const e=this.expense!;
  const tags=['source:fuzzy-text',...(e.inferredDate?['inferred:date']:[]),...(e.inferredCurrency?['inferred:currency']:[])];
  const payload={transactions:[{kind:'expense',description:e.description,amount:e.amount,date:e.date,currency:e.currency,debit:category,tags,import_id:this.importId}]};
  this.move('writing');
  try{
   const output=await this.backend(['ingest-json','-'],payload);
   if(!/Records ready: \d+; duplicates skipped: \d+/.test(output))throw new Error('Unrecognized write receipt; inspect journal before retrying');
   this.move('done');
   return `${/Records ready: 0; duplicates skipped: 1/.test(output)?'重複請求，未再次寫入':'已記帳'}：${e.date} ${e.description} ${e.amount} ${e.currency}｜${category}。${e.inferredDate?'日期預設本地今天；':''}${e.inferredCurrency?'幣別預設台幣；':''}付款來源依帳本設定。\n${output.trim()}`;
  }catch(error){this.move('failed');throw error;}
 }
}
export function pythonBackend(options:{journal:string;python?:string;script?:string;timeoutMs?:number}):Backend {
 const script=options.script??resolve(fileURLToPath(new URL('../scripts/finance.py',import.meta.url)));
 return async(args,payload)=>new Promise((ok,fail)=>{
  const child=spawn(options.python??'python3',[script,'--journal',options.journal,...args],{stdio:['pipe','pipe','pipe']});
  let out='',err='',oversize=false;
  const timer=setTimeout(()=>{child.kill('SIGTERM');fail(new Error('Backend timeout: mutation outcome uncertain; no automatic retry'));},options.timeoutMs??60000);
  child.stdout.on('data',d=>{out+=d;if(out.length>1_000_000){oversize=true;child.kill('SIGTERM');}});
  child.stderr.on('data',d=>{err=(err+d).slice(-50000);});
  child.stdin.on('error',()=>{});
  child.on('error',e=>{clearTimeout(timer);fail(e);});
  child.on('close',code=>{clearTimeout(timer);if(oversize)fail(new Error('Backend output limit exceeded; outcome uncertain'));else if(code!==0)fail(new Error(`finance.py exit ${code}: ${err||out}`));else ok(args[0]==='ingest-json'?`${out}\n${err}`:out);});
  child.stdin.end(payload===undefined?'':JSON.stringify(payload));
 });
}

// While a finance state machine owns the turn, plain-text answers are never
// valid; local models otherwise print pseudo tool calls. Force a real call on
// OpenAI-compatible payloads (llama.cpp enforces it with a grammar).
export function requireToolCall(payload:unknown):unknown{
 const p=payload as any;
 if(!p||typeof p!=='object'||!Array.isArray(p.tools)||!p.tools.length||p.tools.some((t:any)=>t?.type!=='function'))return undefined;
 return {...p,tool_choice:'required'};
}

// piweb/piscord wrap every prompt: optional cross-harness history, then
// "[Web user: x]" / "[Discord user: x]", then the message, with voice
// transcripts under "[Voice message transcription: f]". Finance detection and
// confirmation tokens must look only at what the user just said.
export function userMessage(text:string):string{
 const lines=text.split('\n');
 let start=-1;for(let i=0;i<lines.length;i++)if(/^\[(?:Web|Discord) user: [^\]\n]*\]$/.test(lines[i].trim()))start=i;
 const body=(start>=0?lines.slice(start+1):lines).filter(l=>!/^\[(?:Voice message transcription: [^\]]*|Attachment-only message: \d+ files? attached\.)\]$/.test(l.trim()));
 return spokenAmounts(body.join('\n').trim());
}
const digit:Record<string,number>={零:0,〇:0,一:1,二:2,兩:2,两:2,三:3,四:4,五:5,六:6,七:7,八:8,九:9};
const unit:Record<string,number>={十:10,拾:10,百:100,佰:100,千:1000,仟:1000};
function chineseNumber(s:string):number|undefined{
 let total=0,section=0,num=0,seen=false;
 for(const ch of s){
  if(ch in digit){num=digit[ch];seen=true;}
  else if(ch in unit){section+=(seen||unit[ch]!==10?num:1)*unit[ch];num=0;seen=false;}
  else if(ch==='萬'||ch==='万'){total+=(section+num)*10000;section=0;num=0;seen=false;}
  else if(ch==='億'||ch==='亿'){total+=(section+num)*100000000;section=0;num=0;seen=false;}
  else return undefined;
 }
 return total+section+num;
}
// Speech recognition writes amounts as words ("四百六十塊"); convert only a
// numeral run directly followed by a currency word, never other text.
export function spokenAmounts(text:string):string{
 return text.replace(/([零〇一二兩两三四五六七八九十拾百佰千仟萬万億亿]+)\s*(元|塊錢|塊|块钱|块|圓)/g,(m,num,cur)=>{const n=chineseNumber(num);return n===undefined||n===0&&!/^[零〇]+$/.test(num)?m:`${n} ${cur==='圓'?'元':cur.startsWith('塊')||cur.startsWith('块')?'元':cur}`;});
}
