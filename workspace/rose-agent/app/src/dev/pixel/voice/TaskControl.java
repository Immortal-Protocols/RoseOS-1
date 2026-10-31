package dev.pixel.voice;

/** Watch and Stop for the selected task, decided from the existing contracts only. Pure logic so it is
 * testable. Watch needs the background controller's live session to belong to the same task id
 * (controller get_state schema 3: status, sessionId, taskId). Stop results come from the backend's
 * POST /task/stop (server/task-stop.mjs request): accepted means stopping, never stopped; the task is
 * stopped only when its own state becomes interrupted. Voice End and mute are not involved. */
final class TaskControl {
 static final String WATCH_NO_TASK="Rose isn't working in an app right now.";
 static final String WATCH_IDLE="Rose isn't running a background workspace right now.";
 static final String WATCH_CHECKING="Rose is checking its background workspace.";
 static final String WATCH_NO_PROVIDER="Rose couldn't find its background workspace on this phone.";
 static final String WATCH_DENIED="Rose wasn't allowed to check its background workspace.";
 static final String WATCH_INVALID="Rose's background workspace gave an answer Rose couldn't read.";
 static final String WATCH_READ_FAILED="Rose couldn't check its background workspace.";
 static final String WATCH_UNKNOWN_STATE="Rose's background workspace is in a state Rose doesn't recognise.";
 static final String WATCH_STARTING="Rose's workspace is still starting.";
 static final String WATCH_CONSENT="Android is waiting for your consent before Rose's workspace can start.";
 static final String WATCH_NOT_ALLOWED="This phone doesn't let Rose use that app in the background.";
 static final String WATCH_STOPPED="Rose's workspace has stopped.";
 static final String WATCH_FAILED="Rose's workspace couldn't start.";
 static final String WATCH_OTHER_TASK="Rose's workspace is busy with another task.";
 static final String WATCH_UNLINKED="Rose's workspace isn't linked to this task.";

 /** Why the controller's state is not usable, as a fixed category: never an exception text or identifier. */
 enum ReadFailure { NONE, PENDING, NO_PROVIDER, DENIED, INVALID_RESPONSE, READ_FAILED }
 /** One read of the background controller's get_state (schema 3: status, sessionId, taskId). When failure is not
  * NONE the fields are empty and say nothing about the workspace. */
 static final class ControllerRead {
  static final ControllerRead PENDING_READ=new ControllerRead("","","",ReadFailure.PENDING);
  final String status,sessionId,taskId;final ReadFailure failure;
  private ControllerRead(String status,String sessionId,String taskId,ReadFailure failure){this.status=status;this.sessionId=sessionId;this.taskId=taskId;this.failure=failure;}
  static ControllerRead failed(ReadFailure failure){return new ControllerRead("","","",failure);}
  /** Validates the three fields as read (null when absent or JSON null). A missing status, an active status without a
   * session or an idle status with one is a malformed answer, never relabelled idle; the provider's own error answer
   * is a failed read. */
  static ControllerRead of(String status,String sessionId,String taskId){
   String session=sessionId==null||"null".equals(sessionId)?"":sessionId,task=taskId==null||"null".equals(taskId)?"":taskId;
   if(status==null||status.isEmpty())return failed(ReadFailure.INVALID_RESPONSE);
   if("error".equals(status))return failed(ReadFailure.READ_FAILED);
   boolean active=ACTIVE.contains(status);
   if(active&&session.isEmpty()||"idle".equals(status)&&!session.isEmpty())return failed(ReadFailure.INVALID_RESPONSE);
   return new ControllerRead(status,session,task,ReadFailure.NONE);
  }
  String key(){return failure+"|"+status+"|"+sessionId+"|"+taskId;}
 }
 private static final java.util.List<String> ACTIVE=java.util.Arrays.asList("ready","blocked","launching");
 /** The provider replied, but not with a readable get_state answer. */
 static final class InvalidReply extends Exception { InvalidReply(){super(null,null,false,false);} }
 /** The controller's provider is not on this phone (or not visible to Rose). */
 static final class NoProvider extends Exception { NoProvider(){super(null,null,false,false);} }
 /** Reads {status, sessionId, taskId} (null when absent or JSON null); returns null when the provider gave no reply. */
 interface StateCall { String[] read() throws Exception; }
 /** The read adapter: runs one get_state call and classifies its outcome. Exceptions are reduced to a category. */
 static ControllerRead readController(StateCall call){
  String[] f;
  try{f=call.read();}
  catch(NoProvider e){return ControllerRead.failed(ReadFailure.NO_PROVIDER);}
  catch(SecurityException e){return ControllerRead.failed(ReadFailure.DENIED);}
  catch(InvalidReply e){return ControllerRead.failed(ReadFailure.INVALID_RESPONSE);}
  catch(Exception e){return ControllerRead.failed(ReadFailure.READ_FAILED);}
  if(f==null||f.length!=3)return ControllerRead.failed(ReadFailure.INVALID_RESPONSE);
  return ControllerRead.of(f[0],f[1],f[2]);
 }

 /** What Watch can do for the task shown now: canWatch, or the one fixed reason it can't (never empty then). */
 static final class WatchState {
  final boolean canWatch;final String reason;
  private WatchState(boolean canWatch,String reason){this.canWatch=canWatch;this.reason=reason;}
 }
 /** Watch opens only a live controller session tagged with this task's id; otherwise the reason says why: no task,
  * the controller could not be read (by category), no workspace running (idle), or the controller's own status.
  * Observational only: it never starts a task or workspace. A session of another task is never offered. */
 static WatchState watchState(String taskId,boolean taskActive,ControllerRead read){
  if(!taskActive||taskId==null||taskId.isEmpty())return new WatchState(false,WATCH_NO_TASK);
  if(read==null)read=ControllerRead.failed(ReadFailure.READ_FAILED);
  switch(read.failure){
   case NONE:break;
   case PENDING:return new WatchState(false,WATCH_CHECKING);
   case NO_PROVIDER:return new WatchState(false,WATCH_NO_PROVIDER);
   case DENIED:return new WatchState(false,WATCH_DENIED);
   case INVALID_RESPONSE:return new WatchState(false,WATCH_INVALID);
   default:return new WatchState(false,WATCH_READ_FAILED);
  }
  switch(read.status){
   case "idle":return new WatchState(false,WATCH_IDLE);
   case "ready":case "blocked":case "launching":
    if(read.taskId.isEmpty())return new WatchState(false,WATCH_UNLINKED);
    return taskId.equals(read.taskId)?new WatchState(true,""):new WatchState(false,WATCH_OTHER_TASK);
   case "starting":case "scheduled":return new WatchState(false,WATCH_STARTING);
   case "consent_required":return new WatchState(false,WATCH_CONSENT);
   case "target_not_allowed":return new WatchState(false,WATCH_NOT_ALLOWED);
   case "stopped":case "closed":case "user_stopped":case "cleanup_failed":return new WatchState(false,WATCH_STOPPED);
   case "failed":case "rejected":return new WatchState(false,WATCH_FAILED);
   default:return new WatchState(false,WATCH_UNKNOWN_STATE);
  }
 }
 /** The same decision from the three fields as read (validated by ControllerRead.of). */
 static WatchState watchState(String taskId,boolean taskActive,String controllerStatus,String sessionId,String controllerTaskId){
  return watchState(taskId,taskActive,ControllerRead.of(controllerStatus,sessionId,controllerTaskId));
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
 static String[] watchBinding(String selectedTaskId,boolean stillSelected,ControllerRead read){
  if(!stillSelected||!watchState(selectedTaskId,true,read).canWatch)return null;
  return new String[]{selectedTaskId,read.sessionId};
 }
 static String[] watchBinding(String selectedTaskId,boolean stillSelected,String controllerStatus,String sessionId,String controllerTaskId){
  return watchBinding(selectedTaskId,stillSelected,ControllerRead.of(controllerStatus,sessionId,controllerTaskId));
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
