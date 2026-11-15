package dev.pixel.voice;

import android.app.NotificationManager;
import android.content.Context;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * Project 2: the phone's real notification and media source for the workspace (WorkspaceInputs.Source). It is
 * fail-closed: it gives nothing unless the person has done both of these.
 * <ul>
 *   <li>Turned on Notification access for Rose Agent in Android Settings
 *   (NotificationManager.isNotificationListenerAccessGranted), so the listener is connected.</li>
 *   <li>Accepted the workspace's own disclosure (CONSENT, off unless set; WorkspacePage's "Notifications on this
 *   page" card). The disclosure says the titles go to their own Rose server to arrange the page, and that message
 *   text is never read.</li>
 * </ul>
 * Turning either off stops it at the next read. Nothing is stored. Content is limited to media sessions; Rose Agent
 * has no other content source.
 */
final class WorkspaceDeviceInputs implements WorkspaceInputs.Source {
 /** The workspace's own consent, stored in the workspace's preferences. Off unless the person accepts the disclosure. */
 static final String CONSENT="notifications-consent";
 /** The person chose "Not now" on the offer: it is not shown again until they ask from the page. */
 static final String DISMISSED="notifications-offer-dismissed";

 private final Context context;private final Map<String,WorkspaceSnapshot.App> appsByPackage;
 WorkspaceDeviceInputs(Context context,Map<String,WorkspaceSnapshot.App> appsByPackage){this.context=context.getApplicationContext();this.appsByPackage=appsByPackage;}

 static boolean consented(Context context){
  try{return context.getSharedPreferences(WorkspaceTaskGate.PREFS,Context.MODE_PRIVATE).getBoolean(CONSENT,false);}catch(RuntimeException e){return false;}
 }
 static void setConsent(Context context,boolean on){
  context.getSharedPreferences(WorkspaceTaskGate.PREFS,Context.MODE_PRIVATE).edit().putBoolean(CONSENT,on).apply();
 }
 static boolean dismissed(Context context){
  try{return context.getSharedPreferences(WorkspaceTaskGate.PREFS,Context.MODE_PRIVATE).getBoolean(DISMISSED,false);}catch(RuntimeException e){return false;}
 }
 static void dismiss(Context context){context.getSharedPreferences(WorkspaceTaskGate.PREFS,Context.MODE_PRIVATE).edit().putBoolean(DISMISSED,true).apply();}

 /** Whether Android has granted Rose Agent's listener Notification access. Only the person can grant it, in Settings. */
 static boolean accessGranted(Context context){
  try{NotificationManager nm=context.getSystemService(NotificationManager.class);return nm!=null&&nm.isNotificationListenerAccessGranted(WorkspaceNotificationListener.component(context));}
  catch(RuntimeException e){return false;}
 }

 static String status(Context context){return WorkspaceNotices.status(accessGranted(context),consented(context),WorkspaceNotificationListener.isConnected());}

 @Override public String notificationStatus(){return status(context);}
 @Override public List<WorkspaceSnapshot.Notice> notices(){
  if(!WorkspaceInputs.AVAILABLE.equals(notificationStatus()))return Collections.emptyList();
  return WorkspaceNotices.notices(WorkspaceNotificationListener.posted(),appsByPackage,context.getPackageName());
 }
 @Override public String contentStatus(){return status(context);}
 @Override public List<WorkspaceSnapshot.Content> content(){
  if(!WorkspaceInputs.AVAILABLE.equals(contentStatus()))return Collections.emptyList();
  return WorkspaceNotices.media(WorkspaceNotificationListener.playing(context),appsByPackage,context.getPackageName());
 }
}
