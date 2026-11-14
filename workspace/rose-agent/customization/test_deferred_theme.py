"""Focused #744 regression: execute actual deferral methods, never an Android runtime.

The JVM probe extracts the current themeBusy/maybeRetheme implementations and
uses the real ReturnModel and TaskControl. It checks control flow/identity, not
Activity.recreate or rendered pixels. The reporting tests require the conversation
acknowledgment, including deferred/rebuilt/closed and older-producer cases.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / 'workspace/rose-agent/app/src/dev/pixel/voice'
sys.path.insert(0, str(ROOT / 'prototypes/voice-phone-demo'))
from rose import customization


def method(source, signature):
    start = source.index(signature)
    opening = source.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


class DeferredTheme(unittest.TestCase):
    def test_production_deferral_methods_keep_open_context_and_retry_after_close(self):
        source = (SRC / 'RoseConversationActivity.java').read_text()
        busy = method(source, 'boolean themeBusy()')
        retheme = method(source, 'void maybeRetheme()')
        retained = method(source, 'public Object onRetainNonConfigurationInstance()')
        # The actual refresh tick must continue retrying the deferred decision.
        self.assertIn('if(themePending){maybeRetheme();if(isFinishing()||isDestroyed())return;}', source)
        self.assertIn('final Runnable themeChanged=this::maybeRetheme;', source)
        self.assertIn('builtTheme=RoseThemeNow.id;', source)
        probe = r'''
package dev.pixel.voice;
public final class DeferredThemeProbe {
 static int checks;
 static void check(boolean value,String why){checks++;if(!value)throw new AssertionError(why);}
 static final class Panel { boolean open; boolean isOpen(){return open;} }
 static final class Dialog { boolean showing; boolean isShowing(){return showing;} }
 static final class Config { String theme="glacier"; }
 static final class CustomizationStore { static Config read(Screen s){return s.saved;} }
 static final class Screen {
  Config saved=new Config();String builtTheme="oxblood",shownTaskId="task-42";boolean themePending,themeRecreating,finishing,destroyed;
  final Object themeToken=new Object();
  Screen(){ConversationThemeState.LIVE.opened(themeToken);ConversationThemeState.LIVE.drawn(themeToken,builtTheme);}
  Panel overview;Dialog stopDialog;boolean stopInFlight;String stopPendingFor,stopMessage="";
  ReturnModel.Target exact=new ReturnModel.Target("org.fossify.notes","Notes","task-42");
  ReturnModel.State returnState=ReturnModel.initial().observed(exact);
  final Object taskProjection=new Object();int recreations;
  boolean isFinishing(){return finishing;}boolean isDestroyed(){return destroyed;}
  void recreate(){recreations++;}
  void tick(){if(themePending){maybeRetheme();if(isFinishing()||isDestroyed())return;}}
  BUSY_METHOD
  RETHEME_METHOD
  RETAIN_METHOD
 }
 static void pending(Screen s,String kind){
  if(kind.equals("return_pending"))s.returnState=s.returnState.tapped();
  if(kind.equals("return_sheet")){s.returnState=s.returnState.tapped();s.returnState=s.returnState.answered(s.returnState.request,ReturnModel.Answer.REFUSED);}
  if(kind.equals("background_work")){s.overview=new Panel();s.overview.open=true;}
  if(kind.equals("stop_dialog")){s.stopDialog=new Dialog();s.stopDialog.showing=true;}
  if(kind.equals("stop_in_flight"))s.stopInFlight=true;
  if(kind.equals("stop_pending"))s.stopPendingFor="exact-task-77";
 }
 static void close(Screen s,String kind){
  if(kind.equals("return_pending"))s.returnState=s.returnState.cancelled();
  if(kind.equals("return_sheet"))s.returnState=s.returnState.stayed();
  if(kind.equals("background_work"))s.overview.open=false;
  if(kind.equals("stop_dialog"))s.stopDialog.showing=false;
  if(kind.equals("stop_in_flight"))s.stopInFlight=false;
  if(kind.equals("stop_pending"))s.stopPendingFor=null;
 }
 public static void main(String[] args){
  for(String kind:new String[]{"return_pending","return_sheet","background_work","stop_dialog","stop_in_flight","stop_pending"}){
   Screen s=new Screen();pending(s,kind);ReturnModel.State before=s.returnState;Object tasks=s.taskProjection;
   String stop=s.stopPendingFor;Panel panel=s.overview;Dialog dialog=s.stopDialog;
   s.maybeRetheme();for(int i=0;i<10;i++)s.tick();
   check(s.themePending&&s.recreations==0,kind+": never recreates while open");
   check("deferred".equals(ConversationThemeState.LIVE.snapshot("glacier").get("state")),kind+": open flow is reported as deferred");
   check(s.onRetainNonConfigurationInstance()==null,kind+": no live action is transferred");
   check(s.returnState==before&&s.returnState.target==s.exact,kind+": exact return state untouched");
   check(s.taskProjection==tasks&&s.stopPendingFor==stop&&s.overview==panel&&s.stopDialog==dialog,kind+": task/stop/panel identity untouched");
   close(s,kind);ReturnModel.State closed=s.returnState;s.stopMessage="Stop was not confirmed.";s.tick();
   check(!s.themePending&&s.recreations==1,kind+": close schedules the latest theme once");
   check(s.returnState==closed&&s.taskProjection==tasks,kind+": scheduling rebuild does not mutate context");
   ConversationThemeState.Context kept=(ConversationThemeState.Context)s.onRetainNonConfigurationInstance();
   check(kept.returnState==closed&&kept.taskId.equals("task-42")&&kept.stopMessage.equals("Stop was not confirmed."),kind+": settled exact return/task and Stop note survive handoff");
   check(Boolean.FALSE.equals(ConversationThemeState.LIVE.snapshot("glacier").get("applied")),kind+": scheduling a rebuild is not a frame");
   for(int i=0;i<10;i++)s.tick();check(s.recreations==1,kind+": no repeated recreation while Android handles it");
   // Separate case: latest request returns to the theme already built.
   Screen cancelled=new Screen();pending(cancelled,kind);cancelled.maybeRetheme();cancelled.saved.theme="oxblood";
   cancelled.tick();close(cancelled,kind);cancelled.tick();
   check(!cancelled.themePending&&cancelled.recreations==0,kind+": reverted request cancels obsolete rebuild");
   ConversationThemeState.LIVE.closed(s.themeToken);ConversationThemeState.LIVE.closed(cancelled.themeToken);
  }
  Screen both=new Screen();pending(both,"background_work");pending(both,"return_pending");both.maybeRetheme();
  close(both,"background_work");both.tick();check(both.recreations==0,"one busy flow remains");
  close(both,"return_pending");both.tick();check(both.recreations==1,"all busy flows closed");
  for(boolean dead:new boolean[]{false,true}){Screen s=new Screen();s.finishing=!dead;s.destroyed=dead;s.maybeRetheme();check(s.recreations==0,"no recreation after finish/destroy");}
  // Pending Stop must stay bound through acknowledgments, rather than being cleared merely for the theme.
  check(!TaskControl.settled("exact-task-77","exact-task-77","running"),"running Stop is not settled");
  check(TaskControl.settled("exact-task-77","exact-task-77","completed"),"own terminal state settles Stop");
  ConversationThemeState tracker=new ConversationThemeState();Object old=new Object(),replacement=new Object(),second=new Object();
  check("not_open".equals(tracker.snapshot("glacier").get("state")),"closed screen is not a made-up draw");
  tracker.opened(old);check(Boolean.FALSE.equals(tracker.snapshot("glacier").get("applied")),"opening is not drawing");
  tracker.drawn(old,"oxblood");check(Boolean.TRUE.equals(tracker.snapshot("oxblood").get("applied")),"exact drawn theme is applied");
  check(Boolean.FALSE.equals(tracker.snapshot("glacier").get("applied")),"wrong-theme frame never acknowledges requested theme");
  check("oxblood".equals(tracker.snapshot("glacier").get("shown")),"reports the old drawn theme honestly");
  tracker.deferred(old,true);check("deferred".equals(tracker.snapshot("glacier").get("state")),"protected old frame defers new theme");
  tracker.deferred(old,false);tracker.closed(old);
  check(Boolean.FALSE.equals(tracker.snapshot("glacier").get("applied")),"recreation gap is not completion");
  tracker.drawn(old,"glacier");check(Boolean.FALSE.equals(tracker.snapshot("glacier").get("applied")),"destroyed callback cannot resurrect evidence");
  tracker.opened(replacement);tracker.drawn(old,"glacier");
  check("pending".equals(tracker.snapshot("glacier").get("state")),"old callback cannot confirm replacement");
  tracker.drawn(replacement,"glacier");check(Boolean.TRUE.equals(tracker.snapshot("glacier").get("applied")),"replacement draw completes latest theme");
  tracker.opened(second);check(Boolean.FALSE.equals(tracker.snapshot("glacier").get("applied")),"every existing screen must acknowledge");
  tracker.drawn(second,"oxblood");check(Boolean.FALSE.equals(tracker.snapshot("glacier").get("applied")),"one stale screen blocks everywhere");
  check(tracker.snapshot("glacier").get("shown")==MiniJson.NULL,"mixed screens have no common shown theme");
  tracker.closed(second);check(Boolean.TRUE.equals(tracker.snapshot("glacier").get("applied")),"only remaining drawn screen confirms");
  tracker.closed(replacement);check(Boolean.FALSE.equals(tracker.snapshot("glacier").get("applied")),"closed screen does not leave persistent applied evidence");
  System.out.println("DeferredThemeProbe: "+checks+" checks passed");
 }
}
'''.replace('BUSY_METHOD', busy).replace('RETHEME_METHOD', retheme).replace('RETAIN_METHOD', retained)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'DeferredThemeProbe.java'
            path.write_text(probe)
            subprocess.run(['java', '-m', 'jdk.compiler/com.sun.tools.javac.Main', '-d', directory,
                            str(SRC / 'ReturnModel.java'), str(SRC / 'TaskControl.java'),
                            str(SRC / 'MiniJson.java'), str(SRC / 'ConversationThemeState.java'), str(path)], check=True)
            done = subprocess.run(['java', '-cp', directory, 'dev.pixel.voice.DeferredThemeProbe'],
                                  check=True, text=True, capture_output=True)
            self.assertIn('87 checks passed', done.stdout)

    def test_no_everywhere_claim_without_a_conversation_acknowledgment(self):
        # This is exactly the producer's present result shape. Home and SystemUI
        # can acknowledge while the independently hosted conversation is deferred.
        result = {'status': 'verified', 'customization': {'theme': 'glacier', 'themeLabel': 'Glacier', 'revision': 9},
                  'home': {'appliedRevision': 9, 'applied': True},
                  'system': {'shown': 'glacier', 'applied': True}}
        out = customization.outcome('customization_theme', result)
        self.assertNotEqual(out['outcome'], 'applied',
                            'No conversation acknowledgment proves that its deferred rebuild has completed.')
        self.assertNotIn('everywhere', out['message'])

    def test_conversation_acknowledgment_is_wired_to_actual_lifecycle_and_draw(self):
        agent = (SRC / 'CustomizationAgent.java').read_text()
        self.assertIn('.put("conversation"', agent)
        self.assertIn('ConversationThemeState.LIVE.snapshot(stored.theme)', agent)
        source = (SRC / 'RoseConversationActivity.java').read_text()
        create = method(source, '@Override protected void onCreate(Bundle b)')
        self.assertIn('ConversationThemeState.LIVE.opened(themeToken)', create)
        self.assertIn('getLastNonConfigurationInstance()', create)
        self.assertIn('returnState=kept.returnState;shownTaskId=kept.taskId;stopMessage=kept.stopMessage;', create)
        draw = method(create, 'public void onDraw()')
        self.assertIn('root.post(()->', draw)
        self.assertIn('!isFinishing()&&!isDestroyed()&&!themeRecreating', draw)
        self.assertIn('ConversationThemeState.LIVE.drawn(themeToken,builtTheme)', draw)
        self.assertEqual(create.count('ConversationThemeState.LIVE.drawn('), 1)
        self.assertIn('ConversationThemeState.LIVE.closed(themeToken)', method(source, '@Override protected void onDestroy()'))
        # The acknowledgment adds no Return/Stop action or provider access.
        tracker = (SRC / 'ConversationThemeState.java').read_text()
        self.assertNotIn('import android.', tracker)
        self.assertIn('new ConversationThemeState.Context(returnState,shownTaskId,stopMessage)', source)


if __name__ == '__main__':
    unittest.main()
