import { mkdirSync, writeFileSync, renameSync, readFileSync, unlinkSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { homedir } from 'node:os';
import { createHash } from 'node:crypto';
import type { Request, Reply } from './workflows.ts';

export type PendingAuthorization={token:string;request:Request;reply:Reply;expires:number;journal:string};
export type PendingStore={save(p:PendingAuthorization):void;claim(token:string):PendingAuthorization|undefined;drop(token:string):void};

// A preview must survive the pi process: piweb/piscord use one-shot print mode
// for attachments and recycle idle RPC workers. The file is only an
// authorization claim; execution is still bound to the preview snapshot.
export function fileStore(journal:string,root=process.env.PI_FINANCE_STATE_DIR??join(homedir(),'.local/state/pi-finance')):PendingStore{
 const dir=join(root,createHash('sha256').update(journal).digest('hex').slice(0,16));
 const path=(token:string)=>{if(!/^[0-9a-f-]{36}$/.test(token))throw new Error('Invalid token');return join(dir,token+'.json');};
 const sweep=()=>{try{for(const f of readdirSync(dir))if(Date.now()-statSync(join(dir,f)).mtimeMs>3600000)unlinkSync(join(dir,f));}catch{}};
 return {
  save(p){mkdirSync(dir,{recursive:true,mode:0o700});sweep();const tmp=path(p.token)+'.tmp';writeFileSync(tmp,JSON.stringify(p),{mode:0o600});renameSync(tmp,path(p.token));},
  claim(token){
   let file:string;try{file=path(token);}catch{return undefined;}
   const claimed=`${file}.claimed-${process.pid}-${Date.now()}`;
   try{renameSync(file,claimed);}catch{return undefined;} // atomic: only one claimant wins
   try{const p=JSON.parse(readFileSync(claimed,'utf8')) as PendingAuthorization;return p.token===token&&p.journal===journal?p:undefined;}
   catch{return undefined;}finally{try{unlinkSync(claimed);}catch{}}
  },
  drop(token){try{unlinkSync(path(token));}catch{}},
 };
}
