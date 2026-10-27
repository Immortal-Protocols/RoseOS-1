#!/usr/bin/env python3
"""Publish a bounded launcher visual through the existing paired phone channel."""
import argparse,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'phone'))
from control import call
p=argparse.ArgumentParser(description=__doc__)
s=p.add_subparsers(dest='action',required=True)
s.add_parser('status');s.add_parser('clear');pub=s.add_parser('set');pub.add_argument('file',type=Path)
a=p.parse_args();request={'action':'visualization_'+a.action}
if a.action=='set':
    raw=a.file.read_bytes()
    if len(raw)>100000: p.error('Visual file exceeds 100 KB')
    request['visual']=json.loads(raw)
c=call('/phone/commands',request)
while c['status']=='pending':
    time.sleep(.3);c=call('/phone/commands/'+c['id'])
# Never print the command payload: it can contain private meal data.
print(json.dumps({'id':c['id'],'action':c['action'],'status':c['status'],'result':c.get('result')},ensure_ascii=False))
sys.exit(0 if c['status']=='completed' and c.get('result',{}).get('status')=='ok' else 1)
