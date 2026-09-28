import json,sys,re
import pathlib
S=json.load(open(pathlib.Path(__file__).resolve().parent.parent/'reference'/'bff-openapi.json',encoding='utf8'))
CS=S['components']['schemas']
def deref(n):
    return CS.get(n.split('/')[-1],{})
def props(name,depth=0,seen=None):
    seen=seen or set()
    if name in seen or depth>2: return
    seen=seen|{name}
    sc=deref(name)
    if 'allOf' in sc:
        for a in sc['allOf']:
            if '$ref' in a: props(a['$ref'],depth,seen)
    for k,v in (sc.get('properties') or {}).items():
        t=v.get('type','')
        ref=v.get('$ref') or (v.get('items',{}).get('$ref') if v.get('items') else None)
        arr='[]' if t=='array' else ''
        d=(v.get('description') or '')[:95]
        nm=ref.split('/')[-1] if ref else t
        sys.stdout.buffer.write(("  "*(depth+1)+f"{k:<32} {nm}{arr:<3} {d}\n").encode('utf8','replace'))
        if ref and depth<1: props(ref,depth+1,seen)
def respschema(path,method):
    o=S['paths'][path][method]
    r=o['responses'].get('200',{}).get('content',{}).get('*/*') or o['responses'].get('200',{}).get('content',{}).get('application/json')
    if not r: return None
    s=r['schema']
    return s.get('$ref') or (s.get('items',{}) or {}).get('$ref')
if __name__=='__main__':
    for path,method in [(a,b) for a,b in zip(sys.argv[1::2],sys.argv[2::2])]:
        ref=respschema(path,method)
        sys.stdout.buffer.write(f"\n=== {method.upper()} {path}\n    -> {ref}\n".encode('utf8'))
        if ref: props(ref)
