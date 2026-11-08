// Read-only smoke probe. Evidence is diagnostic, not acceptance.
import { layaClient,routeQuestion,operationQuestion } from './laya.ts';
const judge=layaClient();
for(const text of ['午餐120元','這個月花了多少','我的現金還剩多少','多一筆','幫我改finance extension API','咖啡價格120元，值得嗎','午餐120不要記帳','再一筆一樣的']){
 const start=performance.now();try{const decisions=await judge(text,{route:routeQuestion,operation:operationQuestion});console.log(JSON.stringify({text,ms:performance.now()-start,decisions}));}catch(error){console.log(JSON.stringify({text,error:String(error)}));}
}
