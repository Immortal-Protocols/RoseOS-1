package dev.pixel.voice;

import android.content.pm.PackageManager;

/** Shared presentation names and launcher identities. These never authorize app access. */
final class AppLabels {
 static final String[] SUITE_PACKAGES={"com.bluebubbles.messaging","dev.pixel.mail","org.fossify.calendar.pixel","dev.pixel.clock","@camera","dev.pixel.calculator.agent","dev.pixel.notes.agent","dev.pixel.reminders","dev.pixel.news.feeder","dev.pixel.books","dev.pixel.podcasts","dev.pixel.compass","dev.pixel.sky","dev.pixel.weather","com.android.documentsui","com.android.gallery3d","com.android.dialer","com.android.settings","app.vanadium.browser","com.android.contacts","dev.pixel.memories"};
 static final String[] SUITE_NAMES={"Messages","Mail","Calendar","Clock","Camera","Calculator","Notes","Reminders","News","Books","Podcasts","Compass","Sky","Weather","Files","Gallery","Phone","Settings","Browser","Contacts","Memories"};
 static final int[] SUITE_ICONS={R.drawable.suite_messages,R.drawable.suite_mail,R.drawable.suite_calendar,R.drawable.suite_clock,R.drawable.suite_camera,R.drawable.suite_calculator,R.drawable.suite_notes,R.drawable.suite_reminders,R.drawable.suite_news,R.drawable.suite_books,R.drawable.suite_podcasts,R.drawable.suite_compass,R.drawable.suite_sky,R.drawable.suite_weather,R.drawable.suite_files,R.drawable.suite_gallery,R.drawable.suite_phone,R.drawable.suite_settings,R.drawable.suite_browser,R.drawable.suite_contacts,R.drawable.suite_memories};
 static String preferredCamera(PackageManager pm){
  if(pm.getLaunchIntentForPackage("dev.pixel.aicamera")!=null)return "dev.pixel.aicamera";
  return pm.getLaunchIntentForPackage("net.sourceforge.opencamera")!=null?"net.sourceforge.opencamera":"";
 }
 static int suiteIndex(String packageName,String cameraPackage){
  for(int i=0;i<SUITE_PACKAGES.length;i++)if((i==4?cameraPackage:SUITE_PACKAGES[i]).equals(packageName))return i;
  return -1;
 }
 static String label(PackageManager pm,String packageName,String original){
  int index=suiteIndex(packageName,preferredCamera(pm));
  if(index>=0)return SUITE_NAMES[index];
  if("dev.pixel.mail.debug".equals(packageName))return "Mail (test)";
  if("org.fossify.calendar.pixel.debug".equals(packageName))return "Calendar (test)";
  if("dev.pixel.weather.opus".equals(packageName))return "Weather (alternate)";
  if("app.grapheneos.camera".equals(packageName))return "Camera (system)";
  if("net.sourceforge.opencamera".equals(packageName))return "Open Camera";
  if("dev.pixel.aicamera".equals(packageName))return "Camera (preview)";
  return original;
 }
 static int icon(PackageManager pm,String packageName){
  int index=suiteIndex(packageName,preferredCamera(pm));
  if(index>=0)return SUITE_ICONS[index];
  if("dev.pixel.mail.debug".equals(packageName))return R.drawable.suite_mail;
  if("org.fossify.calendar.pixel.debug".equals(packageName))return R.drawable.suite_calendar;
  if("dev.pixel.weather.opus".equals(packageName))return R.drawable.suite_weather;
  return 0;
 }
}
