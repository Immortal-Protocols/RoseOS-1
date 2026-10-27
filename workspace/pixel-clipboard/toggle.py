#!/usr/bin/env python3
import json, sys
from pathlib import Path
p=Path.home()/'.config/pixel-clipboard/config.json'
config=json.loads(p.read_text())
if len(sys.argv)!=2 or sys.argv[1] not in ('on','off','status'):
    raise SystemExit('Usage: toggle.py on|off|status')
if sys.argv[1]!='status':
    config['enabled']=sys.argv[1]=='on'
    temp=p.with_suffix('.tmp');temp.write_text(json.dumps(config));temp.chmod(0o600);temp.replace(p)
print({'enabled':config['enabled']})
