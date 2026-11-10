package dev.pixel.voice;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.Set;
public final class BackgroundActivityTest {
 static int checks;
 static void expect(boolean ok,String why){checks++;if(!ok)throw new AssertionError(why);}
 static Set<String> keys(String... values){return new HashSet<>(Arrays.asList(values));}
 static String receipt(String key,int sample,String state,boolean executing){
  return "{\"key\":\""+key+"\",\"connection\":\"c\",\"server\":\"s\",\"contract\":1,\"background\":true,\"status\":\""+state+"\",\"activity\":{\"version\":1,\"sample\":"+sample+",\"executing\":"+executing+"}}";
 }
 public static void main(String[] args){
  BackgroundActivity m=new BackgroundActivity();Set<String> none=Collections.emptySet();
  expect(m.frame(none,none,true,0,true)==0,"idle still");
  m.frame(keys("a"),none,true,100,true);
  float moving=m.frame(keys("a"),none,true,700,true);
  expect(moving>.59f&&moving<=.6f,"gentle bounded travel");
  m.frame(keys("a","b"),none,true,800,true);
  m.frame(keys("b"),keys("a"),true,900,true);
  expect(m.phase==BackgroundActivity.Phase.WORKING,"one finished, another executing");
  float settling=m.frame(none,keys("a","b"),true,1000,true);
  expect(settling!=0&&m.phase==BackgroundActivity.Phase.SETTLING,"actual completion settles");
  expect(m.frame(none,keys("a","b"),true,1240,true)==0,"settles exactly once");
  m.frame(keys("a"),none,true,2000,true);
  expect(m.frame(none,none,false,2600,true)==0&&m.phase==BackgroundActivity.Phase.STILL,"disconnect does not celebrate completion");
  m.frame(keys("b"),none,true,3000,true);
  expect(m.frame(none,none,true,3600,true)==0,"waiting does not settle");
  expect(m.frame(keys("a"),none,true,4000,false)==0,"reduced motion still");
  expect(m.frame(none,keys("a"),true,4600,false)==0,"reduced completion still");
  expect(!BackgroundActivity.fresh(-1,0)&&!BackgroundActivity.fresh(100,99),"missing/future clocks rejected");
  expect(BackgroundActivity.fresh(100,3100)&&!BackgroundActivity.fresh(100,3101),"stale threshold");

  WorkspaceOverviewFeed.clearForTest();
  expect(!WorkspaceOverviewFeed.activitySnapshot(WorkspaceOverviewFeed.activityClock()).reliable,"no evidence");
  WorkspaceOverviewFeed.accept("["+receipt("a",1,"running",true)+"]");
  long now=WorkspaceOverviewFeed.activityClock();
  WorkspaceOverviewFeed.ActivitySnapshot snap=WorkspaceOverviewFeed.activitySnapshot(now);
  expect(snap.reliable&&snap.running.size()==1,"real executing task");
  expect(!WorkspaceOverviewFeed.activitySnapshot(now+3100).reliable,"stale task no longer executing");
  WorkspaceOverviewFeed.accept("["+receipt("a",1,"running",true)+"]");
  expect(!WorkspaceOverviewFeed.activitySnapshot(now+3100).reliable,"same sample cannot renew freshness");
  WorkspaceOverviewFeed.accept("["+receipt("a",2,"running",false)+"]");
  snap=WorkspaceOverviewFeed.activitySnapshot(WorkspaceOverviewFeed.activityClock());
  expect(snap.running.isEmpty()&&snap.finished.isEmpty(),"waiting workspace never executing or completed");
  WorkspaceOverviewFeed.accept("["+receipt("a",3,"done",false)+"]");
  snap=WorkspaceOverviewFeed.activitySnapshot(WorkspaceOverviewFeed.activityClock());
  expect(snap.finished.size()==1&&snap.running.isEmpty(),"explicit terminal receipt");
  WorkspaceOverviewFeed.accept("["+receipt("a",4,"running",true)+","+receipt("b",4,"running",true)+"]");
  snap=WorkspaceOverviewFeed.activitySnapshot(WorkspaceOverviewFeed.activityClock());
  expect(snap.running.size()==2,"concurrent tasks");
  WorkspaceOverviewFeed.accept("["+receipt("a",5,"running",true).replace("\"background\":true","\"background\":false")+"]");
  expect(WorkspaceOverviewFeed.activitySnapshot(WorkspaceOverviewFeed.activityClock()).running.isEmpty(),"foreground task not background work");
  WorkspaceOverviewFeed.accept("["+receipt("a",6,"running",true).replace("\"contract\":1","\"contract\":0")+"]");
  expect(!WorkspaceOverviewFeed.activitySnapshot(WorkspaceOverviewFeed.activityClock()).reliable,"legacy/unscoped task not authoritative");
  WorkspaceOverviewFeed.accept("["+receipt("a",7,"running",true)+"]");
  WorkspaceOverviewFeed.accept("bad json");
  expect(!WorkspaceOverviewFeed.activitySnapshot(WorkspaceOverviewFeed.activityClock()).reliable,"malformed clears activity not readable history");
  expect(WorkspaceOverviewFeed.tasks().size()==1,"malformed preserves readable history");
  WorkspaceOverviewFeed.accept("[]");
  expect(!WorkspaceOverviewFeed.activitySnapshot(WorkspaceOverviewFeed.activityClock()).reliable,"disconnect clear");
  System.out.println("BackgroundActivityTest: "+checks+" checks passed");
 }
}
