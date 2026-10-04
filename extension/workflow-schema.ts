import { Type } from 'typebox';
import { StringEnum } from '@earendil-works/pi-ai';
import { readonlyOperations, mutationOperations } from './workflows.ts';
const S=()=>Type.String({maxLength:20000});
const O=<T>(value:T)=>Type.Optional(value as any);
const kinds=StringEnum(['expense','income','transfer','refund','opening-balance','adjustment']);
const row=Type.Object({kind:kinds,description:S(),amount:Type.String({description:'Positive decimal magnitude as string; NEVER source sign'}),date:O(S()),currency:O(S()),debit:O(S()),credit:O(S()),tags:O(Type.Array(S())),import_id:O(S()),installments:O(Type.Integer({minimum:1})),fee:O(S()),fee_account:O(S()),debit_amount:O(S()),debit_currency:O(S())},{additionalProperties:false});
const defaults=Type.Object({kind:O(kinds),date:O(S()),currency:O(S()),debit:O(S()),credit:O(S()),tags:O(Type.Array(S())),installments:O(Type.Integer({minimum:1})),fee:O(S()),fee_account:O(S())},{additionalProperties:false});
const batch=Type.Object({defaults:O(defaults),transactions:Type.Array(row,{minItems:1,maxItems:1200})},{additionalProperties:false});
const nativeOptions=Type.Object({'begin':O(S()),'end':O(S()),'period':O(S()),'forecast':O(S()),'market':O(Type.Boolean()),'value':O(S()),'monthly':O(Type.Boolean()),'daily':O(Type.Boolean()),'weekly':O(Type.Boolean()),'quarterly':O(Type.Boolean()),'yearly':O(Type.Boolean()),'depth':O(S()),'flat':O(Type.Boolean()),'tree':O(Type.Boolean()),'cost':O(Type.Boolean()),'investment':O(S()),'pnl':O(S()),'cashflow':O(S()),'today':O(S()),'output-format':O(S())},{additionalProperties:false});
// Deliberately enumerate field shapes instead of a generic Record/Unknown:
// constrained local models otherwise hallucinate "kind: posting" or flags.
export const dataSchema=Type.Object({
 transactions:O(Type.Array(row,{minItems:1,maxItems:1200})),defaults:O(defaults),payload:O(batch),
 filter:O(Type.Object({period:O(S()),begin:O(S()),end:O(S()),account:O(Type.Array(S())),description:O(Type.Array(S())),tag:O(Type.Array(S())),where:O(Type.Array(S()))},{additionalProperties:false})),
 controls:O(Type.Object({count:Type.Integer({minimum:1}),totals:Type.Record(Type.String(),S())},{additionalProperties:false})),
 report:O(S()),period:O(S()),begin:O(S()),end:O(S()),account:O(Type.Array(S())),description:O(Type.Union([S(),Type.Array(S())])),tag:O(Type.Array(S())),where:O(Type.Array(S())),interval:O(StringEnum(['daily','weekly','monthly','quarterly','yearly'])),format:O(StringEnum(['txt','csv','json','html'])),currency:O(S()),title:O(S()),strict:O(Type.Boolean()),rules_only:O(Type.Boolean()),
 start:O(S()),total:O(S()),count:O(S()),debit:O(S()),credit:O(S()),fee:O(S()),fee_account:O(S()),loan_id:O(S()),principal:O(S()),annual_rate:O(S()),payment:O(S()),paid_periods:O(S()),remaining_periods:O(S()),liability:O(S()),interest_account:O(S()),payment_account:O(S()),rounding:O(S()),
 kind:O(StringEnum(['classification','payment'])),value:O(Type.Object({rules:O(Type.Array(Type.Object({pattern:S(),account:S()},{additionalProperties:false}))),currency_accounts:O(Type.Record(Type.String(),S())),require_match:O(Type.Boolean())},{additionalProperties:false})),remote:O(S()),options:O(nativeOptions),terms:O(Type.Array(S()))
},{additionalProperties:false});
const filters=['period','begin','end','account','description','tag','where'];
const fields:Record<string,string[]>={
 ingest:['defaults','transactions'],amend:['filter','defaults','transactions'],historical:['payload','controls'],
 installment:['start','description','total','count','currency','debit','credit','fee','fee_account'],
 loan:['loan_id','start','description','principal','annual_rate','payment','paid_periods','remaining_periods','currency','liability','interest_account','payment_account','rounding'],
 query:[...filters,'report','interval','format'],stats:['period','begin','end','where'],visualize:[...filters,'currency','title'],delete:filters,
 undo:[],rules:['kind','value'],backup:['remote'],native:['report','options','terms'],check:[],audit:['strict'],classify:['description','rules_only'],settings:[]
};
export function actionSchemaFor(operation?:string){
 const keys=operation?fields[operation]:Object.keys(dataSchema.properties);
 // Qwen XML tool parsers can reject nested optional/union object schemas.
 // Keep the transport flat; Python still validates the structured JSON.
 return Type.Object({operation:StringEnum(operation?[operation]:[...readonlyOperations,...mutationOperations].filter(o=>!['classify','native','settings'].includes(o))),data:Type.String({maxLength:200000,description:'JSON object encoded as a string. Allowed fields: '+keys.join(', ')+'. Use transaction kind expense/income/transfer/refund/opening-balance/adjustment; positive amounts as strings. Empty options: "{}". Never supply shell/journal/output/confirm fields.'})},{additionalProperties:false});
}
export const inspectSchema=Type.Object({operation:StringEnum(['classify','native','settings']),data:Type.String({maxLength:45000,description:'JSON object as string. classify: {"description":"merchant"}; native: {"report":"accounts"}; settings: {}. Optional native options must use canonical allowed flag names.'})},{additionalProperties:false});
export function operationHint(text:string):string|undefined{
 // The hint locks finance_action to one operation, so every pattern must be
 // specific: words like 改成/規則/balance also appear inside ordinary requests.
 if(/撤銷|undo/i.test(text))return 'undo';
 if(/備份|backup/i.test(text))return 'backup';
 if(/(?:新增|修改|設定|更新|加一條|add|update).{0,12}(?:分類|付款)?規則|規則.{0,6}(?:一律|改成|設為)/i.test(text))return 'rules';
 if(/(?:看|查|列出|目前|現有).{0,6}規則|^規則/.test(text))return 'settings';
 if(/(?:把|將).{0,24}(?<!不得|不要|別|勿|不可|不能)(?:改成|改為|改到)|(?:金額|分類|日期|那筆|這筆|上一筆).{0,10}(?<!不得|不要|別|勿|不可|不能)(?:改成|改為|修改|更正)|更正|amend/i.test(text))return 'amend';
 if(/刪除|刪掉|刪了|移除|delete/i.test(text))return 'delete';
 if(/儀表板|畫.*(?:圖|表)|圖表|visualiz/i.test(text))return 'visualize';
 if(/歷史|舊帳|遷移|historical|migration/i.test(text))return 'historical';
 if(/loan_id|貸款排程|本金|principal/i.test(text))return 'loan';
 if(/分期|installment/i.test(text))return 'installment';
 if(/audit|稽核/i.test(text))return 'audit';
 if(/檢查帳本|hledger check/i.test(text))return 'check';
 if(/餘額|還剩|剩多少|存款|帳上/.test(text))return 'query';
 if(/統計|stats|花了多少|花多少|多少錢|支出多少|收支|開銷|花費|消費多少/i.test(text))return 'stats';
 if(/查詢|明細|query/i.test(text))return 'query';
 if(/記帳|記這|記一下|收據|發票|元|塊|TWD|USD|JPY|收入|退款|轉帳|匯入/i.test(text))return 'ingest';
 // "午餐 120、飲料 50" / "花了 230": items with amounts are something to record.
 if((text.match(/[\p{L}]{1,12}\s*\d+(?:\.\d{1,2})?(?=\s*(?:[、，,；;\n]|$))/gu)?.length??0)>=1||/(?:花了|付了|繳了)\s*\d/.test(text))return 'ingest';
 return undefined;
}
