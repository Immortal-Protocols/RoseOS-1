package com.android.wm.shell;
public final class TrayActivityMirrorTest {
 public static void main(String[] args){
  if(!AgentStatusControls.backgroundActivity("working:3100",4,100).equals("4:working:3100"))throw new AssertionError("working");
  if(!AgentStatusControls.backgroundActivity("terminal:3100",4,200).equals("4:terminal:3100"))throw new AssertionError("terminal deadline preserved");
  for(String value:new String[]{null,"","still:0","working:3101","working:99","working:100:extra","working:nope"})
   if(!AgentStatusControls.backgroundActivity(value,4,100).isEmpty())throw new AssertionError("invalid "+value);
  if(!AgentStatusControls.backgroundActivity("working:3100",-1,100).isEmpty())throw new AssertionError("unknown boot");
  System.out.println("TrayActivityMirrorTest: 10 checks passed");
 }
}
