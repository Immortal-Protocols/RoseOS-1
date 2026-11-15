package dev.pixel.voice;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Calendar;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;

/**
 * Project 2: the rules for real notification and media inputs (WorkspaceNotices), with made-up data only. No
 * notification is read and no permission is involved.
 */
public final class WorkspaceNoticesTest {
 static int checks;
 static void expect(boolean condition,String message){checks++;if(!condition)throw new AssertionError(message);}

 static final String OWN="dev.pixel.voice";
 static final WorkspaceSnapshot.App MESSAGES=new WorkspaceSnapshot.App("com.example.messages","com.example.messages.Main","Messages","social",0);
 static final WorkspaceSnapshot.App MUSIC=new WorkspaceSnapshot.App("com.example.music","com.example.music.Player","Music","audio",0);
 static Map<String,WorkspaceSnapshot.App> apps(){Map<String,WorkspaceSnapshot.App> m=new HashMap<>();m.put(MESSAGES.pkg,MESSAGES);m.put(MUSIC.pkg,MUSIC);return m;}
 static WorkspaceNotices.Posted posted(String key,String pkg,String title,long at,boolean ongoing,boolean summary,boolean secret,WorkspaceNotices.Action... actions){
  return new WorkspaceNotices.Posted(key,pkg,title,at,ongoing,summary,secret,Arrays.asList(actions));
 }
 static WorkspaceNotices.Action plain(String l){return new WorkspaceNotices.Action(l,false);}
 static WorkspaceNotices.Action reply(String l){return new WorkspaceNotices.Action(l,true);}

 public static void main(String[] args){
  consent();filters();actions();media();snapshot();
  System.out.println("WorkspaceNoticesTest: "+checks+" checks passed (made-up data; no notification read, no permission)");
 }

 /** Both consents and a connected listener, or nothing. */
 static void consent(){
  expect(WorkspaceInputs.AVAILABLE.equals(WorkspaceNotices.status(true,true,true)),"access granted, disclosure accepted, connected: available");
  expect(WorkspaceInputs.NOT_GRANTED.equals(WorkspaceNotices.status(false,true,true)),"no Notification access: not granted");
  expect(WorkspaceInputs.NOT_GRANTED.equals(WorkspaceNotices.status(true,false,true)),"access granted but the workspace's disclosure not accepted: not granted");
  expect(WorkspaceInputs.NOT_GRANTED.equals(WorkspaceNotices.status(true,true,false)),"not connected: not granted");
  expect(WorkspaceInputs.NOT_GRANTED.equals(WorkspaceNotices.status(false,false,false)),"neither: not granted");
 }

 static void filters(){
  List<WorkspaceNotices.Posted> in=new ArrayList<>();
  in.add(posted("k1",MESSAGES.pkg,"Jamie: dinner at 7?",5,false,false,false));
  in.add(posted("k2",OWN,"Rose is listening",6,false,false,false));
  in.add(posted("k3",MUSIC.pkg,"Playing",7,true,false,false));
  in.add(posted("k4",MESSAGES.pkg,"3 new messages",8,false,true,false));
  in.add(posted("k5",MESSAGES.pkg,"Bank code",9,false,false,true));
  in.add(posted("k6","com.example.unlisted","From an app not listed",10,false,false,false));
  in.add(posted("k7",MESSAGES.pkg,"  ",11,false,false,false));
  in.add(posted(null,MESSAGES.pkg,"No key",12,false,false,false));
  List<WorkspaceSnapshot.Notice> out=WorkspaceNotices.notices(in,apps(),OWN);
  expect(out.size()==1&&"k1".equals(out.get(0).key)&&out.get(0).app==MESSAGES,"only a listed app's own titled, non-ongoing, non-summary, non-secret notification: "+out.size());
  expect(WorkspaceNotices.notices(null,apps(),OWN).isEmpty()&&WorkspaceNotices.notices(in,null,OWN).isEmpty(),"nothing in, nothing out");
 }

 /** Actions that ask for typed input (a reply) are left out; the plain ones keep their order. */
 static void actions(){
  WorkspaceNotices.Posted p=posted("k",MESSAGES.pkg,"Jamie",1,false,false,false,reply("Reply"),plain("Mark as read"),plain(""),plain("Mute"));
  List<WorkspaceSnapshot.Notice> out=WorkspaceNotices.notices(Arrays.asList(p),apps(),OWN);
  expect(out.get(0).actionLabels.equals(Arrays.asList("Mark as read","Mute")),"plain actions only: "+out.get(0).actionLabels);
  expect(WorkspaceNotices.plainActionIndexes(p.actions).equals(Arrays.asList(1,3)),"nact /0 and /1 map back to the notification's own actions 1 and 3");
 }

 static void media(){
  List<WorkspaceNotices.Playing> in=Arrays.asList(
   new WorkspaceNotices.Playing(MUSIC.pkg,"session-a","Liked songs",true),
   new WorkspaceNotices.Playing(MUSIC.pkg,"session-b","Stopped",false),
   new WorkspaceNotices.Playing("com.example.unlisted","s","Not listed",true),
   new WorkspaceNotices.Playing(OWN,"s","Rose's own",true),
   new WorkspaceNotices.Playing(MUSIC.pkg,"session-c",null,true));
  List<WorkspaceSnapshot.Content> out=WorkspaceNotices.media(in,apps(),OWN);
  expect(out.size()==1&&"media".equals(out.get(0).kind)&&"media".equals(out.get(0).source)&&"Liked songs".equals(out.get(0).label),"only an active, titled session of a listed app");
  String id=out.get(0).id;
  expect(id.matches("[0-9a-f]{16}")&&!id.contains("session")&&!id.contains("example"),"its id is a hash: "+id);
  expect(id.equals(WorkspaceNotices.mediaId(new WorkspaceNotices.Playing(MUSIC.pkg,"session-a","another title",true))),"stable for the same session, whatever its title");
 }

 /** The whole path into the snapshot: titles only, hashed ids, the plain actions. */
 @SuppressWarnings("unchecked")
 static void snapshot(){
  List<WorkspaceSnapshot.Notice> n=WorkspaceNotices.notices(Arrays.asList(posted("0|com.example.messages|1|t|1",MESSAGES.pkg,"Jamie: dinner at 7?",5,false,false,false,reply("Reply"),plain("Mark as read"))),apps(),OWN);
  List<WorkspaceSnapshot.Content> c=WorkspaceNotices.media(Arrays.asList(new WorkspaceNotices.Playing(MUSIC.pkg,"session-a","Liked songs",true)),apps(),OWN);
  WorkspaceSnapshot s=WorkspaceSnapshot.build(Arrays.asList(MESSAGES,MUSIC),Collections.<WorkspaceSnapshot.Shortcut>emptyList(),Collections.<WorkspaceSnapshot.Template>emptyList(),
   Collections.<WorkspaceSnapshot.Task>emptyList(),n,c,new HashSet<String>(),1794600000000L,Calendar.getInstance());
  List<Map<String,Object>> notes=(List<Map<String,Object>>)s.snapshot.get("notifications"),content=(List<Map<String,Object>>)s.snapshot.get("content");
  expect(notes.size()==1&&"Jamie: dinner at 7?".equals(notes.get(0).get("title"))&&content.size()==1&&((String)content.get(0).get("id")).startsWith("c:media/"),"one notification and one media item");
  int nact=0;for(Map<String,Object> a:(List<Map<String,Object>>)s.snapshot.get("actions"))if(((String)a.get("id")).startsWith("nact:")){nact++;expect("Mark as read".equals(a.get("label")),"the plain action only");}
  expect(nact==1,"the reply action is not offered");
  expect(!WorkspaceLayout.json(s.snapshot).contains("0|com.example.messages"),"the raw key never leaves the phone");
 }
}
