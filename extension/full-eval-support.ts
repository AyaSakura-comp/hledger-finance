import { realpath, lstat } from 'node:fs/promises';
import { resolve, dirname, sep, basename } from 'node:path';
import { spawn } from 'node:child_process';
export async function scopedPath(root:string,input:string,allowReportPNG=false):Promise<string>{
 if(input.split(/[\\/]/).includes('..'))throw new Error('Traversal refused');
 const path=resolve(root,input),base=await realpath(root);
 const report=allowReportPNG&&path.startsWith(base+sep+'book'+sep+'reports'+sep)&&path.endsWith('.png');
 if(!path.startsWith(base+sep)||(!report&&path.includes(`${sep}book${sep}`))||path.includes(`${sep}home${sep}`))throw new Error('Path refused outside editable fixture/artifact scope');
 let ancestor=path;
 while(true){try{await lstat(ancestor);break;}catch(e){if((e as any).code!=='ENOENT')throw e;ancestor=dirname(ancestor);}}
 const actual=await realpath(ancestor);if(actual!==base&&!actual.startsWith(base+sep))throw new Error('Symlink escape refused');
 return path;
}
const hfin=new Set(['init','add','classify','installment','loan-resume','ingest-json','import-csv','query','stats','visualize','check','audit','delete','undo','backup']);
// Canonical finance.py option names only: argparse accepts abbreviations otherwise.
const helperOptions=new Set(['--account','--amount','--amount-column','--annual-rate','--begin','--category-column','--category-prefix','--confirm','--count','--credit','--currency','--date','--date-column','--debit','--description','--description-column','--encoding','--end','--fee','--fee-account','--format','--help','--id-column','--installment-column','--installments','--interest-account','--interval','--liability','--loan-id','--message','--output','--paid-periods','--payment','--payment-account','--period','--preview','--principal','--remaining-periods','--remote','--report','--rounding','--rules-only','--show-command','--start','--strict','--tag','--title','--total','--where','--yes']);
const native=new Set(['accounts','activity','aregister','balance','balancesheet','balancesheetequity','cashflow','codes','commodities','descriptions','files','incomestatement','notes','payees','prices','print','register','roi','stats','tags']);
export async function validateCLI(root:string,program:'hfin'|'hledger',input:string[]):Promise<string[]>{
 const args=[...input];if(!args.length||args.length>80||args.some(a=>a.length>20000))throw new Error('Command refused');
 if(args.some(a=>/^(-f$|--(file|journal|rules-file|output-file|scripts|run|debug-file)(=|$))/.test(a)))throw new Error('Journal/output override refused');
 if(program==='hledger'){
  if(!native.has(args[0]))throw new Error('Native mutation refused');
  const allowed=/^(--(begin|end|period|forecast|market|value|monthly|daily|weekly|quarterly|yearly|depth|flat|tree|cost|json|csv|output-format|help|investment|pnl|cashflow|today)|-O)(=|$)/;
  if(args.slice(1).some(a=>a.startsWith('-')&&!allowed.test(a)))throw new Error('Native option refused');
 }else{
  if(!hfin.has(args[0])&&args[0]!=='--help')throw new Error('Unknown helper refused');
  if(args.some(a=>a.startsWith('--')&&!helperOptions.has(a.split('=')[0])))throw new Error('Noncanonical helper option refused');
  if(['import-csv','ingest-json'].includes(args[0])&&args[1]&&args[1]!=='-')args[1]=await scopedPath(root,args[1]);
  for(let i=1;i<args.length;i++){
   if(args[i].startsWith('--output='))args[i]='--output='+await scopedPath(root,args[i].slice(9),true);
   else if(args[i]==='--output'){if(!args[i+1])throw new Error('Output refused');args[i+1]=await scopedPath(root,args[i+1],true);i++;}
   if(args[i].startsWith('--remote=')&&args[i]!=='--remote=qa:')throw new Error('Cloud remote refused');
   if(args[i]==='--remote'&&args[i+1]!=='qa:')throw new Error('Cloud remote refused');
  }
  if(args[0]==='backup'&&!args.some(a=>a==='--remote'||a==='--remote=qa:'))throw new Error('Default cloud backup refused');
 }
 return args;
}
export async function execute(program:string,args:string[],options:{cwd:string;stdin?:string;env?:NodeJS.ProcessEnv;timeoutMs?:number}):Promise<{code:number;stdout:string;stderr:string}>{
 return new Promise((ok,fail)=>{
  const child=spawn(program,args,{cwd:options.cwd,env:options.env??process.env,stdio:['pipe','pipe','pipe']});let stdout='',stderr='';
  const timer=setTimeout(()=>{child.kill('SIGTERM');fail(new Error('Command timeout; outcome uncertain'));},options.timeoutMs??45000);
  child.stdout.on('data',data=>{stdout+=data;if(stdout.length>2000000){child.kill();fail(new Error('Output limit exceeded'));}});
  child.stderr.on('data',data=>stderr=(stderr+data).slice(-100000));child.stdin.on('error',()=>{});
  child.on('error',e=>{clearTimeout(timer);fail(e);});child.on('close',code=>{clearTimeout(timer);ok({code:code??-1,stdout,stderr});});
  child.stdin.end(options.stdin??'');
 });
}
export type Expected={date:string;account:string|string[];amount:string;currency:string;description?:string;credit?:string};
export function decimalString(mantissa:string|number,places:number):string {
 let n=BigInt(mantissa).toString(),negative=n.startsWith('-');if(negative)n=n.slice(1);
 n=n.padStart(places+1,'0');let result=places?`${n.slice(0,-places)}.${n.slice(-places)}`:n;
 result=result.replace(/(\.\d*?)0+$/,'$1').replace(/\.$/,'');return (negative&&result!=='0'?'-':'')+result;
}
export function amounts(posting:any):{currency:string;amount:string}[]{return (posting.pamount??[]).map((a:any)=>({currency:a.acommodity,amount:decimalString(a.aquantity.decimalMantissa,a.aquantity.decimalPlaces)}));}
export function checkTransactions(actual:any[],expected:Expected[]):string[]{
 const failures:string[]=[];if(actual.length!==expected.length)failures.push(`Transaction count ${actual.length}, expected ${expected.length}`);
 const remaining=[...actual];
 for(const e of expected){
  const accounts=Array.isArray(e.account)?e.account:[e.account];
  const index=remaining.findIndex(t=>t.tdate===e.date&&(!e.description||t.tdescription.includes(e.description))&&(t.tpostings??[]).some((p:any)=>accounts.includes(p.paccount)&&amounts(p).some(a=>a.currency===e.currency&&a.amount===e.amount))&&(!e.credit||(t.tpostings??[]).some((p:any)=>p.paccount===e.credit&&amounts(p).some(a=>a.currency===e.currency&&a.amount===('-'+e.amount)))));
  if(index<0)failures.push(`Missing expected ${JSON.stringify(e)}`);else remaining.splice(index,1);
 }
 return failures;
}
