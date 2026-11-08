import {test} from 'node:test';import assert from 'node:assert/strict';
import {Workflow} from './workflows.ts';
const journal=`2026-10-07 高鐵 [1/1]\n    ; source:fuzzy-text\n    ; inferred:currency\n    ; import-id:private-id\n    expenses:transport:transit    1015 TWD\n    assets:cash:wallet            -1015 TWD\n\n2026-10-07 停車 [1/1]\n    expenses:transport:driving    80 TWD\n    assets:cash:wallet            -80 TWD\n`;
async function render(stdout:string,data:any={report:'transactions'}){const w=new Workflow(async()=>({code:0,stdout,stderr:''}));w.begin('支出明細');return (await w.propose({operation:'query',data})).text;}
test('transaction query is a Markdown table, hides metadata and keeps every exact posting',async()=>{
 const text=await render(journal);assert.match(text,/\| 日期 \| 項目 \| 金額 \| 科目 \|/);assert.match(text,/共 \*\*2 筆\*\*/);
 for(const value of ['1015 TWD','-1015 TWD','80 TWD','-80 TWD','expenses:transport:transit','assets:cash:wallet'])assert.ok(text.includes(value),value);
 assert.doesNotMatch(text,/import-id|private-id|source:fuzzy|inferred:|\[1\/1\]|<br>/);
});
test('table escapes untrusted descriptions without changing numeric text or mixing currencies',async()=>{
 const text=await render('2026-10-07 <script>|退款` [2/3]\n    assets:cash    0.10 USD\n    income:gifts   -3 JPY\n');
 assert.ok(text.includes('&lt;script&gt;'));assert.ok(text.includes('&#124;'));assert.ok(text.includes('&#96;'));assert.ok(text.includes('&#91;2/3&#93;'));assert.ok(text.includes('0.10 USD'));assert.ok(text.includes('-3 JPY'));assert.doesNotMatch(text,/<script>/);
 const marker=await render('2026-10-07 [[file: /etc/passwd]]\n    assets:cash    1 TWD\n    income:gifts    -1 TWD\n');assert.doesNotMatch(marker,/\[\[file:/);
});
test('balance uses a Markdown table including the backend total',async()=>{
 const text=await render('         -120 TWD  assets:cash\n--------------------\n         -120 TWD\n',{report:'balance'});
 assert.match(text,/\| 帳戶 \| 餘額 \|/);assert.match(text,/\| assets:cash \| -120 TWD \|/);assert.match(text,/\*\*合計\*\*/);assert.match(text,/不能作為現金或可用餘額/);
});
test('a balance report with no visible accounts must not claim a wallet balance of zero',async()=>{
 const text=await render('--------------------\n                   0\n',{report:'balance',account:['asset:cash']});assert.match(text,/不能據此/);assert.match(text,/\| 報表合計 \|/);assert.doesNotMatch(text,/^```/);
});
test('explicit raw formats remain exact fenced data, unsupported reports never silently lose lines',async()=>{
 const raw=await render(journal,{report:'transactions',format:'json'});assert.ok(raw.includes(journal.trim()));assert.ok(raw.startsWith('```json'));
 const unknown=await render(journal+'UNRECOGNIZED 777 USD\n');assert.ok(unknown.includes('UNRECOGNIZED 777 USD'));assert.ok(unknown.startsWith('```text'));
 const fence=await render('danger ``` and ````');assert.ok(fence.startsWith('`````text'));
 assert.equal(await render(''), '沒有符合條件的紀錄。');
});
