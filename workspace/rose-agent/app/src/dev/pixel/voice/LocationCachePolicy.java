package dev.pixel.voice;

/** Pure cache rules, shared by native location collection and its tests. */
final class LocationCachePolicy {
 static final long MAX_AGE_MS=5*60*1000, RETRY_MS=30*1000;
 static final float CITY_DISTANCE_METERS=500;
 static long ageMs(long nowNanos,long fixNanos){return Math.max(0,(nowNanos-fixNanos)/1000000);}
 static boolean stale(long ageMs){return ageMs>MAX_AGE_MS;}
 static boolean moved(float distance,float oldAccuracy,float newAccuracy){
  return distance>CITY_DISTANCE_METERS+Math.max(0,oldAccuracy)+Math.max(0,newAccuracy);
 }
 static boolean shouldRefresh(boolean hasFix,long ageMs,long nowMs,long lastAttemptMs){
  return (!hasFix||stale(ageMs))&&(lastAttemptMs==0||nowMs-lastAttemptMs>=RETRY_MS);
 }
}
