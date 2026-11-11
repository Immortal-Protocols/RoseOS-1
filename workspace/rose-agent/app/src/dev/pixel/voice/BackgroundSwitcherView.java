package dev.pixel.voice;

import android.content.Context;
import android.view.View;

/** Small motion on the existing switcher glyph, never on its control or hit box.
 * Palette and artwork remain the existing theme-aware drawable's responsibility. */
final class BackgroundSwitcherView extends View {
 private final float density;
 private final BackgroundActivity activity=new BackgroundActivity();
 private boolean motion,released,scheduled;
 private final Runnable frame=new Runnable(){public void run(){
  scheduled=false;
  if(released||!motion||!isShown()){setTranslationY(0);return;}
  long now=WorkspaceOverviewFeed.activityClock();
  WorkspaceOverviewFeed.ActivitySnapshot state=WorkspaceOverviewFeed.activitySnapshot(now);
  setTranslationY(density*activity.frame(state.running,state.finished,state.reliable,now,true));
  schedule(activity.phase==BackgroundActivity.Phase.STILL?250:50);
 }};
 BackgroundSwitcherView(Context context,float density){
  super(context);this.density=density;
  setBackground(new OxbloodEdge.SwitcherGlyph(density));
  setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
 }
 void show(boolean animate){
  motion=animate;
  if(!motion){removeCallbacks(frame);scheduled=false;setTranslationY(0);}
  else schedule(0);
 }
 private void schedule(long delay){
  if(!released&&motion&&!scheduled){scheduled=true;postDelayed(frame,delay);}
 }
 void release(){released=true;removeCallbacks(frame);scheduled=false;setTranslationY(0);}
 @Override protected void onDetachedFromWindow(){removeCallbacks(frame);scheduled=false;setTranslationY(0);super.onDetachedFromWindow();}
}
