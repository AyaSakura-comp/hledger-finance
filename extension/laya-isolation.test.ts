import {test} from 'node:test';import assert from 'node:assert/strict';import {homedir} from 'node:os';import {join} from 'node:path';import {createFinanceExtension} from './index.ts';
test('experimental Laya mode requires an explicitly isolated journal and refuses production defaults',()=>{
 const saved=process.env.PI_FINANCE_JOURNAL;delete process.env.PI_FINANCE_JOURNAL;
 try{assert.throws(()=>createFinanceExtension({decisions:'laya'})({} as any),/isolated|experimental/i);assert.throws(()=>createFinanceExtension({decisions:'laya',journal:join(homedir(),'finance/main.journal')})({} as any),/isolated|experimental/i);}finally{if(saved===undefined)delete process.env.PI_FINANCE_JOURNAL;else process.env.PI_FINANCE_JOURNAL=saved;}
});
