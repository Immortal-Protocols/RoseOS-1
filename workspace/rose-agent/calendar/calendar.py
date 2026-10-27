#!/usr/bin/env python3
"""Run Calendar operations under an explicitly selected Rose Agent account."""
import json, os, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
ACCOUNTS = json.loads((Path(os.environ.get('PIXEL_VOICE_CONFIG_DIR', str(Path.home()/'.config/pixel-voice')))/'google-accounts.json').read_text())
if len(sys.argv) < 3 or sys.argv[1] not in ACCOUNTS:
    sys.exit('Usage: calendar.py immortal|alist calendarList|events <method> [options]')
account, args = sys.argv[1], sys.argv[2:]
if len(args) < 2 or (args[0], args[1]) not in {('calendarList','list'),('calendarList','get'),('events','list'),('events','get'),('events','instances'),('events','insert'),('events','patch'),('events','update'),('events','delete')}:
    sys.exit('Only calendar listing and event operations are supported. Authentication is managed separately.')
env = os.environ.copy()
for key in ['GOOGLE_WORKSPACE_CLI_TOKEN','GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE','GOOGLE_WORKSPACE_CLI_CLIENT_ID','GOOGLE_WORKSPACE_CLI_CLIENT_SECRET','GWS_SANITIZE_TEMPLATE','GOOGLE_WORKSPACE_PROJECT_ID','GOOGLE_APPLICATION_CREDENTIALS']:
    env.pop(key, None)
env['GOOGLE_WORKSPACE_CLI_CONFIG_DIR'] = str(Path.home()/'.config/pixel-voice/google'/account)
cli = os.environ.get('PIXEL_GWS_BIN', str(ROOT/'tools/gws-0.22.5/gws'))
# Fail closed if the account's saved credentials point at the wrong mailbox.
profile = subprocess.run([cli,'gmail','users','getProfile','--params','{"userId":"me"}'],env=env,capture_output=True,text=True)
if profile.returncode:
    sys.stderr.write(profile.stderr + profile.stdout)
    sys.exit(profile.returncode)
if json.loads(profile.stdout).get('emailAddress','').lower() != ACCOUNTS[account]:
    sys.exit('Account identity mismatch; Calendar operation refused.')
sys.exit(subprocess.run([cli,'calendar',*args],env=env).returncode)
