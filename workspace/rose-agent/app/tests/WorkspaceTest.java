package dev.pixel.voice;

import java.util.Arrays;
import java.util.HashSet;
import java.util.List;

/**
 * Project 2: the workspace page's contract check, saved state and request handling, against the approved
 * HiFi r2 behaviour (prototypes/rose-hifi/HANDOFF-WORKSPACE.md at 6fcf8c9b). Pure JVM; no model, no device.
 */
public final class WorkspaceTest {
 static int checks;
 static void expect(boolean condition,String message){checks++;if(!condition)throw new AssertionError(message);}

 static final WorkspaceLayout.Ids IDS=new WorkspaceLayout.Ids(
   new HashSet<>(Arrays.asList("a1","a2","a3","a4","a5","a6","a7","a8","a9")),
   new HashSet<>(Arrays.asList("x1","x2","x3","x4","x5")),
   new HashSet<>(Arrays.asList("n1")));

 static String reply(String cards){return "{\"rose_layout\":\"workspace-v1\",\"title\":\"Cooking tonight\",\"cards\":["+cards+"]}";}
 static final String APPS="{\"type\":\"apps\",\"title\":\"Kitchen apps\",\"items\":[\"a1\",\"a2\"]}";
 static final String ACTIONS="{\"type\":\"actions\",\"title\":\"Quick actions\",\"items\":[\"x1\",\"x2\"]}";
 static final String LIST="{\"type\":\"list\",\"title\":\"Ingredients\",\"check\":true,\"items\":[\"Spaghetti 200 g\",\"Lemon\"]}";
 static final String CAROUSEL="{\"type\":\"carousel\",\"title\":\"Listen\",\"items\":[\"a3\",\"x3\"]}";
 static final String NOTE="{\"type\":\"notification\",\"title\":\"Jamie\",\"items\":[\"n1\"]}";

 static WorkspaceLayout parse(WorkspaceState s,String reply)throws WorkspaceLayout.Invalid{return WorkspaceLayout.parse(reply,IDS,s.keys());}
 static String rejects(String reply){
  try{WorkspaceLayout.parse(reply,IDS,new WorkspaceState().keys());return null;}catch(WorkspaceLayout.Invalid e){return e.reason;}
 }
 static List<String> types(WorkspaceLayout l){String[] t=new String[l.cards.size()];for(int i=0;i<t.length;i++)t[i]=l.cards.get(i).type+":"+l.cards.get(i).title;return Arrays.asList(t);}

 public static void main(String[] args)throws Exception{
  contract();pins();undoAndRecents();storage();composer();
  System.out.println("WorkspaceTest: "+checks+" checks passed");
 }

 static void contract()throws Exception{
  WorkspaceState s=new WorkspaceState();
  WorkspaceLayout l=parse(s,reply(APPS+","+ACTIONS+","+LIST+","+CAROUSEL+","+NOTE));
  expect(l.cards.size()==5&&l.title.equals("Cooking tonight"),"every card type of the HiFi vocabulary is accepted");
  expect(l.cards.get(0).key.equals("k1")&&l.cards.get(4).key.equals("k5"),"card keys are the phone's own, never the composer's");
  expect(l.cards.get(2).check&&l.cards.get(2).items.get(0).equals("Spaghetti 200 g"),"a checklist keeps its plain text items");

  // One JSON object only, as local-agent-v1: prose, fences and extra objects reject the whole reply.
  expect("malformed".equals(rejects("Here is your layout: "+reply(APPS))),"prose around the JSON rejects");
  expect("malformed".equals(rejects("```json\n"+reply(APPS)+"\n```")),"a code fence rejects");
  expect("malformed".equals(rejects(reply(APPS)+reply(APPS))),"two objects reject");
  expect("malformed".equals(rejects("{\"rose_layout\":\"workspace-v1\",\"rose_layout\":\"workspace-v1\"}")),"a duplicate key rejects");
  expect("contract".equals(rejects("{\"rose_layout\":\"workspace-v0\",\"title\":\"T\",\"cards\":["+APPS+"]}")),"another contract version rejects");
  expect("unknown_field.style".equals(rejects("{\"rose_layout\":\"workspace-v1\",\"title\":\"T\",\"style\":\"x\",\"cards\":["+APPS+"]}")),
    "an unknown field rejects (the composer cannot style the page)");
  expect(rejects(reply("{\"type\":\"apps\",\"title\":\"A\",\"items\":[\"a1\"],\"color\":\"red\"}"))!=null,"an unknown card field rejects");
  expect(rejects(reply("{\"type\":\"webview\",\"title\":\"A\",\"items\":[\"a1\"]}"))!=null,"a card type outside the vocabulary rejects");

  // Bounds.
  StringBuilder nine=new StringBuilder();for(int i=0;i<9;i++)nine.append(i==0?"":",").append(LIST);
  expect("cards.count".equals(rejects(reply(nine.toString()))),"more than 8 cards reject");
  expect("cards.count".equals(rejects(reply(""))),"no cards rejects");
  expect(rejects("{\"rose_layout\":\"workspace-v1\",\"title\":\"A title far too long for the status line\",\"cards\":["+APPS+"]}")!=null,"a long page title rejects");
  expect(rejects(reply("{\"type\":\"apps\",\"title\":\"A\",\"items\":[\"a1\",\"a2\",\"a3\",\"a4\",\"a5\",\"a6\",\"a7\",\"a8\",\"a9\"]}"))!=null,"more than 8 apps reject");
  expect(rejects(reply("{\"type\":\"actions\",\"title\":\"A\",\"items\":[\"x1\",\"x2\",\"x3\",\"x4\",\"x5\"]}"))!=null,"more than 4 actions reject");
  expect(rejects(reply("{\"type\":\"carousel\",\"title\":\"A\",\"items\":[\"a1\"]}"))!=null,"a one-item carousel rejects");
  expect(rejects(reply("{\"type\":\"list\",\"title\":\"A\",\"items\":[\"line\\u0007\"]}"))!=null,"control characters in text reject");
  expect(rejects(reply("{\"type\":\"list\",\"title\":\"A\",\"check\":\"yes\",\"items\":[\"x\"]}"))!=null,"a non-boolean check rejects");

  // The composer names ids only: never a package, component or URI, and never another kind's id.
  expect(rejects(reply("{\"type\":\"apps\",\"title\":\"A\",\"items\":[\"com.example/.Main\"]}"))!=null,"a component name is not an id");
  expect(rejects(reply("{\"type\":\"actions\",\"title\":\"A\",\"items\":[\"a1\"]}"))!=null,"an app id in an actions card rejects");
  expect(rejects(reply("{\"type\":\"notification\",\"title\":\"A\",\"items\":[\"x1\"]}"))!=null,"an action id in a notification card rejects");
  expect(rejects(reply(APPS+",{\"type\":\"carousel\",\"title\":\"B\",\"items\":[\"a1\",\"a3\"]}"))!=null,"an id used twice on one page rejects");

  // Stale references (the app was removed, the notification went away) are dropped, not fatal.
  l=parse(s,reply("{\"type\":\"apps\",\"title\":\"A\",\"items\":[\"a1\",\"a99\"]}"));
  expect(l.cards.get(0).items.equals(Arrays.asList("a1")),"a stale id is dropped from its card");
  l=parse(s,reply("{\"type\":\"notification\",\"title\":\"Gone\",\"items\":[\"n9\"]},"+APPS));
  expect(l.cards.size()==1&&l.cards.get(0).type.equals("apps"),"a card left empty is dropped");
  l=parse(s,reply("{\"type\":\"carousel\",\"title\":\"A\",\"items\":[\"a1\",\"a99\"]},"+LIST));
  expect(l.cards.size()==1&&l.cards.get(0).type.equals("list"),"a carousel left with one item is dropped");
  expect("nothing_available".equals(rejects(reply("{\"type\":\"apps\",\"title\":\"A\",\"items\":[\"a98\",\"a99\"]}"))),
    "a reply with nothing left to show rejects (the last layout stays)");
 }

 static void pins()throws Exception{
  WorkspaceState s=new WorkspaceState();
  s.commit(parse(s,reply(APPS+","+ACTIONS+","+LIST)),"dinner");
  String pinnedKey=s.layout().cards.get(1).key;
  expect(s.togglePin(pinnedKey)&&s.pinned(pinnedKey),"a card of the layout can be pinned");
  expect(!s.togglePin("k999"),"a key not on the page cannot be pinned");

  // Regenerate: the pinned card stays at index 1; only the cards around it change.
  s.commit(parse(s,reply(CAROUSEL+","+NOTE+",{\"type\":\"apps\",\"title\":\"Other\",\"items\":[\"a5\"]}")),"dinner");
  expect(s.layout().cards.get(1).key.equals(pinnedKey)&&s.layout().cards.size()==4,"a pinned card keeps its exact position");
  expect(types(s.layout()).equals(Arrays.asList("carousel:Listen","actions:Quick actions","notification:Jamie","apps:Other")),
    "the unpinned cards around it are the new layout's, in order");

  // A pinned card past the end of a shorter layout goes to the end (the HiFi's min(index, length)).
  WorkspaceState t=new WorkspaceState();
  t.commit(parse(t,reply(APPS+","+ACTIONS+","+LIST)),"work");
  String last=t.layout().cards.get(2).key;t.togglePin(last);
  t.commit(parse(t,reply(CAROUSEL)),"work");
  expect(t.layout().cards.size()==2&&t.layout().cards.get(1).key.equals(last),"a pin beyond a shorter layout lands at its end");

  // The same content in the new layout is not shown twice beside the pin.
  WorkspaceState u=new WorkspaceState();
  u.commit(parse(u,reply(APPS+","+LIST)),"x");String k=u.layout().cards.get(0).key;u.togglePin(k);
  u.commit(parse(u,reply(APPS+","+CAROUSEL)),"x");
  int apps=0;for(WorkspaceLayout.Card c:u.layout().cards)if(c.type.equals("apps"))apps++;
  expect(apps==1&&u.layout().cards.get(0).key.equals(k),"a new card with a pinned card's content is dropped");

  // Pins never push the page past its bound: unpinned cards from the end give way.
  WorkspaceState v=new WorkspaceState();
  StringBuilder eight=new StringBuilder();for(int i=0;i<8;i++)eight.append(i==0?"":",").append("{\"type\":\"list\",\"title\":\"L"+i+"\",\"items\":[\"x\"]}");
  v.commit(parse(v,reply(eight.toString())),"r");
  for(int i=0;i<3;i++)v.togglePin(v.layout().cards.get(i).key);
  StringBuilder other=new StringBuilder();for(int i=0;i<8;i++)other.append(i==0?"":",").append("{\"type\":\"list\",\"title\":\"M"+i+"\",\"items\":[\"y\"]}");
  v.commit(parse(v,reply(other.toString())),"r");
  expect(v.layout().cards.size()==WorkspaceLayout.MAX_CARDS&&v.layout().cards.get(0).title.equals("L0")&&v.layout().cards.get(2).title.equals("L2"),
    "with pins the page still holds at most 8 cards, pins first where they were");
 }

 static void undoAndRecents()throws Exception{
  WorkspaceState s=new WorkspaceState();
  expect(!s.undo()&&!s.canUndo(),"nothing to undo on a new page");
  s.commit(parse(s,reply(APPS)),"dinner");
  expect(!s.canUndo(),"the first layout has nothing before it");
  String first=s.layout().cards.get(0).key;
  s.togglePin(first);
  s.commit(parse(s,reply(LIST)),"unwind");
  expect(s.request().equals("unwind")&&s.undoDepth()==2,"pin and commit are each one undo step");
  expect(s.undo()&&s.request().equals("dinner")&&s.pinned(first)&&s.layout().cards.size()==1,
    "undo restores the layout, its request and its pins exactly");
  expect(s.undo()&&!s.pinned(first),"undo steps back through pins too");

  for(int i=0;i<25;i++)s.commit(parse(s,reply(LIST)),"r"+i);
  expect(s.undoDepth()==WorkspaceState.UNDO,"undo keeps the last 20 steps");

  List<WorkspaceState.Entry> recent=s.recent();
  expect(recent.size()==WorkspaceState.RECENT&&recent.get(0).request.equals("r24")&&recent.get(2).request.equals("r22"),"three recent layouts, newest first");
  s.commit(parse(s,reply(APPS)),"r23");
  expect(s.recent().get(0).request.equals("r23")&&s.recent().size()==3&&s.recent().get(1).request.equals("r24"),"asking again moves a request to the front once");
  WorkspaceLayout before=s.layout();
  expect(s.reopen(1)&&s.request().equals("r24")&&s.layout()!=before,"a recent chip reopens its layout");
  expect(s.undo()&&s.layout()==before,"reopening is an undo step");
  expect(!s.reopen(7),"there is no fourth recent");
 }

 static void storage()throws Exception{
  WorkspaceState s=new WorkspaceState();
  s.commit(parse(s,reply(APPS+","+ACTIONS+","+LIST)),"dinner \"tonight\"");
  String pin=s.layout().cards.get(2).key;s.togglePin(pin);
  s.commit(parse(s,reply(CAROUSEL)),"unwind");
  s.actionResult("x1","started");
  String saved=s.toJson();
  WorkspaceState r=WorkspaceState.fromJson(saved);
  expect(r.toJson().equals(saved),"a reload restores the state exactly (layout, request, pins, undo, recents, actions)");
  expect(r.request().equals("unwind")&&r.pinned(pin)&&r.undoDepth()==2&&r.recent().size()==2&&"started".equals(r.actionResult("x1")),
    "the restored state reads back the same");
  WorkspaceLayout next=parse(r,reply(NOTE));
  for(WorkspaceLayout.Card c:next.cards)for(WorkspaceLayout.Card o:r.layout().cards)expect(!c.key.equals(o.key),"keys keep counting after a reload: a pin never jumps to a new card");
  expect(r.undo()&&r.request().equals("dinner \"tonight\""),"quotes in a request survive storage");

  expect(WorkspaceState.fromJson(null).layout()==null&&WorkspaceState.fromJson("").layout()==null,"no saved state: an empty page");
  expect(WorkspaceState.fromJson("{not json").layout()==null,"a corrupt store gives an empty page, never a crash");
  expect(WorkspaceState.fromJson("{\"v\":2,\"layout\":null}").layout()==null,"an unknown version is not read");
  expect(WorkspaceState.fromJson(saved.replace("\"type\":\"carousel\"","\"type\":\"webview\"")).layout()==null,"a damaged current layout is not shown");
  String damagedUndo=saved.replace("\"undo\":[{\"layout\":{","\"undo\":[{\"layout\":{\"bad\":1,");
  WorkspaceState d=WorkspaceState.fromJson(damagedUndo);
  expect(d.layout()!=null&&d.undoDepth()==1,"a damaged undo step is skipped alone");
  String strayPin=saved.replace("\"pins\":[","\"pins\":[\"k404\",");
  expect(!WorkspaceState.fromJson(strayPin).pinned("k404"),"a pin for a card that is not there is dropped");
  WorkspaceLayout repeated=WorkspaceLayout.fromMap(MiniJson.parse("{\"title\":\"T\",\"cards\":[{\"key\":\"k1\",\"type\":\"apps\",\"title\":\"A\",\"items\":[\"a1\"]},{\"key\":\"k1\",\"type\":\"apps\",\"title\":\"B\",\"items\":[\"a2\"]}]}"));
  expect(repeated==null,"a stored layout with a repeated key is not read");
 }

 static void composer()throws Exception{
  WorkspaceState s=new WorkspaceState();WorkspaceComposer c=new WorkspaceComposer(s);
  expect(c.begin("   ",false)==-1&&c.phase()==WorkspaceComposer.Phase.IDLE,"an empty request is not sent");
  StringBuilder huge=new StringBuilder();for(int i=0;i<501;i++)huge.append('a');
  expect(c.begin(huge.toString(),false)==-1,"an oversized request is not sent");

  int g1=c.begin("dinner",false);
  expect(c.busy()&&c.status().equals("Composing on this phone…"),"composing shows in the status line");
  int g2=c.begin("work",false);
  expect(c.finish(g1,reply(APPS),IDS)==WorkspaceComposer.Outcome.IGNORED&&s.layout()==null,"a newer request supersedes: the late reply is ignored");
  expect(c.finish(g2,reply(ACTIONS),IDS)==WorkspaceComposer.Outcome.COMMITTED&&s.request().equals("work")&&!c.busy(),"the current reply is committed");
  expect(c.status().equals("“work”"),"the status echoes the request of the layout on screen");
  expect(c.finish(g2,reply(LIST),IDS)==WorkspaceComposer.Outcome.IGNORED,"a second reply to the same request is ignored");

  String pinned=s.layout().cards.get(0).key;s.togglePin(pinned);WorkspaceLayout kept=s.layout();
  int g3=c.begin("unwind",false);
  expect(c.finish(g3,"I could not decide.",IDS)==WorkspaceComposer.Outcome.REJECTED,"a malformed reply is rejected");
  expect(s.layout()==kept&&s.pinned(pinned)&&s.request().equals("work"),"failure keeps the last layout and its pins untouched");
  expect(c.status().equals("Couldn't arrange · kept your last layout")&&"malformed".equals(c.lastError()),"failure says the last layout was kept");
  int g4=c.begin("unwind",false);
  expect(c.fail(g4,"timeout")==WorkspaceComposer.Outcome.REJECTED&&s.layout()==kept&&"timeout".equals(c.lastError()),"a model timeout is the same as a rejected reply");
  expect(c.fail(g4,"timeout")==WorkspaceComposer.Outcome.IGNORED,"a failure is reported once");

  int g5=c.regenerate();
  expect(g5>0&&c.finish(g5,reply(LIST),IDS)==WorkspaceComposer.Outcome.COMMITTED&&s.request().equals("work")&&s.layout().cards.get(0).key.equals(pinned),
    "Regenerate asks again with the same request, around the pins");
  int g6=c.begin(null,false);c.finish(g6,reply(CAROUSEL),IDS);
  expect(s.request().equals(WorkspaceComposer.ARRANGE)&&c.status().equals("Best guess"),"Arrange for me is a best guess");
  int g7=c.regenerate();
  expect(g7>0&&c.finish(g7,reply(NOTE),IDS)==WorkspaceComposer.Outcome.COMMITTED&&s.request().equals(WorkspaceComposer.ARRANGE),"Regenerate repeats a best guess");

  // From inside an app: the app stays in front; only the offer card changes.
  int g8=c.begin("cooking",true);
  expect(c.offer()==WorkspaceComposer.Offer.PREPARING&&c.offerText().equals("Preparing workspace: “cooking”"),"a request from an app offers while preparing");
  c.finish(g8,reply(APPS),IDS);
  expect(c.offer()==WorkspaceComposer.Offer.READY&&c.offerText().equals("Workspace ready: “cooking”"),"then offers Show workspace");
  c.workspaceShown();
  expect(c.offer()==WorkspaceComposer.Offer.NONE&&c.offerText()==null,"the offer goes once the workspace is reached");
  int g9=c.begin("cooking",true);c.fail(g9,"timeout");
  expect(c.offerText().equals("Couldn't prepare workspace · kept your last layout"),"a failed request from an app says so and keeps the layout");
  c.dismissOffer();expect(c.offer()==WorkspaceComposer.Offer.NONE,"the offer can be dismissed");
  c.begin("cooking",true);int g11=c.begin("work",false);
  expect(c.offer()==WorkspaceComposer.Offer.NONE,"a request from the page supersedes a pending offer");
  c.finish(g11,reply(ACTIONS),IDS);
  expect(c.offer()==WorkspaceComposer.Offer.NONE,"and no stale offer appears afterwards");
 }
}
