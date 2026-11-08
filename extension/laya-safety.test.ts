import {test} from 'node:test';import assert from 'node:assert/strict';
import {mkdtempSync} from 'node:fs';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {createFinanceExtension} from './index.ts';import {LayaFinance,filters} from './laya-engine.ts';
import type {Choice,Judge} from './laya.ts';
const d=(choice:string):Choice=>({choice,status:'match',confidence:1,margin:1,probabilities:{[choice]:1}});
test('even a confident false routing cannot record software examples/translations/test data',async()=>{
 let writes=0;const e=new LayaFinance({judge:async(s,q)=>Object.fromEntries(Object.keys(q).map(k=>[k,d(k==='route'?'record':'expenses:food:dining')])),backend:async a=>{if(a[0]==='classify')return JSON.stringify({binding:false,suggestion:{account:'expenses:food:dining'},categories:[{account:'expenses:food:dining'}]});writes++;return 'Records ready: 1; duplicates skipped: 0';},bridge:async()=>({code:0,stdout:'',stderr:''}),accounts:async()=>[]});
 for(const t of ['請寫函式解析午餐120元','我要產生房租15000的測試資料','請翻譯午餐120元','請改程式處理午餐120元'])await e.run(t,{session:'s'});assert.equal(writes,0);
});
test('intervening ordinary input cancels pending authorization even without another handoff; bare ok is not finance',async()=>{
 let executes=0;const hooks:any={},tools:any={},messages:any[]=[];const pi:any={on:(n:string,f:any)=>hooks[n]=f,registerTool:(t:any)=>tools[t.name]=t,sendMessage:(m:any)=>messages.push(m)};
 const judge:Judge=async(s,q)=>Object.fromEntries(Object.keys(q).map(k=>[k,d(k==='route'?'change':'delete')]));
 createFinanceExtension({decisions:'laya',journal:'/tmp/fin-laya-safe/main.journal',stateDir:mkdtempSync(join(tmpdir(),'fin-laya-safe-')),judge,backend:async()=>'',accounts:async()=>[],bridge:async(r,o)=>{if(o.mode==='execute')executes++;return {code:0,stdout:'Lunch 120 TWD',stderr:'',snapshot:'snap',delete_token:'tok'};}})(pi);
 const ctx:any={isIdle:()=>true,sessionManager:{getSessionId:()=> 's'}};
 assert.equal(await hooks.input({text:'好',source:'interactive'},ctx),undefined);assert.equal(messages.length,0);
 await hooks.input({text:'刪除描述含Lunch的交易',source:'interactive'},ctx);await tools.finance_handoff.execute('one',{},undefined,undefined,ctx);
 await hooks.input({text:'請幫我寫程式',source:'interactive'},ctx);await hooks.input({text:'確認',source:'interactive'},ctx);assert.equal(executes,0);
});
test('query scope rejects partially understood filters rather than widening to all',()=>{
 for(const t of ['刪除昨天午餐那筆','刪除本月金額超過500的交易','查2026-10-01到2026-10-03的USD交易','查2026-10-01的午餐','查今年所有帳目','刪除描述含Lunch但不含refund的交易','刪除今天全部交易，除了薪水'])assert.throws(()=>filters(t,'2026-10-06'),t);
 assert.deepEqual(filters('查2026-10-01到2026-10-03的交易','2026-10-06'),{begin:'2026-10-01',end:'2026-10-04'});
});
