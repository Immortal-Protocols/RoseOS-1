package dev.pixel.voice;

import android.content.Context;
import android.graphics.*;
import android.os.SystemClock;
import android.view.View;

/** The Rose presence (conversation and Home tray): two metal hemispheres and a light surround drawn with Canvas
 * from RosePresenceModel, in the HiFi renderer's rendered tones and its camera's elliptical rims. Canvas needs no GL
 * context, so there is no context loss to recover from; the static presentation (reduced motion, or the activity not visible) is the same drawing without frame updates. Frames
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
  float r=Math.min(getWidth(),getHeight())/(2f*RosePresenceModel.VIEW_RADII);
  float lift=model.gap*r,flat=RosePresenceModel.TILT*r;
  // No contact shadow: the reference renderer draws the shells alone on a transparent canvas.
  float lower=cy+lift*.5f,upper=cy-lift*.5f;
  // Lower shell: its bottom half and, seen from above, the whole rim with the dark cut face inside it.
  paint.setShader(new LinearGradient(cx,lower,cx,lower+r,model.lowerTones(),RosePresenceModel.LOWER_STOPS,Shader.TileMode.CLAMP));
  box.set(cx-r,lower-r,cx+r,lower+r);c.drawArc(box,0f,180f,true,paint);
  box.set(cx-r,lower-flat,cx+r,lower+flat);c.drawOval(box,paint);
  paint.setShader(new RadialGradient(cx-r*.25f,lower+r*.45f,r*.32f,0x1fffffff,0x00ffffff,Shader.TileMode.CLAMP));
  box.set(cx-r,lower-r,cx+r,lower+r);c.drawArc(box,0f,180f,true,paint);
  paint.setShader(null);paint.setColor(model.faceColor());
  box.set(cx-r,lower-flat,cx+r,lower+flat);box.inset(r*.04f,flat*.04f);c.drawOval(box,paint);
  // Upper shell: its top half and the near half of its rim, which hides the face while joined.
  half.reset();box.set(cx-r,upper-r,cx+r,upper+r);half.arcTo(box,180f,180f,false);
  box.set(cx-r,upper-flat,cx+r,upper+flat);half.arcTo(box,0f,180f,false);half.close();
  paint.setShader(new LinearGradient(cx,upper-r,cx,upper+flat,model.upperTones(),RosePresenceModel.UPPER_STOPS,Shader.TileMode.CLAMP));c.drawPath(half,paint);
  c.save();c.clipPath(half);
  for(float[] h:RosePresenceModel.HIGHLIGHTS){
   paint.setShader(new RadialGradient(cx+h[0]*r,upper+h[1]*r,h[2]*r,0xb3ffffff,0x00ffffff,Shader.TileMode.CLAMP));
   c.drawCircle(cx+h[0]*r,upper+h[1]*r,h[2]*r,paint);}
  c.restore();paint.setShader(null);
  // The seam the renderer darkens by 26% where the shells meet.
  ring.setStrokeWidth(Math.max(1f,r*.012f));ring.setColor(0x42000000);c.drawArc(box,0f,180f,false,ring);
  // Light surround, never changing the sphere's scale.
  float a=model.ringAlpha();
  if(a>0f){float rr=model.ringRadius(running?time:0f)*r;
   ring.setStrokeWidth(r*.05f);ring.setColor(withAlpha(RosePresenceModel.LIGHT,a*.35f));c.drawCircle(cx,cy,rr,ring);
   ring.setStrokeWidth(r*.018f);ring.setColor(withAlpha(RosePresenceModel.LIGHT,a*.95f));c.drawCircle(cx,cy,rr,ring);}
 }
 private static int withAlpha(int color,float a){return (Math.round(Math.max(0f,Math.min(1f,a))*255)<<24)|(color&0xffffff);}
}
