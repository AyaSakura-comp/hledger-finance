import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { parseExpense, FinanceFlow, pythonBackend, localDate } from './core.ts';

test('short explicit expense, amount-first and full-width input', () => {
 assert.deepEqual(parseExpense('【記帳】1500 午餐', '2026-10-03'), { description:'午餐', amount:'1500', date:'2026-10-03', inferredDate:true, currency:'TWD', inferredCurrency:true });
 assert.equal(parseExpense('午餐 １，５００ 元', '2026-10-03')?.amount, '1500');
 assert.equal(parseExpense('昨天咖啡 95 元', '2026-10-03')?.date,'2026-10-02');
 assert.equal(parseExpense('2026-09-30 午餐 80 元','2026-10-03')?.date,'2026-09-30');
 assert.equal(localDate(new Date('2026-10-02T17:00:00Z'),'Asia/Taipei'),'2026-10-03');
});
test('questions, examples, income, multiple amounts and unsupported instructions do not auto-post', () => {
 for(const text of ['如果午餐120元','午餐多少元？','範例：午餐120元','收到薪水50000元','【記帳】午餐1500或150元','【記帳】午餐150元，執行rm -rf','午餐 -150 元','午餐 0 元','午餐 1,50 元','午餐 3e2 元','午餐 150 美元','【記帳】1500 午餐 信用卡','2026-02-30 午餐 100元','明天午餐100元','上週午餐100元','午餐大約150元','午餐價格150元','想買午餐150元']) assert.equal(parseExpense(text,'2026-10-03'),null,text);
});
test('English examples, budgets and hypothetical intents do not auto-post',()=>{
 for(const text of ['example lunch 150','lunch budget 150','hypothetical lunch 150','estimated lunch 150','if lunch 150','sample lunch 150','income 150','refund 150','transfer 150','tomorrow lunch 150','cash lunch 150','credit card lunch 150'])assert.equal(parseExpense(text,'2026-10-03'),null,text);
 assert.equal(parseExpense('YouBike 15 元','2026-10-03')?.amount,'15');
});
const evidence = {binding:false,suggestion:{account:'expenses:food:dining',source:'builtin-rule'},categories:[{account:'expenses:food:dining',journal_count:1},{account:'expenses:food:lunch',journal_count:10}]};
test('legal state order and execution is at most once', async()=>{
 const calls:any[]=[];
 const backend=async(args:string[],payload?:unknown)=>{calls.push({args,payload}); return args[0]==='classify'?JSON.stringify(evidence):'Records ready: 1; duplicates skipped: 0\nAppended';};
 const flow=new FinanceFlow(backend); assert.equal(flow.state,'idle');
 await assert.rejects(flow.commit('expenses:food:lunch'),/state/);
 await flow.prepare('【記帳】1500 午餐','2026-10-03','test-input');
 assert.equal(flow.state,'ready');
 await assert.rejects(flow.commit('expenses:travel'),/category/);
 assert.equal(flow.state,'ready');
 const result=await flow.commit('expenses:food:lunch'); assert.equal(flow.state,'done');
 assert.ok(result.includes('1500')); assert.equal(calls.length,2);
 const tx=(calls[1].payload as any).transactions[0];
 assert.equal(tx.date,'2026-10-03'); assert.ok(tx.tags.includes('inferred:date')); assert.ok(tx.tags.includes('inferred:currency')); assert.equal(tx.import_id,'test-input');
 await assert.rejects(flow.commit('expenses:food:lunch'),/state/); assert.equal(calls.length,2);
});
test('binding category cannot be overridden; failed write cannot be retried',async()=>{
 const backend=async(args:string[])=>args[0]==='classify'?JSON.stringify({...evidence,binding:true}):Promise.reject(new Error('validation failed'));
 const flow=new FinanceFlow(backend); await flow.prepare('午餐150元','2026-10-03','test');
 await assert.rejects(flow.commit('expenses:food:lunch'),/binding/);
 await assert.rejects(flow.commit('expenses:food:dining'),/validation failed/); assert.equal(flow.state,'failed');
 await assert.rejects(flow.commit('expenses:food:dining'),/state/);
});
test('clarification ends without write',async()=>{
 const flow=new FinanceFlow(async()=>JSON.stringify(evidence)); await flow.prepare('午餐150元','2026-10-03','test');
 assert.equal(flow.clarify('金額是每人還是合計？'),'金額是每人還是合計？'); assert.equal(flow.state,'clarifying');
 await assert.rejects(flow.commit('expenses:food:dining'),/state/);
});
test('real CLI uses isolated journal, preserves routing and creates one commit; stable id skips replay',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'pi-finance-test-')); const journal=join(dir,'main.journal');
 const backend=pythonBackend({journal}); await backend(['init']);
 const flow=new FinanceFlow(backend); await flow.prepare('午餐1500元','2026-10-03','real-test'); await flow.commit('expenses:food:dining');
 const text=await readFile(journal,'utf8'); assert.ok(text.includes('1500 TWD')); assert.ok(text.includes('inferred:date')); assert.ok(text.includes('import-id:real-test'));
 const again=new FinanceFlow(backend); await again.prepare('午餐1500元','2026-10-03','real-test'); const msg=await again.commit('expenses:food:dining');
 assert.ok(msg.includes('重複')); assert.equal(await readFile(journal,'utf8'),text); await backend(['check']);
});

test('tool call is required only on OpenAI function-tool payloads',async()=>{
 const {requireToolCall}=await import('./core.ts');
 assert.equal((requireToolCall({messages:[],tools:[{type:'function',function:{name:'x'}}]}) as any).tool_choice,'required');
 assert.equal(requireToolCall({messages:[]}),undefined);
 assert.equal(requireToolCall({tools:[{name:'x',input_schema:{}}]}),undefined);
});
