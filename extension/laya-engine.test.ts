import {test} from 'node:test';
import assert from 'node:assert/strict';
import {LayaFinance} from './laya-engine.ts';
import type {Judge,Choice} from './laya.ts';
const choice=(label:string):Choice=>({choice:label,status:'match',confidence:1,margin:1,probabilities:{[label]:1}});
const evidence={binding:false,suggestion:{account:'expenses:food:dining'},categories:[{account:'expenses:food:dining',description:'餐廳'}, {account:'expenses:food:drinks',description:'飲料'}, {account:'expenses:uncategorized',description:'其他'}]};
function fixture(labels:Record<string,string>={}){
 const calls:any[]=[];const writes:any[]=[];const judge:Judge=async(s,q)=>{calls.push({s,q});return Object.fromEntries(Object.entries(q).map(([key,question])=>[key,choice(labels[key]??(key==='route'?'record':key==='operation'?'stats':key==='repeat'?'unclear':Object.keys(question.criteria)[0]))]));};
 const backend=async(args:string[],payload?:any)=>{if(args[0]==='classify')return JSON.stringify(evidence);writes.push(payload);return `Records ready: ${payload.transactions.length}; duplicates skipped: 0`;};
 const bridge:any=async(r:any,o:any)=>{writes.push({r,o});return {code:0,stdout:'850 TWD',stderr:''};};
 const engine=new LayaFinance({judge,backend,bridge,accounts:async()=>['assets:cash'],today:()=> '2026-10-06'});
 return {engine,calls,writes};
}
test('one handoff owns routing/categories and fixed user facts; no model decision tools',async()=>{
 const h=fixture();const r=await h.engine.run('午餐120、飲料50',{session:'a'});assert.equal(r.state,'done');assert.equal(h.writes.length,1);assert.deepEqual(h.writes[0].transactions.map((r:any)=>r.amount),['120','50']);assert.ok(h.calls.some(c=>c.q.category_0&&c.q.category_1));
});
test('unknown/low-confidence choices and Laya outage never write',async()=>{
 for(const judge of [async()=>{throw new Error('connection refused')},async()=>({route:{...choice('record'),status:'uncertain' as const}}),async()=>({route:choice('banana')})]){let writes=0;const engine=new LayaFinance({judge,backend:async()=>{writes++;return '';},bridge:async()=>{writes++;return {code:0,stdout:'',stderr:''}},accounts:async()=>[]});const r=await engine.run('午餐120',{session:'a'});assert.notEqual(r.state,'done');assert.equal(writes,0);}
});
test('none, quoted expense, hypothetical and unsupported amounts cannot mutate',async()=>{
 for(const text of ['午餐120不要記帳','如果午餐120元','<quoted_selection>午餐120</quoted_selection>你好','午餐可能150或1500','300 ㄒㄧ ㄔㄜˋ']){const h=fixture();await h.engine.run(text,{session:'a'});assert.equal(h.writes.length,0,text);}
 const h=fixture({route:'none'});assert.equal((await h.engine.run('午餐120',{session:'a'})).state,'released');assert.equal(h.writes.length,0);
});
test('binding categories win and invalid category responses fail before write',async()=>{
 const h=fixture({category_0:'alien'});await h.engine.run('午餐120',{session:'a'});assert.equal(h.writes.length,0);
 let calls=0;let payload:any;const bindingEngine=new LayaFinance({today:()=> '2026-10-06',accounts:async()=>[],bridge:async()=>({code:0,stdout:'',stderr:''}),judge:async(s,q)=>{calls++;assert.deepEqual(Object.keys(q),['route']);return {route:choice('record')};},backend:async(a,p)=>{if(a[0]==='classify')return JSON.stringify({...evidence,binding:true,suggestion:{account:'expenses:custom:binding'}});payload=p;return 'Records ready: 1; duplicates skipped: 0';}});
 const r=await bindingEngine.run('公司午餐120',{session:'a'});assert.equal(r.state,'done');assert.equal(calls,1);assert.equal(payload.transactions[0].debit,'expenses:custom:binding');
});
test('ambiguity never repeats last receipt; explicit repeat needs actual successful receipt',async()=>{
 const h=fixture();await h.engine.run('午餐120',{session:'a'});const r=await h.engine.run('多一筆',{session:'a'});assert.equal(r.state,'clarifying');assert.equal(h.writes.length,1);
 const other=fixture({route:'record',repeat:'repeat'});await other.engine.run('再一筆一樣的',{session:'a'});assert.equal(other.writes.length,0);
});
test('read query includes exact host period, no guessed balance from model',async()=>{
 const h=fixture({route:'query',operation:'query'});const r=await h.engine.run('這個月我的現金還剩多少',{session:'a'});assert.equal(r.state,'done');assert.ok(r.text.includes('850'));assert.equal(h.writes[0].r.data.begin,'2026-10-01');assert.equal(h.writes[0].r.data.end,'2026-11-01');assert.equal(h.writes[0].r.data.report,'balance');assert.deepEqual(h.writes[0].r.data.account,['assets:cash']);
});
test('complex operations are previewed, partial confirmation cannot execute, unrelated input cancels',async()=>{
 let executed=0;const judge:Judge=async(s,q)=>Object.fromEntries(Object.keys(q).map(k=>[k,choice(k==='route'?'change':'delete')]));
 const engine=new LayaFinance({judge,backend:async()=>'',accounts:async()=>[],bridge:async(r,o)=>{if(o.mode==='execute')executed++;return {code:0,stdout:'[1] 2026-10-06 Lunch 120 TWD',stderr:'',snapshot:'snap',delete_token:'host-token'};},today:()=> '2026-10-06'});
 const r=await engine.run('刪除描述含Lunch的交易',{session:'a'});assert.equal(r.state,'awaiting_confirmation');assert.equal(executed,0);
 await engine.run('只確認刪除一筆',{session:'a'});assert.equal(executed,0);
 await engine.run('你好',{session:'a'});await engine.run('確認',{session:'a'});assert.equal(executed,0);
});
test('overlap, cancellation and uncertain writes never replay or store success memory',async()=>{
 const h=fixture();const ac=new AbortController();ac.abort();await h.engine.run('午餐120',{session:'a',signal:ac.signal});assert.equal(h.writes.length,0);
 let release:any;let calls=0;const slow:Judge=async()=>{calls++;return new Promise(r=>release=r)};const engine=new LayaFinance({judge:slow,backend:async()=>'',bridge:async()=>({code:0,stdout:'',stderr:''}),accounts:async()=>[]});const first=engine.run('午餐120',{session:'a'});const second=await engine.run('咖啡80',{session:'a'});assert.equal(second.state,'busy');assert.equal(calls,1);release({route:choice('none')});await first;
});
