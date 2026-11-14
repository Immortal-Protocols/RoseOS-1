package dev.pixel.voice;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.LauncherApps;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.content.pm.ShortcutInfo;
import android.os.Process;
import android.provider.AlarmClock;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Project 2: what the workspace may offer the composer, read from the phone (workspace-contract.md, "Registry
 * and ids").
 * <ul>
 *   <li><b>Apps:</b> the launchable activities Home lists, without Rose Agent itself, under the labels Home shows.</li>
 *   <li><b>Shortcuts:</b> launcher shortcuts from LauncherApps, which Rose Agent may read only while it holds the
 *   HOME role. Nothing new is requested; without that role there are no shortcuts.</li>
 *   <li><b>Intent templates:</b> Rose's existing timer and alarms intents, each resolved to the app that handles it.</li>
 * </ul>
 * There are no notifications (D1 is undecided), no content source, and no Rose tasks until App Core's route is
 * enabled. Everything is read on the page's worker thread.
 */
final class WorkspaceSources {
 static final int SHORTCUTS_PER_APP=2;

 /** The Rose intent templates the workspace offers, by template name. */
 static final Map<String,String> TEMPLATES;
 static{
  Map<String,String> t=new LinkedHashMap<>();
  t.put("timer","Start a timer");t.put("alarms","Alarms");
  TEMPLATES=Collections.unmodifiableMap(t);
 }

 /** The intent behind a template, or null. */
 static Intent template(String name){
  switch(name){
   case "timer":return new Intent(AlarmClock.ACTION_SET_TIMER).putExtra(AlarmClock.EXTRA_SKIP_UI,false);
   case "alarms":return new Intent(AlarmClock.ACTION_SHOW_ALARMS);
   default:return null;
  }
 }

 /** One read of the phone, with what each id opens. */
 static final class Read {
  final WorkspaceSnapshot snapshot;
  final Map<String,ResolveInfo> apps;final Map<String,ShortcutInfo> shortcuts;
  Read(WorkspaceSnapshot snapshot,Map<String,ResolveInfo> apps,Map<String,ShortcutInfo> shortcuts){this.snapshot=snapshot;this.apps=apps;this.shortcuts=shortcuts;}
 }

 static Read read(Context context,Set<String> pinned){
  PackageManager pm=context.getPackageManager();
  Map<String,ResolveInfo> resolved=new LinkedHashMap<>();ResolveInfo rose=null;
  List<WorkspaceSnapshot.App> apps=new ArrayList<>();Map<String,WorkspaceSnapshot.App> byKey=new HashMap<>(),byPackage=new HashMap<>();
  List<ResolveInfo> launchable=pm.queryIntentActivities(new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER),0);
  final Map<ResolveInfo,String> labels=new HashMap<>();
  for(ResolveInfo r:launchable)labels.put(r,AppLabels.label(pm,r.activityInfo.packageName,String.valueOf(r.loadLabel(pm))));
  List<ResolveInfo> sorted=new ArrayList<>(launchable);
  Collections.sort(sorted,(a,b)->labels.get(a).compareToIgnoreCase(labels.get(b)));
  for(ResolveInfo r:sorted){
   String pkg=r.activityInfo.packageName,activity=r.activityInfo.name;
   if(pkg.equals(context.getPackageName())){if(rose==null)rose=r;continue;}
   WorkspaceSnapshot.App app=new WorkspaceSnapshot.App(pkg,activity,labels.get(r),category(r.activityInfo.applicationInfo),0);
   String key=pkg+"/"+activity;if(byKey.containsKey(key))continue;
   apps.add(app);byKey.put(key,app);if(!byPackage.containsKey(pkg))byPackage.put(pkg,app);
   resolved.put(WorkspaceSnapshot.appId(app),r);
  }

  List<WorkspaceSnapshot.Shortcut> shortcuts=new ArrayList<>();Map<String,ShortcutInfo> shortcutInfo=new HashMap<>();
  LauncherApps launcher=context.getSystemService(LauncherApps.class);
  try{
   if(launcher!=null&&launcher.hasShortcutHostPermission()){
    LauncherApps.ShortcutQuery q=new LauncherApps.ShortcutQuery().setQueryFlags(LauncherApps.ShortcutQuery.FLAG_MATCH_MANIFEST|LauncherApps.ShortcutQuery.FLAG_MATCH_DYNAMIC);
    List<ShortcutInfo> found=launcher.getShortcuts(q,Process.myUserHandle());
    Map<String,Integer> perApp=new HashMap<>();
    if(found!=null)for(ShortcutInfo s:found){
     if(!s.isEnabled())continue;
     ComponentName c=s.getActivity();
     WorkspaceSnapshot.App app=c==null?null:byKey.get(c.getPackageName()+"/"+c.getClassName());
     if(app==null)app=byPackage.get(s.getPackage());
     if(app==null)continue;
     Integer n=perApp.get(app.pkg);if(n!=null&&n>=SHORTCUTS_PER_APP)continue;perApp.put(app.pkg,n==null?1:n+1);
     CharSequence label=s.getShortLabel();if(label==null)continue;
     WorkspaceSnapshot.Shortcut sc=new WorkspaceSnapshot.Shortcut(app,s.getId(),label.toString());
     shortcuts.add(sc);shortcutInfo.put("sc:"+app.pkg+"/"+s.getId(),s);
    }
   }
  }catch(SecurityException|IllegalStateException e){/* not the HOME role, or the user is locked: no shortcuts */}

  List<WorkspaceSnapshot.Template> templates=new ArrayList<>();
  for(Map.Entry<String,String> t:TEMPLATES.entrySet()){
   Intent intent=template(t.getKey());ResolveInfo r=intent==null?null:pm.resolveActivity(intent,PackageManager.MATCH_DEFAULT_ONLY);
   if(r==null||r.activityInfo==null)continue;
   WorkspaceSnapshot.App app=byPackage.get(r.activityInfo.packageName);
   if(app!=null)templates.add(new WorkspaceSnapshot.Template(t.getKey(),t.getValue(),app));
  }

  // Rose tasks only through WorkspaceTaskGate: none unless explicitly enabled, and no template exists today.
  // A task's target is Rose Agent's own entry, offered only when there is a task to target it.
  List<WorkspaceSnapshot.Task> tasks=new ArrayList<>();
  List<WorkspaceSnapshot.Task> offered=WorkspaceTaskGate.offered(context);
  if(!offered.isEmpty()&&rose!=null){
   WorkspaceSnapshot.App self=new WorkspaceSnapshot.App(rose.activityInfo.packageName,rose.activityInfo.name,"Rose Agent",null,0);
   apps.add(self);resolved.put(WorkspaceSnapshot.appId(self),rose);
   for(WorkspaceSnapshot.Task t:offered)tasks.add(new WorkspaceSnapshot.Task(t.templateId,t.label,t.ask,t.goal,self));
  }
  WorkspaceSnapshot snapshot=WorkspaceSnapshot.build(apps,shortcuts,templates,tasks,pinned,System.currentTimeMillis(),Calendar.getInstance());
  return new Read(snapshot,resolved,shortcutInfo);
 }

 /** Android's own app category, as a plain word the composer can use, or null. */
 static String category(ApplicationInfo info){
  if(info==null)return null;
  switch(info.category){
   case ApplicationInfo.CATEGORY_GAME:return "games";
   case ApplicationInfo.CATEGORY_AUDIO:return "audio";
   case ApplicationInfo.CATEGORY_VIDEO:return "video";
   case ApplicationInfo.CATEGORY_IMAGE:return "photos";
   case ApplicationInfo.CATEGORY_SOCIAL:return "social";
   case ApplicationInfo.CATEGORY_NEWS:return "news";
   case ApplicationInfo.CATEGORY_MAPS:return "maps";
   case ApplicationInfo.CATEGORY_PRODUCTIVITY:return "productivity";
   case ApplicationInfo.CATEGORY_ACCESSIBILITY:return "accessibility";
   default:return null;
  }
 }
}
