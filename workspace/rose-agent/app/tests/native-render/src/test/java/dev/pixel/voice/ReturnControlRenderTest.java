package dev.pixel.voice;

import static org.junit.Assert.*;
import android.app.Activity;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.view.View;
import android.widget.FrameLayout;
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

/** 1C: the production V3 return control (ReturnControl) and its unavailable sheet (ReturnSheet), every state. No phone,
 * task or app. Renders go to target/render for review against Designer's Rose-F11-Return.html. */
@RunWith(RobolectricTestRunner.class)
@Config(manifest=Config.NONE,sdk=35,qualifiers="w390dp-h844dp-xxhdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class ReturnControlRenderTest {
 static final ReturnModel.Target NOTES=new ReturnModel.Target("org.fossify.notes","Notes","task-42");
 Activity activity(){return Robolectric.buildActivity(Activity.class).setup().get();}
 ReturnModel.State ready(){return ReturnModel.initial().observed(NOTES);}
 int dp(Activity a,float v){return Math.round(v*a.getResources().getDisplayMetrics().density);}
 void measure(View v,int width){v.measure(View.MeasureSpec.makeMeasureSpec(width,View.MeasureSpec.EXACTLY),View.MeasureSpec.makeMeasureSpec(0,View.MeasureSpec.UNSPECIFIED));v.layout(0,0,v.getMeasuredWidth(),v.getMeasuredHeight());}

 @Test public void hiddenWithoutASource(){
  ReturnControl c=new ReturnControl(activity());c.show(ReturnModel.initial());
  assertEquals(View.GONE,c.getVisibility());
 }

 @Test public void readyIsTheV3ControlNamedForItsSource(){
  Activity a=activity();ReturnControl c=new ReturnControl(a);c.show(ready());
  assertEquals(View.VISIBLE,c.getVisibility());
  assertEquals("Notes",c.label.getText().toString());
  assertEquals("Return to Notes",c.button.getContentDescription().toString());
  assertEquals(ReturnModel.HELP,c.button.getTooltipText().toString());
  assertTrue(c.button.isEnabled()&&c.button.isClickable()&&c.button.isFocusable());
  assertEquals(View.GONE,c.cancel.getVisibility());
  assertEquals(ReturnControl.INK,c.label.getCurrentTextColor());
  measure(c,dp(a,358));
  assertTrue("48dp target",c.button.getHeight()>=dp(a,48));
  assertTrue("12dp side padding inside the surface",c.button.getPaddingLeft()==dp(a,12)&&c.button.getPaddingRight()==dp(a,12));
  assertTrue("sized to its content, not the footer",c.button.getWidth()<dp(a,140));
  render(c,"return-v3-ready");
 }

 @Test public void pendingReadsReturningWithASeparateCancel(){
  Activity a=activity();ReturnControl c=new ReturnControl(a);
  c.show(ready().tapped());
  assertEquals("Returning…",c.label.getText().toString());
  assertEquals("Return to Notes",c.button.getContentDescription().toString());
  assertEquals("Returning",c.button.getStateDescription().toString());
  assertFalse("duplicate requests are ignored",c.button.isEnabled());
  assertEquals(View.VISIBLE,c.cancel.getVisibility());
  assertEquals("Cancel",c.cancel.getText().toString());
  assertEquals(ReturnControl.PENDING_INK,c.label.getCurrentTextColor());
  measure(c,dp(a,358));
  assertTrue("Cancel is a 48dp target",c.cancel.getHeight()>=dp(a,48)&&c.cancel.getWidth()>=dp(a,48));
  render(c,"return-v3-pending");
 }

 @Test public void aLongNameTruncatesButIsSpokenInFull(){
  Activity a=activity();ReturnControl c=new ReturnControl(a);
  String name="A very long application name that will certainly not fit in the footer at all";
  c.show(ReturnModel.initial().observed(new ReturnModel.Target("x.y",name,"t")));
  measure(c,dp(a,254));
  assertEquals("Return to "+name,c.button.getContentDescription().toString());
  assertTrue("the label is cut, not the control",c.label.getLayout()!=null&&c.label.getLayout().getEllipsisCount(0)>0);
  assertTrue(c.getMeasuredWidth()<=dp(a,254));
  render(c,"return-v3-long");
 }

 @Test public void theSheetOffersStaySwitcherAndHome(){
  Activity a=activity();List<String> chosen=new ArrayList<>();
  ReturnSheet s=new ReturnSheet(a,chosen::add);FrameLayout root=new FrameLayout(a);root.addView(s,new FrameLayout.LayoutParams(-1,-1));a.setContentView(root);
  s.show(true);
  assertEquals(View.VISIBLE,s.getVisibility());
  assertEquals("Your source app is unavailable.",s.title.getText().toString());
  assertEquals("You are still in Rose. Choose where to go; Rose keeps working.",s.body.getText().toString());
  for(View b:new View[]{s.stay,s.switcher,s.home}){measure(s,dp(a,390));assertTrue(b.getHeight()>=dp(a,48));}
  s.stay.performClick();s.switcher.performClick();s.home.performClick();
  assertEquals(java.util.Arrays.asList("Stay in Rose","Open switcher","Go Home"),chosen);
  assertTrue(s.title.isAccessibilityHeading());
  render(s,"return-v3-sheet");
  s.show(false);assertEquals(View.GONE,s.getVisibility());
 }

 void render(View v,String name){
  int w=Math.max(1,v.getMeasuredWidth()),h=Math.max(1,v.getMeasuredHeight());
  Bitmap b=Bitmap.createBitmap(w,h,Bitmap.Config.ARGB_8888);Canvas canvas=new Canvas(b);canvas.drawColor(0xff170b11);v.draw(canvas);
  File out=new File("target/render");out.mkdirs();
  try(FileOutputStream f=new FileOutputStream(new File(out,name+".png"))){b.compress(Bitmap.CompressFormat.PNG,100,f);}catch(Exception e){throw new AssertionError(e);}
 }
}
