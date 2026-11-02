package dev.pixel.backgroundcontrol;

import java.util.HashSet;
import java.util.Set;

/** When the controller may send its next session request (the multi-app report's close-and-replacement race).
 * Android lists at most one session device-wide (ComputerControlSessionProcessor MAXIMUM_CONCURRENT_SESSIONS = 1) and
 * refuses a same-owner, same-name request while an earlier session is still listed. A closed session leaves that list
 * only in its own release, which runs before its client onClosed is dispatched (OS Core MULTI-SESSION-CONSULT, 04:46
 * Nov 2). So after this controller's own successful close() it waits for that session's onClosed, and while any earlier
 * request is unanswered it waits too; then it sends exactly one request. There is no timer and no retry: if Android's
 * release itself failed, the session stays listed although onClosed arrived, and Android's own refusal of the next
 * request is the answer (reason()). A close() that throws is cleanup_failed, held by the controller and never waited
 * out here. Pure logic; the controller keeps one instance under its lock. */
final class Replacement {
 static final String REPLACING="replacing", PREVIOUS_SESSION_UNRELEASED="previous_session_unreleased", SESSION_LIMIT="session_limit";
 /** Android's same-owner, same-name refusal (validateRequest), thrown by requestSession. */
 static final String NAME_NOT_UNIQUE="Session name must be unique";
 /** onSessionCreationFailed(ERROR_SESSION_LIMIT_REACHED = 1) as the controller records it; the limit counts every owner. */
 static final String LIMIT_REACHED="Android session error 1";

 // reported: tokens whose close Android already reported, so a close() that returns after that report waits for nothing.
 private final Set<Long> closing=new HashSet<>(), unanswered=new HashSet<>(), reported=new HashSet<>();

 /** The request of this start token is being sent to Android. */
 void requested(long token){unanswered.add(token);}
 /** Android answered that request: created, creation failed, consent pending, or the request call itself threw. */
 void answered(long token){unanswered.remove(token);}
 /** This controller's close() of the session of this token returned normally; Android has not yet reported it closed. */
 void closing(long token){if(!reported.contains(token))closing.add(token);}
 /** Android reported the session of this token closed (onClosed or onSessionClosed). */
 void closed(long token){closing.remove(token);reported.add(token);}
 /** A session this controller closed may still be listed by Android. */
 boolean releasing(){return !closing.isEmpty();}
 /** Nothing this controller closed or requested earlier can still be pending in Android's session list. */
 boolean clear(){return closing.isEmpty()&&unanswered.isEmpty();}

 /** Whether a recorded error is Android's refusal because a session is still listed. */
 static boolean listed(String error){return error!=null&&(error.contains(NAME_NOT_UNIQUE)||error.equals(LIMIT_REACHED));}
 /** The fixed reason for such a refusal, else "". The name refusal can only be this controller's own earlier session;
  * the limit may be another owner's session, so it is not called this controller's. */
 static String reason(String error){
  if(error==null)return "";
  if(error.contains(NAME_NOT_UNIQUE))return PREVIOUS_SESSION_UNRELEASED;
  if(error.equals(LIMIT_REACHED))return SESSION_LIMIT;
  return "";
 }
}
