package dev.pixel.voice;

import static org.junit.Assert.*;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.view.View;
import java.util.ArrayList;
import java.util.List;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.GraphicsMode;
import org.robolectric.shadows.ShadowLooper;

/** 1I: the one-time offer to uninstall the retired Rose Models app. Executes the production dialog; no package is
 * touched (the uninstall action is a counter here, Android's confirmation in the app). */
@RunWith(RobolectricTestRunner.class)
@Config(manifest=Config.NONE,sdk=35)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class RetiredAppNoticeRenderTest {
 private Activity activity(){return Robolectric.buildActivity(Activity.class).setup().get();}

 @Test public void itSaysWhatHappensAndUninstallHandsOffOnce(){
  Activity a=activity();int[] answered={0},uninstalls={0};
  AlertDialog d=RetiredAppNotice.show(a,()->answered[0]++,()->uninstalls[0]++);
  assertTrue(d.isShowing());
  List<String> shown=SettingsDialogRenderTest.texts(d.getWindow().getDecorView(),new ArrayList<>());
  for(String expected:new String[]{"Rose Models is retired",AppRemoval.RETIRED_BODY,"Uninstall","Not now"})assertTrue(expected+" in "+shown,shown.contains(expected));
  assertTrue(AppRemoval.RETIRED_BODY.contains("saved sign-in key"));
  assertTrue(AppRemoval.RETIRED_BODY.contains("System folder"));
  d.getButton(DialogInterface.BUTTON_POSITIVE).performClick();ShadowLooper.idleMainLooper();
  assertEquals(1,uninstalls[0]);assertEquals(1,answered[0]);assertFalse(d.isShowing());
 }

 @Test public void notNowRecordsTheAnswerAndRemovesNothing(){
  Activity a=activity();int[] answered={0},uninstalls={0};
  AlertDialog d=RetiredAppNotice.show(a,()->answered[0]++,()->uninstalls[0]++);
  d.getButton(DialogInterface.BUTTON_NEGATIVE).performClick();ShadowLooper.idleMainLooper();
  assertEquals(0,uninstalls[0]);assertEquals(1,answered[0]);assertFalse(d.isShowing());
 }

 @Test public void backRecordsTheAnswerOnceAndRemovesNothing(){
  Activity a=activity();int[] answered={0},uninstalls={0};
  AlertDialog d=RetiredAppNotice.show(a,()->answered[0]++,()->uninstalls[0]++);
  d.cancel();ShadowLooper.idleMainLooper();
  assertEquals(0,uninstalls[0]);assertEquals(1,answered[0]);assertFalse(d.isShowing());
 }
}
