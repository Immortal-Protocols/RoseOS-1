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
 * controller requests has the same fixed name. So a request that returns normally attests that none of the sessions
 * this controller closed before sending it is still listed (admitted); a name refusal attests nothing and clears nothing.
 *
 * Rules: wait while a session this controller closed has not reported onClosed, and while an earlier request is
 * unanswered; then send exactly one request for the start, with no timer and no retry. Keep each closed session's
 * handle as unconfirmed until a later request is admitted. A close() that throws is cleanup_failed, held by the
 * controller itself and never recorded here. */
final class Replacement {
 static final String REPLACING="replacing", PREVIOUS_SESSION_UNRELEASED="previous_session_unreleased", SESSION_LIMIT="session_limit";
 /** Android's same-owner, same-name refusal (validateRequest), thrown by requestSession. */
 static final String NAME_NOT_UNIQUE="Session name must be unique";
 /** onSessionCreationFailed(ERROR_SESSION_LIMIT_REACHED = 1) as the controller records it. It is dispatched only after the
  * name check passed, so it is another owner's session (or one of another name), never one of this controller's. */
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
 /** requestSession returned normally: Android's name check, under its session-list lock, found none of this
  * controller's sessions closed before the request was sent. */
 void admitted(long token){
  Long last=sentAfter.get(token);if(last==null)return;
  for(Iterator<Map.Entry<Long,Closed>> i=unconfirmed.entrySet().iterator();i.hasNext();){Map.Entry<Long,Closed> e=i.next();if(e.getValue().order<=last){awaiting.remove(e.getKey());i.remove();}}
 }
 /** Android answered that request: created, creation failed, consent pending, or the request call itself threw. */
 void answered(long token){unanswered.remove(token);sentAfter.remove(token);}
 /** A session this controller closed may still be listed by Android (its handle is kept). */
 boolean releasing(){return !unconfirmed.isEmpty();}
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
