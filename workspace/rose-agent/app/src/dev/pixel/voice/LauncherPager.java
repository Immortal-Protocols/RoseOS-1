package dev.pixel.voice;

import android.content.Context;
import android.view.MotionEvent;
import android.view.VelocityTracker;
import android.view.View;
import android.view.ViewConfiguration;
import android.view.ViewGroup;
import android.view.accessibility.AccessibilityNodeInfo;
import android.widget.OverScroller;

/** Small native pager: horizontal drags page, vertical gestures stay with the child (HomePages.owner, settle).
 * Its background is drawn at the scroll position, so the shared Home wallpaper stays put while pages slide. */
final class LauncherPager extends ViewGroup {
 interface SwipePermission { boolean allow(MotionEvent down); }
 interface PageListener { void selected(int index); }
 final OverScroller scroller;
 final int slop;
 final float flingThreshold;
 SwipePermission permission;
 PageListener listener;
 VelocityTracker velocity;
 int selected, downPage, downScroll;
 float downX, downY;
 boolean eligible, dragging;

 LauncherPager(Context context) {
  super(context);
  scroller=new OverScroller(context);
  slop=ViewConfiguration.get(context).getScaledPagingTouchSlop();
  flingThreshold=650*getResources().getDisplayMetrics().density;
  setClipChildren(true);
  setFocusable(true);
  setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_YES);
 }
 int currentPage(){return selected;}
 void showPage(int requested,boolean animate){
  int next=Math.max(0,Math.min(getChildCount()-1,requested));
  selected=next;
  for(int i=0;i<getChildCount();i++)getChildAt(i).setImportantForAccessibility(
    i==next?IMPORTANT_FOR_ACCESSIBILITY_AUTO:IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS);
  int target=next*getWidth();
  scroller.abortAnimation();
  if(animate&&getWidth()>0){scroller.startScroll(getScrollX(),0,target-getScrollX(),0,240);postInvalidateOnAnimation();}
  else scrollTo(target,0);
  if(listener!=null)listener.selected(next);
 }
 @Override protected void onMeasure(int widthSpec,int heightSpec){
  int width=MeasureSpec.getSize(widthSpec),height=MeasureSpec.getSize(heightSpec);
  setMeasuredDimension(width,height);
  for(int i=0;i<getChildCount();i++)getChildAt(i).measure(
    MeasureSpec.makeMeasureSpec(width,MeasureSpec.EXACTLY),MeasureSpec.makeMeasureSpec(height,MeasureSpec.EXACTLY));
 }
 @Override protected void onLayout(boolean changed,int l,int t,int r,int b){
  for(int i=0;i<getChildCount();i++)getChildAt(i).layout(i*getWidth(),0,(i+1)*getWidth(),getHeight());
  if(changed&&!dragging){scroller.abortAnimation();scrollTo(selected*getWidth(),0);}
 }
 @Override public android.view.WindowInsets dispatchApplyWindowInsets(android.view.WindowInsets insets){
  // A child's consumed insets must not starve the other pages.
  for(int i=0;i<getChildCount();i++)getChildAt(i).dispatchApplyWindowInsets(insets);
  return insets;
 }
 private void begin(MotionEvent event){
  recycleVelocity();velocity=VelocityTracker.obtain();velocity.addMovement(event);
  downX=event.getX();downY=event.getY();downPage=selected;downScroll=getScrollX();dragging=false;
  eligible=getChildCount()>1&&(permission==null||permission.allow(event));
 }
 @Override public boolean onInterceptTouchEvent(MotionEvent event){
  switch(event.getActionMasked()){
   case MotionEvent.ACTION_DOWN:begin(event);return false;
   case MotionEvent.ACTION_MOVE:
    if(!eligible||event.getPointerCount()!=1)return false;
    int owner=HomePages.owner(event.getX()-downX,event.getY()-downY,slop);
    if(owner==HomePages.CHILD){eligible=false;return false;}
    if(owner==HomePages.PAGER){
     dragging=true;scroller.abortAnimation();getParent().requestDisallowInterceptTouchEvent(true);return true;
    }
    return false;
   case MotionEvent.ACTION_UP:case MotionEvent.ACTION_CANCEL:eligible=false;recycleVelocity();return false;
   default:return dragging;
  }
 }
 @Override public boolean onTouchEvent(MotionEvent event){
  if(event.getActionMasked()==MotionEvent.ACTION_DOWN){begin(event);return eligible;}
  if(!eligible)return false;
  if(velocity!=null)velocity.addMovement(event);
  if(event.getPointerCount()!=1){finishGesture(selected);return true;}
  if(event.getActionMasked()==MotionEvent.ACTION_MOVE){
   dragging=true;
   int x=downScroll+Math.round(downX-event.getX());
   scrollTo(Math.max(0,Math.min((getChildCount()-1)*getWidth(),x)),0);return true;
  }
  if(event.getActionMasked()==MotionEvent.ACTION_UP){
   float dx=event.getX()-downX,vx=0;
   if(velocity!=null){velocity.computeCurrentVelocity(1000);vx=velocity.getXVelocity();}
   finishGesture(HomePages.settle(downPage,dx,vx,getWidth(),flingThreshold,Math.round((float)getScrollX()/Math.max(1,getWidth())),getChildCount()));return true;
  }
  if(event.getActionMasked()==MotionEvent.ACTION_CANCEL){finishGesture(selected);return true;}
  return true;
 }
 private void finishGesture(int target){eligible=false;dragging=false;recycleVelocity();showPage(target,true);}
 private void recycleVelocity(){if(velocity!=null){velocity.recycle();velocity=null;}}
 @Override public void computeScroll(){if(scroller.computeScrollOffset()){scrollTo(scroller.getCurrX(),0);postInvalidateOnAnimation();}}
 @Override public void onInitializeAccessibilityNodeInfo(AccessibilityNodeInfo info){
  super.onInitializeAccessibilityNodeInfo(info);info.setScrollable(getChildCount()>1);
  if(selected>0)info.addAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_SCROLL_BACKWARD);
  if(selected<getChildCount()-1)info.addAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_SCROLL_FORWARD);
 }
 @Override public boolean performAccessibilityAction(int action,android.os.Bundle arguments){
  if(action==AccessibilityNodeInfo.ACTION_SCROLL_FORWARD&&selected<getChildCount()-1){showPage(selected+1,true);return true;}
  if(action==AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD&&selected>0){showPage(selected-1,true);return true;}
  return super.performAccessibilityAction(action,arguments);
 }
 @Override protected void onDetachedFromWindow(){recycleVelocity();scroller.abortAnimation();super.onDetachedFromWindow();}
}
