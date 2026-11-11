package dev.pixel.voice;
public final class TrayBackgroundActivityTest {
 static int checks;
 static void expect(boolean ok,String message){checks++;if(!ok)throw new AssertionError(message);}
 public static void main(String[] args){
  TrayBackgroundActivity m=new TrayBackgroundActivity();
  WorkspaceOverviewFeed.ActivitySnapshot s=new WorkspaceOverviewFeed.ActivitySnapshot();s.reliable=true;
  expect(m.state(s,100).equals("still:0"),"idle workspace");
  s.running.add("a");s.running.add("b");s.runningUntil=3100;
  expect(m.state(s,100).equals("working:3100"),"concurrent work");
  s.running.remove("a");s.finished.add("a");s.finishedUntil=3200;
  expect(m.state(s,200).equals("working:3100"),"one task done does not settle another");
  s.running.clear();
  expect(m.state(s,300).equals("still:0"),"unobserved b completion cannot settle");
  s.running.add("b");s.runningUntil=3400;m.state(s,400);
  s.running.clear();s.finished.add("b");s.finishedUntil=3500;
  expect(m.state(s,500).equals("terminal:3500"),"observed completion");
  expect(m.state(s,600).equals("terminal:3500"),"poll never refreshes receipt");
  expect(m.state(s,3501).equals("still:0"),"expired terminal");
  s.running.add("c");s.runningUntil=7000;m.state(s,4000);
  s.running.clear();s.reliable=false;
  expect(m.state(s,4100).equals("still:0"),"disconnect/stale never settles");
  System.out.println("TrayBackgroundActivityTest: "+checks+" checks passed");
 }
}
