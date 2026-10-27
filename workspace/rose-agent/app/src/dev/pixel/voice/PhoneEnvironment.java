package dev.pixel.voice;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.location.*;
import android.os.*;
import org.json.JSONObject;
import java.time.*;
import java.time.format.*;
import java.util.*;
import java.util.concurrent.*;

/** A local snapshot. Reading it never waits for GPS or reverse geocoding. */
final class PhoneEnvironment {
 private static PhoneEnvironment instance;
 static synchronized PhoneEnvironment get(Context c){if(instance==null)instance=new PhoneEnvironment(c.getApplicationContext());return instance;}
 static boolean permitted(Context c){return c.checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION)==PackageManager.PERMISSION_GRANTED||c.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)==PackageManager.PERMISSION_GRANTED;}
 private final Context context;
 private final LocationManager manager;
 private final Handler main=new Handler(Looper.getMainLooper());
 private final ExecutorService geocoding=Executors.newSingleThreadExecutor();
 private Location latest,cityAt;
 private String neighborhood,city,region,country;
 private boolean active,voiceActive,widgetActive,tracking,geocodingBusy;
 private long lastAttempt,lastGeocodeAttempt;
 private CancellationSignal pending;
 private PhoneEnvironment(Context c){context=c;manager=(LocationManager)c.getSystemService(Context.LOCATION_SERVICE);}
 private final LocationListener listener=new LocationListener(){
  public void onLocationChanged(Location l){accept(l);}
  public void onProviderEnabled(String provider){refresh();}
  public void onProviderDisabled(String provider){}
 };
 void setActive(boolean value){setOwnerActive(value,false);}
 void setWidgetActive(boolean value){setOwnerActive(value,true);}
 private void setOwnerActive(boolean value,boolean widget){main.post(()->{
  if(widget)widgetActive=value;else voiceActive=value;
  active=voiceActive||widgetActive;
  if(!active){if(tracking)manager.removeUpdates(listener);tracking=false;if(pending!=null)pending.cancel();pending=null;return;}
  refresh();
 });}
 private String provider(){
  for(String p:new String[]{"fused",LocationManager.NETWORK_PROVIDER,LocationManager.GPS_PROVIDER})try{if(manager.isProviderEnabled(p))return p;}catch(Exception ignored){}
  return null;
 }
 private void refresh(){
  if(!permitted(context))return;
  try{
   if(!manager.isLocationEnabled())return;
   for(String p:manager.getProviders(true))try{Location l=manager.getLastKnownLocation(p);if(l!=null)accept(l);}catch(Exception ignored){}
   String p=provider();if(p==null)return;
   if(active&&!tracking){
    // Passive updates reuse fixes requested by other apps. Balanced updates avoid
    // continuous GPS on devices without a network/fused provider.
    if(manager.getAllProviders().contains(LocationManager.PASSIVE_PROVIDER))manager.requestLocationUpdates(LocationManager.PASSIVE_PROVIDER,30000,500,listener,Looper.getMainLooper());
    if(!LocationManager.GPS_PROVIDER.equals(p))manager.requestLocationUpdates(p,new LocationRequest.Builder(60000).setQuality(LocationRequest.QUALITY_BALANCED_POWER_ACCURACY).setMinUpdateIntervalMillis(30000).setMinUpdateDistanceMeters(500).build(),context.getMainExecutor(),listener);
    tracking=true;
   }
   long now=SystemClock.elapsedRealtime();Location fix; synchronized(this){fix=latest;}
   long age=fix==null?Long.MAX_VALUE:LocationCachePolicy.ageMs(SystemClock.elapsedRealtimeNanos(),fix.getElapsedRealtimeNanos());
   if(active&&pending==null&&LocationCachePolicy.shouldRefresh(fix!=null,age,now,lastAttempt)){
    lastAttempt=now;CancellationSignal request=new CancellationSignal();pending=request;
    manager.getCurrentLocation(p,new LocationRequest.Builder(0).setQuality(LocationRequest.QUALITY_BALANCED_POWER_ACCURACY).setDurationMillis(20000).setMaxUpdates(1).build(),request,context.getMainExecutor(),l->{if(pending==request)pending=null;if(l!=null)accept(l);});
    main.postDelayed(()->{if(pending==request){request.cancel();pending=null;}},20000);
   }
  }catch(SecurityException ignored){}catch(Exception ignored){}
 }
 private synchronized void accept(Location fix){
  if(fix==null||!fix.hasAccuracy()||!Double.isFinite(fix.getLatitude())||!Double.isFinite(fix.getLongitude()))return;
  if(latest!=null&&fix.getElapsedRealtimeNanos()<=latest.getElapsedRealtimeNanos())return;
  latest=new Location(fix);
  boolean moved=cityAt!=null&&LocationCachePolicy.moved(cityAt.distanceTo(fix),cityAt.getAccuracy(),fix.getAccuracy());
  if(moved){neighborhood=null;city=null;region=null;country=null;cityAt=null;}
  if(cityAt==null&&!geocodingBusy&&Geocoder.isPresent()&&SystemClock.elapsedRealtime()-lastGeocodeAttempt>30000){
   geocodingBusy=true;lastGeocodeAttempt=SystemClock.elapsedRealtime();Location target=new Location(fix);
   geocoding.execute(()->{
    try{
     List<Address> results=new Geocoder(context,Locale.US).getFromLocation(target.getLatitude(),target.getLongitude(),1);
     synchronized(PhoneEnvironment.this){if(results!=null&&!results.isEmpty()&&latest!=null&&!LocationCachePolicy.moved(target.distanceTo(latest),target.getAccuracy(),latest.getAccuracy())){
      Address a=results.get(0);neighborhood=a.getSubLocality();city=a.getLocality()!=null?a.getLocality():a.getSubAdminArea();region=a.getAdminArea();country=a.getCountryName();cityAt=target;
     }}
    }catch(Exception ignored){}finally{synchronized(PhoneEnvironment.this){geocodingBusy=false;}}
   });
  }
 }
 JSONObject snapshot(){
  JSONObject out=new JSONObject();
  try{
   ZonedDateTime now=ZonedDateTime.now();
   out.put("clock",new JSONObject().put("localDate",now.toLocalDate().toString()).put("weekday",now.getDayOfWeek().getDisplayName(TextStyle.FULL,Locale.US)).put("localTime",now.format(DateTimeFormatter.ofPattern("HH:mm:ss",Locale.US))).put("timeZone",now.getZone().getId()).put("utcOffset",now.getOffset().toString()));
   JSONObject loc=new JSONObject();out.put("location",loc);
   if(!permitted(context)){loc.put("status","permission_required");return out;}
   if(!manager.isLocationEnabled()){loc.put("status","disabled");return out;}
   main.post(this::refresh);
   synchronized(this){
    if(latest==null){loc.put("status","unavailable");return out;}
    long age=LocationCachePolicy.ageMs(SystemClock.elapsedRealtimeNanos(),latest.getElapsedRealtimeNanos());
    loc.put("status",LocationCachePolicy.stale(age)?"stale":"available").put("latitude",latest.getLatitude()).put("longitude",latest.getLongitude()).put("accuracyMeters",Math.round(latest.getAccuracy())).put("ageSeconds",age/1000).put("observedAt",latest.getTime()).put("approximate",context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)!=PackageManager.PERMISSION_GRANTED);
    if(cityAt!=null){if(neighborhood!=null&&!neighborhood.trim().isEmpty())loc.put("neighborhood",neighborhood);if(city!=null)loc.put("city",city);if(region!=null)loc.put("region",region);if(country!=null)loc.put("country",country);}
   }
  }catch(Exception ignored){}
  return out;
 }
}
