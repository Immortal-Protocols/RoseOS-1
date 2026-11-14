"""Unwired, bounded read-only guest probe. Offline tests never contact metadata.

Only the explicit library caller can invoke this probe. It has no controller,
login, disk mutation, credential handling, CLI execution mode or workflow hook.
"""
import json
import os
import re
import selectors
import signal
import subprocess
import time
import urllib.request

import mustang_bootstrap_git as git

MAX_OUTPUT=8192
PROBE_SECONDS=5


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise git.Refused('guest-metadata-redirect-refused')


def metadata_id(deadline):
    limit=min(deadline,time.monotonic()+PROBE_SECONDS)
    remaining=limit-time.monotonic()
    if remaining<=0:raise git.Refused('collector-deadline')
    # Fixed numeric link-local endpoint, no inherited proxy or redirect path.
    request=urllib.request.Request('http://169.254.169.254/computeMetadata/v1/instance/id',
                                   headers={'Metadata-Flavor':'Google'})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open(request,timeout=remaining) as response:
        raw=b''
        while len(raw)<65:
            if time.monotonic()>=limit:raise git.Refused('guest-metadata-deadline')
            block=response.read1(65-len(raw))
            if not block:break
            raw+=block
        if response.status!=200 or response.headers.get('Metadata-Flavor')!='Google' \
                or len(raw)>64 or not re.fullmatch(rb'[1-9][0-9]{0,19}\n?',raw):
            raise git.Refused('guest-metadata-response-differs')
        if time.monotonic()>=limit:raise git.Refused('guest-metadata-deadline')
        return raw.decode('ascii').strip()


def mount_record(home,deadline):
    """One fixed findmnt read; bound output while streaming and end only its child."""
    argv=['/usr/bin/findmnt','--json','--list','--target',str(home),
          '--output','UUID,TARGET,FSTYPE,OPTIONS,MAJ:MIN,FSROOT']
    limit=min(deadline,time.monotonic()+PROBE_SECONDS)
    if time.monotonic()>=limit:raise git.Refused('collector-deadline')
    proc=subprocess.Popen(argv,env=git.environment(),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL,start_new_session=True)
    output=bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout,selectors.EVENT_READ)
            while True:
                remaining=limit-time.monotonic()
                if remaining<=0:raise git.Refused('guest-mount-probe-deadline')
                if not selector.select(min(remaining,0.25)):continue
                block=os.read(proc.stdout.fileno(),4096)
                if not block:break
                output.extend(block)
                if len(output)>MAX_OUTPUT:raise git.Refused('guest-mount-output-bound')
        try:code=proc.wait(timeout=max(0.001,limit-time.monotonic()))
        except subprocess.TimeoutExpired:raise git.Refused('guest-mount-probe-deadline') from None
        if code:raise git.Refused('guest-mount-probe-failed')
        value=json.loads(output)
        if set(value)!={'filesystems'} or not isinstance(value['filesystems'],list) or len(value['filesystems'])!=1:
            raise git.Refused('guest-mount-response-differs')
        row=value['filesystems'][0]
        if not isinstance(row,dict) or set(row)!={'uuid','target','fstype','options','maj:min','fsroot'}:
            raise git.Refused('guest-mount-response-differs')
        return row
    finally:
        try:os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        proc.wait();proc.stdout.close()


class LocalGuestProbe:
    kind='native-read-only'

    def observe(self,home,deadline):
        return dict(instanceId=metadata_id(deadline),mount=mount_record(home,deadline))
