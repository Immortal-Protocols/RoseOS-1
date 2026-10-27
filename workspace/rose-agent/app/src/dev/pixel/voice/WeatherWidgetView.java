package dev.pixel.voice;

import android.animation.ValueAnimator;
import android.content.*;
import android.graphics.*;
import android.net.Uri;
import android.os.*;
import android.util.TypedValue;
import android.view.*;
import android.widget.*;
import java.util.concurrent.*;
import org.json.*;

/** A small live scene over cached weather. Forecast work is never performed on the UI thread. */
final class WeatherWidgetView extends FrameLayout {
 private static final ExecutorService weatherWorker=Executors.newSingleThreadExecutor();
 private final Handler handler=new Handler(Looper.getMainLooper());
 private final TextView temperature,condition,city;
 private final WeatherSkyView scene;
 private boolean requestedActive,running,fetching;
 private long nextFetch;
 private int generation;
 private JSONObject weather,location;
 private final Runnable poll=new Runnable(){@Override public void run(){
  if(!running)return;
  JSONObject fix=PhoneEnvironment.get(getContext()).snapshot().optJSONObject("location");
  if(fix!=null&&"available".equals(fix.optString("status"))&&fix.has("latitude")&&fix.has("longitude")){
   if(location!=null&&distanceChanged(location,fix)){nextFetch=0;weather=null;scene.setPlaying(false);}
   location=fix;
   if(!fetching&&SystemClock.elapsedRealtime()>=nextFetch)fetch(fix);
   render();
  }else{
   if(location!=null){generation++;fetching=false;}
   location=null;weather=null;nextFetch=0;
   temperature.setText("—");city.setText("Your location");scene.setState(null,-1,-1,0);scene.setPlaying(false);
   condition.setText(fix!=null&&("disabled".equals(fix.optString("status"))||"permission_required".equals(fix.optString("status")))?"Location off":"Finding location…");setContentDescription("Open Weather. "+condition.getText());
  }
  handler.postDelayed(this,weather==null||"Your location".equals(cityName())?5000:30000);
 }};
 WeatherWidgetView(Context context,Typeface font){
  super(context);
  setBackground(PlayfulTheme.surface(color(0xfffff7e5),color(0xff789ab5),getResources().getDisplayMetrics().density,24));
  setClickable(true);setFocusable(true);setContentDescription("Open Weather");setOnClickListener(v->openWeather());
  scene=new WeatherSkyView(context);
  FrameLayout.LayoutParams skyParams=new FrameLayout.LayoutParams(-1,-1);skyParams.setMargins(dp(1),dp(1),dp(1),dp(4));addView(scene,skyParams);
  // A transparent text layer: the sky retains the entire compact tile, even at large font scales.
  LinearLayout labels=new LinearLayout(context);labels.setOrientation(LinearLayout.VERTICAL);
  labels.setPadding(dp(13),dp(10),dp(13),dp(12));addView(labels,new FrameLayout.LayoutParams(-1,-1));
  city=label("Your location",14,font);city.setMaxLines(2);city.setEllipsize(android.text.TextUtils.TruncateAt.END);
  labels.addView(city,new LinearLayout.LayoutParams(-1,-2));
  labels.addView(new View(context),new LinearLayout.LayoutParams(1,0,1));
  temperature=label("—",30,font);temperature.setSingleLine(true);temperature.setAutoSizeTextTypeUniformWithConfiguration(getResources().getConfiguration().fontScale>=1.5f?12:20,30,1,TypedValue.COMPLEX_UNIT_SP);
  labels.addView(temperature,new LinearLayout.LayoutParams(-1,dp(40)));
  condition=label("Finding weather…",12,font);condition.setMaxLines(2);condition.setEllipsize(android.text.TextUtils.TruncateAt.END);
  labels.addView(condition,new LinearLayout.LayoutParams(-1,-2));
  if(getResources().getConfiguration().fontScale>=1.5f){city.setTextSize(12);condition.setTextSize(11);}
 }
 /** A thin navy keyline keeps cream type readable over any sky without an opaque panel. */
 private final class SkyLabel extends TextView {
  SkyLabel(){super(WeatherWidgetView.this.getContext());}
  @Override protected void onDraw(Canvas canvas){
   if(getLayout()!=null){
    android.text.TextPaint paint=getPaint();int previous=paint.getColor();
    paint.setStyle(Paint.Style.STROKE);paint.setStrokeJoin(Paint.Join.ROUND);paint.setStrokeWidth(dp(2.5f));paint.setColor(0xff29385d);
    canvas.save();canvas.translate(getCompoundPaddingLeft(),getExtendedPaddingTop());getLayout().draw(canvas);canvas.restore();
    paint.setStyle(Paint.Style.FILL);paint.setColor(previous);
   }
   super.onDraw(canvas);
  }
 }
 private TextView label(String value,int size,Typeface font){TextView v=new SkyLabel();v.setText(value);v.setTextSize(size);v.setTypeface(font);v.setTextColor(0xfffff7e5);v.setGravity(Gravity.TOP|Gravity.LEFT);v.setIncludeFontPadding(false);return v;}
 private int dp(float value){return Math.round(value*getResources().getDisplayMetrics().density);}
 private static int color(int value){return PixelAppearance.color(value);}
 void setActive(boolean active){requestedActive=active;updateRunning();}
 private void updateRunning(){
  boolean next=requestedActive&&isAttachedToWindow()&&isShown()&&getWindowVisibility()==VISIBLE;if(next==running)return;running=next;
  handler.removeCallbacks(poll);
  if(next)handler.post(poll);else{generation++;fetching=false;}
  scene.setPlaying(next&&weather!=null&&"ready".equals(weather.optString("status")));
 }
 @Override protected void onVisibilityChanged(View changed,int visibility){super.onVisibilityChanged(changed,visibility);if(scene!=null)updateRunning();}
 @Override protected void onWindowVisibilityChanged(int visibility){super.onWindowVisibilityChanged(visibility);if(scene!=null)updateRunning();}
 @Override protected void onAttachedToWindow(){super.onAttachedToWindow();updateRunning();}
 @Override protected void onDetachedFromWindow(){requestedActive=false;updateRunning();super.onDetachedFromWindow();}
 private boolean distanceChanged(JSONObject a,JSONObject b){
  double dy=a.optDouble("latitude")-b.optDouble("latitude"),dx=(a.optDouble("longitude")-b.optDouble("longitude"))*Math.cos(Math.toRadians(b.optDouble("latitude")));
  return dx*dx+dy*dy>.045*.045;
 }
 private String cityName(){if(location==null)return "Your location";String name=location.optString("city");if(name.isEmpty())name=location.optString("region");return name.isEmpty()?"Your location":name;}
 private void fetch(JSONObject fix){
  fetching=true;final int request=++generation;final double lat=fix.optDouble("latitude"),lon=fix.optDouble("longitude");final String place=cityName();
  weatherWorker.execute(()->{
   JSONObject result;
   try{
    Bundle in=new Bundle();in.putDouble("latitude",lat);in.putDouble("longitude",lon);in.putString("city",place);
    Bundle out=getContext().getContentResolver().call(Uri.parse("content://dev.pixel.weather.widget"),"current",null,in);
    if(out==null)throw new IllegalStateException("Weather unavailable");result=new JSONObject(out.getString("json","{}"));
   }catch(Exception unavailable){result=new JSONObject();}
   final JSONObject delivered=result;
   handler.post(()->{if(request!=generation||!running)return;fetching=false;
    String status=delivered.optString("status");
    if(location!=null&&distanceChanged(fix,location)){nextFetch=0;handler.removeCallbacks(poll);handler.post(poll);return;}
    if("ready".equals(status)||"saved".equals(status)){weather=delivered;nextFetch=SystemClock.elapsedRealtime()+("ready".equals(status)?15*60_000:60_000);}
    else{weather=null;nextFetch=SystemClock.elapsedRealtime()+60_000;}
    render();
   });
  });
 }
 private void render(){
  city.setText(cityName());
  if(weather==null){temperature.setText("—");condition.setText(fetching?"Finding weather…":"Weather unavailable");scene.setState(null,-1,-1,0);scene.setPlaying(false);setContentDescription("Open Weather. "+city.getText()+". "+condition.getText());return;}
  String status=weather.optString("status"),summary=weather.optString("condition","Conditions unavailable");
  temperature.setText(weather.optString("temperature","—")+(weather.has("temperature")?(weather.optBoolean("fahrenheit",true)?"F":"C"):""));
  condition.setText(("saved".equals(status)?"Saved · ":"")+summary);
  WeatherAstronomy.State sky=null;
  if(location!=null)try{sky=WeatherAstronomy.at(System.currentTimeMillis(),location.optDouble("latitude"),location.optDouble("longitude"));}catch(IllegalArgumentException ignored){}
  if(location!=null)scene.setObserver(System.currentTimeMillis(),location.optDouble("latitude"),location.optDouble("longitude"));
  scene.setState(sky,weather.optInt("weatherCode",-1),weather.optInt("cloudCoverPercent",-1),weather.optDouble("windSpeedMps",0));scene.setPlaying(running&&"ready".equals(status));
  setContentDescription("Open Weather. "+city.getText()+". "+temperature.getText()+". "+condition.getText());
 }
 private void openWeather(){
  Intent launch=getContext().getPackageManager().getLaunchIntentForPackage("dev.pixel.weather");
  if(launch==null){Toast.makeText(getContext(),"Weather is unavailable",Toast.LENGTH_SHORT).show();return;}
  if(location!=null){launch.putExtra("weatherLatitude",location.optDouble("latitude")).putExtra("weatherLongitude",location.optDouble("longitude")).putExtra("weatherCity",cityName());}
  launch.addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP|Intent.FLAG_ACTIVITY_SINGLE_TOP);getContext().startActivity(launch);
 }
}
