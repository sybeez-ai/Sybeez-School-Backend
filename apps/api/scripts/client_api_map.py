import json,re,os,sys,collections
spec=json.load(open('/tmp/dbe-openapi.json'))
routes=collections.defaultdict(set)
for p,ops in spec['paths'].items():
    for m in ops:
        if m in('get','post','put','patch','delete'): routes[p].add(m.upper())
def rx(p):
    p=re.sub(r'^/api/v1','',p)
    return re.compile('^'+re.sub(r'\\\{[^}]+\\\}','[^/]+',re.escape(p).replace('\\{','{').replace('\\}','}').replace('{','\\{').replace('}','\\}'))+'$')
pats=[(re.compile('^'+re.sub(r'\{[^}]+\}','[^/]+',re.sub(r'^/api/v1','',p))+'/?$'),p) for p in routes]
call=re.compile(r'(apiFetch|authorizedFetch|request)\s*(?:<[^()]*?>)?\(\s*(?:(\'|"|`)([^\'"`]*?(?:\$\{[^}]*\}[^\'"`]*?)*)\2|(\'(?:GET|POST|PUT|PATCH|DELETE)\'),\s*(`|\')([^`\']*)\5)',re.S)
out=[]
for client,root in [('web',sys.argv[1]),('mobile',sys.argv[2])]:
  for dp,_,fs in os.walk(root):
    if 'node_modules' in dp or '__tests__' in dp: continue
    for f in fs:
      if not f.endswith(('.ts','.tsx')) or '.test.' in f: continue
      fp=os.path.join(dp,f); s=open(fp).read()
      for m in call.finditer(s):
        if m.group(4): meth=m.group(4).strip("'"); path=m.group(6)
        else:
          path=m.group(3); win=s[m.end():m.end()+400]
          mm=re.search(r"method:\s*'(\w+)'",win.split('apiFetch')[0]); meth=mm.group(1) if mm else 'GET'
        path=re.sub(r'\$\{[^}]*\}','X',path); path=re.sub(r'X\)\}.*$','',path).split('?')[0].replace('/api/v1','',1)
        if not path.startswith('/'): continue
        path=re.sub(r'X$','',path) if path.endswith('/X') is False and path.endswith('X') else path
        hits=[p for r,p in pats if r.match(path)]
        st='OK' if any(meth in routes[p] for p in hits) else ('WRONG_METHOD' if hits else 'MISSING')
        out.append((st,client,meth,path,fp.split('/src/')[-1]+':'+str(s[:m.start()].count('\n')+1),hits))
used=set(p for o in out for p in o[5])
for o in sorted(out): 
  if o[0]!='OK': print(*o[:5],o[5])
c=collections.Counter((o[0],o[1]) for o in out); print(c)
print('backend paths unused by any client:',len([p for p in routes if p not in used]))
json.dump({'calls':[o[:5]+(o[5],) for o in out],'unused':sorted(p for p in routes if p not in used)},open('/tmp/dbe-apimap.json','w'),indent=1)
