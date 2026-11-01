package dev.pixel.voice;

/** The workspace card's live preview (#225), as pure logic so it is testable on the host. It consumes the controller's
 * read-only provider call("preview") from PR340 (os/background-control/agent-pip Controller.preview, PreviewPolicy):
 * params {sessionId, taskId}; success {status:"ok", sessionId, taskId, targetPackage, capturedAt, width, height,
 * mimeType:"image/jpeg", imageBase64}, at most 320x720 and 48 KiB; otherwise one fixed refusal word.
 *
 * <ul>
 * <li>A request is made only for the overview's verified target: the binding {controllerTaskId, sessionId,
 * targetPackage} of the task row whose own explicit binding equals the live controller read
 * (WorkspaceOverview.Model.watchRow). No binding, no request: it is never inferred from whichever workspace is open.</li>
 * <li>Only while the overview is visible (open and resumed), one in flight at a time, at most once per INTERVAL_MS.</li>
 * <li>A response is shown only when it answers the request in flight, says ok, names exactly the requested
 * session, task and package (which must still be the current target), is a bounded JPEG, and was captured after the
 * request was sent and after the frame already shown. Anything else is dropped; a refusal clears the frame.</li>
 * <li>cancel() (Back, pause, teardown, lock, a replaced or changed binding) invalidates any request in flight and drops
 * the frame. A frame older than MAX_AGE_MS is not shown.</li>
 * </ul>
 * The image is held in memory for the visible card only: never stored, logged or uploaded. The decoded image is an
 * opaque object here (an android Bitmap in the app), so this class has no Android types. Times are wall-clock
 * milliseconds on this phone, the clock the controller stamps capturedAt with. */
final class WorkspaceOverviewPreview {
 static final long INTERVAL_MS=1000,MAX_AGE_MS=3000,CLOCK_SLACK_MS=1000;
 static final int MAX_WIDTH=320,MAX_HEIGHT=720,MAX_BYTES=48*1024;
 /** The base64 length of MAX_BYTES, for refusing an oversized answer before decoding it. */
 static final int MAX_BASE64=(MAX_BYTES+2)/3*4;

 /** One preview request: the exact binding it names and when it was sent. Only the request object currently in flight
  * is answered; cancel() forgets it, so a late answer to an earlier request never matches. */
 static final class Request {
  final long sentAt;final WorkspaceOverview.Binding binding;
  Request(WorkspaceOverview.Binding binding,long sentAt){this.binding=binding;this.sentAt=sentAt;}
  /** The provider params: the live session and the controller's task id (a workspace generation). */
  String sessionId(){return binding.sessionId;}
  String taskId(){return binding.controllerTaskId;}
 }
 /** One provider answer, as read and decoded off the UI thread. image is null unless the JPEG decoded within bounds. */
 static final class Response {
  final String status,sessionId,taskId,targetPackage,mimeType;final long capturedAt;final int width,height,bytes;final Object image;
  Response(String status,String sessionId,String taskId,String targetPackage,long capturedAt,int width,int height,String mimeType,int bytes,Object image){
   this.status=nz(status);this.sessionId=nz(sessionId);this.taskId=nz(taskId);this.targetPackage=nz(targetPackage);this.capturedAt=capturedAt;
   this.width=width;this.height=height;this.mimeType=nz(mimeType);this.bytes=bytes;this.image=image;}
  static Response failed(){return new Response("error","","","",0,0,0,"",0,null);}
 }
 /** A preview accepted for one binding. */
 static final class Frame {
  final WorkspaceOverview.Binding binding;final long capturedAt;final int width,height;final Object image;
  Frame(WorkspaceOverview.Binding binding,long capturedAt,int width,int height,Object image){this.binding=binding;this.capturedAt=capturedAt;this.width=width;this.height=height;this.image=image;}
 }

 private Request inFlight;private Frame frame;private long lastSent;private WorkspaceOverview.Binding target;
 /** Why the last answer was not shown (a fixed word: a refusal, or "rejected"), for tests; never shown with identifiers. */
 String lastDrop="";

 /** The next request to send, or null. visible: the overview is open and its activity resumed. target: the verified
  * binding (null without one). A changed target cancels first, so nothing from the old binding is ever shown. */
 synchronized Request next(boolean visible,WorkspaceOverview.Binding target,long now){
  if(!sameTarget(target,this.target)){cancel();this.target=target;}
  if(!visible||target==null||!target.complete()){if(!visible||target==null)cancel();return null;}
  if(inFlight!=null||lastSent>0&&now-lastSent<INTERVAL_MS)return null;
  inFlight=new Request(target,now);lastSent=now;return inFlight;
 }
 /** Takes an answer for a request; true when it became the shown frame. */
 synchronized boolean answer(Request r,Response a,long now){
  if(r==null||r!=inFlight)return drop("rejected");
  inFlight=null;
  if(a==null)a=Response.failed();
  if(!"ok".equals(a.status)){frame=null;return drop(a.status.isEmpty()?"rejected":a.status);}
  WorkspaceOverview.Binding b=r.binding;
  boolean identity=b.sessionId.equals(a.sessionId)&&b.controllerTaskId.equals(a.taskId)&&b.targetPackage.equals(a.targetPackage)&&sameTarget(b,target);
  boolean bounded="image/jpeg".equals(a.mimeType)&&a.width>0&&a.height>0&&a.width<=MAX_WIDTH&&a.height<=MAX_HEIGHT&&a.bytes>0&&a.bytes<=MAX_BYTES&&a.image!=null;
  boolean fresh=a.capturedAt>=r.sentAt-CLOCK_SLACK_MS&&a.capturedAt<=now+CLOCK_SLACK_MS&&now-a.capturedAt<=MAX_AGE_MS&&(frame==null||a.capturedAt>frame.capturedAt);
  if(!identity){frame=null;return drop("rejected");}
  if(!bounded||!fresh)return drop("rejected");
  frame=new Frame(b,a.capturedAt,a.width,a.height,a.image);return true;
 }
 /** The frame to show for this target now, or null (no frame, another binding, or too old). */
 synchronized Frame frameFor(WorkspaceOverview.Binding target,long now){
  if(frame==null||target==null||!sameTarget(frame.binding,target))return null;
  if(now-frame.capturedAt>MAX_AGE_MS){frame=null;return null;}
  return frame;
 }
 /** Back, pause, teardown, lock or a changed binding: any answer in flight is ignored and nothing is shown. */
 synchronized void cancel(){inFlight=null;frame=null;}
 synchronized boolean inFlight(){return inFlight!=null;}

 private boolean drop(String why){lastDrop=why;return false;}
 static boolean sameTarget(WorkspaceOverview.Binding a,WorkspaceOverview.Binding b){
  if(a==null||b==null)return a==b;
  return a.controllerTaskId.equals(b.controllerTaskId)&&a.sessionId.equals(b.sessionId)&&a.targetPackage.equals(b.targetPackage)
   &&a.scope.server.equals(b.scope.server)&&a.scope.pairing.equals(b.scope.pairing);
 }
 private static String nz(String s){return s==null?"":s;}
}
