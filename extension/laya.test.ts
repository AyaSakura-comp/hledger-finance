import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { choose, layaClient, routeQuestion, cleanIntent } from './laya.ts';

const criteria={record:'Record a completed payment',none:'Not bookkeeping'};
test('choice requires complete finite probabilities, allowed label and margin',()=>{
 assert.equal(choose({choice:'record',probabilities:{record:0.94,none:0.06}},criteria).status,'match');
 assert.equal(choose({choice:'record',probabilities:{record:0.51,none:0.49}},criteria).status,'uncertain');
 for(const answer of [ {choice:'delete',probabilities:{record:1,none:0}}, {choice:'record',probabilities:{record:NaN,none:0}}, {choice:'record',probabilities:{record:1}}, {choice:'record',probabilities:{record:0.1,none:0.9}}, {choice:'record',probabilities:{record:1,none:0,extra:0}}, {choice:'record',probabilities:{record:3,none:0}} ])assert.throws(()=>choose(answer,criteria));
});
test('gateway quotes and attachment annotations are not user intent',()=>{
 assert.equal(cleanIntent('old history 午餐120\n[Web user: web]\n<quoted_selection>午餐 120 元</quoted_selection>\n> coffee50\n[Binary attachment: x]\n請問這是什麼？'),'請問這是什麼？');
 assert.ok(Object.hasOwn(routeQuestion.criteria,'none'));
});
test('local transport uses browser-intent protocol, no secrets or external endpoint',async()=>{
 let body:any;const server=createServer(async(req,res)=>{let raw='';for await(const b of req)raw+=b;body=JSON.parse(raw);res.setHeader('content-type','application/json');res.end(JSON.stringify({answers:{route:{choice:'none',probabilities:Object.fromEntries(Object.keys(routeQuestion.criteria).map(k=>[k,k==='none'?1:0]))}}}));});
 await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));
 try {const port=(server.address() as any).port;const client=layaClient({url:`http://127.0.0.1:${port}/v1/systemone`});const r=await client('午餐價格多少？',{route:routeQuestion});assert.equal(r.route.choice,'none');assert.equal(body.state,'午餐價格多少？');assert.equal(body.questions.route.type,'choice');assert.throws(()=>layaClient({url:'https://example.com/v1/systemone'}));}
 finally{server.closeAllConnections();await new Promise<void>(r=>server.close(()=>r()));}
});
test('transport fails closed on missing answers, oversize input, timeout and cancellation',async()=>{
 const server=createServer((req,res)=>{if(req.url==='/hang')return;res.end('{"answers":{}}');});await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));
 try{const base=`http://127.0.0.1:${(server.address() as any).port}`;await assert.rejects(layaClient({url:base})('午餐',{route:routeQuestion}));await assert.rejects(layaClient({url:base})('x'.repeat(50001),{route:routeQuestion}));await assert.rejects(layaClient({url:base+'/hang',timeoutMs:40})('午餐',{route:routeQuestion}));const ac=new AbortController();ac.abort();await assert.rejects(layaClient({url:base})('午餐',{route:routeQuestion},ac.signal));}
 finally{server.closeAllConnections();await new Promise<void>(r=>server.close(()=>r()));}
});
