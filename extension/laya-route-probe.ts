import {layaClient,type Question} from './laya.ts';
const j=layaClient();
const simple:Question={type:'choice',instructions:'這句話要記帳還是問問題？',criteria:{record:'已發生消費，記帳',query:'詢問自己帳本的花費或餘額',none:'一般問題，不是記帳'}};
const four:Question={...simple,criteria:{...simple.criteria,change:'修改、刪除、撤銷或備份自己的帳本'}};
for(const text of ['午餐120元','這個月花了多少','我的現金還剩多少','多一筆','幫我改finance extension API','咖啡價格120元，值得嗎','午餐120不要記帳','再一筆一樣的','刪除昨天午餐那筆','幫我畫支出圖','撤銷','以後公司午餐算工作支出','威力彩100元']) console.log(JSON.stringify({text,...await j(text,{simple,four})}));
