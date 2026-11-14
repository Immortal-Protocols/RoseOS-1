package dev.pixel.voice;

import java.util.Map;

/**
 * Project 2: the line under a Rose task's button on the workspace page. It is built from what the tap actually
 * got back (WorkspaceTaskClient.Outcome) and from the phone's own task stream: VoiceService.taskStateJson, the
 * server's single-task snapshot {"version":1,"current":{id,state,title,...}}, which Home's TaskStatusCard
 * also shows.
 *
 * The page never assumes success. A started task follows the stream only when the reply named its taskId and
 * the stream's current task is that one. App Core's taskId is often null, because the conversation decides
 * what work to start. Then the line stays "Started · in Rose's work" and never claims finished. Pure.
 */
final class WorkspaceTaskCard {
 private WorkspaceTaskCard() {}

 /** The line for a tap's outcome, given the task stream (raw JSON, or null) and whether that stream is current. */
 static String line(WorkspaceTaskClient.Outcome outcome,String taskStateJson,boolean streamAvailable){
  if(outcome==null)return "Rose task · asks first";
  if(!outcome.started()||outcome.taskId==null||!streamAvailable)return outcome.status();
  Map<?,?> current=current(taskStateJson);
  if(current==null||!outcome.taskId.equals(current.get("id")))return outcome.status();
  Object state=current.get("state");
  switch(state instanceof String?(String)state:""){
   case "running":case "checking":return "Rose is working";
   case "stopping":return "Stopping…";
   case "completed":return "Finished · in Rose's work";
   case "failed":return "Failed · in Rose's work";
   case "interrupted":return "Stopped · in Rose's work";
   default:return outcome.status();
  }
 }

 private static Map<?,?> current(String raw){
  if(raw==null)return null;
  try{Object o=MiniJson.parse(raw);if(!(o instanceof Map))return null;Map<?,?> m=(Map<?,?>)o;
   if(!Long.valueOf(1).equals(m.get("version")))return null;Object c=m.get("current");return c instanceof Map?(Map<?,?>)c:null;}
  catch(RuntimeException e){return null;}
 }
}
