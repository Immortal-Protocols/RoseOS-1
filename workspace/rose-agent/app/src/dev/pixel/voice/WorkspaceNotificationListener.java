package dev.pixel.voice;

import android.app.Notification;
import android.app.PendingIntent;
import android.content.ComponentName;
import android.content.Context;
import android.media.MediaMetadata;
import android.media.session.MediaController;
import android.media.session.MediaSessionManager;
import android.media.session.PlaybackState;
import android.os.Bundle;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Project 2: the workspace's notification listener. Android connects it only after the person turns on
 * Notification access for Rose Agent in Settings; nothing in Rose Agent can grant that. Even then the workspace reads
 * nothing until the person also accepts the workspace's own disclosure (WorkspaceDeviceInputs, WorkspaceNotices).
 *
 * It keeps nothing: each read asks the system for the active notifications and media sessions at that moment, copies
 * titles and action labels only into plain data (WorkspaceNotices.Posted and Playing), and returns. It never logs,
 * stores or sends anything itself. Opening a notification, one of its actions or a media session sends that item's
 * own PendingIntent, which the person tapped.
 */
public final class WorkspaceNotificationListener extends NotificationListenerService {
 private static volatile WorkspaceNotificationListener connected;

 @Override public void onListenerConnected(){connected=this;}
 @Override public void onListenerDisconnected(){if(connected==this)connected=null;}
 @Override public void onDestroy(){if(connected==this)connected=null;super.onDestroy();}

 static boolean isConnected(){return connected!=null;}
 static ComponentName component(Context context){return new ComponentName(context,WorkspaceNotificationListener.class);}

 /** The active notifications now, as plain data; empty when the listener is not connected or the system refuses. */
 static List<WorkspaceNotices.Posted> posted(){
  WorkspaceNotificationListener l=connected;if(l==null)return Collections.emptyList();
  List<WorkspaceNotices.Posted> out=new ArrayList<>();
  try{
   StatusBarNotification[] active=l.getActiveNotifications();
   if(active!=null)for(StatusBarNotification s:active){
    Notification n=s.getNotification();if(n==null)continue;
    Bundle extras=n.extras;CharSequence title=extras==null?null:extras.getCharSequence(Notification.EXTRA_TITLE);
    List<WorkspaceNotices.Action> actions=new ArrayList<>();
    if(n.actions!=null)for(Notification.Action a:n.actions)actions.add(new WorkspaceNotices.Action(a.title==null?null:a.title.toString(),a.getRemoteInputs()!=null&&a.getRemoteInputs().length>0));
    out.add(new WorkspaceNotices.Posted(s.getKey(),s.getPackageName(),title==null?null:title.toString(),s.getPostTime(),
     s.isOngoing(),(n.flags&Notification.FLAG_GROUP_SUMMARY)!=0,n.visibility==Notification.VISIBILITY_SECRET,actions));
   }
  }catch(RuntimeException e){return Collections.emptyList();}
  return out;
 }

 /** The active media sessions now, as plain data; empty when the listener is not connected. */
 static List<WorkspaceNotices.Playing> playing(Context context){
  if(connected==null)return Collections.emptyList();
  List<WorkspaceNotices.Playing> out=new ArrayList<>();
  try{
   MediaSessionManager m=context.getSystemService(MediaSessionManager.class);if(m==null)return out;
   for(MediaController c:m.getActiveSessions(component(context))){
    MediaMetadata md=c.getMetadata();PlaybackState ps=c.getPlaybackState();
    String title=md==null?null:md.getString(MediaMetadata.METADATA_KEY_TITLE);
    boolean active=ps!=null&&(ps.getState()==PlaybackState.STATE_PLAYING||ps.getState()==PlaybackState.STATE_PAUSED);
    out.add(new WorkspaceNotices.Playing(c.getPackageName(),c.getTag(),title,active));
   }
  }catch(RuntimeException e){return Collections.emptyList();}
  return out;
 }

 /** Opens the notification with this key (its content intent), as if tapped in the shade. False if it is gone. */
 static boolean open(String key){
  StatusBarNotification s=find(key);if(s==null||s.getNotification().contentIntent==null)return false;
  return send(s.getNotification().contentIntent);
 }

 /** Runs the plainIndex-th plain action of the notification with this key. False if it or the action is gone. */
 static boolean action(String key,int plainIndex){
  StatusBarNotification s=find(key);if(s==null||s.getNotification().actions==null)return false;
  Notification.Action[] all=s.getNotification().actions;
  List<WorkspaceNotices.Action> plain=new ArrayList<>();
  for(Notification.Action a:all)plain.add(new WorkspaceNotices.Action(a.title==null?null:a.title.toString(),a.getRemoteInputs()!=null&&a.getRemoteInputs().length>0));
  List<Integer> indexes=WorkspaceNotices.plainActionIndexes(plain);
  if(plainIndex<0||plainIndex>=indexes.size())return false;
  PendingIntent p=all[indexes.get(plainIndex)].actionIntent;return p!=null&&send(p);
 }

 /** Opens the media session whose workspace id this is (its session activity). False if it is gone. */
 static boolean openMedia(Context context,String mediaId){
  if(connected==null)return false;
  try{
   MediaSessionManager m=context.getSystemService(MediaSessionManager.class);if(m==null)return false;
   for(MediaController c:m.getActiveSessions(component(context))){
    if(!mediaId.equals(WorkspaceNotices.mediaId(new WorkspaceNotices.Playing(c.getPackageName(),c.getTag(),null,true))))continue;
    PendingIntent p=c.getSessionActivity();return p!=null&&send(p);
   }
  }catch(RuntimeException e){return false;}
  return false;
 }

 private static StatusBarNotification find(String key){
  WorkspaceNotificationListener l=connected;if(l==null||key==null)return null;
  try{StatusBarNotification[] s=l.getActiveNotifications(new String[]{key});return s!=null&&s.length==1?s[0]:null;}
  catch(RuntimeException e){return null;}
 }
 private static boolean send(PendingIntent p){try{p.send();return true;}catch(PendingIntent.CanceledException|RuntimeException e){return false;}}
}
