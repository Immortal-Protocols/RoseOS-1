#!/usr/bin/env python3
"""Read notes, explicitly start agreed work, or write verified progress back."""
import argparse, json, sys, uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'phone'))
from control import call
p=argparse.ArgumentParser(description=__doc__)
sub=p.add_subparsers(dest='command',required=True)
sub.add_parser('status')
start=sub.add_parser('start-work');start.add_argument('--meeting-id',required=True);start.add_argument('--authorized',action='store_true',required=True);start.add_argument('--key',required=True)
progress=sub.add_parser('progress');progress.add_argument('--meeting-id',required=True);progress.add_argument('--item',type=int,required=True);progress.add_argument('--status',choices=['in_progress','completed','blocked'],required=True);progress.add_argument('--summary',required=True);progress.add_argument('--evidence',default='');progress.add_argument('--key',required=True)
a=vars(p.parse_args());command=a.pop('command')
if command=='status':result=call('/conversation-mode')
else:
 a['meetingId']=a.pop('meeting_id');a['action']='start_work' if command=='start-work' else 'progress';result=call('/conversation-mode',a)
print(json.dumps(result,ensure_ascii=False))
