"""Source checks for the controller's close-and-replacement race (multi-app report; no device). Android lists one session
device-wide and refuses a same-owner, same-name request while an earlier session is still listed. Neither onClosed nor
an empty owned-device snapshot proves a closed session unlisted (its release can throw after onClosed is due; OS Core
MULTI-SESSION-CONSULT); Android's admission does: requestComputerControlSession is synchronous and its same-name check
holds the session-list lock, and every platform callback for a request follows that check. So the controller sends one
request per start at the earliest point (every session it closed reported onClosed, no earlier request unanswered), with
no timer and no retry, keeps each closed session's handle unconfirmed until Android's first callback for a later request
(a bare normal return proves nothing: with no service it is a silent no-op), and reports Android's refusal with a fixed
reason. cleanup_failed is
held, never cleared; the fixed name (which makes the admission cover every earlier session) and every reply key are
unchanged. Real simultaneous apps are not claimed: that needs a platform change."""
from pathlib import Path
import re
import unittest

SRC = Path(__file__).resolve().parents[1]/'agent-pip/src/dev/pixel/backgroundcontrol'
CONTROLLER = (SRC/'Controller.java').read_text()
REPLACEMENT = (SRC/'Replacement.java').read_text()


def span(start, end):
    return CONTROLLER[CONTROLLER.index(start):CONTROLLER.index(end, CONTROLLER.index(start))]


START = span('static synchronized JSONObject start(', 'static void attachLifecycle(')
LIFECYCLE = span('static void attachLifecycle(', 'static void launch(')
LAUNCH = span('static void launch(', 'static void dispatch(')
DISPATCH = span('static void dispatch(', 'static void require(')
STOP = span('static synchronized void stop(', '\n')


class ReplacementWiringTest(unittest.TestCase):
    def test_a_start_never_requests_directly(self):
        self.assertNotIn('main.post(()->{', START, 'the request is not posted from start itself')
        self.assertIn('Runnable request=()->{', START)
        self.assertIn('pending=request;pendingToken=token;dispatch();', START)
        self.assertEqual(CONTROLLER.count('main.post(r);'), 1, 'only dispatch posts a request')
        self.assertEqual(CONTROLLER.count('"requestSession"'), 1, 'exactly one request call: no retry')
        self.assertIn('synchronized(Controller.class){if(token!=generation)return;replacement.requested(token);}\n    type.getMethod("requestSession",', START)

    def test_admission_is_only_the_first_platform_callback(self):
        switch = START.index('switch(method.getName()){case "onSessionCreated":case "onSessionCreationFailed":case "onSessionPending":'
                             'replacement.admitted(token);replacement.answered(token);break;')
        guard = START.index('if(token!=generation){')
        self.assertLess(switch, guard, 'a late or stale callback still attests: it is counted before the generation check')
        self.assertEqual(CONTROLLER.count('replacement.admitted('), 1, 'admission comes from nowhere else')
        # Not from requestSession's return (silent with no service), not from a call that threw.
        call = START.index('type.getMethod("requestSession",')
        catch = START.index('}catch(Throwable e){synchronized(Controller.class){replacement.answered(token);')
        self.assertNotIn('replacement.admitted(', START[call:catch])
        self.assertNotIn('replacement.admitted(', START[catch:])
        # onClosed and the owned-device snapshot never clear an unconfirmed session.
        for hook in (LIFECYCLE, span('static synchronized JSONObject currentApps(', 'static JSONObject capture()')):
            self.assertNotIn('replacement.admitted(', hook)
        self.assertEqual(CONTROLLER.count('replacement.closed(token)'), 2, 'onClosed and onSessionClosed only mark the earliest point')

    def test_an_unanswered_request_is_reported_and_never_empty(self):
        reason = span('static String reason(Context c)', '\n')
        self.assertIn('if("starting".equals(status)){String w=pending!=null&&pendingToken==generation?replacement.blocker()'
                      ':replacement.unanswered(generation)?Replacement.REQUEST_UNANSWERED:"";if(!w.isEmpty())return w;}', reason)
        # current_apps reads Replacement.releasing(), which counts an unanswered request: unknown, never empty.
        self.assertIn(':CurrentApps.of(locked,session!=null||replacement.releasing(),status,blocked,target,front,c.getPackageName());', CONTROLLER)
        self.assertIn('boolean releasing(){return !unconfirmed.isEmpty()||!unanswered.isEmpty();}', REPLACEMENT)
        self.assertIn('String blocker(){return !unanswered.isEmpty()?REQUEST_UNANSWERED:!awaiting.isEmpty()?REPLACING:"";}', REPLACEMENT)

    def test_dispatch_waits_for_the_platform_and_drops_older_starts(self):
        self.assertEqual(DISPATCH.strip(), 'static void dispatch(){if(pending==null)return;if(pendingToken!=generation){pending=null;return;}'
                         'if(!replacement.clear())return;Runnable r=pending;pending=null;main.post(r);}')
        # No guessed wait: the only delayed post is the start's existing 600 s deadline.
        self.assertEqual(CONTROLLER.count('postDelayed('), 1)
        self.assertIn('main.postDelayed(()->{synchronized(Controller.class){if(token==generation)stop(c,StopLedger.DEADLINE);}},DEADLINE);', START)

    def test_only_a_successful_close_is_waited_on_and_cleanup_failed_is_held(self):
        self.assertIn('if(old!=null)try{old.getClass().getMethod("close").invoke(old);replacement.closing(sessionToken,old);}'
                      'catch(Throwable e){session=old;error=root(e).toString();status="cleanup_failed";}', STOP)
        self.assertIn('stop(c,StopLedger.AGENT);if(session!=null)throw new IllegalStateException("Previous session cleanup is incomplete");', START)
        self.assertIn('old.getClass().getMethod("close").invoke(old);replacement.closing(token,old);}catch(Throwable ignored){}', LAUNCH)
        self.assertIn('case "onSessionCreated":session=args[0];sessionToken=token;', START)

    def test_every_answer_and_close_is_counted_before_the_generation_check(self):
        answered = START.index('switch(method.getName()){case "onSessionCreated":case "onSessionCreationFailed":case "onSessionPending":'
                               'replacement.admitted(token);replacement.answered(token);break;case "onSessionClosed":replacement.closed(token);break;}')
        stale = START.index('if(token!=generation){if(method.getName().equals("onSessionCreated")){args[0].getClass().getMethod("close")'
                            '.invoke(args[0]);replacement.closing(token,args[0]);}dispatch();return null;}')
        self.assertLess(answered, stale)
        self.assertIn('synchronized(Controller.class){replacement.answered(token);if(token==generation){', START,
                      'a request call that throws is answered too')
        closed = LIFECYCLE.index('if(m.getName().equals("onClosed")){replacement.closed(token);dispatch();}')
        self.assertLess(closed, LIFECYCLE.index('if(token!=generation)return null;'))

    def test_refusals_are_named_and_states_stay_compatible(self):
        self.assertIn('status=AnyAppControl.targetRefused(error)?"target_not_allowed":Replacement.listed(error)?"rejected":"failed";', START)
        reason = span('static String reason(Context c)', '\n')
        self.assertIn('return w;}', reason)
        self.assertIn('if("rejected".equals(status))return Replacement.reason(error);', reason)
        # Waiting is the existing non-terminal "starting" (callers already wait on it); no new status word.
        for word in ('status="replacing"', 'status="request_unanswered"', 'status="unavailable"', 'status=Replacement'):
            self.assertNotIn(word, CONTROLLER)
        state = span('static synchronized JSONObject state(Context c)', '\n')
        self.assertEqual(re.findall(r'\.put\("(\w+)"', state), ['status', 'sessionId', 'targetPackage', 'error', 'reason', 'deviceLocked', 'blocked',
                         'width', 'height', 'operationCount', 'elapsedMs', 'foregroundFallback', 'taskId', 'anyAppControl', 'stopReason',
                         'stoppedSessionId', 'stoppedTaskId'])

    def test_name_unchanged_and_no_concurrency_claim(self):
        # Unique names alone would only turn the name refusal into the session-limit refusal.
        self.assertEqual(CONTROLLER.count('setName'), 1)
        self.assertIn('b.getMethod("setName",String.class).invoke(builder,"pixel-voice-background");', START)
        self.assertEqual(CONTROLLER.count('static Object session;'), 1, 'one session: real multi-app is a separate platform change')

    def test_current_apps_never_calls_a_releasing_session_empty(self):
        self.assertIn(':CurrentApps.of(locked,session!=null||replacement.releasing(),status,blocked,target,front,c.getPackageName());', CONTROLLER)


if __name__ == '__main__':
    unittest.main()
