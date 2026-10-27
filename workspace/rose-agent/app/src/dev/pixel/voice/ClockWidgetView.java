package dev.pixel.voice;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.widget.LinearLayout;
import android.widget.TextView;
import org.json.JSONObject;
import java.util.Calendar;
import java.util.Date;

/** A local clock and cached area. It never sends context or starts a model. */
final class ClockWidgetView extends LinearLayout {
 private final Handler handler=new Handler(Looper.getMainLooper());
 private final TextView time,date,area;
 private final Face face;
 private boolean requestedActive,running;
 private long nextLocationUpdate;
 private final Runnable tick=new Runnable(){@Override public void run(){
  if(!running)return;
  Date now=new Date();
  updateClock(now);
  face.invalidate();
  if(SystemClock.elapsedRealtime()>=nextLocationUpdate)updateArea();
  handler.postDelayed(this,1000-System.currentTimeMillis()%1000);
 }};

 ClockWidgetView(Context context,Typeface font,Runnable openClock){
  super(context);setOrientation(VERTICAL);setGravity(Gravity.CENTER_HORIZONTAL);
  setPadding(dp(12),dp(12),dp(12),dp(14));
  setBackground(PlayfulTheme.surface(color(0xfffff7e5),color(0xff789ab5),getResources().getDisplayMetrics().density,24));
  setClickable(true);setFocusable(true);setContentDescription("Open Clock");
  setOnClickListener(v->openClock.run());
  face=new Face(context);addView(face,new LinearLayout.LayoutParams(-1,0,1));
  // At larger accessibility sizes, give the text the space instead of shrinking it
  // to accommodate a decorative second representation of the same time.
  if(getResources().getConfiguration().fontScale>=1.5f)face.setVisibility(GONE);
  time=label("",30,font);time.setGravity(Gravity.CENTER);time.setSingleLine(true);
  time.setAutoSizeTextTypeUniformWithConfiguration(20,30,1,TypedValue.COMPLEX_UNIT_SP);
  time.setAccessibilityLiveRegion(ACCESSIBILITY_LIVE_REGION_NONE);
  LinearLayout.LayoutParams timeParams=new LinearLayout.LayoutParams(-1,dp(44));timeParams.topMargin=dp(4);addView(time,timeParams);
  date=label("",13,font);date.setGravity(Gravity.CENTER);date.setSingleLine(true);
  date.setAutoSizeTextTypeUniformWithConfiguration(11,13,1,TypedValue.COMPLEX_UNIT_SP);
  date.setAccessibilityLiveRegion(ACCESSIBILITY_LIVE_REGION_NONE);
  LinearLayout.LayoutParams dateParams=new LinearLayout.LayoutParams(-1,-2);dateParams.topMargin=dp(2);addView(date,dateParams);
  area=label("Finding area…",14,font);area.setGravity(Gravity.CENTER);area.setMaxLines(2);
  area.setEllipsize(android.text.TextUtils.TruncateAt.END);
  LinearLayout.LayoutParams areaParams=new LinearLayout.LayoutParams(-1,-2);areaParams.topMargin=dp(3);addView(area,areaParams);
  if(face.getVisibility()==GONE)setGravity(Gravity.CENTER);
  updateClock(new Date());
 }
 private TextView label(String value,int size,Typeface font){TextView v=new TextView(getContext());v.setText(value);v.setTextSize(size);v.setTypeface(font);v.setTextColor(color(0xff29385d));v.setIncludeFontPadding(false);return v;}
 private int dp(float n){return Math.round(n*getResources().getDisplayMetrics().density);}
 private static int color(int value){return PixelAppearance.color(value);}
 private static void setText(TextView view,String value){if(!value.contentEquals(view.getText()))view.setText(value);}
 private void updateClock(Date now){
  setText(time,android.text.format.DateFormat.getTimeFormat(getContext()).format(now));
  setText(date,android.text.format.DateFormat.getMediumDateFormat(getContext()).format(now));
  updateDescription();
 }
 private void updateDescription(){
  String description="Open Clock. "+time.getText()+". "+date.getText()+". "+area.getText();
  if(!description.contentEquals(getContentDescription()))setContentDescription(description);
 }
 void setActive(boolean value){requestedActive=value;updateRunning();}
 private void updateRunning(){
  boolean next=requestedActive&&isAttachedToWindow();if(next==running)return;
  running=next;handler.removeCallbacks(tick);PhoneEnvironment.get(getContext()).setWidgetActive(next);
  if(next){nextLocationUpdate=0;handler.post(tick);}
 }
 @Override protected void onAttachedToWindow(){super.onAttachedToWindow();updateRunning();}
 @Override protected void onDetachedFromWindow(){requestedActive=false;updateRunning();handler.removeCallbacks(tick);super.onDetachedFromWindow();}
 private void updateArea(){
  JSONObject location=PhoneEnvironment.get(getContext()).snapshot().optJSONObject("location");
  String status=location==null?"unavailable":location.optString("status","unavailable");
  String city=location==null?"":location.optString("city",""),region=location==null?"":location.optString("region","");
  String place=city.isEmpty()?region:city;
  if(place.isEmpty()&&location!=null)place=location.optString("country","");
  String display;
  if(status.equals("permission_required"))display="Location off";
  else if(status.equals("disabled"))display="Location off";
  else if(!place.isEmpty())display=(status.equals("stale")?"Last known: ":"")+place;
  else display=status.equals("unavailable")?"Finding area…":"Area unavailable";
  setText(area,display);
  updateDescription();
  nextLocationUpdate=SystemClock.elapsedRealtime()+(place.isEmpty()?5000:30000);
 }
 private final class Face extends View {
  private final Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);
  Face(Context c){super(c);setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);}
  @Override protected void onDraw(Canvas canvas){
   super.onDraw(canvas);float x=getWidth()/2f,y=getHeight()/2f,r=Math.min(getWidth(),getHeight())/2f-dp(5);
   if(r<=dp(12))return;
   paint.setStyle(Paint.Style.FILL);paint.setColor(color(0xffb9dff3));canvas.drawCircle(x,y+dp(2),r+dp(3),paint);
   paint.setColor(color(0xff29385d));canvas.drawCircle(x,y,r,paint);
   paint.setColor(color(0xfffff7e5));canvas.drawCircle(x,y,r-dp(3),paint);
   paint.setStrokeCap(Paint.Cap.ROUND);paint.setStrokeWidth(dp(2));paint.setColor(color(0xff29385d));
   for(int i=0;i<12;i++){double a=Math.toRadians(i*30-90);float outer=r-dp(8),inner=outer-dp(i%3==0?4:1);canvas.drawLine(x+(float)Math.cos(a)*inner,y+(float)Math.sin(a)*inner,x+(float)Math.cos(a)*outer,y+(float)Math.sin(a)*outer,paint);}
   Calendar now=Calendar.getInstance();float seconds=now.get(Calendar.SECOND),minutes=now.get(Calendar.MINUTE)+seconds/60f,hours=now.get(Calendar.HOUR)+minutes/60f;
   hand(canvas,x,y,r*.47f,hours*30,dp(4),color(0xff29385d));
   hand(canvas,x,y,r*.70f,minutes*6,dp(3),color(0xff29385d));
   hand(canvas,x,y,r*.76f,seconds*6,dp(1.2f),color(0xfff58b78));
   paint.setStyle(Paint.Style.FILL);paint.setColor(0xfff0bc39);canvas.drawCircle(x,y,dp(4),paint);
  }
  private void hand(Canvas c,float x,float y,float length,float degrees,float width,int ink){double a=Math.toRadians(degrees-90);paint.setColor(ink);paint.setStrokeWidth(width);c.drawLine(x,y,x+(float)Math.cos(a)*length,y+(float)Math.sin(a)*length,paint);}
 }
}
