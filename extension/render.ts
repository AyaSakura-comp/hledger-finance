// Presentation only: never calculate money or change backend filters.
export function cell(value:string):string{
 return value.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\|/g,'&#124;').replace(/`/g,'&#96;').replace(/\*/g,'&#42;').replace(/_/g,'&#95;').replace(/\[/g,'&#91;').replace(/\]/g,'&#93;').replace(/[\r\n]/g,' ');
}
export function fenced(text:string,language='text'):string{
 if(!text.trim())return '沒有符合條件的紀錄。';
 const longest=Math.max(0,...(text.match(/`+/g)??[]).map(s=>s.length));const fence='`'.repeat(Math.max(3,longest+1));
 return `${fence}${language}\n${text.trim()}\n${fence}`;
}
export function transactionTable(text:string):string|undefined{
 const rows:{date:string;description:string;posts:{account:string;amount:string}[]}[]=[];let current:typeof rows[number]|undefined;
 for(const line of text.split('\n')){
  if(!line.trim()||/^\s*;/.test(line))continue;
  const head=/^(\d{4}-\d{2}-\d{2}) (.+)$/.exec(line);
  if(head){current={date:head[1],description:head[2].replace(/ \[1\/1\]$/,''),posts:[]};rows.push(current);continue;}
  const post=/^\s{2,}(.+?)\s{2,}(.+?)\s*$/.exec(line);
  if(post&&current){current.posts.push({account:post[1],amount:post[2]});continue;}
  return undefined; // Unknown output must not quietly lose facts.
 }
 if(!rows.length||rows.some(r=>!r.posts.length))return undefined;
 return ['### 交易明細',`共 **${rows.length} 筆**。分錄金額保留帳本正負號，幣別不合併。`,'','| 日期 | 項目 | 金額 | 科目 |','| --- | --- | ---: | --- |',...rows.flatMap(r=>r.posts.map((p,i)=>`| ${i?'':cell(r.date)} | ${i?'↳ 同筆分錄':cell(r.description)} | ${cell(p.amount)} | ${cell(p.account)} |`))].join('\n');
}
export function balanceTable(text:string):string|undefined{
 const rows:string[]=[];const totals:string[]=[];let separator=false;
 for(const line of text.split('\n')){
  if(!line.trim())continue;
  if(/^\s*-{3,}\s*$/.test(line)){separator=true;continue;}
  const match=/^\s*(.+?)\s{2,}(\S.*?)\s*$/.exec(line);
  if(match&&!separator&&/\d/.test(match[1])&&/^\p{L}/u.test(match[2])){rows.push(`| ${cell(match[2])} | ${cell(match[1])} |`);continue;}
  if(separator&&/^\s*[-+]?\d/.test(line)){totals.push(line.trim());continue;}
  return undefined;
 }
 if(!rows.length)return totals.length?['沒有顯示符合條件的帳戶餘額；零餘額也可能被報表隱藏，不能據此確認現金為零。','','| 報表合計 |','| ---: |',...totals.map(total=>`| ${cell(total)} |`)].join('\n'):undefined;
 return ['### 帳戶餘額','','| 帳戶 | 餘額 |','| --- | ---: |',...rows,...totals.map((total,i)=>`| ${i?'':'**合計**'} | ${cell(total)} |`)].join('\n');
}
export function renderQuery(data:Record<string,any>,text:string):string{
 const format=String(data.format??'txt');
 if(format!=='txt')return fenced(text,format==='json'?'json':format==='csv'?'csv':format==='html'?'html':'text');
 const rendered=(data.report??'transactions')==='transactions'?transactionTable(text):data.report==='balance'?balanceTable(text):undefined;
 const shown=rendered&&data.report==='balance'&&!data.account?.length?`這是未限縮科目的報表；合計可能包含收支對應分錄，不能作為現金或可用餘額。\n\n${rendered}`:rendered;
 if(shown&&Buffer.byteLength(shown)<=45000)return shown;
 return fenced(text);
}
