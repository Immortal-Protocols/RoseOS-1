package dev.pixel.voice;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.*;
import android.os.SystemClock;
import android.view.View;

/** A decorative projection, not a compass: east is left, west right, altitude is vertical.
 * All astronomy is supplied by WeatherAstronomy; drawing never performs network work. */
final class WeatherSkyView extends View {
 private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
 private final Path path=new Path();
 private WeatherAstronomy.State sky;
 private int code=-1,cloud=-1;
 private double wind=0;
 private float[][] constellation;
 /** Catalogue: legacy Sky Map tools/data/constellations.ascii (Apache-2.0), Cassiopeia W, J2000 degrees.
  * This is a recognisable illustrated asterism, selected only when every vertex is >= 12 degrees up.
  * Tangent-plane shape/tilt is physical; translation below is deliberately accelerated art, not tracking. */
 private static final double[][] CASSIOPEIA={{28.600006,63.6833},{21.454163,60.2333},{14.175003,60.7167},{10.129166,56.5333},{2.2916718,59.15}};
 void setObserver(long utcMillis,double latitude,double longitude){
  constellation=null;
  if(!Double.isFinite(latitude)||!Double.isFinite(longitude)||Math.abs(latitude)>90||Math.abs(longitude)>180)return;
  double[][] directions=new double[5][];double cx=0,cy=0,cz=0;
  for(int i=0;i<5;i++){
   double[] h=starHorizon(utcMillis,latitude,longitude,CASSIOPEIA[i][0],CASSIOPEIA[i][1]);
   if(h[0]<12)return;
   double alt=Math.toRadians(h[0]),az=Math.toRadians(h[1]);
   directions[i]=new double[]{Math.cos(alt)*Math.sin(az),Math.cos(alt)*Math.cos(az),Math.sin(alt)};
   cx+=directions[i][0];cy+=directions[i][1];cz+=directions[i][2];
  }
  double az=Math.atan2(cx,cy),alt=Math.atan2(cz,Math.hypot(cx,cy)),norm=Math.sqrt(cx*cx+cy*cy+cz*cz);
  cx/=norm;cy/=norm;cz/=norm;float[][] points=new float[5][2];float spanX=0,spanY=0;
  for(int i=0;i<5;i++){
   double[] v=directions[i];double front=v[0]*cx+v[1]*cy+v[2]*cz;
   points[i][0]=(float)((v[0]*Math.cos(az)-v[1]*Math.sin(az))/front);
   points[i][1]=(float)((v[0]*Math.sin(alt)*Math.sin(az)+v[1]*Math.sin(alt)*Math.cos(az)-v[2]*Math.cos(alt))/front);
   spanX=Math.max(spanX,Math.abs(points[i][0]));spanY=Math.max(spanY,Math.abs(points[i][1]));
  }
  float scale=Math.min(44/Math.max(.001f,spanX),29/Math.max(.001f,spanY));
  for(float[] point:points){point[0]*=scale;point[1]*=scale;}constellation=points;invalidate();
 }
 /** IAU 1976 precession (Meeus ch.21) and mean sidereal time. Geometric altitude, no refraction. */
 static double[] starHorizon(long utc,double lat,double lon,double ra,double dec){
  double jd=utc/86400000.0+2440587.5,t=(jd-2451545)/36525,r=Math.PI/180;
  double zeta=(2306.2181*t+.30188*t*t+.017998*t*t*t)/3600*r;
  double z=(2306.2181*t+1.09468*t*t+.018203*t*t*t)/3600*r;
  double theta=(2004.3109*t-.42665*t*t-.041833*t*t*t)/3600*r;
  double a=Math.cos(dec*r)*Math.sin(ra*r+zeta),b=Math.cos(theta)*Math.cos(dec*r)*Math.cos(ra*r+zeta)-Math.sin(theta)*Math.sin(dec*r);
  double d=Math.asin(Math.sin(theta)*Math.cos(dec*r)*Math.cos(ra*r+zeta)+Math.cos(theta)*Math.sin(dec*r));
  double h=(280.46061837+360.98564736629*(jd-2451545)+.000387933*t*t-t*t*t/38710000+lon)*r-(Math.atan2(a,b)+z);
  double altitude=Math.asin(Math.sin(lat*r)*Math.sin(d)+Math.cos(lat*r)*Math.cos(d)*Math.cos(h))/r;
  double azimuth=(Math.atan2(Math.sin(h),Math.cos(h)*Math.sin(lat*r)-Math.tan(d)*Math.cos(lat*r))/r+540)%360;
  return new double[]{altitude,azimuth};
 }
 private boolean requested,playing;
 private long origin=SystemClock.uptimeMillis();
 private float frozenTime=0;
 private final Runnable frame=new Runnable(){public void run(){if(!eligible()){updatePlaying();return;}invalidate();postDelayed(this,50);}};
 WeatherSkyView(Context c){super(c);setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);}
 void setState(WeatherAstronomy.State next,int weatherCode,int cover,double windMps){sky=next;code=weatherCode;cloud=cover;wind=Double.isFinite(windMps)?Math.max(0,Math.min(35,windMps)):0;updatePlaying();invalidate();}
 void setPlaying(boolean active){requested=active;updatePlaying();}
 private boolean eligible(){return requested&&sky!=null&&code>=0&&isAttachedToWindow()&&isShown()&&getWindowVisibility()==VISIBLE&&ValueAnimator.areAnimatorsEnabled();}
 private void updatePlaying(){boolean next=eligible();if(playing==next)return;if(playing)frozenTime=(SystemClock.uptimeMillis()-origin)/1000f;playing=next;removeCallbacks(frame);if(next){origin=SystemClock.uptimeMillis()-(long)(frozenTime*1000);post(frame);}invalidate();}
 @Override protected void onAttachedToWindow(){super.onAttachedToWindow();updatePlaying();}
 @Override protected void onDetachedFromWindow(){playing=false;removeCallbacks(frame);super.onDetachedFromWindow();}
 @Override protected void onVisibilityChanged(View v,int value){super.onVisibilityChanged(v,value);if(frame!=null)updatePlaying();}
 @Override protected void onWindowVisibilityChanged(int value){super.onWindowVisibilityChanged(value);if(frame!=null)updatePlaying();}
 private static float clamp(float x,float a,float b){return Math.max(a,Math.min(b,x));}
 private void fill(int color){p.setShader(null);p.setColor(color);p.setStyle(Paint.Style.FILL);}
 private void line(int color,float width){fill(color);p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(width);p.setStrokeCap(Paint.Cap.ROUND);p.setStrokeJoin(Paint.Join.ROUND);}
 private static int mix(int a,int b,float f){f=clamp(f,0,1);return Color.rgb((int)(Color.red(a)+(Color.red(b)-Color.red(a))*f),(int)(Color.green(a)+(Color.green(b)-Color.green(a))*f),(int)(Color.blue(a)+(Color.blue(b)-Color.blue(a))*f));}
 @Override protected void onDraw(Canvas c){
  super.onDraw(c);if(getWidth()==0||getHeight()==0)return;
  c.save();c.scale(getWidth()/190f,getHeight()/190f);
  path.reset();path.addRoundRect(0,0,190,190,24,24,Path.Direction.CW);c.clipPath(path);
  float alt=sky==null?30:(float)sky.sunAltitude;
  float daylight=clamp((alt+12)/18,0,1),warm=clamp(1-Math.abs(alt+1)/9,0,1);
  int top=mix(0xff202d53,0xff91c8ec,daylight),bottom=mix(0xff59638c,0xffd3edf3,daylight);
  bottom=mix(bottom,0xfff7ad88,warm);top=mix(top,0xffbd9cba,warm*.55f);
  float overcast=clamp((coverage()-60)/40f,0,1)*.65f;top=mix(top,mix(0xff343e58,0xff9daebe,daylight),overcast);bottom=mix(bottom,mix(0xff596078,0xffc9d4da,daylight),overcast);
  fill(top);p.setShader(new LinearGradient(0,0,0,190,top,bottom,Shader.TileMode.CLAMP));c.drawRect(0,0,190,190,p);p.setShader(null);
  float t=playing?(SystemClock.uptimeMillis()-origin)/1000f:frozenTime;
  if(sky!=null&&code>=0){
   if(alt<0&&code<=2&&coverage()<70){
    for(int i=0;i<24;i++){
     float x=10+(i*47)%173,y=24+(i*29)%122,a=clamp(-alt/12,0,1),pulse=(float)((1+Math.sin(t*1.15+i*1.7))/2);
     fill(Color.argb((int)(a*(85+170*pulse)),255,247,229));float r=i%4==0?1.1f+pulse:0.7f+pulse*.45f;c.drawCircle(x,y,r,p);
     if(i%4==0){line(Color.argb((int)(a*170*pulse),255,247,229),.8f);c.drawLine(x-3*pulse,y,x+3*pulse,y,p);c.drawLine(x,y-3*pulse,x,y+3*pulse,p);}
    }
   }
   if(alt<-6&&code<=2&&coverage()<35&&constellation!=null)drawConstellation(c,t);
   c.save();c.clipRect(0,0,190,166);
   if(sky.sunAltitude>-.833&&coverage()<90)sun(c,projectX(sky.sunAzimuth),166-(float)(sky.sunAltitude+.566)/90*124,t);
   // Conservative geometric-centre rule: no below-horizon moon, even if refracted upper limb is visible.
   if(sky.moonAltitude>0&&sky.fraction>.004&&coverage()<90)moon(c,projectX(sky.moonAzimuth),166-(float)sky.moonAltitude/90*124,13,sky);
   c.restore();
   int cover=coverage();
   if(cover>0){int count=cover>=75?4:cover>=35?2:1;
    for(int i=0;i<count;i++){float speed=4.5f+(float)wind*.75f;float x=((110+i*79+t*speed*(i%2==0?1:.7f))%310)-55;cloud(c,x,56+(i%3)*25,70+(i%3)*13,code>=51||cover>=75);}
   }
   if(code==45||code==48){for(int i=0;i<6;i++){float shift=(float)Math.sin(t*.65+i)*24;line(0x99eaf3f8,7);c.drawLine(-20+shift,65+i*19,155+shift,65+i*19,p);}}

  }
  fill(mix(0xff3c5870,0xff91b6a8,daylight));path.reset();path.moveTo(0,169);path.cubicTo(35,155,67,176,103,165);path.cubicTo(135,153,165,153,190,169);path.lineTo(190,190);path.lineTo(0,190);path.close();c.drawPath(path,p);
  fill(mix(0xff293e56,0xff719c90,daylight));path.reset();path.moveTo(0,182);path.cubicTo(48,167,100,190,150,179);path.quadTo(176,170,190,179);path.lineTo(190,190);path.lineTo(0,190);path.close();c.drawPath(path,p);
  // Tiny foreground blades show breeze without inventing cloud cover.
  for(int i=0;i<5;i++){float x=142+i*8,sway=(float)Math.sin(t*1.15+i)*Math.min(5,(float)wind*.6f);line(mix(0xff6a8090,0xffe0e6b5,daylight),1.4f);path.reset();path.moveTo(x,187);path.quadTo(x+1+sway,179,x-3+sway,174-i%2*3);c.drawPath(path,p);}
  if(sky!=null&&code>=51)precipitation(c,t);
  // Gentle whole-scene shading behind the floating labels, never an opaque text panel.
  fill(0);p.setShader(new LinearGradient(0,0,0,190,new int[]{0x2429385d,0x0029385d,0x0029385d,0x4829385d},new float[]{0,.23f,.55f,1},Shader.TileMode.CLAMP));c.drawRect(0,0,190,190,p);p.setShader(null);
  if(sky==null||code<0){fill(0x553d5874);c.drawCircle(95,85,2,p);c.drawCircle(86,85,2,p);c.drawCircle(104,85,2,p);}
  c.restore();
 }
 private void drawConstellation(Canvas c,float t){
  // A 28-second sweep makes clear-night motion legible. The Sun and Moon keep their computed positions.
  float dx=95+28*(float)Math.sin(t*.22),dy=78+9*(float)Math.sin(t*.18);
  line(0x70fff7e5,1.0f);path.reset();
  for(int i=0;i<constellation.length;i++){float x=dx+constellation[i][0],y=dy+constellation[i][1];if(i==0)path.moveTo(x,y);else path.lineTo(x,y);}c.drawPath(path,p);
  for(int i=0;i<constellation.length;i++){
   float x=dx+constellation[i][0],y=dy+constellation[i][1],pulse=(1+(float)Math.sin(t*1.25+i*1.1f))/2;
   fill(0x22ffd66b);c.drawCircle(x,y,5+pulse*2,p);fill(0xffffedbd);c.drawCircle(x,y,1.8f+pulse*1.1f,p);
   line(0xdffff7e5,1);c.drawLine(x-3-pulse*2,y,x+3+pulse*2,y,p);c.drawLine(x,y-3-pulse*2,x,y+3+pulse*2,p);
  }
 }
 private int coverage(){if(code==0)return 0;if(cloud>=0)return Math.max(code>=51?55:0,Math.min(100,cloud));return code==1?20:code==2?50:code==3||code>=51?100:0;}
 private float projectX(double az){return 95-65*(float)Math.sin(Math.toRadians(az));}
 private void sun(Canvas c,float x,float y,float t){
  fill(0x24ffd66b);c.drawCircle(x,y,29+(float)Math.sin(t*1.2)*3,p);line(0xfff7c45c,2);
  for(int i=0;i<8;i++){double a=i*Math.PI/4+t*.14;float inner=19,outer=25+(float)Math.sin(t*1.2+i)*2.8f;c.drawLine(x+(float)Math.cos(a)*inner,y+(float)Math.sin(a)*inner,x+(float)Math.cos(a)*outer,y+(float)Math.sin(a)*outer,p);}
  fill(0xffcba05a);c.drawCircle(x,y+1.7f,14.5f,p);fill(0xffffd66b);c.drawCircle(x,y,14.5f,p);line(0xff7e694f,1.4f);c.drawCircle(x,y,14.5f,p);
 }
 private void moon(Canvas c,float x,float y,float r,WeatherAstronomy.State s){
  c.save();c.translate(x,y);c.rotate((float)s.limbRotation);
  fill(0xff64718d);c.drawCircle(0,0,r,p);
  path.reset();for(int i=0;i<=48;i++){double a=-Math.PI/2+Math.PI*i/48;float xx=(float)Math.cos(a)*r,yy=(float)Math.sin(a)*r;if(i==0)path.moveTo(xx,yy);else path.lineTo(xx,yy);}
  for(int i=48;i>=0;i--){double a=-Math.PI/2+Math.PI*i/48;path.lineTo((float)((1-2*s.fraction)*Math.cos(a)*r),(float)Math.sin(a)*r);}path.close();fill(0xffffebaf);c.drawPath(path,p);line(0xffb8b3a1,.7f);c.drawCircle(0,0,r,p);c.restore();
 }
 private void cloud(Canvas c,float x,float y,float width,boolean heavy){
  c.save();c.translate(x,y);float k=width/70;c.scale(k,k);
  path.reset();path.moveTo(-33,12);path.cubicTo(-45,4,-35,-8,-23,-5);path.cubicTo(-24,-23,4,-28,10,-10);path.cubicTo(28,-18,39,-5,33,6);path.cubicTo(46,18,13,19,2,17);path.lineTo(-25,17);path.quadTo(-33,17,-33,12);path.close();
  c.save();c.translate(0,2);fill(0x25425275);c.drawPath(path,p);c.restore();fill(heavy?0xffabbfd1:0xfffff7e5);c.drawPath(path,p);line(heavy?0xff657d99:0xff6a829e,1.5f);c.drawPath(path,p);c.restore();
 }
 private void precipitation(Canvas c,float t){
  boolean snow=code>=71&&code<=77||code==85||code==86;int count=snow?26:code<60?20:38;
  for(int i=0;i<count;i++){float progress=(t*(snow?.22f:.70f)+i*.137f)%1,xx=4+(i*29)%188-(float)wind*progress*.4f,yy=35+progress*153;
   if(snow){xx+=(float)Math.sin(t*1.1+i)*9;fill(0xfff6faf4);c.drawCircle(xx,yy,2.0f,p);}
   else{line(0xff537eac,1.6f);c.drawLine(xx,yy,xx-2-(float)wind*.15f,yy+10,p);}
  }
  if(code>=95){ // Slow, localized pulse; never flash the full card.
   float pulse=(float)Math.pow((1+Math.sin(t*.9))/2,5);fill(Color.argb((int)(110+145*pulse),255,214,107));path.reset();path.moveTo(139,73);path.lineTo(125,99);path.lineTo(136,98);path.lineTo(129,120);path.lineTo(155,88);path.lineTo(143,90);path.lineTo(150,73);path.close();c.drawPath(path,p);
  }
 }
}
