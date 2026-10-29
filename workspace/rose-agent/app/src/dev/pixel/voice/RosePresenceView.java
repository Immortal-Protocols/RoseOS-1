package dev.pixel.voice;

import android.content.Context;
import android.graphics.*;
import android.os.SystemClock;
import android.view.View;

/** The Rose presence on the conversation screen: two metal hemispheres and a light surround drawn with Canvas
 * from RosePresenceModel. Canvas needs no GL context, so there is no context loss to recover from; the static
 * presentation (reduced motion, or the activity not visible) is the same drawing without frame updates. Frames
 * run only while attached, visible and still changing. Visual only: it sends nothing and never creates state. */
final class RosePresenceView extends View {
 final RosePresenceModel model=new RosePresenceModel();
 private final Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG),ring=new Paint(Paint.ANTI_ALIAS_FLAG);
 private final RectF box=new RectF();private final Path half=new Path();
 private long last;private float time;private boolean running;
 RosePresenceView(Context c){super(c);setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_YES);ring.setStyle(Paint.Style.STROKE);}

 /** Shows `v`; called from the activity's existing refresh with real VoiceService observations. */
 void show(RoseVisualState v){
  model.set(v);String d=RosePresenceModel.describe(v);if(!d.contentEquals(getContentDescription()==null?"":getContentDescription()))setContentDescription(d);
  if(v.animate&&isAttachedToWindow()){if(!running){running=true;last=0;postInvalidateOnAnimation();}}else{running=false;invalidate();}
 }
 @Override protected void onDetachedFromWindow(){running=false;super.onDetachedFromWindow();}
 @Override protected void onWindowVisibilityChanged(int visibility){super.onWindowVisibilityChanged(visibility);if(visibility!=VISIBLE)running=false;}

 @Override protected void onDraw(Canvas c){
  if(running){long now=SystemClock.uptimeMillis();float dt=last==0?0f:(now-last)/1000f;last=now;time+=dt;
   if(model.step(dt))postInvalidateOnAnimation();else running=false;}
  float cx=getWidth()/2f,cy=getHeight()/2f;
  float r=Math.min(getWidth(),getHeight())/(2f*(RosePresenceModel.RING_BROAD+RosePresenceModel.RING_ENERGY+.12f));
  float lift=model.gap*r;int base=model.metal();
  // Soft contact shadow under the lower hemisphere.
  paint.setShader(new RadialGradient(cx,cy+r*1.02f,r*1.1f,0x33283e39,0x00283e39,Shader.TileMode.CLAMP));
  box.set(cx-r*1.1f,cy+r*.86f,cx+r*1.1f,cy+r*1.18f);c.drawOval(box,paint);
  hemisphere(c,cx,cy+lift*.5f,r,base,false);
  hemisphere(c,cx,cy-lift*.5f,r,base,true);
  // Light surround, never changing the sphere's scale.
  float a=model.ringAlpha();
  if(a>0f){float rr=model.ringRadius(running?time:0f)*r;
   ring.setStrokeWidth(r*.05f);ring.setColor(withAlpha(RosePresenceModel.LIGHT,a*.35f));c.drawCircle(cx,cy,rr,ring);
   ring.setStrokeWidth(r*.018f);ring.setColor(withAlpha(RosePresenceModel.LIGHT,a*.95f));c.drawCircle(cx,cy,rr,ring);}
 }
 /** One half-sphere with a metallic vertical sheen; the lower one shows its flat cut face when split. */
 private void hemisphere(Canvas c,float cx,float cy,float r,int base,boolean upper){
  box.set(cx-r,cy-r,cx+r,cy+r);half.reset();half.arcTo(box,upper?180f:0f,180f,false);half.close();
  int light=RosePresenceModel.mix(base,0xffffffff,.55f),dark=RosePresenceModel.mix(base,0xff1a2a36,.45f);
  paint.setShader(new LinearGradient(cx-r*.6f,cy-r,cx+r*.5f,cy+r,new int[]{light,base,dark,base},new float[]{0f,.38f,.72f,1f},Shader.TileMode.CLAMP));
  c.drawPath(half,paint);
  paint.setShader(new RadialGradient(cx-r*.38f,cy-r*(upper?.45f:-.25f),r*.42f,0x88ffffff,0x00ffffff,Shader.TileMode.CLAMP));c.drawPath(half,paint);
  if(!upper&&model.gap>RosePresenceModel.GAP_JOINED*1.5f){ // the camera sits above: only the lower half's cut face showspaint.setShader(null);paint.setColor(RosePresenceModel.mix(base,0xff1a2a36,.7f));
   box.set(cx-r,cy-r*.12f,cx+r,cy+r*.12f);c.drawOval(box,paint);}
  paint.setShader(null);
 }
 private static int withAlpha(int color,float a){return (Math.round(Math.max(0f,Math.min(1f,a))*255)<<24)|(color&0xffffff);}
}
