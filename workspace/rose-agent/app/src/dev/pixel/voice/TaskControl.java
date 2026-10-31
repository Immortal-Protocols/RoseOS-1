package dev.pixel.voice;

/** Watch and Stop for the selected task, decided from the existing contracts only. Pure logic so it is
 * testable. Watch needs the background controller's live session to belong to the same task id
 * (controller get_state schema 3: status, sessionId, taskId). Stop results come from the backend's
 * POST /task/stop (server/task-stop.mjs request): accepted means stopping, never stopped; the task is
 * stopped only when its own state becomes interrupted. Voice End and mute are not involved. */
final class TaskControl {
 static final String WATCH_NO_TASK="Rose isn't working in an app right now.";
 static final String WATCH_UNAVAILABLE="Rose's workspace isn't available on this phone.";
 static final String WATCH_STARTING="Rose's workspace is still starting.";
 static final String WATCH_CONSENT="Android is waiting for your consent before Rose's workspace can start.";
 static final String WATCH_NOT_ALLOWED="This phone doesn't let Rose use that app in the background.";
 static final String WATCH_STOPPED="Rose's workspace has stopped.";
 static final String WATCH_FAILED="Rose's workspace couldn't start.";
 static final String WATCH_OTHER_TASK="Rose's workspace is busy with another task.";

 /** What Watch can do for the task shown now: canWatch, or the one fixed reason it can't (never empty then). */
 static final class WatchState {
  final boolean canWatch;final String reason;
  private WatchState(boolean canWatch,String reason){this.canWatch=canWatch;this.reason=reason;}
 }
 /** Watch opens only a live controller session tagged with this task's id; otherwise the reason says why, from the
  * controller's own status (Controller.state schema 3). A session of another task is never offered. */
 static WatchState watchState(String taskId,boolean taskActive,String controllerStatus,String sessionId,String controllerTaskId){
  if(!taskActive||taskId==null||taskId.isEmpty())return new WatchState(false,WATCH_NO_TASK);
  String status=controllerStatus==null?"":controllerStatus;
  boolean session=sessionId!=null&&!sessionId.isEmpty()&&!"null".equals(sessionId);
  switch(status){
   case "ready":case "blocked":case "launching":
    if(!session)return new WatchState(false,WATCH_UNAVAILABLE);
    return taskId.equals(controllerTaskId)?new WatchState(true,""):new WatchState(false,WATCH_OTHER_TASK);
   case "starting":case "scheduled":return new WatchState(false,WATCH_STARTING);
   case "consent_required":return new WatchState(false,WATCH_CONSENT);
   case "target_not_allowed":return new WatchState(false,WATCH_NOT_ALLOWED);
   case "stopped":case "closed":case "user_stopped":case "cleanup_failed":return new WatchState(false,WATCH_STOPPED);
   case "failed":case "rejected":return new WatchState(false,WATCH_FAILED);
   default:return new WatchState(false,WATCH_UNAVAILABLE);
  }
 }
 /** The viewer may open only for a live controller session tagged with this task's id (watchState.canWatch). */
 static boolean watchable(String taskId,boolean taskActive,String controllerStatus,String sessionId,String controllerTaskId){
  return watchState(taskId,taskActive,controllerStatus,sessionId,controllerTaskId).canWatch;
 }

 /** Watch binding extras on the viewer intent (APP-OS-WATCH-002). Additive; an unbound launch has neither.
  * The viewer attaches only while the controller's session id and task id both still equal these. */
 static final String EXTRA_WATCH_TASK_ID="dev.pixel.backgroundcontrol.extra.WATCH_TASK_ID";
 static final String EXTRA_WATCH_SESSION_ID="dev.pixel.backgroundcontrol.extra.WATCH_SESSION_ID";
 /** The {taskId, sessionId} to bind Watch to, from controller state read after the tap; null when the task
  * selected at the tap is no longer the one shown, or the live session no longer carries it. Never
  * substitutes the controller's current task for the selected one. */
 static String[] watchBinding(String selectedTaskId,boolean stillSelected,String controllerStatus,String sessionId,String controllerTaskId){
  if(!stillSelected||!watchable(selectedTaskId,true,controllerStatus,sessionId,controllerTaskId))return null;
  return new String[]{selectedTaskId,sessionId};
 }

 enum Outcome { STOPPING, ALREADY_STOPPING, CHANGED, NOT_RUNNING, FAILED }
 final Outcome outcome;final String message;
 private TaskControl(Outcome outcome,String message){this.outcome=outcome;this.message=message;}

 /** httpStatus: 0 when the request failed or timed out before a response. status/worker/workspace are
  * the response fields; duplicate is the repeated-Stop flag. selected is the task id the owner confirmed. */
 static TaskControl stopResult(int httpStatus,String status,String responseTaskId,String worker,String workspace,boolean duplicate,String selected){
  if(httpStatus==0)return new TaskControl(Outcome.FAILED,"Couldn’t reach Rose to stop this task. It may still be running.");
  if(httpStatus!=200)return new TaskControl(Outcome.FAILED,"Rose refused the Stop request ("+httpStatus+"). The task may still be running.");
  if("stale_task".equals(status))return new TaskControl(Outcome.CHANGED,"That task had already changed, so nothing was stopped.");
  if("not_active".equals(status))return new TaskControl(Outcome.NOT_RUNNING,"That task isn’t running any more, so nothing was stopped.");
  if(!"stopping".equals(status)||selected==null||!selected.equals(responseTaskId))
   return new TaskControl(Outcome.FAILED,"Rose gave an unexpected answer. The task may still be running.");
  if(duplicate)return new TaskControl(Outcome.ALREADY_STOPPING,"Stop was already requested. Waiting for Rose to confirm.");
  StringBuilder m=new StringBuilder("Stop requested. Waiting for Rose to confirm it has stopped.");
  if(worker!=null&&worker.startsWith("interrupt_failed"))m.append(" Rose couldn’t interrupt the work directly.");
  if(workspace!=null&&workspace.startsWith("close_failed"))m.append(" The background workspace didn’t confirm it closed.");
  return new TaskControl(Outcome.STOPPING,m.toString());
 }

 /** Whether a pending Stop for `selected` has reached its authoritative end in the task projection. */
 static boolean settled(String selected,String currentId,String currentState){
  if(selected==null||!selected.equals(currentId))return true; // that task is gone or replaced: nothing left to wait for
  return "interrupted".equals(currentState)||"completed".equals(currentState)||"failed".equals(currentState);
 }
}
