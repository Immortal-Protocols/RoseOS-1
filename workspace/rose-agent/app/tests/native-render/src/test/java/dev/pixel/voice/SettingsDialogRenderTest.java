package dev.pixel.voice;

import static org.junit.Assert.*;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.view.Menu;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.PopupMenu;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;
import org.robolectric.Shadows;

/** Executes the production settings dialog; no phone, service, pairing or network. */
@RunWith(RobolectricTestRunner.class)
@Config(manifest=Config.NONE,sdk=35)
public class SettingsDialogRenderTest {
 private Activity activity(){return Robolectric.buildActivity(Activity.class).setup().get();}
 @Test public void settingsOpensWithDetachedHomeAnchorAndDispatchesPairingExactlyOnce(){
  Activity a=activity();
  Menu menu=new PopupMenu(a,new View(a)).getMenu(); // old Home anchor may no longer be attached
  int[] calls={0};
  for(int n=0;n<24;n++)SettingsDialog.add(menu,"Setting "+n).setOnMenuItemClickListener(i->{fail("wrong action");return true;});
  SettingsDialog.add(menu,"Pair with Rose").setOnMenuItemClickListener(i->{calls[0]++;a.startActivity(new Intent("test.PAIR"));return true;});
  AlertDialog d=SettingsDialog.show(a,menu);
  assertTrue(d.isShowing());assertEquals(25,d.getListView().getCount());
  assertEquals("Pair with Rose",d.getListView().getAdapter().getItem(24));
  d.getListView().performItemClick(null,24,24);
  assertEquals(1,calls[0]);assertEquals("test.PAIR",Shadows.shadowOf(a).getNextStartedActivity().getAction());
 }
 @Test public void fullscreenHomeDoesNotConstrainSettingsAndCloseDoesNothing(){
  Activity a=activity();FrameLayout home=new FrameLayout(a);a.setContentView(home);
  Menu menu=new PopupMenu(a,home).getMenu();int[] calls={0};
  SettingsDialog.add(menu,"Pair with Rose").setOnMenuItemClickListener(i->{calls[0]++;return true;});
  AlertDialog d=SettingsDialog.show(a,menu);assertTrue(d.isShowing());
  d.getButton(AlertDialog.BUTTON_NEGATIVE).performClick();assertEquals(0,calls[0]);
 }
 @Test public void hiddenRowsDoNotShiftDispatchAndToggleStateRemainsVisible(){
  Activity a=activity();Menu menu=new PopupMenu(a,new View(a)).getMenu();int[] calls={0};
  SettingsDialog.add(menu,"Hidden").setVisible(false);
  SettingsDialog.add(menu,"Keep screen on").setCheckable(true).setChecked(true).setOnMenuItemClickListener(i->{calls[0]++;return true;});
  SettingsDialog.add(menu,"Disabled").setEnabled(false).setOnMenuItemClickListener(i->{fail("disabled action");return true;});
  AlertDialog d=SettingsDialog.show(a,menu);
  assertEquals(2,d.getListView().getCount());assertEquals("Keep screen on — On",d.getListView().getAdapter().getItem(0));
  d.getListView().performItemClick(null,0,0);assertEquals(1,calls[0]);
  d=SettingsDialog.show(a,menu);d.getListView().performItemClick(null,1,1);assertEquals(1,calls[0]);
 }
}
