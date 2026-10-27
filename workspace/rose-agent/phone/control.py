#!/usr/bin/env python3
"""Authenticated phone tools: list_apps, open_app PACKAGE_NAME."""
import json, re, ssl, time, sys, urllib.request
from observations import compact_command
from pathlib import Path
p=Path.home()/'.config/pixel-voice'
ctx=ssl.create_default_context(cafile=str(p/'server-cert.pem'));ctx.check_hostname=False

def task_generation():
    # Read once at start: a tool launched for task A keeps naming A, so the backend refuses it after a Stop or once task B begins.
    try:value=(p/'task-generation').read_text().strip()
    except OSError:return None
    return value if re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',value) else None
TASK_GENERATION=task_generation()

def request_headers():
    headers={'Authorization':'Bearer '+(p/'pair-token').read_text().strip(),'Content-Type':'application/json'}
    if TASK_GENERATION:headers['X-Rose-Task']=TASK_GENERATION
    return headers

def call(route,data=None):
    req=urllib.request.Request('https://localhost:9443'+route,data=None if data is None else json.dumps(data).encode(),headers=request_headers())
    with urllib.request.urlopen(req,context=ctx,timeout=30) as r:return json.load(r)

def run_phone_command(request):
    command=call('/phone/commands',request)
    while command['status']=='pending':
        time.sleep(.3)
        command=call('/phone/commands/'+command['id'])
    return command

def execute_command(request,open_first=False,send=run_phone_command):
    """One model tool call; open/verify before sending the requested app operation.

    The two device commands keep their own IDs and existing no-replay behavior.
    An uncertain launch never proceeds to the app mutation.
    """
    if not open_first:
        return send(request)
    if request.get('action')!='app_command':
        raise ValueError('--open is only supported for app_command')
    target=request['packageName']
    opened=send({'action':'open_app','packageName':target})
    result=opened.get('result',{})
    if (opened.get('status')!='completed' or result.get('status')!='opened'
            or result.get('packageName')!=target
            or not (result.get('foregroundVerified') is True or result.get('backgroundVerified') is True)):
        return {'action':'app_command','status':'not_executed','result':{
            'status':'not_executed','stage':'open_app',
            'reason':'App opening was not verified; the app operation was not sent.',
            'opening':opened}}
    command=send(request)
    command.setdefault('result',{})['opening']={
        'id':opened.get('id'),'status':'opened','packageName':target,
        'foregroundVerified':result.get('foregroundVerified') is True,
        'backgroundVerified':result.get('backgroundVerified') is True}
    return command

def save_screenshot(command):
    """Keep pixels out of text/tool logs; expose a local file for image inspection."""
    result = command.get('result', {})
    encoded = result.pop('imageBase64', None)
    if encoded is None:
        return command
    import base64, os, uuid
    if command.get('action') != 'screenshot' or result.get('mimeType') != 'image/jpeg':
        raise ValueError('Unexpected image payload')
    data = base64.b64decode(encoded, validate=True)
    if len(data) > 800000 or not data.startswith(b'\xff\xd8'):
        raise ValueError('Invalid screenshot')
    directory = p / 'screenshots'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = directory / (str(uuid.uuid4()) + '.jpg')
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(data)
    result['imagePath'] = str(target)
    result['nextStep'] = 'Use view_image on imagePath to see this frame; text metadata alone is not visual inspection.'
    for stale in sorted(directory.glob('*.jpg'), key=lambda f: f.stat().st_mtime, reverse=True)[8:]:
        stale.unlink()
    return command

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Wireless Pixel controls; inspect UI before acting on nodes.')
    parser.add_argument('--raw',action='store_true',help='Return the full diagnostic schema instead of compact observations')
    parser.add_argument('--display',choices=('background','phone'),help='Select the agent workspace or the physical phone screen for this UI command')
    sub=parser.add_subparsers(dest='action',required=True)
    for name in ('screenshot','diagnostics','environment','app_context','list_apps','control_status','setup_ui_control','inspect_ui','home','back'):
        sub.add_parser(name)
    duck=sub.add_parser('voice_ducking',help='Optional music ducking trial; disabling immediately restores normal focus')
    duck.add_argument('--enabled',dest='duckEnabled',choices=('on','off'))
    duck.add_argument('--mode',dest='duckMode',choices=('light','system'))
    duck.add_argument('--reduction-db',dest='reductionDb',type=float)
    duck.add_argument('--test-seconds',dest='testSeconds',type=int,choices=range(1,11))
    sub.add_parser('open_app').add_argument('packageName')
    app=sub.add_parser('app_command',help='Call an agent-ready app signed with our application identity')
    app.add_argument('packageName');app.add_argument('operation');app.add_argument('--params',type=json.loads,default={})
    app.add_argument('--open',dest='openFirst',action='store_true',help='Open and verify the app, then invoke the operation in this single tool call')
    msg=sub.add_parser('messages_read',help='Read-only named chat, latest messages or reply status through the Mac worker')
    msg.add_argument('operation',choices=('latest','find','read','check_reply'))
    msg.add_argument('--query');msg.add_argument('--chat-id',dest='chatId',type=int);msg.add_argument('--limit',type=int,default=3)
    media=sub.add_parser('media_control',help='Read, pause or resume Apple Music through the optional verified worker')
    media.add_argument('operation',choices=('get_state','pause','resume'))
    clock=sub.add_parser('clock_control',help='Verified local timer, stopwatch, alarm and world clock control')
    clock.add_argument('operation');clock.add_argument('--params',type=json.loads,default={})
    timer=sub.add_parser('clock_timer_start',help='Start one verified timer in Pixel Clock')
    timer.add_argument('seconds',type=int);timer.add_argument('--label',default='')
    music=sub.add_parser('apple_music_run',help='Run the mapped Apple Music routine locally on the Pixel')
    music.add_argument('title');music.add_argument('artist',nargs='?',default='');music.add_argument('--pause-after',dest='pauseAfter',action='store_true')
    label=sub.add_parser('tap_label',help='Tap exactly one enabled visible control by text or description; returns a fresh screen')
    label.add_argument('snapshotId');label.add_argument('label')
    label.add_argument('--node-id',dest='nodeId',type=int,help='Disambiguate identical labels using a visually verified target from this snapshot')
    touch=sub.add_parser('touch_label',help='Touch a fresh uniquely labelled visible control when semantic click did not work')
    touch.add_argument('snapshotId');touch.add_argument('label')
    touch.add_argument('--node-id',dest='nodeId',type=int,help='Disambiguate identical labels using a visually verified target from this snapshot')
    for name in ('tap','long_press','set_text','ime_enter','scroll'):
        paction=sub.add_parser(name)
        paction.add_argument('snapshotId');paction.add_argument('nodeId',type=int)
        if name=='set_text':paction.add_argument('text')
        if name=='scroll':paction.add_argument('direction',choices=('forward','backward'))
    visual=sub.add_parser('screen_gesture',help='Tap or swipe at points from a fresh screenshot')
    visual.add_argument('screenshotId');visual.add_argument('points',nargs='+',type=int)
    typed=sub.add_parser('type_text',help='Type into the editor focused in a fresh screenshot')
    typed.add_argument('screenshotId');typed.add_argument('text');typed.add_argument('--commit',action='store_true')
    swipe=sub.add_parser('swipe',help='Swipe horizontally within a visually verified region of a fresh visible node')
    swipe.add_argument('snapshotId');swipe.add_argument('nodeId',type=int)
    swipe.add_argument('direction',choices=('left','right'))
    swipe.add_argument('--bounds',nargs=4,type=int,required=True,metavar=('LEFT','TOP','RIGHT','BOTTOM'))
    point=sub.add_parser('touch_point',help='Tap a screenshot-verified point inside a fresh visible surface')
    point.add_argument('snapshotId');point.add_argument('nodeId',type=int)
    point.add_argument('x',type=int);point.add_argument('y',type=int)
    drag=sub.add_parser('drag',help='Drag one finger between display-pixel endpoints within a fresh visible node')
    drag.add_argument('snapshotId');drag.add_argument('nodeId',type=int)
    drag.add_argument('points',nargs=4,type=int,metavar=('FROM_X','FROM_Y','TO_X','TO_Y'))
    drag.add_argument('--duration-ms',dest='durationMs',type=int,default=600)
    request=vars(parser.parse_args());raw=request.pop('raw')
    if request.get('display') is None:request.pop('display',None)
    open_first=request.pop('openFirst',False)
    if request.get('action')=='clock_control':
        params=request.pop('params')
        if not isinstance(params,dict) or 'operation' in params:parser.error('--params must be a JSON object without operation')
        request['params']={**params,'operation':request.pop('operation')}
    if request.get('action')=='media_control':request['app']='apple_music'
    if request.get('action')=='voice_ducking':
        if request.get('duckEnabled') is None: request.pop('duckEnabled',None)
        else: request['duckEnabled']=request['duckEnabled']=='on'
        if request.get('testSeconds') is None: request.pop('testSeconds',None)
        for option in ('duckMode','reductionDb'):
            if request.get(option) is None: request.pop(option,None)
    if request.get('nodeId','missing') is None:request.pop('nodeId')
    try:
        if request['action']=='messages_read':
            request.pop('action');request={k:v for k,v in request.items() if v is not None}
            print(json.dumps(call('/messages-worker/read',request),ensure_ascii=False,separators=(',',':')));sys.exit(0)
        c=execute_command(request,open_first)
        c=save_screenshot(c)
        print(json.dumps(c if raw else compact_command(c),ensure_ascii=False,separators=(',',':')))
        sys.exit(0 if c['status']=='completed' else 1)
    except Exception as e:sys.exit('Phone command failed: '+str(e))
