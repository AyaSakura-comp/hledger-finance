// Local, bounded candidate judge. Never returns executable facts/authorization.
import { userMessage } from './core.ts';
import { intentText } from './managed.ts';
export type Question={type:'choice';instructions:string;criteria:Record<string,string>};
export type Choice={choice:string;confidence:number;margin:number;status:'match'|'uncertain';probabilities:Record<string,number>};
export type Judge=(state:string,questions:Record<string,Question>,signal?:AbortSignal)=>Promise<Record<string,Choice>>;
export function cleanIntent(raw:string){return intentText(userMessage(raw));}
export const routeQuestion:Question={type:'choice',instructions:'這句話要記帳還是問問題？',criteria:{
 record:'已發生消費，記帳',
 query:'詢問自己帳本的花費、餘額或圖表',
 change:'修改、刪除、撤銷或備份自己的帳本',
 none:'一般問題，不是記帳'}};
export const operationQuestion:Question={type:'choice',instructions:'使用者需要哪種帳本操作？沒有相符操作選 none_match。',criteria:{
 ingest:'新增實際支出、收入、轉帳或收據記帳',
 query:'查帳戶餘額、交易明細或財務報表',
 stats:'花了多少、收入支出總額及分類統計',
 visualize:'畫財務圖表或收支儀表板',
 amend:'更正既有交易的金額、日期或分類',
 delete:'刪除交易或列出待刪交易',
 undo:'撤銷或復原最近一次記帳操作',
 rules:'新增或修改未來分類、付款規則',
 settings:'查看目前分類、付款規則',
 backup:'備份整份帳本與歷史',
 historical:'匯入舊帳、歷史遷移',
 installment:'購買費用分期排程',
 loan:'貸款還款排程',
 check:'檢查帳本語法和平衡',
 audit:'稽核帳本交易語意',
 native:'原生hledger報表或帳戶科目列表',
 none_match:'一般問題、沒有相符操作或意圖不明'}};
export function choose(answer:any,criteria:Record<string,string>,minimum=0.55,gap=0.15):Choice{
 const keys=Object.keys(criteria);const p=answer?.probabilities;
 if(!p||typeof p!=='object'||Array.isArray(p)||Object.keys(p).length!==keys.length||keys.some(k=>!Object.hasOwn(p,k)||typeof p[k]!=='number'||!Number.isFinite(p[k])||p[k]<0||p[k]>1)||!keys.includes(answer?.choice))throw new Error('Invalid Laya choice/probabilities');
 const ranked=keys.map(k=>[k,p[k]] as const).sort((a,b)=>b[1]-a[1]);
 if(ranked[0][0]!==answer.choice||Math.abs(ranked.reduce((n,[,v])=>n+v,0)-1)>0.02)throw new Error('Inconsistent Laya probabilities');
 const confidence=ranked[0][1],margin=confidence-(ranked[1]?.[1]??0);
 return {choice:answer.choice,confidence,margin,status:confidence>=minimum&&margin>=gap?'match':'uncertain',probabilities:{...p}};
}
export function layaClient(options:{url?:string;timeoutMs?:number;minimum?:number;margin?:number}={}):Judge{
 const url=options.url??process.env.PI_FINANCE_LAYA_URL??'http://127.0.0.1:8000/v1/systemone';const parsed=new URL(url);
 // Finance messages never leave this machine, even through redirects.
 if(parsed.protocol!=='http:'||!['127.0.0.1','localhost','[::1]'].includes(parsed.hostname)||parsed.username||parsed.password)throw new Error('Finance Laya endpoint must be local HTTP');
 return async(state,questions,signal)=>{
  if(!state.trim()||state.length>50000||Object.keys(questions).length<1||Object.keys(questions).length>32)throw new Error('Laya request size refused');
  const body=JSON.stringify({state,questions});if(Buffer.byteLength(body)>200000)throw new Error('Laya request too large');
  const timeout=AbortSignal.timeout(options.timeoutMs??5000),combined=signal?AbortSignal.any([timeout,signal]):timeout;
  const response=await fetch(url,{method:'POST',headers:{'content-type':'application/json'},body,signal:combined,redirect:'error'});
  if(!response.ok)throw new Error('Laya HTTP '+response.status);
  if(!response.body)throw new Error('Empty Laya response');const reader=response.body.getReader();let size=0;const chunks:Uint8Array[]=[];
  try{while(true){const {value,done}=await reader.read();if(done)break;size+=value.length;if(size>256000)throw new Error('Laya response too large');chunks.push(value);}}finally{await reader.cancel();}
  const data=JSON.parse(Buffer.concat(chunks).toString('utf8'));const out:Record<string,Choice>={};
  for(const [key,q] of Object.entries(questions))out[key]=choose(data.answers?.[key],q.criteria,options.minimum,options.margin);
  return out;
 };
}
