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
 * characters. Notifications and content stay empty until D1 and a content source exist. The version is a
 * hash of every entry, so it changes whenever an entry does and a layout made for another snapshot is
 * stale_snapshot.
 */
final class WorkspaceSnapshot {
 static final int LABEL=60,MAX_APPS=120,MAX_ACTIONS=60;
 static final int TASK_LABEL=60,TASK_ASK=200,TASK_GOAL=300;

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

 final String snapshotVersion;
 final Map<String,Object> snapshot;
 final WorkspaceLayout.Registry registry;
 private final Map<String,App> appsById;
 private final Map<String,Shortcut> shortcutsById;
 private final Map<String,Template> templatesById;
 private final Map<String,Task> tasksById;

 private WorkspaceSnapshot(String version,Map<String,Object> snapshot,WorkspaceLayout.Registry registry,Map<String,App> apps,Map<String,Shortcut> shortcuts,Map<String,Template> templates,Map<String,Task> tasks){
  this.snapshotVersion=version;this.snapshot=snapshot;this.registry=registry;
  this.appsById=apps;this.shortcutsById=shortcuts;this.templatesById=templates;this.tasksById=tasks;
 }

 App app(String id){return appsById.get(id);}
 Shortcut shortcut(String id){return shortcutsById.get(id);}
 Template template(String id){return templatesById.get(id);}
 Task task(String id){return tasksById.get(id);}
 /** Every id present now, for WorkspaceLayout.available at render time. */
 Set<String> present(){
  Set<String> s=new LinkedHashSet<>(appsById.keySet());
  s.addAll(shortcutsById.keySet());s.addAll(templatesById.keySet());s.addAll(tasksById.keySet());
  return s;
 }

 static String appId(App a){return "app:"+a.pkg+"/"+a.activity;}

 /**
  * Builds the snapshot. tasks is empty until App Core's task route is enabled on the server; pinned are the
  * item ids of the page's pinned sections, kept only when present. nowMs and calendar give createdAtMs and
  * timeOfDay, which are not part of the version.
  */
 static WorkspaceSnapshot build(List<App> apps,List<Shortcut> shortcuts,List<Template> templates,List<Task> tasks,Set<String> pinned,long nowMs,Calendar calendar){
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

  Set<String> known=new LinkedHashSet<>(appIds.keySet());for(String[] a:actions)known.add(a[0]);
  List<Object> pins=new ArrayList<>();for(String p:pinned)if(known.contains(p))pins.add(p);

  Map<String,Object> versioned=new LinkedHashMap<>();
  versioned.put("catalogVersion",WorkspaceLayout.CATALOG);
  versioned.put("apps",appEntries);versioned.put("notifications",new ArrayList<Object>());
  versioned.put("content",new ArrayList<Object>());versioned.put("actions",actionEntries);versioned.put("pinned",pins);
  String version="s-"+sha256(WorkspaceLayout.json(versioned)).substring(0,12);

  Map<String,Object> snapshot=new LinkedHashMap<>();
  snapshot.put("snapshotVersion",version);snapshot.put("catalogVersion",WorkspaceLayout.CATALOG);
  snapshot.put("createdAtMs",nowMs);snapshot.put("timeOfDay",timeOfDay(calendar.get(Calendar.HOUR_OF_DAY)));
  snapshot.put("apps",appEntries);snapshot.put("notifications",versioned.get("notifications"));
  snapshot.put("content",versioned.get("content"));snapshot.put("actions",actionEntries);snapshot.put("pinned",pins);

  registry=new WorkspaceLayout.Registry(version);
  for(String id:appIds.keySet())registry.add(WorkspaceLayout.APPS,id);
  for(String[] a:actions)registry.action(a[0],a[1],a[2]);
  return new WorkspaceSnapshot(version,snapshot,registry,appIds,shortcutIds,templateIds,taskIds);
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
