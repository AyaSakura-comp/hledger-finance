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
test('gateway envelopes and history are stripped; spoken amounts become digits',async()=>{
 const {userMessage}=await import('./core.ts');
 const piweb='[PiWeb cross-harness context]\n{"role":"user","text":"【記帳】1500 午餐"}\nEnd of context.]\n\n[Web user: web]\n確認 4a4912e3-871d-47a4-ae28-d65391509209';
 assert.equal(userMessage(piweb),'確認 4a4912e3-871d-47a4-ae28-d65391509209');
 assert.equal(userMessage('[Web user: web]\n今天天氣如何'),'今天天氣如何');
 assert.equal(userMessage('[Discord user: aya]\n\n[Voice message transcription: voice.ogg]\n剛剛在全聯買東西 花了四百六十塊'),'剛剛在全聯買東西 花了460 元');
 assert.equal(userMessage('幫我記帳 午餐一百五十元'),'幫我記帳 午餐150 元');
 assert.equal(userMessage('收到薪水五萬元、十二塊、兩千零五元、一萬兩千三百元'),'收到薪水50000 元、12 元、2005 元、12300 元');
 assert.equal(userMessage('一百分的考試'),'一百分的考試');
});
test('voice filler is trimmed from the fast-path description only',async()=>{
 const {parseExpense,userMessage}=await import('./core.ts');
 assert.equal(parseExpense(userMessage('幫我記帳 午餐一百五十元'),'2026-10-04')?.description,'午餐');
 assert.equal(parseExpense(userMessage('剛剛在全聯買東西 花了四百六十塊'),'2026-10-04')?.description,'在全聯買東西');
 assert.equal(parseExpense('記一下，咖啡 95 元','2026-10-04')?.amount,'95');
 assert.equal(parseExpense('花花 50 元','2026-10-04')?.description,'花花');
});
test('amount-first with a unit needs no space before the item',async()=>{
 const {parseExpense,userMessage}=await import('./core.ts');
 const e=parseExpense(userMessage('[Web user: web]\n【記帳】300元洗車'),'2026-10-04');
 assert.equal(e?.amount,'300');assert.equal(e?.description,'洗車');
 assert.equal(parseExpense('300洗車','2026-10-04'),null); // bare digits glued to text stay ambiguous
});
test('batch: unrequested payment is ignored; income routes by payment rules; one write',async()=>{
 const {FinanceBatch}=await import('./core.ts');const writes:any[]=[];
 const backend=async(args:string[],payload?:any)=>{if(args[0]==='classify')return JSON.stringify({binding:false,suggestion:{account:'expenses:food:dining'},categories:[{account:'expenses:food:dining'},{account:'expenses:food:drinks'}]});writes.push(payload);return 'Records ready: 3; duplicates skipped: 0';};
 const b=new FinanceBatch(backend,async()=>['assets:cash:wallet','income:salary'],()=>{},async()=>({TWD:'assets:cash:wallet'}));
 await b.prepare('午餐 120、飲料 50、收到薪水 50000 元','2026-10-04','t1');
 assert.deepEqual(b.entries.map(e=>e.kind),['expense','expense','income']);
 const r=await b.commit([{category:'expenses:food:dining',payment:'cash'},{category:'expenses:food:drinks'},{category:'income:salary'}]);
 const rows=writes[0].transactions;assert.equal(writes.length,1);
 assert.equal(rows[0].credit,undefined);assert.equal(rows[2].debit,'assets:cash:wallet');assert.equal(rows[2].credit,'income:salary');
 assert.match(r,/已記帳 3 筆/);
});
test('unconverted Zhuyin is never accepted as an item name',async()=>{
 const {parseEntries,userMessage}=await import('./core.ts');
 assert.equal(parseEntries(userMessage('【記帳】300 ㄒㄧ ㄔㄜˋ'),'2026-10-04'),null);
 assert.equal(parseEntries('300 洗車','2026-10-04')?.[0].description,'洗車');
});
