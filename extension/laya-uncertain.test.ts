import {test} from 'node:test';import assert from 'node:assert/strict';import {LayaFinance} from './laya-engine.ts';import type {Choice} from './laya.ts';
const d=(choice:string):Choice=>({choice,status:'match',confidence:1,margin:1,probabilities:{[choice]:1}});
test('uncertain execution discards earlier repeat memory; no automatic write retry',async()=>{
 let writes=0;
 const e=new LayaFinance({accounts:async()=>[],bridge:async()=>({code:0,stdout:'',stderr:''}),judge:async(s,q)=>Object.fromEntries(Object.keys(q).map(k=>[k,d(k==='route'?'record':k==='repeat'?'repeat':'expenses:food:dining')])),backend:async(a,p)=>{if(a[0]==='classify')return JSON.stringify({binding:false,suggestion:{account:'expenses:food:dining'},categories:[{account:'expenses:food:dining'}]});if(++writes===2)throw new Error('write outcome uncertain');return 'Records ready: 1; duplicates skipped: 0';}});
 assert.equal((await e.run('午餐120',{session:'s'})).state,'done');assert.equal((await e.run('午餐150',{session:'s'})).state,'failed');await e.run('再一筆一樣的',{session:'s'});assert.equal(writes,2);
});
