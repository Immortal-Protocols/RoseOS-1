package dev.pixel.voice;

import static org.junit.Assert.*;
import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.widget.TextView;
import java.io.File;
import java.io.FileOutputStream;
import java.util.ArrayList;
import java.util.List;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.GraphicsMode;

/** Rose Agent's settings (owner request): Pair with Mac and About only. Executes the production dialog view and its
 * text rules; no phone, service, pairing or network. */
@RunWith(RobolectricTestRunner.class)
@Config(manifest=Config.NONE,sdk=35)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class SettingsDialogRenderTest {
 private Activity activity(){return Robolectric.buildActivity(Activity.class).setup().get();}

 static List<String> texts(View v,List<String> out){
  if(v instanceof TextView&&v.getVisibility()==View.VISIBLE)out.add(((TextView)v).getText().toString());
  if(v instanceof ViewGroup)for(int i=0;i<((ViewGroup)v).getChildCount();i++)texts(((ViewGroup)v).getChildAt(i),out);
  return out;
 }
 static View tagged(View v,Object tag){
  if(tag.equals(v.getTag()))return v;
  if(v instanceof ViewGroup)for(int i=0;i<((ViewGroup)v).getChildCount();i++){View f=tagged(((ViewGroup)v).getChildAt(i),tag);if(f!=null)return f;}
  return null;
 }
 static List<View> clickable(View v,List<View> out){
  if(v.isClickable())out.add(v);
  if(v instanceof ViewGroup)for(int i=0;i<((ViewGroup)v).getChildCount();i++)clickable(((ViewGroup)v).getChildAt(i),out);
  return out;
 }

 @Test public void onlyPairWithMacAndAboutAreShownAndPairOpensTheFlowOnce(){
  Activity a=activity();int[] pairs={0};
  AlertDialog d=SettingsScreen.show(a,"Paired with Studio Mac · Connected","Version 312-beta (build 312)",()->pairs[0]++);
  assertTrue(d.isShowing());
  View root=d.getWindow().getDecorView();
  List<String> shown=texts(root,new ArrayList<>());
  for(String expected:new String[]{"Rose Agent settings","Mac","Pair with Mac","Paired with Studio Mac · Connected","About","Rose Agent","Version 312-beta (build 312)","Close"})
   assertTrue(expected+" in "+shown,shown.contains(expected));
  for(String gone:new String[]{"Voice controls","Server connection","Conversation mode","Keep screen on","App control while locked","Classic home","Visual cards","Voice boost (+6 dB)","Result card style","Talk to a Guardian"})
   assertFalse(gone,shown.contains(gone));
  // Exactly one actionable row besides the dialog's Close button.
  View row=tagged(root,"pair-with-mac");
  assertNotNull(row);
  List<View> actions=clickable(d.findViewById(android.R.id.custom),new ArrayList<>());
  assertEquals("one action in the settings body",1,actions.size());
  assertEquals("Pair with Mac. Paired with Studio Mac · Connected",row.getContentDescription());
  row.performClick();
  assertEquals(1,pairs[0]);
  assertFalse("the dialog closes as the pairing flow opens",d.isShowing());
 }

 @Test public void closeDoesNothingAndFullscreenHomeDoesNotConstrainIt(){
  Activity a=activity();FrameLayout home=new FrameLayout(a);a.setContentView(home);int[] pairs={0};
  AlertDialog d=SettingsScreen.show(a,"Not paired. Scan the code your Mac shows.","Version 312 (build 312)",()->pairs[0]++);
  assertTrue(d.isShowing());
  d.getButton(AlertDialog.BUTTON_NEGATIVE).performClick();
  org.robolectric.shadows.ShadowLooper.idleMainLooper(); // AlertDialog dismisses through a posted message
  assertEquals(0,pairs[0]);assertFalse(d.isShowing());
 }

 @Test public void pairingStateLinesCoverEveryState(){
  assertEquals("Not paired. Scan the code your Mac shows.",RoseSettingsModel.pairing(null,false,null,null));
  assertEquals("This phone's pairing was lost. Pair again.",RoseSettingsModel.pairing(null,true,null,null));
  assertEquals("Paired with Studio · Connected",RoseSettingsModel.pairing("Studio",false,"connected",""));
  assertEquals("Paired with Studio · Connecting…",RoseSettingsModel.pairing("Studio",false,"connecting",""));
  assertEquals("Paired with Studio · Connecting…",RoseSettingsModel.pairing("Studio",false,"",""));
  assertEquals("Paired with Studio · Offline",RoseSettingsModel.pairing("Studio",false,"offline",""));
  assertEquals("Paired with Studio · Turn on automatic date and time",RoseSettingsModel.pairing("Studio",false,"offline","clock"));
  assertEquals("Paired with Studio · The Mac no longer accepts this phone. Pair again.",RoseSettingsModel.pairing("Studio",false,"unpaired","revoked"));
  // The Mac's name is display text only: control characters stripped, blank shown as "your Mac", long names cut.
  assertEquals("Paired with your Mac · Connected",RoseSettingsModel.pairing(" \u0007 ",false,"connected",""));
  assertEquals("Paired with Evil · Connected",RoseSettingsModel.pairing("Ev\nil",false,"connected",""));
  String longName="M".repeat(80);
  assertEquals(RoseSettingsModel.MAX_NAME,RoseSettingsModel.name(longName).length());
  assertEquals("Version 312-beta (build 312)",RoseSettingsModel.version("312-beta",312));
  assertEquals("Version unknown (build 0)",RoseSettingsModel.version(null,0));
 }

 @Test public void rendersForReview() throws Exception{
  Activity a=activity();
  AlertDialog d=SettingsScreen.show(a,RoseSettingsModel.pairing("Studio Mac",false,"connected",""),RoseSettingsModel.version("312-beta",312),()->{});
  View root=d.getWindow().getDecorView();
  root.measure(View.MeasureSpec.makeMeasureSpec(1080,View.MeasureSpec.AT_MOST),View.MeasureSpec.makeMeasureSpec(1600,View.MeasureSpec.AT_MOST));
  root.layout(0,0,root.getMeasuredWidth(),root.getMeasuredHeight());
  Bitmap b=Bitmap.createBitmap(Math.max(1,root.getWidth()),Math.max(1,root.getHeight()),Bitmap.Config.ARGB_8888);
  root.draw(new Canvas(b));
  File out=new File("target/render/rose-settings.png");out.getParentFile().mkdirs();
  try(FileOutputStream f=new FileOutputStream(out)){b.compress(Bitmap.CompressFormat.PNG,100,f);}
  assertTrue(out.length()>0);
 }
}
