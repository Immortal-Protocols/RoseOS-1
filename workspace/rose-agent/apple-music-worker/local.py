#!/usr/bin/env python3
"""Send one Apple Music procedure to the phone; the device runs its own steps."""
import argparse,json,os,time,uuid
from pathlib import Path
from worker import Phone,NeedsAssistance
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('title');p.add_argument('artist',nargs='?',default='');p.add_argument('--pause-after',action='store_true')
a=p.parse_args()
start=time.monotonic()
try:
 result=Phone()({'action':'apple_music_run','title':a.title,'artist':a.artist,'pauseAfter':a.pause_after})
except (NeedsAssistance,OSError,ValueError) as e:
 result={'status':'needs_assistance','reason':str(e)}
result['requestToFinalResultMs']=round((time.monotonic()-start)*1000)
result['runner']='on-device'
d=Path.home()/'.config/pixel-voice/apple-music-worker';d.mkdir(parents=True,exist_ok=True,mode=0o700)
fd=os.open(d/('local-'+str(uuid.uuid4())+'.json'),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:json.dump(result,f,indent=2)
print(json.dumps(result))
raise SystemExit(0 if result['status'] in ('verified_playing','verified_playing_then_paused') else 2)
