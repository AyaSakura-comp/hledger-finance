import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Workflow, parseData } from './workflows.ts';
import { fileStore } from './pending-store.ts';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
// Test bridges answer the host's expense-category check like the real backend.
const catalogReply={code:0,stdout:JSON.stringify({categories:[{account:'expenses:food:dining'},{account:'expenses:food:groceries'},{account:'expenses:transport:transit'},{account:'expenses:shopping'}]}),stderr:''};
const withCatalog=(bridge:any)=>async(r:any,o:any)=>r.operation==='classify'?catalogReply:bridge(r,o);
const W=(bridge:any,...rest:any[])=>new (Workflow as any)(withCatalog(bridge),...rest) as Workflow;
const preview={code:0,stdout:'150 TWD debit=expenses:food:dining credit=assets:cash',stderr:'preview',snapshot:'a'};
const request={operation:'ingest',data:{transactions:[{kind:'expense',description:'Lunch',amount:'150',debit:'expenses:food:dining',credit:'assets:cash'}]}};
test('mutation proposal is immutable and requires a later exact host confirmation',async()=>{
 const calls:any[]=[];
 const w=W(async(r,o)=>{calls.push(structuredClone({r,o}));return o.mode==='preview'?preview:{code:0,stdout:'written',stderr:'',snapshot:'a'};},()=> '2026-10-03');
 w.begin('午餐150元');
 const p=await w.propose(request);
 assert.equal(w.state,'awaiting_confirmation');assert.equal(calls.length,1);
 request.data.transactions[0].amount='999';
 assert.match(p.text,/確認/);assert.ok(w.confirmation);
 assert.equal(await w.confirm('確認午餐150元而已'),null);assert.equal(calls.length,1);
 const receipt=await w.confirm('確認 '+w.confirmation);
 assert.equal(w.state,'done');assert.match(receipt!.text,/written/);
 assert.equal(calls[1].r.data.transactions[0].amount,'150');
 assert.equal(calls[1].o.expected_snapshot,'a');
 assert.equal(await w.confirm('確認 '+w.confirmation),null);
 request.data.transactions[0].amount='150';
});
test('delete backend token is never accepted as user authorization; subsets do not execute',async()=>{
 const calls:any[]=[];const w=W(async(r,o)=>{calls.push({r,o});return o.mode==='preview'?{...preview,code:3,delete_token:'0123456789abcdef',stdout:'Lunch 800 AND Lunch refund 50'}:{...preview,stdout:'deleted'};});
 w.begin('刪除Lunch');await w.propose({operation:'delete',data:{description:['Lunch']}});
 assert.equal(await w.confirm('確認 0123456789abcdef'),null);
 assert.equal(await w.confirm('只確認Lunch800'),null);assert.equal(calls.length,1);
 await w.confirm('確認 '+w.confirmation);
 assert.equal(calls[1].o.delete_token,'0123456789abcdef');
});
test('single-flight, cancellation, failed writes and stale authorization never auto-retry',async()=>{
 let calls=0;const w=W(async(_r,o)=>{calls++;return o.mode==='preview'?preview:{...preview,code:1,stderr:'stale snapshot'};});
 w.begin('記帳');await w.propose(request);assert.throws(()=>w.begin('new'),/pending/);
 const token=w.confirmation;const r=await w.confirm('確認 '+token);assert.match(r!.text,/stale snapshot/);assert.equal(w.state,'failed');
 assert.equal(await w.confirm('確認 '+token),null);assert.equal(calls,2);
 w.begin('new');await w.propose(request);w.cancel();assert.equal(w.state,'cancelled');assert.equal(await w.confirm('確認 '+token),null);
});
test('read-only reports finish without mutation confirmation and preserve verified image markers',async()=>{
 const w=W(async()=>({...preview,stdout:'[[image: /tmp/chart.png]]',images:['/tmp/chart.png']}));
 w.begin('畫報表');const r=await w.propose({operation:'visualize',data:{currency:'TWD'}});
 assert.equal(w.state,'done');assert.match(r.text,/\[\[image: \/tmp\/chart.png\]\]/);assert.equal(w.confirmation,undefined);
});
test('host keeps an explicit all-currency chart request from becoming a single-currency chart',async()=>{
 let request:any;const w=W(async(r)=>{request=r;return preview;});w.begin('每個幣別各一張財務圖表');
 await w.propose({operation:'visualize',data:{currency:'TWD'}});assert.equal(request.data.currency,undefined);
});
test('only failed previews can reopen collection; execution failures cannot be repaired/replayed',async()=>{
 let n=0;const w=W(async(_r,o)=>{n++;return n===1||o.mode==='execute'?{...preview,code:2,stderr:'format error'}:preview;});w.begin('記帳');
 await assert.rejects(w.propose(request),/format/);assert.equal(w.resumeDraft(),true);
 await w.propose(request);await w.confirm('確認 '+w.confirmation);assert.equal(w.resumeDraft(),false);
});
test('host rejects explicit cross-currency facts without exactly one transfer row before preview',async()=>{
 let calls=0;const w=W(async()=>{calls++;return preview;});w.begin('從assets:bank:twd轉出3200 TWD，收到100 USD到assets:bank:usd');
 await assert.rejects(w.propose({operation:'ingest',data:{transactions:[{kind:'expense',description:'Exchange',amount:'3200',currency:'TWD',debit:'assets:bank:twd',credit:'assets:bank:usd'}]}}),/Explicit cross-currency/);
 assert.equal(calls,0);
});
test('transport JSON rejects duplicate keys and numeric money before normalization',()=>{
 assert.throws(()=>parseData('{"transactions":[{"amount":"150","amount":"900"}]}'),/Duplicate/);
 assert.throws(()=>parseData('{"transactions":[{"amount":0.1}]}'),/string/);
 assert.equal(parseData('{"transactions":[{"amount":"150"},{"amount":"900"}]}').transactions.length,2);
});
test('expired tokens and abort signals cannot execute a mutation',async()=>{
 let now=0,calls=0;const w=W(async()=>{calls++;return preview;},undefined,()=>now);
 w.begin('記帳');await w.propose(request);const token=w.confirmation;now=600001;
 assert.equal(await w.confirm('確認 '+token),null);assert.equal(calls,1);
 w.cancel();w.begin('記帳');await w.propose(request);
 await assert.rejects(w.confirm('確認 '+w.confirmation,AbortSignal.abort()),/abort/i);assert.equal(calls,2);
});
test('host owns the source tag and explicit cross-currency transfer facts',async()=>{
 const calls:any[]=[];const w=W(async(r)=>{calls.push(structuredClone(r));return preview;},()=>'2026-10-03');
 w.begin('PDF',"pdf");
 await w.propose({operation:'ingest',data:{transactions:[{kind:'expense',description:'QA',amount:'189',tags:['receipt-pdf','source:pdf-receipt','project:qa']}]}});
 assert.deepEqual(calls[0].data.transactions[0].tags.slice(0,2),['source:pdf','project:qa']);assert.ok(!calls[0].data.transactions[0].tags.includes('receipt-pdf'));
 const x=W(async(r)=>{calls.push(structuredClone(r));return preview;},()=>'2026-10-03');
 x.begin('今天從assets:bank:twd轉出3200 TWD，收到100 USD到assets:bank:usd。');
 await x.propose({operation:'ingest',data:{transactions:[{kind:'transfer',description:'FX',amount:'3200',currency:'TWD',debit:'assets:bank:twd',credit:'assets:bank:usd',credit_amount:'100',credit_currency:'USD'}]}});
 const row=calls[1].data.transactions[0];
 assert.equal(row.debit,'assets:bank:usd');assert.equal(row.credit,'assets:bank:twd');assert.equal(row.debit_amount,'100');assert.equal(row.debit_currency,'USD');assert.equal(row.credit_amount,undefined);
});
test('transport JSON drops only unmatched trailing closers',()=>{
 assert.equal(parseData('{"transactions":[{"amount":"189"}]}}').transactions[0].amount,'189');
 assert.throws(()=>parseData('{"transactions":[{"amount":"189"}'));
 assert.throws(()=>parseData('{"a":"1"}x'));
});
test('integer coercion keeps historical controls.count numeric',()=>{
 const d=parseData('{"payload":{"transactions":[{"amount":150}]},"controls":{"count":2,"totals":{"expense:TWD":"150"}}}');
 assert.equal(d.controls.count,2);assert.equal(d.payload.transactions[0].amount,'150');
 assert.equal(parseData('{"count":3,"total":900}').count,'3');
});
test('lenient JSON removes an early root closer before more members',()=>{
 const d=parseData('{"payload":{"transactions":[{"amount":"1"}]}}, "controls":{"count":1,"totals":{}}}');
 assert.equal(d.controls.count,1);assert.equal(d.payload.transactions.length,1);
});
test('explicit cross-currency transfer refuses a duplicated received leg',async()=>{
 const w=W(async()=>preview,()=>'2026-10-03');w.begin('從assets:bank:twd轉出3200 TWD，收到100 USD到assets:bank:usd');
 await assert.rejects(w.propose({operation:'ingest',data:{transactions:[{kind:'transfer',description:'FX',amount:'3200',currency:'TWD'},{kind:'income',description:'USD',amount:'100',currency:'USD',debit:'assets:bank:usd',credit:'income:other'}]}}),/double count/);
});
test('a single explicit payment account fills missing expense credit only',async()=>{
 const calls:any[]=[];const w=W(async(r)=>{calls.push(structuredClone(r));return preview;},()=>'2026-10-03');
 w.begin('午餐150元、咖啡95元，付款都用assets:cash');
 await w.propose({operation:'ingest',data:{transactions:[{kind:'expense',description:'午餐',amount:'150',debit:'expenses:food:dining'},{kind:'expense',description:'咖啡',amount:'95',debit:'expenses:food:dining',credit:'liabilities:card'}]}});
 assert.equal(calls[0].data.transactions[0].credit,'assets:cash');assert.equal(calls[0].data.transactions[1].credit,'liabilities:card');
 const x=W(async(r)=>{calls.push(structuredClone(r));return preview;},()=>'2026-10-03');
 x.begin('午餐付款assets:cash，咖啡付款liabilities:card');
 await x.propose({operation:'ingest',data:{transactions:[{kind:'expense',description:'午餐',amount:'150'}]}});
 assert.equal(calls[1].data.transactions[0].credit,undefined);
});
test('lenient JSON closes a body truncated at the end only',()=>{
 assert.equal(parseData('{"transactions":[{"amount":"189","tags":["a"]}]').transactions[0].amount,'189');
 assert.throws(()=>parseData('{"a":"1" "b":"2"}'));
});
test('receipt sources reject identical duplicate rows; failed inspection keeps collecting',async()=>{
 const w=W(async(r)=>r.operation==='native'?{code:2,stdout:'',stderr:'bad options'}:preview,()=>'2026-10-03');w.begin('receipt','image');
 await assert.rejects(w.inspect({operation:'native',data:{report:'accounts',options:''}}),/bad options/);
 assert.equal(w.state,'collecting');
 const row={kind:'expense',description:'QA',amount:'189',debit:'expenses:food:dining',credit:'assets:cash'};
 await assert.rejects(w.propose({operation:'ingest',data:{transactions:[row,{...row}]}}),/duplicate|One receipt/);
});
test('a preview can be confirmed exactly once from another process, bound to its journal and snapshot',async()=>{
 const dir=mkdtempSync(join(tmpdir(),'fin-pending-'));const calls:any[]=[];
 const bridge=async(r:any,o:any)=>{calls.push(structuredClone({r,o}));return o.mode==='preview'?preview:{code:0,stdout:'written',stderr:''};};
 const a=W(bridge,()=>'2026-10-04',Date.now,fileStore('/j/main.journal',dir),'/j/main.journal');
 a.begin('午餐150元');await a.propose(request);const token=a.confirmation!;
 // New process: no in-memory state.
 const b=W(bridge,()=>'2026-10-04',Date.now,fileStore('/j/main.journal',dir),'/j/main.journal');
 assert.equal(await b.confirm('確認 not-a-token'),null);
 const other=W(bridge,()=>'2026-10-04',Date.now,fileStore('/other.journal',dir),'/other.journal');
 assert.equal(await other.confirm('確認 '+token),null);
 const r=await b.confirm('確認 '+token);assert.match(r!.text,/written/);
 assert.equal(calls.at(-1).o.expected_snapshot,'a');assert.equal(calls.at(-1).r.data.transactions[0].amount,'150');
 const c=W(bridge,()=>'2026-10-04',Date.now,fileStore('/j/main.journal',dir),'/j/main.journal');
 assert.equal(await c.confirm('確認 '+token),null);assert.equal(await a.confirm('確認 '+token),null);
 assert.equal(calls.filter(x=>x.o.mode==='execute').length,1);
});
test('cancel and expiry remove the persisted authorization',async()=>{
 const dir=mkdtempSync(join(tmpdir(),'fin-pending-'));let now=1000;let executed=0;
 const bridge=async(_r:any,o:any)=>{if(o.mode==='execute')executed++;return preview;};
 const a=W(bridge,()=>'2026-10-04',()=>now,fileStore('/j',dir),'/j');a.begin('x');await a.propose(request);const t1=a.confirmation!;a.cancel();
 assert.equal(await W(bridge,()=>'2026-10-04',()=>now,fileStore('/j',dir),'/j').confirm('確認 '+t1),null);
 const b=W(bridge,()=>'2026-10-04',()=>now,fileStore('/j',dir),'/j');b.begin('x');await b.propose(request);const t2=b.confirmation!;now+=600001;
 const r=await W(bridge,()=>'2026-10-04',()=>now,fileStore('/j',dir),'/j').confirm('確認 '+t2);
 assert.match(r!.text,/過期/);assert.equal(executed,0);
});
test('invented expense accounts are rejected unless the user wrote them',async()=>{
 const calls:any[]=[];const w=W(async(r:any)=>{calls.push(r.operation);return preview;},()=>'2026-10-04');
 w.begin('幫我記這張收據');
 await assert.rejects(w.propose({operation:'ingest',data:{transactions:[{kind:'expense',description:'全聯',amount:'460',debit:'expenses:Groceries',credit:'assets:cash'}]}}),/Unknown expense account expenses:groceries; choose exactly one of: .*expenses:food:groceries/);
 assert.deepEqual(calls,[]);
 const x=W(async()=>preview,()=>'2026-10-04');x.begin('毛孩市集 230 元，記到 expenses:pets:supplies');
 await x.propose({operation:'ingest',data:{transactions:[{kind:'expense',description:'毛孩市集',amount:'230',debit:'expenses:pets:supplies',credit:'assets:cash'}]}});
 assert.equal(x.state,'awaiting_confirmation');
});
test('host normalizes account casing and NTD, and owns receipt import ids',async()=>{
 const calls:any[]=[];const w=W(async(r:any)=>{calls.push(structuredClone(r));return preview;},()=>'2026-10-04');
 w.begin('幫我記這張收據','image','abc');
 await w.propose({operation:'ingest',data:{defaults:{currency:'NTD'},transactions:[{kind:'expense',description:'全聯',amount:'460',debit:'Expenses:Food:Groceries',credit:'Assets:Cash',import_id:'PX-1'}]}});
 const row=calls.find(c=>c.operation==='ingest').data;
 assert.equal(row.defaults.currency,'TWD');assert.equal(row.transactions[0].debit,'expenses:food:groceries');assert.equal(row.transactions[0].credit,'assets:cash');assert.equal(row.transactions[0].import_id,'src-abc-0');
 const x=W(async(r:any)=>{calls.push(structuredClone(r));return preview;},()=>'2026-10-04');
 x.begin('記到 liabilities:Card-A');
 await x.propose({operation:'ingest',data:{transactions:[{kind:'expense',description:'x',amount:'1',debit:'expenses:shopping',credit:'liabilities:Card-A'}]}});
 assert.equal(calls.at(-1).data.transactions[0].credit,'liabilities:Card-A');
});
test('parallel read-only inspections are allowed; a proposal waits for them',async()=>{
 let release!:()=>void;const gate=new Promise<void>(r=>release=r);
 const w=W(async(r:any)=>{if(r.operation==='settings')await gate;return {code:0,stdout:'{}',stderr:''};},()=>'2026-10-04');w.begin('x');
 const a=w.inspect({operation:'settings',data:{}});const b=w.inspect({operation:'native',data:{report:'accounts'}});
 await b;await assert.rejects(w.propose(request),/concurrent/);release();await a;assert.equal(w.state,'collecting');
});
test('a receipt source cannot be booked as total plus line items unless the user asks to split',async()=>{
 const w=W(async()=>preview,()=>'2026-10-04');w.begin('幫我記這張收據','image','r1');
 const rows=[{kind:'expense',description:'全聯',amount:'460'},{kind:'expense',description:'鮮乳',amount:'92'}];
 await assert.rejects(w.propose({operation:'ingest',data:{transactions:rows}}),/One receipt is one payment/);
 const x=W(async()=>preview,()=>'2026-10-04');x.begin('這張收據請逐項拆開記','image','r2');
 await x.propose({operation:'ingest',data:{transactions:rows}});assert.equal(x.state,'awaiting_confirmation');
});
