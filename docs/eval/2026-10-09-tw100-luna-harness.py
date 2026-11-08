import subprocess,json,os,time,threading,sys,re,shutil
root=os.path.dirname(os.path.abspath(__file__))
exec(open(root+'/cases.py').read())
book=root+'/book'
# Realistic book: a COPY of the real ledger (categories, rules, history). Never ~/finance itself.
if not os.path.exists(book):
    shutil.copytree('/home/chihmin/finance',book);os.chmod(book+'/main.journal',0o644)
J=book+'/main.journal'
git=lambda *a:subprocess.run(['git','-C',book,*a],capture_output=True,text=True).stdout
env=dict(os.environ,PI_FINANCE_HOST='piweb',PI_FINANCE_JOURNAL=J,PI_FINANCE_STATE_DIR=root+'/state')
os.makedirs(root+'/state',exist_ok=True)
p=subprocess.Popen(['node','/home/chihmin/src/pi-agent/packages/coding-agent/dist/cli.js','--mode','rpc','--provider','openai-codex','--model','gpt-5.6-luna','--session-dir',root+'/sessions'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,env=env,cwd=root)
ev=[];lock=threading.Lock()
def rd():
    for l in p.stdout:
        try:e=json.loads(l)
        except:continue
        with lock:ev.append(e)
threading.Thread(target=rd,daemon=True).start()
def send(cmd):p.stdin.write(json.dumps(cmd)+'\n');p.stdin.flush()
def turn(text,limit=180):
    with lock:start=len(ev)
    t=time.time();send({'type':'prompt','id':str(t),'message':'[Web user: web]\n'+text})
    while time.time()-t<limit:
        time.sleep(0.05)
        with lock:new=ev[start:]
        if any(e.get('type') in('agent_settled','input_handled') for e in new):break
    dt=time.time()-t;time.sleep(0.3)
    with lock:new=ev[start:]
    shown=[];path='general';tools=[];final=''
    for e in new:
        m=e.get('message') if isinstance(e.get('message'),dict) else {}
        if e.get('type')=='message_end':
            ct=m.get('customType')
            if ct=='finance-ready':path='fast'
            if ct=='finance-workflow-context':path='workflow'
            if ct in('finance-receipt','finance-workflow-receipt','finance-result-fallback'):shown.append(m['content'])
            if m.get('role')=='assistant':
                for c in m.get('content',[]):
                    if c.get('type')=='toolCall':tools.append(c['name'])
                    if c.get('type')=='text' and c.get('text','').strip():final=c['text']
    if any(e.get('type')=='input_handled' for e in new) and path=='general':path='host'
    return dt,path,tools,shown,final
only=[int(x) for x in sys.argv[1].split(',')] if len(sys.argv)>1 and sys.argv[1] else range(len(C))
res=[];time.sleep(2)
for i in only:
    prompt,amts=C[i]
    send({'type':'new_session','id':'ns'+str(i)});time.sleep(0.4)
    injected=i%10==0
    if injected:  # today's failure mode: an uncommitted hand edit sitting in the ledger
        open(J,'a').write(f'\n2026-10-09 手動修改{i}\n    expenses:food:drinks   {i+1} TWD\n    assets:cash:wallet\n')
    head=git('rev-parse','HEAD').strip();before=open(J).read()
    dt,path,tools,shown,final=turn(prompt)
    confirm=None
    if shown and '回覆「確認」' in shown[-1]:
        cdt,_,_,cshown,cfinal=turn('確認');confirm=round(cdt,1);shown+=cshown;final=cfinal or final
    after=open(J).read();added=after[len(before):] if after.startswith(before) else after
    ok=all(re.search(r'(?<![\d.])'+re.escape(a)+r'(?:\.0+)? [A-Z]{3}',added) for a in amts)
    n_added=len(re.findall(r'^\d{4}-\d{2}-\d{2} ',added,re.M))
    subjects=git('log','--format=%s',head+'..HEAD').strip().split('\n') if git('rev-parse','HEAD').strip()!=head else []
    clean=git('status','--porcelain','--','main.journal').strip()==''
    accts=re.findall(r'^\s{4}((?:expenses|income):[\w:.-]+)',added,re.M)
    r={'i':i,'prompt':prompt,'s':round(dt,1),'confirm':confirm,'path':path,'tools':tools,'ok':ok,'extra':n_added-len(amts),
       'injected':injected,'adopted':'Adopt external journal edits' in subjects,'clean':clean,'accounts':accts,
       'reply':((shown[-1] if shown else '')+' || '+final)[:220].replace('\n',' ')}
    res.append(r);print(json.dumps(r,ensure_ascii=False),flush=True)
p.terminate()
json.dump(res,open(root+'/results.json','w'),ensure_ascii=False,indent=1)
bad=[r for r in res if not r['ok']];ts=sorted(r['s']+(r['confirm'] or 0) for r in res)
inj=[r for r in res if r['injected']]
print('SUMMARY n=%d correct=%d extra_rows=%d dirty_after=%d injected=%d adopted_ok=%d under10=%d median=%.1f p90=%.1f max=%.1f'%(len(res),len(res)-len(bad),sum(1 for r in res if r['extra']>0),sum(1 for r in res if not r['clean']),len(inj),sum(1 for r in inj if r['adopted'] and r['ok']),sum(1 for t in ts if t<=10),ts[len(ts)//2],ts[int(len(ts)*.9)],ts[-1]))
