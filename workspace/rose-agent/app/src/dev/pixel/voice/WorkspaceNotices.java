package dev.pixel.voice;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * Project 2: the rules for the workspace's real notification and media inputs, kept pure so they are tested on the
 * JVM. The Android side (WorkspaceNotificationListener, WorkspaceDeviceInputs) only copies what the system gives into
 * Posted and Playing, then asks these rules.
 *
 * <ul>
 *   <li><b>Two consents, both required.</b> The person turns on Notification access for Rose Agent in Android
 *   Settings; only they can grant it. They also accept the workspace's own disclosure that titles go to their Rose
 *   server, which is off unless set. Either missing is not_granted, and nothing is read.</li>
 *   <li><b>Titles only.</b> A notification brings its title and the labels of its plain actions. Its message text,
 *   people, pictures and extras are never copied.</li>
 *   <li><b>Left out:</b> Rose Agent's own notifications, ongoing ones (foreground services, calls, playback
 *   controls), group summaries, those whose app marks them secret, actions that need typed input (reply), and
 *   anything from an app the snapshot does not list.</li>
 *   <li><b>Media:</b> only a session that is playing or paused with a title becomes a "media" content item, under
 *   a hashed id.</li>
 * </ul>
 */
final class WorkspaceNotices {
 private WorkspaceNotices() {}

 /** One posted notification, as the listener saw it: plain data, nothing Android. */
 static final class Posted {
  final String key,pkg,title;final long postTimeMs;final boolean ongoing,groupSummary,secret;final List<Action> actions;
  Posted(String key,String pkg,String title,long postTimeMs,boolean ongoing,boolean groupSummary,boolean secret,List<Action> actions){
   this.key=key;this.pkg=pkg;this.title=title;this.postTimeMs=postTimeMs;this.ongoing=ongoing;this.groupSummary=groupSummary;this.secret=secret;
   this.actions=actions==null?Collections.<Action>emptyList():actions;
  }
 }
 /** One of a notification's actions: its label, and whether it asks for typed input (a reply). */
 static final class Action {
  final String label;final boolean needsInput;
  Action(String label,boolean needsInput){this.label=label;this.needsInput=needsInput;}
 }
 /** One active media session: its app, a title from its metadata, and whether it is playing or paused. */
 static final class Playing {
  final String pkg,title,sessionTag;final boolean active;
  Playing(String pkg,String sessionTag,String title,boolean active){this.pkg=pkg;this.sessionTag=sessionTag;this.title=title;this.active=active;}
 }

 /** The status of the device source: available only with both consents and a connected listener. */
 static String status(boolean accessGranted,boolean consented,boolean listenerConnected){
  return accessGranted&&consented&&listenerConnected?WorkspaceInputs.AVAILABLE:WorkspaceInputs.NOT_GRANTED;
 }

 /** The notifications that may enter the snapshot, each under the app the snapshot lists for its package. */
 static List<WorkspaceSnapshot.Notice> notices(List<Posted> posted,Map<String,WorkspaceSnapshot.App> appsByPackage,String ownPackage){
  List<WorkspaceSnapshot.Notice> out=new ArrayList<>();
  if(posted==null||appsByPackage==null)return out;
  for(Posted p:posted){
   if(p==null||p.key==null||p.pkg==null||p.pkg.equals(ownPackage))continue;
   if(p.ongoing||p.groupSummary||p.secret)continue;
   WorkspaceSnapshot.App app=appsByPackage.get(p.pkg);
   if(app==null||WorkspaceSnapshot.label(p.title,WorkspaceSnapshot.LABEL)==null)continue;
   List<String> labels=new ArrayList<>();
   for(Action a:p.actions)if(a!=null&&!a.needsInput&&WorkspaceSnapshot.label(a.label,WorkspaceSnapshot.LABEL)!=null)labels.add(a.label);
   out.add(new WorkspaceSnapshot.Notice(p.key,app,p.title,p.postTimeMs,labels));
  }
  return out;
 }

 /** The plain actions of a notification, in order: the snapshot's nact:&lt;hash&gt;/&lt;i&gt; is the i-th of these. */
 static List<Integer> plainActionIndexes(List<Action> actions){
  List<Integer> out=new ArrayList<>();
  if(actions!=null)for(int i=0;i<actions.size();i++){Action a=actions.get(i);if(a!=null&&!a.needsInput&&WorkspaceSnapshot.label(a.label,WorkspaceSnapshot.LABEL)!=null)out.add(i);}
  return out;
 }

 /** The media sessions that may enter as content: active, titled, from a listed app. */
 static List<WorkspaceSnapshot.Content> media(List<Playing> playing,Map<String,WorkspaceSnapshot.App> appsByPackage,String ownPackage){
  List<WorkspaceSnapshot.Content> out=new ArrayList<>();
  if(playing==null||appsByPackage==null)return out;
  for(Playing p:playing){
   if(p==null||p.pkg==null||!p.active||p.pkg.equals(ownPackage))continue;
   WorkspaceSnapshot.App app=appsByPackage.get(p.pkg);
   if(app==null||WorkspaceSnapshot.label(p.title,WorkspaceSnapshot.LABEL)==null)continue;
   out.add(new WorkspaceSnapshot.Content("media",mediaId(p),"media",p.title,app));
  }
  return out;
 }

 /** A media session's stable id: a hash of its app and session tag, so neither appears in the snapshot. */
 static String mediaId(Playing p){return hash("media\n"+p.pkg+"\n"+(p.sessionTag==null?"":p.sessionTag));}

 private static String hash(String s){
  try{byte[] d=MessageDigest.getInstance("SHA-256").digest(s.getBytes(StandardCharsets.UTF_8));
   StringBuilder b=new StringBuilder();for(int i=0;i<8;i++)b.append(String.format("%02x",d[i]&0xff));return b.toString();}
  catch(java.security.NoSuchAlgorithmException e){throw new IllegalStateException(e);}
 }
}
