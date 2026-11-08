import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdtempSync, writeFileSync, appendFileSync, readFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createFinance, toRow, dateOf, amountOf} from './finance.ts';

const TODAY='2026-11-08';

function book(){
 const dir=mkdtempSync(join(tmpdir(),'fin-'));
 const journal=join(dir,'main.journal');
 execFileSync('python3',[new URL('../scripts/finance.py',import.meta.url).pathname,'--journal',journal,'init']);
 writeFileSync(join(dir,'payment-account-rules.json'),JSON.stringify({currency_accounts:{TWD:'assets:cash:wallet',USD:'assets:bank:usd-account'},require_match:false}));
 execFileSync('git',['-C',dir,'add','-A']);execFileSync('git',['-C',dir,'commit','-qm','rules']);
 return {dir,journal};
}

function load(journal:string,host?:string){
 const tools=new Map<string,any>();
 const pi:any={registerTool:(t:any)=>tools.set(t.name,t),on:()=>{}};
 createFinance({journal,today:()=>TODAY,host})(pi);
 const call=async(name:string,args:any)=>{
  const t=tools.get(name);
  const r=await t.execute('id',t.prepareArguments?t.prepareArguments(args):args,undefined,undefined,{});
  return r.content[0].text as string;
 };
 return {tools,call};
}

test('helpers parse loose amounts and dates',()=>{
 assert.equal(amountOf('1,200元'),'1200');
 assert.equal(amountOf('NT$95'),'95');
 assert.equal(amountOf(-30),'30');
 assert.throws(()=>amountOf('很多'));
 assert.equal(dateOf('昨天',TODAY),'2026-11-07');
 assert.equal(dateOf('11/3',TODAY),'2026-11-03');
 assert.equal(dateOf('12/30',TODAY),'2025-12-30');
 assert.equal(dateOf('2026-1-5',TODAY),'2026-01-05');
});

test('rows: expense routes by currency, income/transfer get sides right',()=>{
 const routes={TWD:'assets:cash:wallet'};
 const e=toRow({description:'晚餐',amount:120},TODAY,'x-0',routes);
 assert.equal(e.debit,'auto');assert.equal(e.credit,undefined);
 assert.deepEqual(e.tags,['source:fuzzy-text','inferred:date','inferred:currency']);
 const i=toRow({description:'薪水',amount:'50000',kind:'income',category:'income:salary'},TODAY,'x-1',routes);
 assert.equal(i.debit,'assets:cash:wallet');assert.equal(i.credit,'income:salary');
 const t=toRow({description:'存錢',amount:1000,kind:'transfer',from:'assets:cash:wallet',to:'assets:deposits:twd'},TODAY,'x-2',routes);
 assert.equal(t.debit,'assets:deposits:twd');assert.equal(t.credit,'assets:cash:wallet');
 assert.throws(()=>toRow({description:'轉',amount:1,kind:'transfer'},TODAY,'x',routes),/from and to/);
});

test('record writes immediately, query sees it, undo reverts it',async()=>{
 const {journal}=book();const {call}=load(journal);
 const out=await call('finance_record',{entries:[
  {description:'晚餐',amount:'120'},
  {description:'咖啡',amount:95,date:'昨天',category:'expenses:food:drinks'},
  {description:'薪水',amount:50000,kind:'income',category:'income:salary'},
 ]});
 assert.match(out,/已記帳/);
 assert.match(out,/2026-11-07 咖啡 95 TWD｜expenses:food:drinks ← assets:cash:wallet/);
 assert.match(out,/薪水 50000 TWD｜assets:cash:wallet ← income:salary/);
 assert.match(readFileSync(journal,'utf8'),/晚餐/);
 const q=await call('finance_query',{report:'balance',account:'expenses'});
 assert.match(q,/215/);
 const u=await call('finance_undo',{});
 assert.match(u,/Undid/);
 assert.doesNotMatch(readFileSync(journal,'utf8'),/晚餐/);
});

test('single loose entry, new category is allowed and flagged',async()=>{
 const {journal}=book();const {call}=load(journal);
 const out=await call('finance_record',{description:'貓砂',amount:'350元',category:'expenses:pets:litter'});
 assert.match(out,/新科目：.*expenses:pets:litter/);
});

test('delete needs no confirmation and is undoable',async()=>{
 const {journal}=book();const {call}=load(journal);
 await call('finance_record',{entries:[{description:'全聯',amount:680},{description:'午餐',amount:150}]});
 const dry=await call('finance_delete',{description:'全聯',dry_run:true});
 assert.match(dry,/Would delete/);
 const del=await call('finance_delete',{description:'全聯'});
 assert.match(del,/已刪除/);
 assert.doesNotMatch(readFileSync(journal,'utf8'),/全聯/);
 await call('finance_undo',{});
 assert.match(readFileSync(journal,'utf8'),/全聯/);
});

test('hand edits to the journal are adopted, not refused',async()=>{
 const {journal}=book();const {call}=load(journal);
 appendFileSync(journal,'\n2026-11-08 手寫\n    expenses:food  10 TWD\n    assets:cash  -10 TWD\n');
 const out=await call('finance_record',{description:'早餐',amount:60});
 assert.match(out,/已記帳/);
});

test('errors come back as text the model can fix, not a block',async()=>{
 const {journal}=book();const {call}=load(journal);
 const out=await call('finance_record',{entries:[{description:'??',amount:'abc'}]});
 assert.match(out,/Not written: amount/);
});

test('cli passes through any subcommand',async()=>{
 const {journal}=book();const {call}=load(journal);
 const out=await call('finance_cli',{args:'check'});
 assert.doesNotMatch(out,/^exit/);
});

test('disabled on piscord by default',()=>{
 const {journal}=book();
 assert.equal(load(journal,'piscord').tools.size,0);
 assert.equal(load(journal,'piweb').tools.size,5);
});
