package dev.pixel.voice;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Calendar;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Map;

/**
 * Project 2: the snapshot's notification and content inputs (WorkspaceInputs, WorkspaceSnapshot.Notice and
 * Content), with made-up items only. Nothing reads a real notification, and no permission is involved.
 */
public final class WorkspaceInputsTest {
 static int checks;
 static void expect(boolean condition,String message){checks++;if(!condition)throw new AssertionError(message);}

 static final WorkspaceSnapshot.App MESSAGES=new WorkspaceSnapshot.App("com.example.messages","com.example.messages.Main","Messages","social",0);
 static final WorkspaceSnapshot.App MUSIC=new WorkspaceSnapshot.App("com.example.music","com.example.music.Player","Music","audio",0);
 static final WorkspaceSnapshot.App GONE=new WorkspaceSnapshot.App("com.example.gone","com.example.gone.Main","Gone",null,0);
 static final String KEY="0|com.example.messages|7|chat thread|10001";

 static WorkspaceSnapshot build(List<WorkspaceSnapshot.Notice> n,List<WorkspaceSnapshot.Content> c){
  return WorkspaceSnapshot.build(Arrays.asList(MESSAGES,MUSIC),Collections.<WorkspaceSnapshot.Shortcut>emptyList(),Collections.<WorkspaceSnapshot.Template>emptyList(),
   Collections.<WorkspaceSnapshot.Task>emptyList(),n,c,new HashSet<String>(),1794600000000L,Calendar.getInstance());
 }
 static WorkspaceSnapshot.Notice notice(String key,WorkspaceSnapshot.App app,String title,long at,String... actions){return new WorkspaceSnapshot.Notice(key,app,title,at,Arrays.asList(actions));}
 @SuppressWarnings("unchecked") static List<Map<String,Object>> list(WorkspaceSnapshot s,String kind){return (List<Map<String,Object>>)s.snapshot.get(kind);}

 public static void main(String[] args)throws Exception{
  none();notices();content();check();sources();
  System.out.println("WorkspaceInputsTest: "+checks+" checks passed (made-up items; no notification read, no permission)");
 }

 /** Today the phone has no source: both feeds are empty and the status says why. */
 static void none(){
  expect(WorkspaceInputs.SOURCE==WorkspaceInputs.NONE,"the phone reads NONE today");
  expect(WorkspaceInputs.NOT_DECIDED.equals(WorkspaceInputs.SOURCE.notificationStatus())&&WorkspaceInputs.NO_SOURCE.equals(WorkspaceInputs.SOURCE.contentStatus()),"notifications: D1 not decided; content: no source");
  WorkspaceSnapshot s=build(WorkspaceInputs.notices(WorkspaceInputs.SOURCE),WorkspaceInputs.content(WorkspaceInputs.SOURCE));
  expect(list(s,"notifications").isEmpty()&&list(s,"content").isEmpty(),"so the snapshot has none, and the composer reads that as none");
  WorkspaceSnapshot old=WorkspaceSnapshot.build(Arrays.asList(MESSAGES,MUSIC),Collections.<WorkspaceSnapshot.Shortcut>emptyList(),Collections.<WorkspaceSnapshot.Template>emptyList(),
   Collections.<WorkspaceSnapshot.Task>emptyList(),new HashSet<String>(),1794600000000L,Calendar.getInstance());
  expect(old.snapshotVersion.equals(s.snapshotVersion),"with no inputs the snapshot is exactly what it was before (same version)");
 }

 static void notices(){
  List<WorkspaceSnapshot.Notice> in=new ArrayList<>();
  in.add(notice(KEY,MESSAGES,"Jamie: dinner at 7?",2000,"Reply","Mark as read","Mute"));
  in.add(notice("k-older",MUSIC,"Now playing",1000));
  in.add(notice("k-gone",GONE,"From an app not listed",3000));
  in.add(notice("k-blank",MESSAGES,"   ",4000));
  in.add(notice(null,MESSAGES,"No key",5000));
  WorkspaceSnapshot s=build(in,Collections.<WorkspaceSnapshot.Content>emptyList());
  List<Map<String,Object>> n=list(s,"notifications");
  expect(n.size()==2,"only notifications with a key, a title and a listed app: "+n);
  expect("Jamie: dinner at 7?".equals(n.get(0).get("title"))&&Long.valueOf(1).equals(n.get(0).get("recentRank"))&&Long.valueOf(2).equals(n.get(1).get("recentRank")),"most recent first, ranked");
  expect(n.get(0).keySet().equals(new java.util.LinkedHashSet<>(Arrays.asList("id","appId","title","recentRank"))),"exactly rose-layout-v1's fields: no body text, no key: "+n.get(0).keySet());
  String id=(String)n.get(0).get("id");
  expect(id.matches("n:[0-9a-f]{16}"),"the id is a hash: "+id);
  String json=WorkspaceLayout.json(s.snapshot);
  expect(!json.contains("chat thread")&&!json.contains(KEY),"the raw key never enters the snapshot");
  expect(s.notice(id)!=null&&s.notice(id).key.equals(KEY),"but the phone maps the id back to its notification");
  expect(id.equals(list(build(Arrays.asList(notice(KEY,MESSAGES,"changed title",9)),Collections.<WorkspaceSnapshot.Content>emptyList()),"notifications").get(0).get("id")),"the id is stable while the notification exists");
  int nact=0;for(Map<String,Object> a:list(s,"actions"))if(((String)a.get("id")).startsWith("nact:")){nact++;expect("notification_action".equals(a.get("kind"))&&id.equals(a.get("targetId")),"its actions target it");}
  expect(nact==2,"at most 2 actions per notification");
  expect(s.registry.has(id)&&WorkspaceLayout.NOTIFICATIONS.equals(s.registry.kind(id))&&"notification_action".equals(s.registry.actionKind("nact:"+id.substring(2)+"/0")),"registered for the phone's check");
  expect(s.present().contains(id)&&s.present().contains("nact:"+id.substring(2)+"/1"),"present at render time");
  List<WorkspaceSnapshot.Notice> many=new ArrayList<>();for(int i=0;i<20;i++)many.add(notice("k"+i,MESSAGES,"t"+i,i));
  List<Map<String,Object>> kept=list(build(many,Collections.<WorkspaceSnapshot.Content>emptyList()),"notifications");
  expect(kept.size()==WorkspaceSnapshot.MAX_NOTIFICATIONS&&"t19".equals(kept.get(0).get("title")),"at most 12, the most recent");
  expect(!build(in,Collections.<WorkspaceSnapshot.Content>emptyList()).snapshotVersion.equals(build(Collections.<WorkspaceSnapshot.Notice>emptyList(),Collections.<WorkspaceSnapshot.Content>emptyList()).snapshotVersion),"a notification changes the version");
 }

 static void content(){
  List<WorkspaceSnapshot.Content> in=Arrays.asList(
   new WorkspaceSnapshot.Content("music","album-42","media","Liked songs",MUSIC),
   new WorkspaceSnapshot.Content("music","bad id","media","Space in id",MUSIC),
   new WorkspaceSnapshot.Content("music","x","podcast","Unknown kind",MUSIC),
   new WorkspaceSnapshot.Content("music","y","media","Not listed",GONE),
   new WorkspaceSnapshot.Content("music","z","media","",MUSIC));
  WorkspaceSnapshot s=build(Collections.<WorkspaceSnapshot.Notice>emptyList(),in);
  List<Map<String,Object>> c=list(s,"content");
  expect(c.size()==1&&"c:music/album-42".equals(c.get(0).get("id"))&&"media".equals(c.get(0).get("kind"))&&"Liked songs".equals(c.get(0).get("label")),"only a known kind, an unambiguous id, a label and a listed app: "+c);
  expect(s.registry.has("c:music/album-42")&&WorkspaceLayout.CONTENT.equals(s.registry.kind("c:music/album-42"))&&s.present().contains("c:music/album-42"),"registered and present");
 }

 /** A composer's card naming a notification passes the phone's check only while the phone has it. */
 static void check()throws Exception{
  WorkspaceSnapshot s=build(Arrays.asList(notice(KEY,MESSAGES,"Jamie: dinner at 7?",2000,"Reply")),Collections.<WorkspaceSnapshot.Content>emptyList());
  String id=(String)list(s,"notifications").get(0).get("id");String act="nact:"+id.substring(2)+"/0";
  String result="{\"contract\":\"rose-layout-v1\",\"requestId\":\"r\",\"status\":\"ok\",\"layout\":{\"snapshotVersion\":\""+s.snapshotVersion
   +"\",\"catalogVersion\":\"cat-1\",\"title\":\"Evening\",\"sections\":[{\"component\":\"card\",\"title\":\"Jamie\",\"items\":[\""+id+"\"],\"actions\":[\""+act+"\"]}]},\"model\":{\"id\":\"m\"}}";
  WorkspaceLayout l=WorkspaceLayout.check(result,"r",s.registry,new WorkspaceState().keys());
  expect(l.sections.size()==1&&l.sections.get(0).items.get(0).equals(id),"a card with the notification and its action is valid");
  WorkspaceSnapshot none=build(Collections.<WorkspaceSnapshot.Notice>emptyList(),Collections.<WorkspaceSnapshot.Content>emptyList());
  String stale=result.replace(s.snapshotVersion,none.snapshotVersion);
  try{WorkspaceLayout.check(stale,"r",none.registry,new WorkspaceState().keys());expect(false,"a notification the phone does not have is refused");}
  catch(WorkspaceLayout.Invalid e){expect("unknown_ref".equals(e.failure),"refused as unknown_ref: "+e.failure);}
 }

 /** A source counts only when AVAILABLE; one not granted, or one that throws, gives nothing. */
 static void sources(){
  final List<WorkspaceSnapshot.Notice> one=Arrays.asList(notice("k",MESSAGES,"t",1));
  WorkspaceInputs.Source notGranted=fake(WorkspaceInputs.NOT_GRANTED,one);
  WorkspaceInputs.Source available=fake(WorkspaceInputs.AVAILABLE,one);
  WorkspaceInputs.Source broken=new WorkspaceInputs.Source(){
   public String notificationStatus(){return WorkspaceInputs.AVAILABLE;}
   public List<WorkspaceSnapshot.Notice> notices(){throw new SecurityException("access revoked");}
   public String contentStatus(){throw new IllegalStateException();}
   public List<WorkspaceSnapshot.Content> content(){return null;}
  };
  expect(WorkspaceInputs.notices(notGranted).isEmpty(),"not granted: none");
  expect(WorkspaceInputs.notices(available).size()==1,"available: its items");
  expect(WorkspaceInputs.notices(broken).isEmpty()&&WorkspaceInputs.content(broken).isEmpty(),"a source that throws gives none, never a fabrication");
  expect(WorkspaceInputs.notices(null).isEmpty()&&WorkspaceInputs.content(null).isEmpty(),"no source: none");
 }
 static WorkspaceInputs.Source fake(final String status,final List<WorkspaceSnapshot.Notice> n){
  return new WorkspaceInputs.Source(){
   public String notificationStatus(){return status;}
   public List<WorkspaceSnapshot.Notice> notices(){return n;}
   public String contentStatus(){return WorkspaceInputs.NO_SOURCE;}
   public List<WorkspaceSnapshot.Content> content(){return Collections.emptyList();}
  };
 }
}
