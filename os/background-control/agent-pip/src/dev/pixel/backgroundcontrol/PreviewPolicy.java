package dev.pixel.backgroundcontrol;

/** Whether Rose Agent's own overview may take a read-only preview of the controller's live session (#225), and how big
 * it may be. The preview is never the agent's observation: Controller.preview takes a frame without touching the node
 * map, snapshot id/time, width/height, operation count or observed app, so the agent's next action keeps its
 * freshness. A preview is refused, with one fixed word and nothing else, unless the caller names the live session and
 * its task and that session is ready and unblocked while the phone is unlocked. Secure windows need no check here: the
 * session's virtual display is not VIRTUAL_DISPLAY_FLAG_SECURE, so the platform blanks them in every frame. Pure logic,
 * no Android types. */
final class PreviewPolicy {
 private PreviewPolicy() {}

 static final String OK = "ok";
 /** The request did not name both a session and a task: no binding, no preview. */
 static final String UNBOUND = "unbound";
 /** The phone is locked: nothing of the workspace is shown. */
 static final String LOCKED = "locked";
 /** No live session, or one that is starting, closed, failed or past its deadline. */
 static final String NOT_READY = "not_ready";
 /** Android blocked the session (an owner gate): no frame is shown. */
 static final String BLOCKED = "blocked";
 /** The named session is not the live one (it ended or was replaced). */
 static final String STALE_SESSION = "stale_session";
 /** The live session is not tagged with the named task (or with no task). */
 static final String STALE_TASK = "stale_task";
 /** Asked again sooner than MIN_INTERVAL_MS: the agent's own screenshots keep priority. */
 static final String TOO_SOON = "too_soon";
 /** The platform gave no allowed frame, or it could not be made small enough. */
 static final String NO_FRAME = "no_frame";

 static final int MAX_WIDTH = 320, MAX_HEIGHT = 720, MAX_BYTES = 48 * 1024;
 static final long MIN_INTERVAL_MS = 900;
 /** JPEG qualities tried in order; a frame that does not fit MAX_BYTES at the last one is NO_FRAME. */
 static final int[] QUALITIES = {70, 55, 40};

 /** null when a preview may be taken now; otherwise the one refusal word. lastPreview is 0 before the first. */
 static String refusal(String requestedSession, String requestedTask, boolean locked, boolean hasSession, String status,
                       boolean blocked, boolean expired, String liveSession, String liveTask, long now, long lastPreview) {
  if (empty(requestedSession) || empty(requestedTask)) return UNBOUND;
  if (locked) return LOCKED;
  if (!hasSession || empty(liveSession)) return NOT_READY;
  if (!requestedSession.equals(liveSession)) return STALE_SESSION;
  if (empty(liveTask) || !requestedTask.equals(liveTask)) return STALE_TASK;
  if (blocked || "blocked".equals(status)) return BLOCKED;
  if (!"ready".equals(status) || expired) return NOT_READY;
  if (lastPreview > 0 && now - lastPreview < MIN_INTERVAL_MS) return TOO_SOON;
  return null;
 }

 /** The preview's size for a frame: never larger than MAX_WIDTH x MAX_HEIGHT, never enlarged, aspect kept; null for
  * a frame with no pixels. */
 static int[] size(int width, int height) {
  if (width <= 0 || height <= 0) return null;
  double scale = Math.min(1.0, Math.min(MAX_WIDTH / (double) width, MAX_HEIGHT / (double) height));
  return new int[] {Math.max(1, (int) Math.round(width * scale)), Math.max(1, (int) Math.round(height * scale))};
 }

 /** Whether an encoded preview is within the byte budget. */
 static boolean fits(int bytes) {
  return bytes > 0 && bytes <= MAX_BYTES;
 }

 private static boolean empty(String s) {
  return s == null || s.isEmpty() || "null".equals(s);
 }
}
