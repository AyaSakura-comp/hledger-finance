// hledger finance for pi — the free version.
//
// The model gets plain tools that write straight to the ledger. There is no
// state machine, no preview/確認 token, no input interception and no shell
// guard. Safety comes from the backend instead: every write is validated by
// `hledger check` and committed to Git, so any mistake is one finance_undo
// away, and hand edits to the journal are adopted as their own commit.
import type {ExtensionAPI, ExtensionFactory} from '@earendil-works/pi-coding-agent';
import {Type} from 'typebox';
import {spawn} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {homedir} from 'node:os';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

export type Run=(args:string[],stdin?:string)=>Promise<{code:number;out:string;err:string}>;

export interface FinanceOptions{
 journal?:string;
 today?:()=>string;
 host?:string;
 python?:string;
 script?:string;
}

const SCRIPT=resolve(dirname(fileURLToPath(import.meta.url)),'../scripts/finance.py');
const MAX_OUT=12000;

export function localDate(now=new Date()):string{
 const d=new Date(now.getTime()-now.getTimezoneOffset()*60000);
 return d.toISOString().slice(0,10);
}

function exec(cmd:string,args:string[],cwd:string,stdin?:string,timeoutMs=120000):ReturnType<Run>{
 return new Promise(done=>{
  const child=spawn(cmd,args,{cwd,stdio:['pipe','pipe','pipe']});
  let out='',err='';
  const timer=setTimeout(()=>child.kill('SIGTERM'),timeoutMs);
  child.stdout.on('data',d=>{out+=d;});
  child.stderr.on('data',d=>{err+=d;});
  child.stdin.on('error',()=>{});
  child.on('error',e=>{clearTimeout(timer);done({code:127,out,err:err+String(e)});});
  child.on('close',code=>{clearTimeout(timer);done({code:code??1,out,err});});
  child.stdin.end(stdin??'');
 });
}

const clip=(s:string)=>s.length>MAX_OUT?s.slice(0,MAX_OUT)+`\n…(${s.length-MAX_OUT} more chars)`:s;
const text=(t:string,details:Record<string,unknown>={})=>({content:[{type:'text' as const,text:clip(t.trim()||'(no output)')}],details});
const list=(v:unknown):string[]=>v===undefined||v===null||v===''?[]:(Array.isArray(v)?v:[v]).map(String).filter(Boolean);
// Git noise from finance.py is not useful to the model or the user.
const quiet=(s:string)=>s.split('\n').filter(l=>!/^\[(master|main)\b|^\s+\d+ files? changed|^ create mode|^Appended to /.test(l)).join('\n').trim();

// Amount: "1,200", "120元", "NT$95", 95 → positive decimal string.
export function amountOf(v:unknown):string{
 const s=String(v??'').replace(/[,\s，]/g,'').replace(/^(?:NT\$|NTD|TWD|\$|＄)/i,'').replace(/(?:元|塊|块|圓|円|NTD|TWD)$/i,'');
 const m=s.match(/^[-+]?\d+(?:\.\d+)?$/);
 if(!m)throw new Error(`amount "${v}" is not a number`);
 const n=s.replace(/^[-+]/,'');
 if(Number(n)===0)throw new Error('amount must not be 0');
 return n;
}

// Date: ISO, M/D, M-D, today/yesterday/今天/昨天/前天/大前天. Anything else is passed through for finance.py to judge.
export function dateOf(v:unknown,today:string):string{
 const s=String(v??'').trim();
 const base=new Date(today+'T12:00:00Z');
 const shift=(days:number)=>new Date(base.getTime()-days*86400000).toISOString().slice(0,10);
 const rel:Record<string,number>={today:0,'今天':0,'今日':0,yesterday:1,'昨天':1,'昨日':1,'前天':2,'大前天':3};
 if(s in rel)return shift(rel[s]);
 let m=s.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$/);
 if(m)return `${m[1]}-${m[2].padStart(2,'0')}-${m[3].padStart(2,'0')}`;
 m=s.match(/^(\d{1,2})[-/.](\d{1,2})$/)??s.match(/^(\d{1,2})月(\d{1,2})[日號号]?$/);
 if(m){
  const year=Number(today.slice(0,4));
  const iso=(y:number)=>`${y}-${m![1].padStart(2,'0')}-${m![2].padStart(2,'0')}`;
  return iso(year)>today?iso(year-1):iso(year); // "12/30" said in January = last year
 }
 return s;
}

export interface Entry{
 description:string;amount:string|number;kind?:string;date?:string;currency?:string;
 category?:string;account?:string;from?:string;to?:string;to_amount?:string|number;to_currency?:string;
 debit?:string;credit?:string;tags?:string[]|string;installments?:number;fee?:string|number;note?:string;
}

// Turn the model's loose entry into one finance.py ingest-json row.
export function toRow(e:Entry,today:string,id:string,routes:Record<string,string>):Record<string,unknown>{
 if(!e||typeof e!=='object')throw new Error('entry must be an object');
 const description=String(e.description??'').trim();
 if(!description)throw new Error('description is required');
 const kind=String(e.kind??'expense').toLowerCase().trim();
 const tags=list(e.tags).map(t=>t.trim()).filter(t=>t&&!/^(kind|import-id):/.test(t));
 if(!tags.some(t=>t.startsWith('source:')))tags.push('source:fuzzy-text');
 if(e.note)tags.push(`note:${String(e.note).replace(/[\n,]/g,' ')}`);
 const date=e.date?dateOf(e.date,today):today;
 if(!e.date)tags.push('inferred:date');
 const currency=String(e.currency??'').trim().toUpperCase()||'TWD';
 if(!e.currency)tags.push('inferred:currency');
 const route=routes[currency];
 const row:Record<string,unknown>={kind,description,amount:amountOf(e.amount),date,currency,import_id:id};
 const pay=e.account?.trim();
 switch(kind){
  case 'expense':
   row.debit=e.category?.trim()||'auto';
   if(pay)row.credit=pay; // otherwise finance.py routes by currency and tags it
   break;
  case 'income':
   row.credit=e.category?.trim()||'income:other';
   row.debit=pay||route||'assets:cash';
   break;
  case 'refund':
   row.credit=e.category?.trim()||'expenses:uncategorized';
   row.debit=pay||route||'assets:cash';
   break;
  case 'transfer':
   row.credit=e.from?.trim()||pay||'';
   row.debit=e.to?.trim()||e.category?.trim()||'';
   if(e.to_amount!==undefined||e.to_currency){row.debit_amount=amountOf(e.to_amount);row.debit_currency=String(e.to_currency??'').toUpperCase();}
   break;
  default:
   break; // opening-balance etc.: debit/credit must be given explicitly below
 }
 if(e.debit)row.debit=e.debit.trim();
 if(e.credit)row.credit=e.credit.trim();
 if(kind==='transfer'&&(!row.debit||!row.credit))throw new Error(`transfer "${description}" needs from and to accounts`);
 if(e.installments&&Number(e.installments)>1)row.installments=Math.trunc(Number(e.installments));
 if(e.fee!==undefined&&e.fee!=='')row.fee=String(e.fee);
 row.tags=tags;
 return row;
}

function readRoutes(journal:string):Record<string,string>{
 try{return JSON.parse(readFileSync(join(dirname(journal),'payment-account-rules.json'),'utf8')).currency_accounts??{};}catch{return {};}
}

// Readable receipt from the journal itself, so it shows what was really written.
function receipt(txns:any[]):string{
 return txns.map(t=>{
  const posts=(t.tpostings??[]).map((p:any)=>{
   const a=(p.pamount??[])[0];
   const q=a?Number(a.aquantity?.floatingPoint??0):0;
   return {account:p.paccount,q,c:a?.acommodity??''};
  });
  const to=posts.filter((p:any)=>p.q>0).map((p:any)=>p.account).join('+');
  const from=posts.filter((p:any)=>p.q<0).map((p:any)=>p.account).join('+');
  const pos=posts.find((p:any)=>p.q>0);
  return `・${t.tdate} ${String(t.tdescription).replace(/\s*\[1\/1\]$/,"")} ${pos?pos.q:''} ${pos?.c??''}｜${to} ← ${from}`;
 }).join('\n');
}

export function createFinance(options:FinanceOptions={}):ExtensionFactory{
 return (pi:ExtensionAPI)=>{
  const host=options.host??process.env.PI_FINANCE_HOST??'';
  // Discord is shared with other people; the wallet stays off there unless enabled.
  const off=list((process.env.PI_FINANCE_DISABLED_HOSTS??'piscord').split(','));
  if(host&&off.includes(host))return;
  const journal=resolve((options.journal??process.env.PI_FINANCE_JOURNAL??join(homedir(),'finance/main.journal')).replace(/^~\//,homedir()+'/'));
  const book=dirname(journal);
  const today=()=>(options.today??localDate)();
  const finance:Run=(args,stdin)=>exec(options.python??'python3',[options.script??SCRIPT,'--journal',journal,...args],book,stdin);
  const hledger:Run=args=>exec('hledger',['-f',journal,...args],book);
  const fail=(r:{out:string;err:string})=>quiet(`${r.err}\n${r.out}`).replace(/^error:\s*/m,'');

  const filters=(p:any)=>[
   ...(p.period?['--period',String(p.period)]:[]),
   ...(p.begin?['--begin',dateOf(p.begin,today())]:[]),
   ...(p.end?['--end',dateOf(p.end,today())]:[]),
   ...list(p.account).flatMap(a=>['--account',a]),
   ...list(p.description).flatMap(d=>['--description',d]),
   ...list(p.tag).flatMap(t=>['--tag',t]),
   ...list(p.where).flatMap(w=>['--where',w]),
  ];
  const Filters={
   period:Type.Optional(Type.String({description:'hledger period, e.g. "2026-11", "thismonth", "lastweek", "2026"'})),
   begin:Type.Optional(Type.String({description:'Start date (inclusive), YYYY-MM-DD'})),
   end:Type.Optional(Type.String({description:'End date (inclusive), YYYY-MM-DD'})),
   account:Type.Optional(Type.Array(Type.String(),{description:'Account regex, e.g. "food" or "expenses:transport"'})),
   description:Type.Optional(Type.Array(Type.String(),{description:'Description regex, e.g. "全聯"'})),
   tag:Type.Optional(Type.Array(Type.String())),
   where:Type.Optional(Type.Array(Type.String(),{description:'Raw hledger query terms, e.g. "amt:>1000", "cur:JPY"'})),
  };
  // Small models often send one string where an array is expected.
  const loose=(args:any)=>{if(args&&typeof args==='object')for(const k of ['account','description','tag','where','args'])if(typeof args[k]==='string')args[k]=[args[k]];return args;};

  pi.registerTool({
   name:'finance_record',label:'記帳',
   description:`Write transactions to the hledger ledger (${journal}) immediately — no preview or confirmation. One call can write many entries; they commit together and one finance_undo reverts them all.
Defaults: date = today, currency = TWD, expense category = auto-classified from history, payment account = routed by currency (TWD→cash wallet). Any account name is allowed; new ones are created.
kind: expense (default) | income | refund | transfer | opening-balance.
- expense: category = expenses:* account (omit to auto-classify); account = what paid (e.g. assets:bank:usd-account, liabilities:credit-card:xxx).
- income: category = income:* (salary, dividends, interest, rebates, …); account = where the money went.
- refund: category = the expense account refunded; account = where the money went.
- transfer: from → to (e.g. from assets:cash:wallet to assets:deposits:twd); cross-currency: add to_amount + to_currency.
- anything else: give debit and credit accounts directly (debit/credit always override).`,
   promptSnippet:'Record income/expenses/transfers in the personal hledger ledger',
   promptGuidelines:[
    'When the user states a real, completed spend or income with an amount (e.g. 「晚餐 120」「全聯 680 刷卡」「昨天薪水 50000」), call finance_record right away — do not ask for confirmation and do not ask about the category; mistakes are fixed with finance_undo/finance_delete. Price questions, plans and hypotheticals are not records.',
    'Use finance_query for 帳 questions (spending, balances, 最近記了什麼) and finance_cli for anything else hledger-finance can do (visualize, installment, loan-resume, backup, audit, import-csv). After writing, reply in one or two short lines with what was recorded.',
   ],
   parameters:Type.Object({
    entries:Type.Array(Type.Object({
     description:Type.String({description:'Short item or merchant, in the user\'s words'}),
     amount:Type.Union([Type.String(),Type.Number()],{description:'Positive amount'}),
     kind:Type.Optional(Type.String({description:'expense | income | refund | transfer | opening-balance'})),
     date:Type.Optional(Type.String({description:'YYYY-MM-DD, or 昨天/前天/M/D'})),
     currency:Type.Optional(Type.String()),
     category:Type.Optional(Type.String()),
     account:Type.Optional(Type.String({description:'Payment / receiving account'})),
     from:Type.Optional(Type.String()),to:Type.Optional(Type.String()),
     to_amount:Type.Optional(Type.Union([Type.String(),Type.Number()])),to_currency:Type.Optional(Type.String()),
     debit:Type.Optional(Type.String()),credit:Type.Optional(Type.String()),
     installments:Type.Optional(Type.Number()),fee:Type.Optional(Type.Union([Type.String(),Type.Number()])),
     tags:Type.Optional(Type.Array(Type.String())),note:Type.Optional(Type.String()),
    }),{minItems:1}),
   }),
   prepareArguments(args:any){
    if(args&&!args.entries&&args.description)args={entries:[args]};
    if(args&&typeof args.entries==='string'){try{args.entries=JSON.parse(args.entries);}catch{}}
    if(args?.entries&&!Array.isArray(args.entries))args.entries=[args.entries];
    for(const e of args?.entries??[])if(e&&typeof e.tags==='string')e.tags=[e.tags];
    return args;
   },
   async execute(_id,params){
    const day=today();const batch=`pi-${randomUUID().slice(0,8)}`;
    let rows:Record<string,unknown>[];
    try{rows=params.entries.map((e:Entry,i:number)=>toRow(e,day,`${batch}-${i}`,readRoutes(journal)));}
    catch(error){return text(`Not written: ${String(error).replace(/^Error:\s*/,'')}`,{written:0});}
    const before=new Set((await hledger(['accounts'])).out.split('\n').filter(Boolean));
    const r=await finance(['ingest-json','-'],JSON.stringify({transactions:rows}));
    if(r.code!==0)return text(`Not written: ${fail(r)}`,{written:0});
    const shown=await hledger(['print','-O','json',`tag:import-id=^${batch}-`]);
    let body='';
    try{body=receipt(JSON.parse(shown.out));}catch{body=quiet(`${r.out}\n${r.err}`);}
    const skipped=/duplicates skipped: ([1-9]\d*)/.exec(r.err)?.[1];
    const fresh=(await hledger(['accounts'])).out.split('\n').filter(a=>a&&!before.has(a));
    const notes=[skipped?`（${skipped} 筆重複已略過）`:'',fresh.length?`新科目：${fresh.join('、')}（打錯的話 finance_undo 後重記）`:''].filter(Boolean).join('\n');
    return text(`已記帳：\n${body}${notes?'\n'+notes:''}\n要改回來用 finance_undo。`,{written:rows.length,batch,newAccounts:fresh});
   },
  });

  pi.registerTool({
   name:'finance_query',label:'查帳',
   description:'Read the ledger. report: transactions (default) | register | balance | income-statement | balance-sheet | cashflow | budget | stats (income/expense/net + category totals as JSON) | accounts (list account names). Filters are ANDed. Without filters, transactions shows the latest 30.',
   parameters:Type.Object({
    report:Type.Optional(Type.String()),
    ...Filters,
    interval:Type.Optional(Type.String({description:'daily | weekly | monthly | quarterly | yearly'})),
    limit:Type.Optional(Type.Number({description:'Only the latest N transactions/register rows'})),
   }),
   prepareArguments:loose,
   async execute(_id,p:any){
    const report=String(p.report??'transactions').toLowerCase();
    if(report==='accounts'){const r=await hledger(['accounts',...list(p.account)]);return text(r.code?fail(r):r.out);}
    if(report==='stats'){
     const r=await finance(['stats',...(p.period?['--period',p.period]:[]),...(p.begin?['--begin',dateOf(p.begin,today())]:[]),...(p.end?['--end',dateOf(p.end,today())]:[]),...list(p.where).flatMap(w=>['--where',w])]);
     return text(r.code?fail(r):r.out);
    }
    const args=['query','--report',report,...filters(p)];
    if(p.interval)args.push('--interval',String(p.interval));
    const limit=p.limit??(['transactions','register'].includes(report)&&args.length===3?30:undefined);
    if(limit)args.push('--limit',String(Math.max(1,Math.trunc(limit))));
    const r=await finance(args);
    return text(r.code?fail(r):r.out);
   },
  });

  pi.registerTool({
   name:'finance_delete',label:'刪帳',
   description:'Delete every transaction matching the filters (ANDed; at least one filter) and commit. No confirmation step — the result lists what was removed, and finance_undo restores it. Set dry_run to only list matches.',
   parameters:Type.Object({...Filters,dry_run:Type.Optional(Type.Boolean())}),
   prepareArguments:loose,
   async execute(_id,p:any){
    const args=['delete',...filters(p)];
    const look=await finance(args);
    const token=/Confirmation token: (\S+)/.exec(look.out)?.[1];
    const found=look.out.replace(/\nConfirmation token:.*$/m,'').trim();
    if(!token)return text(`Nothing deleted: ${fail(look)}`,{deleted:0});
    if(p.dry_run)return text(`Would delete:\n${found}`,{deleted:0});
    const r=await finance([...args,'--confirm',token]);
    if(r.code!==0)return text(`Nothing deleted: ${fail(r)}`,{deleted:0});
    return text(`已刪除：\n${found.replace(/^Delete candidates:.*\n/,'')}\n要復原用 finance_undo。`,{deleted:Number(/Delete candidates: (\d+)/.exec(found)?.[1]??0)});
   },
  });

  pi.registerTool({
   name:'finance_undo',label:'撤銷',
   description:'Revert the latest ledger change (a record batch, delete, import, or adopted hand edit) with a new Git commit. Call again to go back further.',
   parameters:Type.Object({}),
   async execute(){
    const r=await finance(['undo']);
    return text(r.code?`Not undone: ${fail(r)}`:quiet(`${r.out}\n${r.err}`));
   },
  });

  pi.registerTool({
   name:'finance_cli',label:'財務指令',
   description:`Run any hledger-finance command directly: python3 finance.py --journal ${journal} <args…>. Subcommands: add, classify, installment, loan-resume, ingest-json (stdin JSON via "input"), import-csv, query, stats, visualize (returns a PNG path), check, audit, delete, undo, backup. Use "--help" after a subcommand for its flags. You may also edit the journal file directly; valid hand edits are committed automatically on the next write.`,
   parameters:Type.Object({
    args:Type.Array(Type.String(),{description:'e.g. ["visualize","--period","2026-10"]'}),
    input:Type.Optional(Type.String({description:'stdin, e.g. JSON for ingest-json -'})),
   }),
   prepareArguments(args:any){
    if(args&&typeof args.args==='string')args.args=args.args.match(/"[^"]*"|'[^']*'|\S+/g)?.map((s:string)=>s.replace(/^["']|["']$/g,''))??[];
    return args;
   },
   async execute(_id,p:any){
    const args=list(p.args);
    if(args[0]==='--journal')args.splice(0,2);
    const r=await finance(args,p.input);
    const out=quiet(`${r.out}\n${r.err}`);
    return text(r.code&&r.code!==3?`exit ${r.code}\n${out}`:out,{code:r.code});
   },
  });
 };
}

export default createFinance();
