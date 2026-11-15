package dev.pixel.voice;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Project 2: the rose-layout-v1 snapshot and its registry, built together from what the phone may show, so
 * every id the composer sees can be resolved and opened, and nothing else can (workspace-contract.md,
 * "Registry and ids"). Pure (no Android types): the page lists apps, shortcuts and intent templates on the
 * Android side and passes them in; this decides the ids, the bounds and the version.
 *
 * Only labels the OS already shows enter the snapshot, cut to 60 characters and stripped of control
 * characters. Notifications and content come from a WorkspaceInputs.Source: on the phone, WorkspaceDeviceInputs,
 * which gives nothing without the person's Notification access and the workspace's own consent. A notification
 * enters only as its title, never its body text, and under a hashed key. The version is a hash of every entry, so it
 * changes whenever an entry does and a layout made for another snapshot is stale_snapshot.
 */
final class WorkspaceSnapshot {
 static final int LABEL=60,MAX_APPS=120,MAX_ACTIONS=60;
 static final int TASK_LABEL=60,TASK_ASK=200,TASK_GOAL=300;
 static final int MAX_NOTIFICATIONS=12,MAX_NOTIFICATION_ACTIONS=2,MAX_CONTENT=12;
 /** rose-layout-v1's content kinds. */
 static final java.util.List<String> CONTENT_KINDS=java.util.Arrays.asList("media","contact","document","place","other");

 /** A launchable activity, as Home already lists it. recentRank is 1 for the most recent, 0 when unknown. */
 static final class App {
  final String pkg,activity,label,category;final int recentRank;
  App(String pkg,String activity,String label,String category,int recentRank){this.pkg=pkg;this.activity=activity;this.label=label;this.category=category;this.recentRank=recentRank;}
 }
 /** A launcher shortcut (LauncherApps), belonging to one of the apps above. */
 static final class Shortcut {
  final String pkg,shortcutId,label;final App app;
  Shortcut(App app,String shortcutId,String label){this.app=app;this.pkg=app.pkg;this.shortcutId=shortcutId;this.label=label;}
 }
 /** One of Rose's intent templates (timer, alarms, ...), already resolved to the app that would handle it. */
 static final class Template {
  final String template,label;final App app;
  Template(String template,String label,App app){this.template=template;this.label=label;this.app=app;}
 }
 /**
  * A Rose task the person may start, which always asks first. Its target is Rose Agent's own app entry,
  * because every rose-layout-v1 action names a target. The ask and the goal never enter the snapshot.
  */
 static final class Task {
  final String templateId,label,ask,goal;final App app;
  Task(String templateId,String label,String ask,String goal,App app){this.templateId=templateId;this.label=label;this.ask=ask;this.goal=goal;this.app=app;}
 }

 /**
  * A posted notification, as a source gives it. Only its title and action labels may enter the snapshot, and
  * its key appears only hashed. The body text is never taken. The app must be one the snapshot lists.
  */
 static final class Notice {
  final String key,title;final App app;final long postedAtMs;final List<String> actionLabels;
  Notice(String key,App app,String title,long postedAtMs,List<String> actionLabels){this.key=key;this.app=app;this.title=title;this.postedAtMs=postedAtMs;this.actionLabels=actionLabels==null?java.util.Collections.<String>emptyList():actionLabels;}
 }
 /** One content item a source offers (media, a contact, a document, a place), opened in its own app. */
 static final class Content {
  final String source,id,kind,label;final App app;
  Content(String source,String id,String kind,String label,App app){this.source=source;this.id=id;this.kind=kind;this.label=label;this.app=app;}
 }

 final String snapshotVersion;
 final Map<String,Object> snapshot;
 final WorkspaceLayout.Registry registry;
 private final Map<String,App> appsById;
 private final Map<String,Shortcut> shortcutsById;
 private final Map<String,Template> templatesById;
 private final Map<String,Task> tasksById;
 private final Map<String,Notice> noticesById;private final Map<String,Content> contentById;

 private WorkspaceSnapshot(String version,Map<String,Object> snapshot,WorkspaceLayout.Registry registry,Map<String,App> apps,Map<String,Shortcut> shortcuts,Map<String,Template> templates,Map<String,Task> tasks,Map<String,Notice> notices,Map<String,Content> content){
  this.snapshotVersion=version;this.snapshot=snapshot;this.registry=registry;
  this.appsById=apps;this.shortcutsById=shortcuts;this.templatesById=templates;this.tasksById=tasks;
  this.noticesById=notices;this.contentById=content;
 }

 App app(String id){return appsById.get(id);}
 Shortcut shortcut(String id){return shortcutsById.get(id);}
 Template template(String id){return templatesById.get(id);}
 Task task(String id){return tasksById.get(id);}
 Notice notice(String id){return noticesById.get(id);}
 Content content(String id){return contentById.get(id);}
 /** Every id present now, for WorkspaceLayout.available at render time. */
 Set<String> present(){
  Set<String> s=new LinkedHashSet<>(appsById.keySet());
  s.addAll(shortcutsById.keySet());s.addAll(templatesById.keySet());s.addAll(tasksById.keySet());
  s.addAll(noticesById.keySet());s.addAll(contentById.keySet());
  for(Object o:(List<?>)snapshot.get("actions")){Object id=((Map<?,?>)o).get("id");if(id instanceof String&&((String)id).startsWith("nact:"))s.add((String)id);}
  return s;
 }

 static String appId(App a){return "app:"+a.pkg+"/"+a.activity;}

 /**
  * Builds the snapshot. tasks is empty until App Core's task route is enabled on the server; pinned are the
  * item ids of the page's pinned sections, kept only when present. nowMs and calendar give createdAtMs and
  * timeOfDay, which are not part of the version.
  */
 static WorkspaceSnapshot build(List<App> apps,List<Shortcut> shortcuts,List<Template> templates,List<Task> tasks,Set<String> pinned,long nowMs,Calendar calendar){
  return build(apps,shortcuts,templates,tasks,java.util.Collections.<Notice>emptyList(),java.util.Collections.<Content>emptyList(),pinned,nowMs,calendar);
 }

 /**
  * The same, with a source's notifications and content (WorkspaceInputs.Source).
  * <ul>
  *   <li>A notification is kept only for an app listed here. Its title must be non-empty, and its id is
  *   n:&lt;hash of its key&gt;. At most 12 are kept, most recent first.</li>
  *   <li>A notification brings at most 2 actions, nact:&lt;hash&gt;/&lt;index&gt;.</li>
  *   <li>A content item needs a rose-layout-v1 kind, a label, an app listed here and an unambiguous
  *   c:&lt;source&gt;/&lt;id&gt;. At most 12 are kept.</li>
  * </ul>
  */
 static WorkspaceSnapshot build(List<App> apps,List<Shortcut> shortcuts,List<Template> templates,List<Task> tasks,List<Notice> notices,List<Content> contents,Set<String> pinned,long nowMs,Calendar calendar){
  WorkspaceLayout.Registry registry;
  Map<String,App> appIds=new LinkedHashMap<>();
  List<Object> appEntries=new ArrayList<>(),actionEntries=new ArrayList<>();
  Map<String,Shortcut> shortcutIds=new LinkedHashMap<>();Map<String,Template> templateIds=new LinkedHashMap<>();Map<String,Task> taskIds=new LinkedHashMap<>();
  // Actions are collected with their registry facts, then registered once the version is known.
  List<String[]> actions=new ArrayList<>();

  for(App a:apps){
   if(appIds.size()>=MAX_APPS)break;
   if(!component(a.pkg)||!component(a.activity))continue;
   String label=label(a.label,LABEL);if(label==null)continue;
   String id=appId(a);if(appIds.containsKey(id))continue;
   appIds.put(id,a);
   Map<String,Object> e=new LinkedHashMap<>();
   e.put("id",id);e.put("label",label);
   String category=label(a.category,LABEL);if(category!=null)e.put("category",category);
   if(a.recentRank>0)e.put("recentRank",(long)a.recentRank);
   appEntries.add(e);
  }
  // No launch action per app: an app entry already opens it, and a copy of every app would only spend the
  // composer's context. launch: ids stay reserved for content that opens in an app.
  for(Shortcut s:shortcuts){
   if(actions.size()>=MAX_ACTIONS)break;
   String target=appId(s.app);if(!appIds.containsKey(target)||!component(s.shortcutId))continue;
   String id="sc:"+s.pkg+"/"+s.shortcutId,label=label(s.label,LABEL);
   if(label==null||shortcutIds.containsKey(id))continue;
   shortcutIds.put(id,s);addAction(actions,actionEntries,id,label,"shortcut",target,false);
  }
  for(Template t:templates){
   if(actions.size()>=MAX_ACTIONS)break;
   if(t.app==null)continue;String target=appId(t.app);
   if(!appIds.containsKey(target)||!component(t.template))continue;
   String id="intent:"+t.template,label=label(t.label,LABEL);
   if(label==null||templateIds.containsKey(id))continue;
   templateIds.put(id,t);addAction(actions,actionEntries,id,label,"deeplink",target,false);
  }
  for(Task t:tasks){
   if(actions.size()>=MAX_ACTIONS)break;
   if(t.app==null||!component(t.templateId))continue;String target=appId(t.app);
   String id="task:"+t.templateId,label=label(t.label,TASK_LABEL);
   if(label==null||!appIds.containsKey(target)||taskIds.containsKey(id))continue;
   if(label(t.ask,TASK_ASK)==null||!fits(t.ask,TASK_ASK)||label(t.goal,TASK_GOAL)==null||!fits(t.goal,TASK_GOAL))continue;
   taskIds.put(id,t);addAction(actions,actionEntries,id,label,WorkspaceLayout.AGENT_TASK,target,true);
  }

  // Notifications: title only, hashed key, most recent first, each with up to 2 action labels.
  Map<String,Notice> noticeIds=new LinkedHashMap<>();List<Object> noticeEntries=new ArrayList<>();
  List<Notice> recent=new ArrayList<>();if(notices!=null)for(Notice n:notices)if(n!=null)recent.add(n);
  java.util.Collections.sort(recent,(a,b)->Long.compare(b.postedAtMs,a.postedAtMs));
  for(Notice n:recent){
   if(noticeIds.size()>=MAX_NOTIFICATIONS)break;
   if(n.key==null||n.key.isEmpty()||n.app==null)continue;
   String target=appId(n.app),title=label(n.title,LABEL);
   if(title==null||!appIds.containsKey(target))continue;
   String hash=sha256("notification\n"+n.key).substring(0,16),id="n:"+hash;
   if(noticeIds.containsKey(id))continue;
   noticeIds.put(id,n);
   Map<String,Object> e=new LinkedHashMap<>();e.put("id",id);e.put("appId",target);e.put("title",title);e.put("recentRank",(long)noticeIds.size());
   noticeEntries.add(e);
   for(int i=0;i<n.actionLabels.size()&&i<MAX_NOTIFICATION_ACTIONS;i++){
    if(actions.size()>=MAX_ACTIONS)break;
    addAction(actions,actionEntries,"nact:"+hash+"/"+i,label(n.actionLabels.get(i),LABEL),"notification_action",id,false);
   }
  }
  // Content: a known kind, a label, an app listed here, and an id that cannot be ambiguous.
  Map<String,Content> contentIds=new LinkedHashMap<>();List<Object> contentEntries=new ArrayList<>();
  if(contents!=null)for(Content c:contents){
   if(contentIds.size()>=MAX_CONTENT)break;
   if(c==null||c.app==null||!component(c.source)||!component(c.id)||!CONTENT_KINDS.contains(c.kind))continue;
   String target=appId(c.app),lbl=label(c.label,LABEL),id="c:"+c.source+"/"+c.id;
   if(lbl==null||!appIds.containsKey(target)||contentIds.containsKey(id))continue;
   contentIds.put(id,c);
   Map<String,Object> e=new LinkedHashMap<>();e.put("id",id);e.put("kind",c.kind);e.put("label",lbl);e.put("appId",target);
   contentEntries.add(e);
  }

  Set<String> known=new LinkedHashSet<>(appIds.keySet());for(String[] a:actions)known.add(a[0]);
  known.addAll(noticeIds.keySet());known.addAll(contentIds.keySet());
  List<Object> pins=new ArrayList<>();for(String p:pinned)if(known.contains(p))pins.add(p);

  Map<String,Object> versioned=new LinkedHashMap<>();
  versioned.put("catalogVersion",WorkspaceLayout.CATALOG);
  versioned.put("apps",appEntries);versioned.put("notifications",noticeEntries);
  versioned.put("content",contentEntries);versioned.put("actions",actionEntries);versioned.put("pinned",pins);
  String version="s-"+sha256(WorkspaceLayout.json(versioned)).substring(0,12);

  Map<String,Object> snapshot=new LinkedHashMap<>();
  snapshot.put("snapshotVersion",version);snapshot.put("catalogVersion",WorkspaceLayout.CATALOG);
  snapshot.put("createdAtMs",nowMs);snapshot.put("timeOfDay",timeOfDay(calendar.get(Calendar.HOUR_OF_DAY)));
  snapshot.put("apps",appEntries);snapshot.put("notifications",versioned.get("notifications"));
  snapshot.put("content",versioned.get("content"));snapshot.put("actions",actionEntries);snapshot.put("pinned",pins);

  registry=new WorkspaceLayout.Registry(version);
  for(String id:appIds.keySet())registry.add(WorkspaceLayout.APPS,id);
  for(String id:noticeIds.keySet())registry.add(WorkspaceLayout.NOTIFICATIONS,id);
  for(String id:contentIds.keySet())registry.add(WorkspaceLayout.CONTENT,id);
  for(String[] a:actions)registry.action(a[0],a[1],a[2]);
  return new WorkspaceSnapshot(version,snapshot,registry,appIds,shortcutIds,templateIds,taskIds,noticeIds,contentIds);
 }

 private static void addAction(List<String[]> actions,List<Object> entries,String id,String label,String kind,String target,boolean confirm){
  if(label==null)return;for(String[] a:actions)if(a[0].equals(id))return;
  actions.add(new String[]{id,kind,target});
  Map<String,Object> e=new LinkedHashMap<>();
  e.put("id",id);e.put("label",label);e.put("kind",kind);e.put("targetId",target);
  e.put("requiresConfirmation",confirm);
  entries.add(e);
 }

 static final int MAX_AVOID=64;

 /**
  * The rose-layout-v1 request for one composer call. limits restate cat-1's own bounds, which the request
  * may only tighten. prompt is null for Arrange for me. avoid is regenerate's soft hint, the previous
  * layout's item ids (at most 64; empty otherwise).
  */
 static String request(String requestId,String trigger,String prompt,WorkspaceSnapshot s,long deadlineMs,List<String> avoid){
  Map<String,Object> m=new LinkedHashMap<>();
  m.put("contract",WorkspaceLayout.CONTRACT);m.put("requestId",requestId);m.put("trigger",trigger);
  m.put("prompt",prompt);m.put("catalogVersion",WorkspaceLayout.CATALOG);m.put("snapshot",s.snapshot);
  Map<String,Object> limits=new LinkedHashMap<>();
  limits.put("maxSections",(long)WorkspaceLayout.MAX_SECTIONS);limits.put("maxItemsPerSection",(long)WorkspaceLayout.MAX_ITEMS);
  limits.put("maxTotalItems",(long)WorkspaceLayout.MAX_TOTAL_ITEMS);limits.put("deadlineMs",deadlineMs);
  m.put("limits",limits);
  if(avoid!=null&&!avoid.isEmpty()){
   List<Object> a=new ArrayList<>();
   for(String id:new LinkedHashSet<>(avoid)){if(a.size()>=MAX_AVOID)break;if(id!=null&&!id.isEmpty())a.add(id);}
   m.put("avoid",a);
  }
  return WorkspaceLayout.json(m);
 }

 static String timeOfDay(int hour){return hour>=5&&hour<12?"morning":hour>=12&&hour<17?"afternoon":hour>=17&&hour<22?"evening":"night";}

 /** Display text: control characters become spaces, runs of space collapse, cut to max code points. Null when empty. */
 static String label(String raw,int max){
  if(raw==null)return null;
  StringBuilder b=new StringBuilder();boolean space=false;
  for(int i=0;i<raw.length();){
   int c=raw.codePointAt(i);i+=Character.charCount(c);
   if(Character.isISOControl(c)||Character.isWhitespace(c)||c==0x2028||c==0x2029){space=b.length()>0;continue;}
   int type=Character.getType(c);
   if(type==Character.FORMAT||type==Character.PRIVATE_USE||type==Character.SURROGATE||type==Character.UNASSIGNED)continue;
   if(space){b.append(' ');space=false;}
   b.appendCodePoint(c);
  }
  String s=b.toString();if(s.isEmpty())return null;
  if(s.codePointCount(0,s.length())>max)s=s.substring(0,s.offsetByCodePoints(0,max)).trim();
  return s;
 }
 private static boolean fits(String s,int max){return s!=null&&s.codePointCount(0,s.length())<=max;}

 /** A package, activity, shortcut or template name: printable, no separators that would make an id ambiguous. */
 static boolean component(String s){
  if(s==null||s.isEmpty()||s.length()>200)return false;
  for(int i=0;i<s.length();i++){char c=s.charAt(i);if(c<=' '||c>'~'||c=='/'||c=='"'||c=='\\')return false;}
  return true;
 }

 private static String sha256(String s){
  try{
   byte[] d=MessageDigest.getInstance("SHA-256").digest(s.getBytes(StandardCharsets.UTF_8));
   StringBuilder b=new StringBuilder();for(byte x:d)b.append(String.format("%02x",x&0xff));return b.toString();
  }catch(NoSuchAlgorithmException e){throw new IllegalStateException(e);}
 }
}
