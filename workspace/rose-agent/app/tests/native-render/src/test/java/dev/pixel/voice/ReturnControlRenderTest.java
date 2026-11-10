package dev.pixel.voice;

import static org.junit.Assert.*;
import android.app.Activity;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.drawable.ColorDrawable;
import android.view.View;
import android.widget.FrameLayout;
import java.io.File;
import java.io.FileOutputStream;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.GraphicsMode;

/** 1C: the production return control (ReturnControl) drawing every ReturnModel phase. No phone, task or app. */
@RunWith(RobolectricTestRunner.class)
@Config(manifest=Config.NONE,sdk=35)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class ReturnControlRenderTest {
 static final ReturnModel.Target MESSAGES=new ReturnModel.Target("com.bluebubbles.messaging","Messages","task-41");
 ReturnControl control(Activity a){ReturnControl c=new ReturnControl(a);FrameLayout root=new FrameLayout(a);root.addView(c,new FrameLayout.LayoutParams(-2,-2));a.setContentView(root);return c;}
 Activity activity(){return Robolectric.buildActivity(Activity.class).setup().get();}
 ReturnModel.State ready(){return ReturnModel.initial().observed(MESSAGES,0);}
 int measure(View v){v.measure(View.MeasureSpec.makeMeasureSpec(1080,View.MeasureSpec.AT_MOST),View.MeasureSpec.makeMeasureSpec(0,View.MeasureSpec.UNSPECIFIED));v.layout(0,0,v.getMeasuredWidth(),v.getMeasuredHeight());return v.getMeasuredHeight();}

 @Test public void hiddenUntilThereIsAnAppToReturnTo(){
  ReturnControl c=control(activity());
  c.show(ReturnModel.initial(),null,true);
  assertEquals(View.GONE,c.getVisibility());
 }

 @Test public void readyIsNamedSpokenAndAFullTarget(){
  Activity a=activity();ReturnControl c=control(a);
  c.show(ready(),new ColorDrawable(0xff3366cc),true);
  assertEquals(View.VISIBLE,c.getVisibility());
  assertEquals("Messages",c.label.getText().toString());
  assertEquals("Return to Messages",c.getContentDescription().toString());
  assertTrue(c.isClickable()&&c.isEnabled()&&c.isFocusable());
  assertEquals(View.VISIBLE,c.icon.getVisibility());
  float density=a.getResources().getDisplayMetrics().density;
  assertTrue("48dp target",measure(c)>=Math.round(48*density));
  assertEquals(1f,c.getAlpha(),0f);
  assertTrue("text sits inside the pill, not on its edge",c.getPaddingLeft()>=Math.round(12*density)&&c.getPaddingRight()>=Math.round(12*density));
  render(c,"return-ready");
 }

 @Test public void noIconIsDrawnWhenAndroidHasNone(){
  ReturnControl c=control(activity());
  c.show(ready(),null,true);
  assertEquals(View.GONE,c.icon.getVisibility());
 }

 @Test public void pendingSaysItCanBeCancelledAndRunningWaits(){
  ReturnControl c=control(activity());
  ReturnModel.State pending=ready().tapped(100);
  c.show(pending,null,true);
  assertEquals("Returning to Messages",c.label.getText().toString());
  assertTrue(c.getContentDescription().toString().endsWith("Tap to cancel"));
  assertTrue(c.isClickable());
  ReturnModel.State running=pending.committed(100+ReturnModel.COMMIT_MS);
  c.show(running,null,true);
  assertFalse("a return already asked for waits",c.isClickable());
  render(c,"return-running");
 }

 @Test public void unavailableIsHonestAndInert(){
  ReturnControl c=control(activity());
  ReturnModel.State r=ready().tapped(100).committed(100+ReturnModel.COMMIT_MS);
  ReturnModel.State gone=r.answered(r.attempt,ReturnModel.Answer.UNAVAILABLE,700);
  c.show(gone,new ColorDrawable(0xff3366cc),true);
  assertEquals("Messages isn’t open now",c.label.getText().toString());
  assertFalse(c.isClickable());
  assertEquals(View.GONE,c.chevron.getVisibility());
  assertEquals(View.GONE,c.icon.getVisibility());
  render(c,"return-unavailable");
 }

 @Test public void reducedMotionShowsAndHidesAtOnce(){
  ReturnControl c=control(activity());
  c.show(ready(),null,true);
  assertEquals(View.VISIBLE,c.getVisibility());assertEquals(0f,c.getTranslationY(),0f);
  c.show(ReturnModel.initial(),null,true);
  assertEquals(View.GONE,c.getVisibility());
 }

 void render(View v,String name){
  int h=measure(v);Bitmap b=Bitmap.createBitmap(Math.max(1,v.getMeasuredWidth()),Math.max(1,h),Bitmap.Config.ARGB_8888);
  Canvas canvas=new Canvas(b);canvas.drawColor(0xff120a0d);v.draw(canvas);
  File out=new File("target/render");out.mkdirs();
  try(FileOutputStream f=new FileOutputStream(new File(out,name+".png"))){b.compress(Bitmap.CompressFormat.PNG,100,f);}catch(Exception e){throw new AssertionError(e);}
 }
}
