import {randomUUID} from 'node:crypto';
import {FinanceBatch,parseEntries,localDate,type Backend} from './core.ts';
import {Workflow,isShortConfirm,type Bridge} from './workflows.ts';
import type {PendingStore} from './pending-store.ts';
import {cleanIntent,routeQuestion,operationQuestion,layaClient,type Judge,type Choice,type Question} from './laya.ts';
export type EngineReceipt={text:string;state:string;trace:{name:string;decision:Choice}[]};
export type EngineOptions={backend:Backend;bridge:Bridge;accounts:()=>Promise<string[]>;judge?:Judge;today?:()=>string;paymentRules?:()=>Promise<Record<string,string>>;store?:PendingStore;journal?:string};
const repeatQuestion:Question={type:'choice',instructions:'這句是明確追加相同消費，還是在說重複記帳？不清楚就選unclear。',criteria:{repeat:'明確要求再記一筆相同消費',duplicate:'抱怨已重複記帳或不要重複',unclear:'追加還是重複不清楚',none:'無關記帳'}};
// Typed read candidates: no shell/argv and no model-created amounts or filters.
const readQuestion:Question={type:'choice',instructions:'要查自己的帳本哪項資料？',criteria:{query:'帳戶餘額或交易明細',stats:'花費、收入支出總額',visualize:'畫收支圖或儀表板',check:'檢查帳本是否平衡',audit:'稽核帳本',settings:'目前分類或付款規則',none_match:'沒有符合的查詢'}};
const changeQuestion:Question={...operationQuestion,criteria:Object.fromEntries(Object.entries(operationQuestion.criteria).filter(([k])=>['amend','delete','undo','rules','backup','installment','loan','historical','none_match'].includes(k)))};
const followup=/^(?:多一筆|多了一筆|重複了|再一筆一樣的|再記一筆一樣的|再記一次|再一筆)$/;
export class LayaFinance{
 private busy=false;private w:Workflow;private judge:Judge;private today:()=>string;private last?:{session:string;at:number;text:string};private session='';
 constructor(private options:EngineOptions){this.judge=options.judge??layaClient();this.today=options.today??localDate;this.w=this.workflow();}
 private workflow(){return new Workflow(this.options.bridge,this.today,Date.now,this.options.store,this.options.journal);}
 reset(){if(this.busy)return;this.w=this.workflow();this.last=undefined;this.session='';}
 hasPending(session:string){return Boolean((this.w.state==='awaiting_confirmation'&&this.session===session)||this.options.store?.latest(session));}
 observeInput(raw:string,session:string){
  if(this.busy)return;
  const text=cleanIntent(raw);
  const confirm=isShortConfirm(text)||/^(?:確認|confirm) [0-9a-f-]{36}$/i.test(text);
  if(confirm||/^取消(?:記帳)?$/.test(text)||/^(?:只|僅|部分).{0,12}確認/.test(text))return;
  if(this.w.state==='awaiting_confirmation')this.w.cancel();
  this.options.store?.dropSession(session);
 }
 async run(raw:string,context:{session:string;signal?:AbortSignal;images?:unknown[]}):Promise<EngineReceipt>{
  const trace:EngineReceipt['trace']=[];const result=(text:string,state:string):EngineReceipt=>({text,state,trace});
  if(this.busy)return result('財務流程執行中，本次未操作。','busy');
  this.busy=true;
  const text=cleanIntent(raw),signal=context.signal;
  const judge=async(state:string,questions:Record<string,Question>)=>{if(signal?.aborted)throw new Error('Aborted');const out=await this.judge(state,questions,signal);for(const [name,q]of Object.entries(questions)){const d=out[name];if(!d||!Object.hasOwn(q.criteria,d.choice)||!['match','uncertain'].includes(d.status))throw new Error('Invalid Laya decision');trace.push({name,decision:d});}return out;};
  try{
   if(signal?.aborted)throw new Error('Aborted');
   if(this.session&&this.session!==context.session){if(this.w.state==='awaiting_confirmation')this.w.cancel();this.w=this.workflow();this.last=undefined;}
   this.session=context.session;this.w.session=context.session;
   const token=/^(?:確認|confirm) [0-9a-f-]{36}$/i.test(text),short=/^(?:確認|確定|好|好的|ok|okay|yes|y|對|是|沒問題|執行|confirm)[!！。.\s]*$/i.test(text);
   if(token||short){const receipt=await this.w.confirm(text,signal);return receipt?result(receipt.text,receipt.state):result('找不到有效的確認預覽，沒有執行。','cancelled');}
   if(/^取消(?:記帳)?$/.test(text)){if(this.w.state==='awaiting_confirmation')this.w.cancel();this.options.store?.dropSession(context.session);return result('已取消，沒有寫入。','cancelled');}
   if(this.w.state==='awaiting_confirmation'){
    if(/^(?:只|僅|部分).{0,12}確認/.test(text))return result('不能部分確認。請取消後重新說明要操作的範圍。','awaiting_confirmation');
    this.w.cancel();
   }
   // Any intervening non-confirm message retires cross-process authorization.
   this.options.store?.dropSession(context.session);
   if(!text)return result('沒有使用者自身的財務請求。','released');
   if(followup.test(text)){
    const {repeat}=await judge(text,{repeat:repeatQuestion});
    // Laya judges the meaning, but ambiguous text can never authorize duplication.
    if(repeat.status==='match'&&repeat.choice==='repeat'&&/^再(?:記)?一筆一樣的$/.test(text)&&this.last?.session===context.session&&Date.now()-this.last.at<900000){
     const replay=this.last.text;return await this.record(replay,context,trace,judge);
    }
    return result(repeat.choice==='duplicate'?'你是要撤銷哪一筆重複記帳？請提供項目與金額。':'你是要再記一筆相同消費，還是剛才多記了？', 'clarifying');
   }
   const {route}=await judge(text,{route:routeQuestion});
   if(route.status!=='match')return result('Laya 尚無法可靠判斷這個請求，沒有操作帳本。請直接說要記帳、查帳或修改哪一筆。','uncertain');
   if(route.choice==='none')return result('不是自身帳本需求，沒有操作帳本。','released');
   if(route.choice==='record')return await this.record(text,context,trace,judge);
   const {operation}=await judge(text,{operation:route.choice==='query'?readQuestion:changeQuestion});
   if(operation.status!=='match'||operation.choice==='none_match')return result('Laya 無法可靠選擇帳本操作，沒有執行。請補充需求。','clarifying');
   const op=operation.choice;const filter=filters(text,this.today());
   this.w=this.workflow();this.w.session=context.session;this.w.begin(text);
   let data:Record<string,any>;
   if(op==='query'){
    const {report}=await judge(text,{report:{type:'choice',instructions:'使用者要餘額還是交易明細？',criteria:{balance:'帳戶現在還剩多少錢、餘額',transactions:'交易紀錄明細、花費項目', 'income-statement':'收入支出損益表', 'balance-sheet':'資產負債表',none_match:'沒有符合的報表'}}});
    if(report.status!=='match'||report.choice==='none_match')return result('請說明要查帳戶餘額或哪一段交易明細。','clarifying');
    data={...filter,report:report.choice};
   }else if(op==='stats'){
    if(filter.account||filter.description)return result('這版 Laya 統計尚不支援這種限定範圍；沒有放寬查詢。','unsupported');
    data=filter;
   }else if(op==='visualize')data=filter;
   else if(['check','settings','undo'].includes(op))data={};
   else if(op==='audit')data={strict:/strict|嚴格/i.test(text)};
   else if(op==='delete'){
    if(/不要|不准|不可|不能|別|勿|如果|假設/.test(text))return result('這不是可授權刪除的直接指令，沒有操作。','released');
    if(!filter.description&&!filter.account&&!/^(?:請|幫我|先)?(?:刪除|刪掉|移除)\s*(?:(?:\d{4}-\d{2}-\d{2}|今天|昨天|前天|本月|這個月|上個月)(?:到\d{4}-\d{2}-\d{2})?\s*的?)?(?:所有|全部)(?:交易|紀錄|帳目)[。!！\s]*$/.test(text))return result('請使用明確描述或帳戶條件，或說明某日期的全部交易；沒有放寬刪除範圍。','clarifying');
    if(!Object.keys(filter).length&&!/(?:全部|所有).{0,6}(?:交易|紀錄|帳目)/.test(text))return result('請提供要刪除的日期、描述或帳戶；尚未刪除任何資料。','clarifying');
    data=filter;
   }else return result(`已由 Laya 選擇 ${op}，但此實驗版尚未接入複雜資料擷取；沒有操作。仍可使用未啟用 Laya 的原流程。`,'unsupported');
   const receipt=await this.w.propose({operation:op,data},signal);return result(receipt.text,receipt.state);
  }catch(error){this.last=undefined;return result('財務流程停止：'+String(error).slice(0,200)+'。沒有自動重試；若發生寫入中斷請先查帳。','failed');}
  finally{this.busy=false;}
 }
 private async record(text:string,context:{session:string;signal?:AbortSignal;images?:unknown[]},trace:EngineReceipt['trace'],judge:(state:string,q:Record<string,Question>)=>Promise<Record<string,Choice>>):Promise<EngineReceipt>{
  const out=(message:string,state:string)=>({text:message,state,trace});
  if(context.images?.length)return out('此 Laya 實驗版尚未接入收據圖像擷取，沒有寫入。','unsupported');
  // Authority guard, not a replacement classifier: known nonfinancial instructions
  // can never be auto-written even when Laya gives a confident false positive.
  if(/(?:寫|改|測試|產生|生成|解析).{0,20}(?:函式|程式|code|function|測試資料|API|extension)|翻譯|請分析|測試(?:資料|範例)/i.test(text))return out('這是程式、翻譯或測試內容，不作為實際消費寫入。','released');
  if(/可能|看不清|不確定|或\s*\d|只預覽|不要寫|不寫入|不要記帳|別記|假設|如果|例如/.test(text))return out('這段文字不是明確可自動寫入的消費，沒有寫入。請補充實際項目、金額或使用原預覽流程。','clarifying');
  if(!parseEntries(text,this.today()))return out('已由 Laya 選擇記帳，但實驗版無法可靠擷取完整金額／日期／方向，沒有寫入。請提供明確「項目 金額 幣別」。','unsupported');
  const batch=new FinanceBatch(this.options.backend,this.options.accounts,()=>{},this.options.paymentRules);
  await batch.prepare(text,this.today(),`laya-${randomUUID()}`);
  const questions:Record<string,Question>={},choices:{category:string;payment?:string}[]=[];
  for(const [i,e]of batch.entries.entries()){
   const ev=batch.evidence[i];const accounts=e.kind==='expense'?ev.categories:batch.incomeAccounts.map(account=>({account,description:account.replace(/^income:/,'')}));
   if(ev?.binding){choices[i]={category:ev.suggestion.account!};}
   else questions[`category_${i}`]={type:'choice',instructions:`「${e.description}」這筆${e.kind==='income'?'收入':'支出'}應歸哪個分類？只能選既有科目。`,criteria:Object.fromEntries(accounts.map(c=>[c.account,c.description??c.account]))};
   if(e.payment&&batch.paymentAccounts.length)questions[`payment_${i}`]={type:'choice',instructions:`付款方式「${e.payment}」對應哪個帳戶？没有明確對應選none_match。`,criteria:{...Object.fromEntries(batch.paymentAccounts.map(a=>[a,a])),none_match:'沒有對應帳戶，使用帳本付款規則'}};
  }
  const decisions=Object.keys(questions).length?await judge(text,questions):{};
  for(const [i,e]of batch.entries.entries()){
   if(!choices[i]){const d=decisions[`category_${i}`];if(d.status!=='match')return out(`「${e.description}」的分類信心不足，整批沒有寫入。`,'uncertain');choices[i]={category:d.choice};}
   const p=decisions[`payment_${i}`];if(p?.status==='match'&&p.choice!=='none_match')choices[i].payment=p.choice;
  }
  if(context.signal?.aborted)return out('記帳已取消，沒有寫入。','cancelled');
  const receipt=await batch.commit(choices);
  // Only actual backend success creates repeat context; never quoted chat or proposals.
  this.last={session:context.session,at:Date.now(),text};return out(receipt,'done');
 }
}
// Recognize only explicit scope. Never guess English merchant names from Chinese,
// or silently drop an unrecognized condition into an all-ledger deletion.
export function filters(text:string,today:string):Record<string,any>{
 const result:Record<string,any>={};let date:Date|undefined;let month=false;
 const iso=[...text.matchAll(/\b(\d{4}-\d{2}-\d{2})\b/g)].map(m=>m[1]);
 const valid=(s:string)=>{const d=new Date(s+'T12:00:00Z');if(!Number.isFinite(+d)||d.toISOString().slice(0,10)!==s)throw new Error('Invalid explicit date');return d;};
 if(iso.length>2)throw new Error('Too many date boundaries');
 if(iso.length){date=valid(iso[0]);result.begin=iso[0];const end=valid(iso.at(-1)!);end.setUTCDate(end.getUTCDate()+1);result.end=end.toISOString().slice(0,10);if(result.end<=result.begin)throw new Error('Reversed period');}
 else if(/這個月|本月|上個月/.test(text)){date=valid(today);date.setUTCDate(1);if(/上個月/.test(text))date.setUTCMonth(date.getUTCMonth()-1);month=true;}
 else if(/今天|昨天|前天/.test(text)){date=valid(today);date.setUTCDate(date.getUTCDate()-(/前天/.test(text)?2:/昨天/.test(text)?1:0));}
 if(date&&!iso.length){result.begin=date.toISOString().slice(0,10);if(month)date.setUTCMonth(date.getUTCMonth()+1);else date.setUTCDate(date.getUTCDate()+1);result.end=date.toISOString().slice(0,10);}
 if(/除了|排除|不含|不包含|但|不要|只(?!有)|僅|金額|超過|低於|大於|小於|\b(?:USD|JPY|EUR)\b|日圓|美元/.test(text)||/這週|上週|最近|今年|去年/.test(text)||(/\d{4}[-/]\d{1,2}(?![-/\d])/.test(text)&&!iso.length))throw new Error('Unsupported scope; refused to broaden query');
 const account=[...new Set([...text.matchAll(/\b((?:assets|liabilities|expenses|income|equity):[\w:.-]+)/g)].map(m=>m[1]))];if(account.length)result.account=account;
 else if(/現金/.test(text))result.account=['assets:cash'];
 const desc=/描述(?:含|包含|為|是|:)\s*[「"]?([^「」"，,。\s]+)[」"]?/.exec(text);if(desc)result.description=[desc[1].replace(/的(?:交易|紀錄|帳目).*$/,'')];
 if(/(?:描述|商家|店家|分類|僅|只查|只刪|午餐|咖啡|晚餐|早餐)/.test(text)&&!result.description&&!result.account)throw new Error('Unsupported named scope; refused to broaden query');
 return result;
}
