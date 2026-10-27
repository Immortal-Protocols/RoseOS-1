#!/usr/bin/env python3
"""Authenticated phone tools: list_apps, open_app PACKAGE_NAME."""
import json, ssl, time, sys, urllib.request
from observations import compact_command
from pathlib import Path
p=Path.home()/'.config/pixel-voice'
ctx=ssl.create_default_context(cafile=str(p/'server-cert.pem'));ctx.check_hostname=False

def call(route,data=None):
    req=urllib.request.Request('https://localhost:9443'+route,data=None if data is None else json.dumps(data).encode(),headers={'Authorization':'Bearer '+(p/'pair-token').read_text().strip(),'Content-Type':'application/json'})
    with urllib.request.urlopen(req,context=ctx,timeout=30) as r:return json.load(r)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Wireless Pixel controls; inspect UI before acting on nodes.')
    parser.add_argument('--raw',action='store_true',help='Return the full diagnostic schema instead of compact observations')
    sub=parser.add_subparsers(dest='action',required=True)
    for name in ('list_apps','control_status','setup_ui_control','inspect_ui','home','back'):
        sub.add_parser(name)
    sub.add_parser('open_app').add_argument('packageName')
    label=sub.add_parser('tap_label',help='Tap exactly one enabled visible control by text or description; returns a fresh screen')
    label.add_argument('snapshotId');label.add_argument('label')
    for name in ('tap','set_text','scroll'):
        paction=sub.add_parser(name)
        paction.add_argument('snapshotId');paction.add_argument('nodeId',type=int)
        if name=='set_text':paction.add_argument('text')
        if name=='scroll':paction.add_argument('direction',choices=('forward','backward'))
    request=vars(parser.parse_args());raw=request.pop('raw')
    try:
        c=call('/phone/commands',request)
        while c['status']=='pending':
            time.sleep(.3);c=call('/phone/commands/'+c['id'])
        print(json.dumps(c if raw else compact_command(c),ensure_ascii=False,separators=(',',':')))
        sys.exit(0 if c['status']=='completed' else 1)
    except Exception as e:sys.exit('Phone command failed: '+str(e))
