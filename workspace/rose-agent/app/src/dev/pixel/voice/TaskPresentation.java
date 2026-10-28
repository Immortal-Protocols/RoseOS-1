package dev.pixel.voice;

/** What the conversation surface presents for the current task, from the server's single-task
 * projection (server/launcher-task.mjs snapshot: id, title, state, activity, progress, result).
 * Pure logic so it is testable. Presentation only: it never starts, stops or evaluates work, and it
 * shows nothing at all when there is no task. Layout follows Rose-Fold-v3's presented output: a small
 * eyebrow, the task title, and the formatted body. */
final class TaskPresentation {
 final boolean visible; final String id,eyebrow,title,body,note;
 private TaskPresentation(boolean visible,String id,String eyebrow,String title,String body,String note){this.visible=visible;this.id=id;this.eyebrow=eyebrow;this.title=title;this.body=body;this.note=note;}
 static final TaskPresentation NONE=new TaskPresentation(false,"","","","","");

 /** current=false when the snapshot's current task is null. available=false when the task stream is
  * stale: the last received task stays, marked as such, rather than vanishing or being guessed at. */
 static TaskPresentation of(boolean current,String id,String state,String title,String activity,String progress,String result,boolean available){
  if(!current||id==null||id.isEmpty())return NONE;
  String s=state==null?"":state,eyebrow,body;
  switch(s){
   case "running":eyebrow="Rose";body=first(activity,progress);break;
   case "checking":eyebrow="Rose";body="Checking the result before finishing.";break;
   case "stopping":eyebrow="Stopping…";body="";break;
   case "completed":eyebrow="For you";body=nz(result);break;
   case "failed":eyebrow="Failed";body=nz(result);break;
   case "interrupted":eyebrow="Stopped";body=nz(result);break;
   default:eyebrow="Status unavailable";body="";
  }
  String t=nz(title).isEmpty()?"Working on your request":title;
  return new TaskPresentation(true,id,eyebrow,t,body,available?"":"Connection lost. Showing the last task received.");
 }
 boolean active(){return eyebrow.equals("Rose")||eyebrow.equals("Stopping…");}
 private static String nz(String v){return v==null?"":v.trim();}
 private static String first(String a,String b){return !nz(a).isEmpty()?nz(a):nz(b);}
}
