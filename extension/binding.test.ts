// Regression found by full Qwen evaluation (fixed): a custom binding account
// must be selectable even before any historical journal use.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { FinanceFlow } from './core.ts';
test('binding custom account must be selectable even before any historical journal use',async()=>{
 const evidence={binding:true,suggestion:{account:'expenses:pets:supplies',source:'custom-rule'},categories:[{account:'expenses:pets'}]};
 let written=false;
 const flow=new FinanceFlow(async args=>args[0]==='classify'?JSON.stringify(evidence):(written=true,'Records ready: 1; duplicates skipped: 0'));
 await flow.prepare('毛孩市集230元','2026-10-03','binding-regression');
 await flow.commit('expenses:pets:supplies');
 assert.equal(written,true);
});
