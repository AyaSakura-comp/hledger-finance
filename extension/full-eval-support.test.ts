import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { scopedPath, validateCLI, checkTransactions } from './full-eval-support.ts';
test('fixture access cannot escape, including symlinks',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'hfin-eval-path-'));await writeFile(join(dir,'fixture.csv'),'safe');
 assert.equal(await scopedPath(dir,'fixture.csv'),join(dir,'fixture.csv'));
 for(const path of ['/etc/passwd','../outside','book/main.journal'])await assert.rejects(scopedPath(dir,path),/refused/);
 await symlink('/etc',join(dir,'escape'));await assert.rejects(scopedPath(dir,'escape/passwd'),/refused/);
});
test('CLI guard rejects journal override, output escape, cloud backup and native mutations',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'hfin-eval-cli-'));
 for(const args of [['stats','--journal','/tmp/other'],['visualize','--output','/etc/image.png'],['backup','--remote','gdrive:any']])await assert.rejects(validateCLI(dir,'hfin',args),/refused/);
 for(const args of [['add'],['print','-f','/etc/passwd'],['balance','--output-file=/etc/out']])await assert.rejects(validateCLI(dir,'hledger',args),/refused/);
 assert.deepEqual(await validateCLI(dir,'hfin',['classify','--description','午餐']),['classify','--description','午餐']);
});
test('helper guard rejects abbreviated options and backup override attempts',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'hfin-eval-abbrev-'));
 for(const args of [
  ['visualize','--out','/etc/image.png'], ['visualize','--out=/etc/image.png'],
  ['backup','--remote','qa:','--rem','gdrive:other'],
  ['backup','--remote=qa:','--rem=gdrive:other'],
  ['backup','--remote','qa:','--remote','gdrive:other'],
 ])await assert.rejects(validateCLI(dir,'hfin',args),/refused/i);
 assert.deepEqual(await validateCLI(dir,'hfin',['backup','--remote','qa:']),['backup','--remote','qa:']);
});
test('readonly/render report PNG scope permits book/reports but not traversal or symlink escapes',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'hfin-eval-report-'));
 assert.equal(await scopedPath(dir,'book/reports/chart.png',true),join(dir,'book/reports/chart.png'));
 await assert.rejects(scopedPath(dir,'book/reports/../../main.journal',true),/refused/);
 await assert.rejects(scopedPath(dir,'book/reports/../../../../etc/photo.png',true),/refused/);
 assert.equal((await validateCLI(dir,'hfin',['visualize','--output','book/reports/chart.png']))[2],join(dir,'book/reports/chart.png'));
});
test('correctness checker catches wrong amount/date/category and extra rows',()=>{
 const tx=(date:string,account:string,quantity:string)=>({tdate:date,tdescription:'Lunch',tpostings:[{paccount:account,pamount:[{acommodity:'TWD',aquantity:{decimalMantissa:quantity,decimalPlaces:0}}]},{paccount:'assets:cash',pamount:[{acommodity:'TWD',aquantity:{decimalMantissa:'-'+quantity,decimalPlaces:0}}]}]});
 const expected=[{date:'2026-10-03',account:'expenses:food:dining',amount:'150',currency:'TWD'}];
 assert.deepEqual(checkTransactions([tx('2026-10-03','expenses:food:dining','150')],expected),[]);
 assert.ok(checkTransactions([tx('2025-10-03','expenses:food:dining','1500')],expected).length>0);
 assert.ok(checkTransactions([tx('2026-10-03','expenses:travel','150')],expected).length>0);
 assert.ok(checkTransactions([tx('2026-10-03','expenses:food:dining','150'),tx('2026-10-03','expenses:food:dining','150')],expected).length>0);
});
