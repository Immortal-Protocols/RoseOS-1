package dev.pixel.voice;

import java.util.Arrays;
import java.util.HashSet;
import java.util.List;

/**
 * Project 2: the phone's check of a rose-layout-v1 result against catalog cat-1, the page's saved state and
 * request handling, against the approved HiFi r2 behaviour (prototypes/rose-hifi/HANDOFF-WORKSPACE.md at
 * 6fcf8c9b). Pure JVM; no model, no device.
 */
public final class WorkspaceTest {
 static int checks;
 static void expect(boolean condition,String message){checks++;if(!condition)throw new AssertionError(message);}

 /** The registry the phone resolved for a request: real, opaque ids (OS Product's formats). */
 static WorkspaceLayout.Registry registry(String version){
  WorkspaceLayout.Registry r=new WorkspaceLayout.Registry(version);
  for(String a:new String[]{"app:clock","app:notes","app:music","app:firefox","app:mail","app:calendar","app:photos","app:files","app:editor"})r.add(WorkspaceLayout.APPS,a);
  r.add(WorkspaceLayout.NOTIFICATIONS,"n:jamie").add(WorkspaceLayout.CONTENT,"c:pasta").add(WorkspaceLayout.CONTENT,"c:salmon").add(WorkspaceLayout.CONTENT,"c:soup");
  for(int i=0;i<12;i++)r.add(WorkspaceLayout.CONTENT,"c:item"+i);
  r.action("act:timer","deeplink","app:clock").action("act:play","shortcut","app:music").action("act:reply","notification_action","n:jamie")
   .action("task:grocery",WorkspaceLayout.AGENT_TASK,"app:notes").action("task:reply-jamie",WorkspaceLayout.AGENT_TASK,"n:jamie")
   .action("act:open-recipe","launch","c:pasta");
  return r;
 }
 static final WorkspaceLayout.Registry REG=registry("s-1");

 static String ok(String sections){return result("r1","s-1","Cooking tonight",sections);}
 static String result(String requestId,String version,String title,String sections){
  return "{\"contract\":\"rose-layout-v1\",\"requestId\":\""+requestId+"\",\"status\":\"ok\",\"layout\":{\"snapshotVersion\":\""+version
    +"\",\"catalogVersion\":\"cat-1\",\"title\":\""+title+"\",\"sections\":["+sections+"]},\"model\":{\"id\":\"m\"},\"timing\":{\"composeMs\":650}}";
 }
 static final String GRID="{\"component\":\"grid\",\"title\":\"Kitchen apps\",\"items\":[\"app:clock\",\"app:notes\",\"app:music\"],\"actions\":[]}";
 static final String BUTTONS="{\"component\":\"button\",\"title\":\"Quick actions\",\"items\":[\"act:timer\",\"act:play\",\"task:grocery\"]}";
 static final String CAROUSEL="{\"component\":\"carousel\",\"title\":\"Recipes\",\"items\":[\"c:pasta\",\"c:salmon\",\"c:soup\"],\"actions\":[]}";
 static final String CARD="{\"component\":\"card\",\"title\":\"Jamie\",\"items\":[\"n:jamie\"],\"actions\":[\"act:reply\",\"task:reply-jamie\"]}";
 static final String LIST="{\"component\":\"list\",\"title\":\"Saved\",\"items\":[\"c:soup\"],\"actions\":[]}";

 static WorkspaceLayout check(WorkspaceState s,String result)throws WorkspaceLayout.Invalid{return WorkspaceLayout.check(result,"r1",REG,s.keys());}
 static String fails(String result){
  try{WorkspaceLayout.check(result,"r1",REG,new WorkspaceState().keys());return null;}catch(WorkspaceLayout.Invalid e){return e.failure;}
 }
 static List<String> shape(WorkspaceLayout l){String[] t=new String[l.sections.size()];for(int i=0;i<t.length;i++)t[i]=l.sections.get(i).component+":"+l.sections.get(i).title;return Arrays.asList(t);}
 static String sections(String component,String prefix,int n){StringBuilder b=new StringBuilder();for(int i=0;i<n;i++)b.append(i==0?"":",").append("{\"component\":\"list\",\"title\":\""+prefix+i+"\",\"items\":[\"c:item"+i+"\"]}");return b.toString();}

 public static void main(String[] args)throws Exception{
  contract();available();pins();undoAndRecents();storage();composer();
  System.out.println("WorkspaceTest: "+checks+" checks passed");
 }

 static void contract()throws Exception{
  WorkspaceState s=new WorkspaceState();
  WorkspaceLayout l=check(s,ok(GRID+","+BUTTONS+","+CAROUSEL+","+CARD));
  expect(l.sections.size()==4&&l.title.equals("Cooking tonight")&&l.snapshotVersion.equals("s-1"),"cat-1's HiFi components are accepted with real ids");
  expect(l.sections.get(0).key.equals("k1")&&l.sections.get(3).key.equals("k4"),"section keys are the phone's own, never the composer's");
  expect(l.sections.get(3).actions.equals(Arrays.asList("act:reply","task:reply-jamie")),"a card keeps up to two of its own actions");

  // A failed result passes its fixed token through; anything else unusable is invalid_output.
  expect("model_unavailable".equals(fails("{\"contract\":\"rose-layout-v1\",\"requestId\":\"r1\",\"status\":\"failed\",\"failure\":\"model_unavailable\"}")),
    "a failed result keeps its own category");
  expect("invalid_output".equals(fails("{\"contract\":\"rose-layout-v1\",\"requestId\":\"r1\",\"status\":\"failed\",\"failure\":\"The model said no\"}")),
    "a failure that is not a fixed token is never shown: invalid_output");
  expect("invalid_output".equals(fails("Here is your layout: "+ok(GRID))),"prose around the JSON is invalid_output");
  expect("invalid_output".equals(fails(ok(GRID)+ok(GRID))),"two objects are invalid_output");
  expect("invalid_output".equals(fails(ok(GRID).replace("\"status\":\"ok\"","\"status\":\"ok\",\"status\":\"ok\""))),"a duplicate key is invalid_output");
  expect("invalid_output".equals(fails(ok(GRID).replace("\"timing\"","\"style\""))),"an unknown top-level field is invalid_output");
  expect("bad_request".equals(fails(result("r9","s-1","T",GRID))),"a result for another request is refused");
  expect("bad_request".equals(fails(ok(GRID).replace("rose-layout-v1","rose-layout-v2"))),"another contract is refused");
  expect("catalog_mismatch".equals(fails(ok(GRID).replace("cat-1","cat-2"))),"another catalog is catalog_mismatch");
  expect("stale_snapshot".equals(fails(result("r1","s-0","T",GRID))),"a layout composed for an older snapshot is stale_snapshot");
  expect("invalid_output".equals(fails(ok("{\"component\":\"webview\",\"title\":\"A\",\"items\":[\"app:clock\"]}"))),"a component outside cat-1 is invalid_output");
  expect("invalid_output".equals(fails(ok(GRID.replace("\"actions\":[]","\"color\":\"red\"")))),"an unknown section field is invalid_output (no styling)");

  // Bounds.
  expect("over_limit".equals(fails(ok(sections("list","L",7)))),"more than 6 sections is over_limit");
  expect("invalid_output".equals(fails(ok(""))),"no sections is invalid_output");
  expect("over_limit".equals(fails(result("r1","s-1","A page title far longer than forty characters","" +GRID))),"a long page title is over_limit");
  expect("over_limit".equals(fails(ok(GRID.replace("Kitchen apps","A section title longer than thirty")))),"a long section title is over_limit");
  expect("invalid_output".equals(fails(ok(GRID.replace("Kitchen apps","Kitchen\\u0007")))),"a control character is invalid_output, even at the end");
  expect("invalid_output".equals(fails(ok("{\"component\":\"grid\",\"title\":\"A\",\"items\":[\"app:clock\"]}"))),"a grid of one is under its minimum");
  expect("over_limit".equals(fails(ok("{\"component\":\"button\",\"title\":\"A\",\"items\":[\"act:timer\",\"act:play\",\"task:grocery\",\"act:reply\",\"act:open-recipe\"]}"))),
    "more than 4 buttons in a row is over_limit");
  expect("over_limit".equals(fails(ok("{\"component\":\"card\",\"title\":\"A\",\"items\":[\"n:jamie\",\"c:pasta\"]}"))),"a card holds one item");

  // References: only ids the phone resolved, of the kinds the component allows.
  expect("unknown_ref".equals(fails(ok("{\"component\":\"grid\",\"title\":\"A\",\"items\":[\"app:clock\",\"com.example/.Main\"]}"))),
    "an id the phone did not give is unknown_ref (a component name is not an id)");
  expect("disallowed_ref".equals(fails(ok("{\"component\":\"list\",\"title\":\"A\",\"items\":[\"app:clock\"]}"))),"an app in a list is disallowed_ref");
  expect("disallowed_ref".equals(fails(ok("{\"component\":\"grid\",\"title\":\"A\",\"items\":[\"app:clock\",\"task:grocery\"]}"))),
    "a Rose task never goes in a grid (only in a button row or as a card action)");
  expect("disallowed_ref".equals(fails(ok("{\"component\":\"grid\",\"title\":\"A\",\"items\":[\"app:clock\",\"app:notes\"],\"actions\":[\"act:timer\"]}"))),
    "a grid takes no actions");
  expect("disallowed_ref".equals(fails(ok("{\"component\":\"card\",\"title\":\"A\",\"items\":[\"n:jamie\"],\"actions\":[\"act:timer\"]}"))),
    "a card's actions must target its own item");
  expect("duplicate_ref".equals(fails(ok(GRID+",{\"component\":\"button\",\"title\":\"B\",\"items\":[\"app:clock\"]}"))),"an id twice on a page is duplicate_ref");
  StringBuilder full=new StringBuilder();
  for(int i=0;i<3;i++)full.append(i==0?"":",").append("{\"component\":\"grid\",\"title\":\"G"+i+"\",\"items\":["+apps(i*3,3)+"]}");
  expect(fails(ok(full.toString()))==null,"nine distinct apps over three grids pass");
 }
 static String apps(int from,int n){String[] a={"app:clock","app:notes","app:music","app:firefox","app:mail","app:calendar","app:photos","app:files","app:editor"};
  StringBuilder b=new StringBuilder();for(int i=from;i<from+n;i++)b.append(i==from?"":",").append("\"").append(a[i]).append("\"");return b.toString();}

 static void available()throws Exception{
  WorkspaceState s=new WorkspaceState();
  WorkspaceLayout l=check(s,ok(GRID+","+CARD+","+CAROUSEL));
  WorkspaceLayout now=l.available(new HashSet<>(Arrays.asList("app:clock","app:notes","act:reply","c:pasta","c:salmon","c:soup")));
  expect(shape(now).equals(Arrays.asList("grid:Kitchen apps","carousel:Recipes")),"a card whose notification went away is left out of what is shown");
  expect(now.sections.get(0).items.equals(Arrays.asList("app:clock","app:notes")),"a removed app is left out of its grid");
  expect(now.sections.get(0).key.equals(l.sections.get(0).key),"what is shown keeps the saved keys (pins still apply)");
  expect(l.sections.size()==3,"the saved layout itself is unchanged: nothing reshuffles");
  expect(l.available(new HashSet<>(Arrays.asList("app:clock")))==null,"nothing usable left: nothing to show (the page keeps its controls)");
 }

 static void pins()throws Exception{
  WorkspaceState s=new WorkspaceState();
  s.commit(check(s,ok(GRID+","+BUTTONS+","+LIST)),"dinner");
  String pinnedKey=s.layout().sections.get(1).key;
  expect(s.togglePin(pinnedKey)&&s.pinned(pinnedKey),"a section of the layout can be pinned");
  expect(!s.togglePin("k999"),"a key not on the page cannot be pinned");

  // Regenerate: the pinned section stays at index 1; only the sections around it change.
  s.commit(check(s,ok(CAROUSEL+","+CARD+",{\"component\":\"grid\",\"title\":\"Other\",\"items\":[\"app:mail\",\"app:files\"]}")),"dinner");
  expect(s.layout().sections.get(1).key.equals(pinnedKey)&&s.layout().sections.size()==4,"a pinned section keeps its exact position");
  expect(shape(s.layout()).equals(Arrays.asList("carousel:Recipes","button:Quick actions","card:Jamie","grid:Other")),
    "the unpinned sections around it are the new layout's, in order");

  // A pinned section past the end of a shorter layout goes to the end (the HiFi's min(index, length)).
  WorkspaceState t=new WorkspaceState();
  t.commit(check(t,ok(GRID+","+BUTTONS+","+LIST)),"work");
  String last=t.layout().sections.get(2).key;t.togglePin(last);
  t.commit(check(t,ok(CAROUSEL)),"work");
  expect(t.layout().sections.size()==2&&t.layout().sections.get(1).key.equals(last),"a pin beyond a shorter layout lands at its end");

  // The same content in the new layout is not shown twice beside the pin.
  WorkspaceState u=new WorkspaceState();
  u.commit(check(u,ok(GRID+","+LIST)),"x");String k=u.layout().sections.get(0).key;u.togglePin(k);
  u.commit(check(u,ok(GRID+","+CAROUSEL)),"x");
  int grids=0;for(WorkspaceLayout.Section c:u.layout().sections)if(c.component.equals("grid"))grids++;
  expect(grids==1&&u.layout().sections.get(0).key.equals(k),"a new section with a pinned section's content is dropped");

  // Pins never push the page past its bound: unpinned sections from the end give way.
  WorkspaceState v=new WorkspaceState();
  v.commit(check(v,ok(sections("list","L",6))),"r");
  for(int i=0;i<3;i++)v.togglePin(v.layout().sections.get(i).key);
  v.commit(check(v,ok(sections("list","M",6))),"r");
  expect(v.layout().sections.size()==WorkspaceLayout.MAX_SECTIONS&&v.layout().sections.get(0).title.equals("L0")&&v.layout().sections.get(2).title.equals("L2"),
    "with pins the page still holds at most 6 sections, pins first where they were");
 }

 static void undoAndRecents()throws Exception{
  WorkspaceState s=new WorkspaceState();
  expect(!s.undo()&&!s.canUndo(),"nothing to undo on a new page");
  s.commit(check(s,ok(GRID)),"dinner");
  expect(!s.canUndo(),"the first layout has nothing before it");
  String first=s.layout().sections.get(0).key;
  s.togglePin(first);
  s.commit(check(s,ok(LIST)),"unwind");
  expect(s.request().equals("unwind")&&s.undoDepth()==2,"pin and commit are each one undo step");
  expect(s.undo()&&s.request().equals("dinner")&&s.pinned(first)&&s.layout().sections.size()==1,
    "undo restores the layout, its request and its pins exactly");
  expect(s.undo()&&!s.pinned(first),"undo steps back through pins too");

  for(int i=0;i<25;i++)s.commit(check(s,ok(LIST)),"r"+i);
  expect(s.undoDepth()==WorkspaceState.UNDO,"undo keeps the last 20 steps");

  List<WorkspaceState.Entry> recent=s.recent();
  expect(recent.size()==WorkspaceState.RECENT&&recent.get(0).request.equals("r24")&&recent.get(2).request.equals("r22"),"three recent layouts, newest first");
  s.commit(check(s,ok(GRID)),"r23");
  expect(s.recent().get(0).request.equals("r23")&&s.recent().size()==3&&s.recent().get(1).request.equals("r24"),"asking again moves a request to the front once");
  WorkspaceLayout before=s.layout();
  expect(s.reopen(1)&&s.request().equals("r24")&&s.layout()!=before,"a recent chip reopens its layout");
  expect(s.undo()&&s.layout()==before,"reopening is an undo step");
  expect(!s.reopen(7),"there is no fourth recent");
 }

 static void storage()throws Exception{
  WorkspaceState s=new WorkspaceState();
  s.commit(check(s,ok(GRID+","+BUTTONS+","+CARD)),"dinner \"tonight\"");
  String pin=s.layout().sections.get(2).key;s.togglePin(pin);
  s.commit(check(s,ok(CAROUSEL)),"unwind");
  s.actionResult("act:timer","started");
  String saved=s.toJson();
  WorkspaceState r=WorkspaceState.fromJson(saved);
  expect(r.toJson().equals(saved),"a reload restores the state exactly (layout, request, pins, undo, recents, actions)");
  expect(r.request().equals("unwind")&&r.pinned(pin)&&r.undoDepth()==2&&r.recent().size()==2&&"started".equals(r.actionResult("act:timer")),
    "the restored state reads back the same");
  WorkspaceLayout next=check(r,ok(LIST));
  for(WorkspaceLayout.Section c:next.sections)for(WorkspaceLayout.Section o:r.layout().sections)expect(!c.key.equals(o.key),"keys keep counting after a reload: a pin never jumps to a new section");
  expect(r.undo()&&r.request().equals("dinner \"tonight\""),"quotes in a request survive storage");

  expect(WorkspaceState.fromJson(null).layout()==null&&WorkspaceState.fromJson("").layout()==null,"no saved state: an empty page");
  expect(WorkspaceState.fromJson("{not json").layout()==null,"a corrupt store gives an empty page, never a crash");
  expect(WorkspaceState.fromJson("{\"v\":2,\"layout\":null}").layout()==null,"an unknown version is not read");
  expect(WorkspaceState.fromJson(saved.replace("\"component\":\"carousel\"","\"component\":\"webview\"")).layout()==null,"a damaged current layout is not shown");
  WorkspaceState d=WorkspaceState.fromJson(saved.replace("\"undo\":[{\"layout\":{","\"undo\":[{\"layout\":{\"bad\":1,"));
  expect(d.layout()!=null&&d.undoDepth()==1,"a damaged undo step is skipped alone");
  expect(!WorkspaceState.fromJson(saved.replace("\"pins\":[","\"pins\":[\"k404\",")).pinned("k404"),"a pin for a section that is not there is dropped");
  WorkspaceLayout repeated=WorkspaceLayout.fromMap(MiniJson.parse("{\"snapshotVersion\":\"s\",\"title\":\"T\",\"sections\":[{\"key\":\"k1\",\"component\":\"grid\",\"title\":\"A\",\"items\":[\"a\"],\"actions\":[]},{\"key\":\"k1\",\"component\":\"grid\",\"title\":\"B\",\"items\":[\"b\"],\"actions\":[]}]}"));
  expect(repeated==null,"a stored layout with a repeated key is not read");
 }

 static void composer()throws Exception{
  WorkspaceState s=new WorkspaceState();WorkspaceComposer c=new WorkspaceComposer(s);
  expect(c.begin("   ",false)==-1&&c.phase()==WorkspaceComposer.Phase.IDLE,"an empty request is not sent");
  StringBuilder huge=new StringBuilder();for(int i=0;i<501;i++)huge.append('a');
  expect(c.begin(huge.toString(),false)==-1,"an oversized request is not sent");

  int g1=c.begin("dinner",false);String r1=c.requestId();
  expect(c.busy()&&c.status().equals("Composing on this phone…")&&c.trigger().equals("direct_text"),"typed text composes as direct_text");
  int g2=c.begin("work",false);String r2=c.requestId();
  expect(!r1.equals(r2),"each page request has its own id");
  expect(c.finish(g1,result(r1,"s-1","T",GRID),REG)==WorkspaceComposer.Outcome.IGNORED&&s.layout()==null,"a newer request supersedes: the late result is ignored");
  expect(c.finish(g2,result(r1,"s-1","T",GRID),REG)==WorkspaceComposer.Outcome.REJECTED&&"bad_request".equals(c.lastFailure()),
    "a result answering another request id is refused");
  int g2b=c.begin("work",false);
  expect(c.finish(g2b,result(c.requestId(),"s-1","Work",BUTTONS),REG)==WorkspaceComposer.Outcome.COMMITTED&&s.request().equals("work")&&!c.busy(),"the current result is committed");
  expect(c.status().equals("“work”"),"the status echoes the request of the layout on screen");
  expect(c.finish(g2b,result(c.requestId(),"s-1","T",LIST),REG)==WorkspaceComposer.Outcome.IGNORED,"a second result for the same request is ignored");

  String pinned=s.layout().sections.get(0).key;s.togglePin(pinned);WorkspaceLayout kept=s.layout();
  int g3=c.begin("unwind",false);
  expect(c.finish(g3,"I could not decide.",REG)==WorkspaceComposer.Outcome.REJECTED,"an unusable result is rejected");
  expect(s.layout()==kept&&s.pinned(pinned)&&s.request().equals("work"),"failure keeps the last layout and its pins untouched");
  expect(c.status().equals("Couldn't arrange · kept your last layout")&&"invalid_output".equals(c.lastFailure()),"failure says the last layout was kept");
  int g4=c.begin("unwind",false);
  expect(c.fail(g4,"timeout")==WorkspaceComposer.Outcome.REJECTED&&s.layout()==kept&&"timeout".equals(c.lastFailure()),"a runtime timeout is the same as a failed result");
  expect(c.fail(g4,"timeout")==WorkspaceComposer.Outcome.IGNORED,"a failure is reported once");
  int g4b=c.begin("unwind",false);c.fail(g4b,"something odd");
  expect("invalid_output".equals(c.lastFailure()),"a failure that is not a fixed token is recorded as invalid_output");

  int g5=c.regenerate();
  expect(g5>0&&c.finish(g5,result(c.requestId(),"s-1","Work",LIST),REG)==WorkspaceComposer.Outcome.COMMITTED&&s.request().equals("work")&&s.layout().sections.get(0).key.equals(pinned),
    "Regenerate asks again with the same request, around the pins");
  int g6=c.begin(null,false);
  expect(c.trigger().equals("button")&&c.prompt()==null,"Arrange for me is the button trigger with no prompt");
  c.finish(g6,result(c.requestId(),"s-1","Best guess",CAROUSEL),REG);
  expect(s.request().equals(WorkspaceComposer.ARRANGE)&&c.status().equals("Best guess"),"Arrange for me is a best guess");
  int g7=c.regenerate();
  expect(g7>0&&c.trigger().equals("button")&&c.finish(g7,result(c.requestId(),"s-1","Best guess",CARD),REG)==WorkspaceComposer.Outcome.COMMITTED,"Regenerate repeats a best guess");

  // From a conversation: the app stays in front; only the offer changes. App Core's requestId passes through.
  int g8=c.begin("cooking",true,"srv-42");
  expect(c.trigger().equals("conversational")&&c.requestId().equals("srv-42"),"a conversational request keeps App Core's requestId");
  expect(c.offer()==WorkspaceComposer.Offer.PREPARING&&c.offerText().equals("Preparing workspace: “cooking”"),"it offers while preparing");
  WorkspaceComposer.Outcome o8=c.finish(g8,result("srv-42","s-1","Cooking",GRID),REG);
  expect(c.offer()==WorkspaceComposer.Offer.READY&&c.offerText().equals("Workspace ready: “cooking”"),"then offers Show workspace");
  expect(WorkspaceComposer.routeStatus(o8,null,false).equals("prepared")&&WorkspaceComposer.routeStatus(o8,null,true).equals("shown"),
    "App Core hears prepared off screen and shown on screen, never for a mere submission");
  c.workspaceShown();
  expect(c.offer()==WorkspaceComposer.Offer.NONE&&c.offerText()==null,"the offer goes once the workspace is reached");
  int g9=c.begin("cooking",true,"srv-43");WorkspaceComposer.Outcome o9=c.fail(g9,"model_unavailable");
  expect(c.offerText().equals("Couldn't prepare workspace · kept your last layout"),"a failed conversational request says so and keeps the layout");
  expect(WorkspaceComposer.routeStatus(o9,"model_unavailable",false).equals("unavailable")&&WorkspaceComposer.routeStatus(WorkspaceComposer.Outcome.REJECTED,"invalid_output",false).equals("rejected"),
    "no model is unavailable; any other failure is rejected");
  c.dismissOffer();expect(c.offer()==WorkspaceComposer.Offer.NONE,"the offer can be dismissed");
  c.begin("cooking",true,"srv-44");int g11=c.begin("work",false);
  expect(c.offer()==WorkspaceComposer.Offer.NONE,"a request from the page supersedes a pending offer");
  c.finish(g11,result(c.requestId(),"s-1","Work",BUTTONS),REG);
  expect(c.offer()==WorkspaceComposer.Offer.NONE,"and no stale offer appears afterwards");
 }
}
