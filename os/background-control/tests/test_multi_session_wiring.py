"""Two-app controller wiring (OS Core MULTI-SESSION-DESIGN; source checks, no device). The single-session logic is unchanged
and runs inside one of two stable slots: every per-session field is saved and loaded with its slot, every Android callback,
lifecycle event, launch step, deadline and dispatched request runs inside its own slot, and every provider operation is
routed by MultiSession (the pure decisions MultiSessionTest covers). Slot 0 is the legacy session; only an explicit
"alongside" start, on a build whose sessionLimit capability is 2, adds slot 1. Real two-app operation on a phone needs
OS Core's platform change and is not claimed here."""
from pathlib import Path
import re
import unittest

SRC = Path(__file__).resolve().parents[1]/'agent-pip/src/dev/pixel/backgroundcontrol'
CONTROLLER = (SRC/'Controller.java').read_text()
PROVIDER = (SRC/'AgentProvider.java').read_text()
RECEIVER = (SRC/'StopReceiver.java').read_text()
PHONE = (Path(__file__).resolve().parents[3]/'workspace/rose-agent/app/src/dev/pixel/voice/PhoneControl.java').read_text()


def span(start, end):
    i = CONTROLLER.index(start)
    return CONTROLLER[i:CONTROLLER.index(end, i)]


START = span('static synchronized JSONObject start(', 'static void attachLifecycle(')
LAUNCH = span('static void launch(', 'static void dispatch(')
ROUTE = span('static synchronized JSONObject route(', 'static synchronized void ownerStop(')
# Fields shared by both slots on purpose: the overview's own counter, the global request gate and the slot table.
SHARED = {'appsGeneration', 'appsSignature', 'posting', 'active'}


class MultiSessionWiringTest(unittest.TestCase):
    def test_every_per_session_field_moves_with_its_slot(self):
        statics = {'session', 'generation', 'started', 'id', 'target', 'taskId', 'status', 'error', 'snapshotId', 'stops', 'width',
                   'height', 'operations', 'blocked', 'consent', 'replacement', 'sessionToken', 'pendingToken', 'pending', 'nodes',
                   'parents', 'labels', 'truncated', 'snapshotTime', 'anyApp', 'observed', 'lastPreview'}
        # No other mutable static field exists: every one is per-session (above) or shared on purpose.
        declared = set()
        for stmt in re.findall(r'\bstatic (?!final\b|synchronized\b|void\b|class\b|interface\b)[\w.]+(?:<[^<>]*>)?(?:\[\])? ([\w,=" .<>()]*?);', CONTROLLER):
            if '(' in stmt.split('=')[0]: continue
            for part in re.split(r',(?![^<]*>)', stmt):
                declared.add(part.split('=')[0].strip())
        self.assertEqual(declared - SHARED, statics, 'every mutable static is listed as per-session or shared')
        save = span('static void save(Slot s){', '\n static void load(')
        load = span('static void load(Slot s){', '\n static Slot enter(')
        for name in sorted(statics):
            with self.subTest(name):
                self.assertIn(f's.{name}={name};', save)
                self.assertIn(f'{name}=s.{name};', load)
        self.assertIn('static Slot enter(Slot s){Slot was=active;if(s!=was){save(was);load(s);active=s;}return was;}', CONTROLLER)
        self.assertIn('static final Slot[] slots={new Slot(0),new Slot(1)}; static Slot active=slots[0];', CONTROLLER)

    def test_every_asynchronous_path_runs_inside_its_own_slot(self):
        self.assertIn('final Slot slot=active;final String sid=id;', START)
        self.assertIn('executor(slot),callback);', START, 'session callbacks')
        self.assertIn('invoke(session,executor(active),listener);', CONTROLLER, 'lifecycle callbacks (attached inside the slot)')
        self.assertIn('worker.execute(()->launch(c,token,slot));', START)
        self.assertEqual(LAUNCH.count('Slot was=enter(slot);try{'), 3, 'each locked launch step enters the slot')
        self.assertEqual(LAUNCH.count('}finally{leave(was);}'), 3)
        self.assertIn('main.postDelayed(in(slot,', START, 'the deadline stops only its own slot')
        self.assertIn('main.post(in(active,()->{posting=false;r.run();dispatch();}));', CONTROLLER, 'the request runs in its slot')
        self.assertIn('static Executor executor(Slot s){return r->main.post(in(s,r));}', CONTROLLER)
        self.assertIn('static Runnable in(Slot s,Runnable r){return ()->{synchronized(Controller.class){Slot was=enter(s);try{r.run();}finally{leave(was);}}};}', CONTROLLER)
        # Nothing posts or executes outside a slot (only the request gate, the slot executor and the in() wrappers).
        self.assertEqual(re.findall(r'main\.post\((?!in\()', CONTROLLER), [], 'every post runs inside a slot')

    def test_each_slot_has_its_own_name_notification_and_stop(self):
        self.assertIn('invoke(builder,MultiSession.name(slot.index));', START)
        self.assertIn('newInstance(nb.build(),MultiSession.notificationId(slot.index));', START)
        self.assertIn('PendingIntent.getBroadcast(c,slot.index,new Intent(c,StopReceiver.class).putExtra("sessionId",sid),', START)
        self.assertIn('Controller.ownerStop(c,i.getStringExtra("sessionId"));', RECEIVER)
        self.assertIn('watch(new Intent(c,AgentPipActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),slot.index,sid,task)', START)
        self.assertIn('if(slot>0){i.putExtra(WatchBinding.EXTRA_SESSION,session);if(task!=null)i.putExtra(WatchBinding.EXTRA_TASK,task);}', CONTROLLER,
                      'a later slot\'s viewer is bound to that session, never slot 0')
        self.assertIn('setTargetPackageNames",List.class).invoke(builder,Arrays.asList(pkg));', START, 'each slot requests exactly its own target')

    def test_the_provider_routes_every_session_operation(self):
        for op in ('start', 'get_state', 'preview', 'current_apps', 'command', 'stop'):
            self.assertIn(f'case "{op}":result=Controller.route(getContext(),"{op}",p);break;', PROVIDER)
        self.assertNotRegex(PROVIDER, r'Controller\.(start|state|preview|currentApps|command|agentStop)\(')
        caps = re.search(r'case "capabilities":.*?new String\[\]\{([^}]*)\}', PROVIDER).group(1)
        self.assertEqual(caps, '"list_apps","start","get_state","command","stop"', 'no new operation is advertised')
        self.assertIn('.put("schemaVersion",4).put("multiSession",limit>=2).put("maxSessions",limit)', PROVIDER)
        self.assertIn('int limit=Controller.sessionLimit(getContext());', PROVIDER)
        self.assertIn('static int sessionLimit(Context c){Bundle b=owned(c);return MultiSession.sessionLimit(b==null?null:b.get("sessionLimit"));}', CONTROLLER)
        signer = PROVIDER.index('checkSignatures(android.os.Process.myUid(),Binder.getCallingUid())!=PackageManager.SIGNATURE_MATCH')
        self.assertLess(signer, PROVIDER.index('Controller.route('), 'same signer only, before any route')
        guard = PHONE.index('"preview".equals(command.optString("operation"))||"current_apps".equals(command.optString("operation"))')
        self.assertGreater(guard, 0, 'the Mac path still never reaches preview or current_apps')

    def test_refusals_change_nothing_and_never_guess(self):
        start = ROUTE[ROUTE.index('case "start":'):ROUTE.index('case "get_state":')]
        self.assertLess(start.index('for(Slot s:slots)if(s.stops.refuses(task))return refusal('), start.index('in(r.slot,'),
                        'an owner Stop of a task in either slot refuses its later starts in every slot')
        self.assertLess(start.index('if(r.refused())return refused(r,c);'), start.index('in(r.slot,'))
        self.assertIn('MultiSession.start(view(true),p.getString("packageName"),mode,mode.isEmpty()?1:sessionLimit(c));', start,
                      'legacy needs no capability; alongside reads it as a capability only')
        stop = ROUTE[ROUTE.index('case "stop":'):ROUTE.index('case "current_apps":')]
        self.assertLess(stop.index('if(r.refused())return refused(r,c);'), stop.index('agentStop(c,p)'), 'session_required stops nothing')
        command = ROUTE[ROUTE.index('case "command":'):ROUTE.index('case "preview":')]
        self.assertLess(command.index('MultiSession.APP_IN_OTHER_SESSION.equals(MultiSession.openApp('), command.index('->command(c,p)'))
        self.assertIn('if(otherHolds(slot,target))throw new IllegalStateException(MultiSession.APP_IN_OTHER_SESSION);', LAUNCH)
        self.assertLess(LAUNCH.index('otherHolds(slot,target)'), LAUNCH.index('"launchApplication"'), 'checked before every launch')
        self.assertIn('static JSONObject refused(MultiSession.Route r,Context c)throws Exception{return new JSONObject().put("status",r.refusal).put("actionExecuted",false)', CONTROLLER)

    def test_bindings_are_attested_owner_user_and_never_membership(self):
        bindings = span('static MultiSession.Bound bindings(boolean locked,CurrentApps.Owned owned,List<String> front0){', '\n /** A session display')
        self.assertIn('if(!locked&&s.session!=null)try{', bindings, 'nothing is read while locked')
        self.assertIn('CurrentApps.platform(b.get("generation"),b.get("packageNames"),b.get("userId"),b.get("complete"),b.get("closed"),android.os.Process.myUid()/100000)', bindings,
                      'the existing owner-user check')
        self.assertIn('if(pl!=null){closed=pl.closed;device=MultiSession.deviceId(b.get("deviceId"));}', bindings, 'closed ids are passed as historical')
        self.assertIn('"ready".equals(s.status)&&!s.blocked?(s==active?front0:windows(s.session)):null', bindings, 'front only when ready and unblocked')
        self.assertIn('w==null||w.isEmpty()?null:w.get(0)', bindings, 'one front: the topmost application window')
        self.assertNotIn('displayId', CONTROLLER, 'displayId is informational: never used for capture, input, selection or content')
        windows = span('static List<String> windows(Object s){', '\n static synchronized JSONObject route(')
        for call in ('clearNodes(', 'snapshotId', 'nodes.', 'observed', 'getScreenshot'):
            self.assertNotIn(call, windows, call)
        self.assertIn('n.recycle();', windows); self.assertIn('w.recycle();', windows)
        current = span('static synchronized JSONObject currentApps(Context c)', ' static final String EPOCH=')
        self.assertIn('CurrentApps.ofDevices(locked,bound.targets,owned,bound.fronts,c.getPackageName())', current,
                      'bindings only flag members; membership is the snapshot')
        self.assertIn('.put("sessionId",owned!=null?nullable(bound.sessionOf(a.packageName)):held?id:JSONObject.NULL)', current)
        self.assertNotIn('membership(', CONTROLLER)

    def test_owner_stop_is_named_exact_confirmed_and_never_the_mac_path(self):
        stop = ROUTE[ROUTE.index('case "owner_stop":'):]
        self.assertIn('sid==null||sid.isEmpty()?MultiSession.Route.refuse(MultiSession.STALE_SESSION', stop, 'a missing sessionId stops nothing')
        self.assertIn('if(!Objects.equals(task,slots[r.slot].taskId))return new JSONObject().put("status","stale_task")', stop, 'the exact task, null only for an untagged slot')
        self.assertLess(stop.index('stale_task'), stop.index('stop(c,StopLedger.USER)'))
        self.assertIn('put("confirmed","stopped".equals(status)&&StopLedger.USER.equals(stops.reason())\n     &&sid.equals(stops.sessionId())&&Objects.equals(task,stops.taskId()))', stop)
        self.assertIn('return in(r.slot,()->{stop(c,StopLedger.USER);', stop, 'that slot only')
        self.assertIn('case "owner_stop":result=Controller.route(getContext(),"owner_stop",p);break;', PROVIDER)
        caps = re.search(r'case "capabilities":.*?new String\[\]\{([^}]*)\}', PROVIDER).group(1)
        self.assertNotIn('owner_stop', caps, 'not advertised')
        self.assertIn('||"owner_stop".equals(command.optString("operation"))))return new JSONObject().put("status","unavailable")', PHONE,
                      'refused on the Mac app_command path')

    def test_one_request_at_a_time_across_slots(self):
        self.assertIn('if(!replacement.clear()||posting||othersUnanswered())return;', CONTROLLER)
        self.assertIn('static void dispatch(){dispatchOne();for(Slot s:slots)if(s!=active){Slot was=enter(s);try{dispatchOne();}finally{leave(was);}}}', CONTROLLER)


if __name__ == '__main__':
    unittest.main()
