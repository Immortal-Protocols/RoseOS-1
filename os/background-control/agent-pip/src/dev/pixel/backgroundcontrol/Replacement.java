package dev.pixel.backgroundcontrol;

import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Map;
import java.util.Set;

/** When the controller may send its next session request, and what it may claim about sessions it closed (the
 * multi-app report's close-and-replacement race). Pure logic; the controller keeps one instance under its lock.
 *
 * Android lists at most one session device-wide (ComputerControlSessionProcessor MAXIMUM_CONCURRENT_SESSIONS = 1) and
 * refuses a same-owner, same-name request while an earlier session is still listed. A closed session leaves that list
 * in its release, which on the normal path finishes before its client onClosed; but if the release throws, the session
 * stays listed although onClosed arrives (OS Core MULTI-SESSION-CONSULT). So neither onClosed nor an empty owned-device
 * snapshot proves release. onClosed is only the earliest point a request can be admitted.
 *
 * What does prove it is Android's admission. requestComputerControlSession is a synchronous binder call that runs
 * validateRequest, whose same-owner, same-name check holds the processor's session-list lock, and every session this
 * controller requests has the same fixed name. Every platform callback for a request (pending, created, creation
 * failed) is sent only after validateRequest passed (ComputerControlSessionProcessor.processNewSessionRequest). So the
 * first callback for a request attests that none of the sessions this controller closed before sending it is still
 * listed (admitted). A bare normal return is not enough: with no virtual-device service the client returns without
 * calling the platform and no callback ever comes (VirtualDeviceManager.requestComputerControlSession, mService null).
 * Such a request stays unanswered and unknown, never released and never empty; a name refusal (thrown) attests nothing.
 *
 * Rules: wait while a session this controller closed has not reported onClosed, and while an earlier request is
 * unanswered; then send exactly one request for the start, with no timer and no retry. Keep each closed session's
 * handle as unconfirmed until a later request is admitted. A close() that throws is cleanup_failed, held by the
 * controller itself and never recorded here. */
final class Replacement {
 static final String REPLACING="replacing", REQUEST_UNANSWERED="request_unanswered",
  PREVIOUS_SESSION_UNRELEASED="previous_session_unreleased", SESSION_LIMIT="session_limit";
 /** Android's same-owner, same-name refusal (validateRequest), thrown by requestSession. */
 static final String NAME_NOT_UNIQUE="Session name must be unique";
 /** onSessionCreationFailed(ERROR_SESSION_LIMIT_REACHED = 1) as the controller records it, after the name check passed.
  * The listed session may be another owner's, or this controller's own: the name check does not see creations already
  * posted, so an earlier request whose consent was answered late can be created just before this one. Its stale
  * onSessionCreated is closed and kept as unconfirmed (closing), so that case is never shown as empty. */
 static final String LIMIT_REACHED="Android session error 1";

 private static final class Closed {final Object handle;final long order;Closed(Object handle,long order){this.handle=handle;this.order=order;}}
 // unconfirmed: sessions this controller closed whose removal Android has not attested, with their handles.
 // awaiting: those of them that have not yet reported onClosed. reported: tokens whose onClosed already arrived.
 // sentAfter: for each request in flight, the order of the last close recorded before it was sent.
 private final Map<Long,Closed> unconfirmed=new HashMap<>();
 private final Set<Long> awaiting=new HashSet<>(), unanswered=new HashSet<>(), reported=new HashSet<>();
 private final Map<Long,Long> sentAfter=new HashMap<>();
 private long order;

 /** This controller's close() of the session of this start token returned normally. */
 void closing(long token,Object handle){unconfirmed.put(token,new Closed(handle,++order));if(!reported.contains(token))awaiting.add(token);}
 /** Android reported the session of this token closed (onClosed or onSessionClosed). Not a proof of release. */
 void closed(long token){awaiting.remove(token);reported.add(token);}
 /** The request of this start token is being sent to Android. */
 void requested(long token){unanswered.add(token);sentAfter.put(token,order);}
 /** The first platform callback for this request arrived (pending, created or creation failed), so Android's name check,
  * under its session-list lock, found none of this controller's sessions closed before the request was sent. Call it
  * before answered(); never for a bare return of requestSession or for a request call that threw. */
 void admitted(long token){
  Long last=sentAfter.get(token);if(last==null)return;
  for(Iterator<Map.Entry<Long,Closed>> i=unconfirmed.entrySet().iterator();i.hasNext();){Map.Entry<Long,Closed> e=i.next();if(e.getValue().order<=last){awaiting.remove(e.getKey());i.remove();}}
 }
 /** Android answered that request: created, creation failed, consent pending, or the request call itself threw. */
 void answered(long token){unanswered.remove(token);sentAfter.remove(token);}
 /** Android may hold a session this controller cannot account for: one it closed and Android has not attested unlisted
  * (its handle is kept), or a request Android has not answered (it may yet be created, or never reached Android). */
 boolean releasing(){return !unconfirmed.isEmpty()||!unanswered.isEmpty();}
 /** The request of this token was sent and Android has not answered it. */
 boolean unanswered(long token){return unanswered.contains(token);}
 /** Why the next request is not sent yet: an unanswered request first (it may never be answered), else a close not yet
  * reported, else "". */
 String blocker(){return !unanswered.isEmpty()?REQUEST_UNANSWERED:!awaiting.isEmpty()?REPLACING:"";}
 /** The handle of an unconfirmed session, or null. */
 Object handle(long token){Closed c=unconfirmed.get(token);return c==null?null:c.handle;}
 /** The next request may be sent: every closed session has reported onClosed and no earlier request is unanswered.
  * Unconfirmed sessions do not block it: Android itself decides admission, atomically, on that one request. */
 boolean clear(){return awaiting.isEmpty()&&unanswered.isEmpty();}

 /** Whether a recorded error is Android's refusal because a session is still listed. */
 static boolean listed(String error){return error!=null&&(error.contains(NAME_NOT_UNIQUE)||error.equals(LIMIT_REACHED));}
 /** The fixed reason for such a refusal, else "". */
 static String reason(String error){
  if(error==null)return "";
  if(error.contains(NAME_NOT_UNIQUE))return PREVIOUS_SESSION_UNRELEASED;
  if(error.equals(LIMIT_REACHED))return SESSION_LIMIT;
  return "";
 }
}
