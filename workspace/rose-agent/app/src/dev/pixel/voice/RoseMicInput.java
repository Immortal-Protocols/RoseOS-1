package dev.pixel.voice;

/**
 * Rose Agent's one microphone-input mute (#410 wave item 6, Nathan's correction of the prototype's "global mic"):
 * whether microphone input into Rose Agent is withheld. Home's edge, the raised edge (through AgentTrayService), the
 * conversation's mic and the call notification all show and change this one state.
 *
 * It mutes only Rose's own capture (the voice providers' existing input mute: WebRTC tracks disabled, the ElevenLabs
 * input mute, LocalRoseLink sending silence); unmute restores it. It never touches Rose's output, other apps' audio,
 * AudioManager's microphone mute or Sensor Privacy, and never starts, ends or stops a call, task or session. It is
 * durable (Rose's private preferences), so a new call, a reconnect or a replaced renderer starts from it instead of
 * silently unmuting. While a call is live, every surface shows that call's actual input mute. Pure rules, no Android
 * types; the stored state and requests are RoseMicState.
 */
final class RoseMicInput {
 private RoseMicInput() {}

 static final String MUTE = "tray_mute", UNMUTE = "tray_unmute";

 // ---- Pure rules (host-tested) ----

 /** What every surface shows: the live call's actual input mute while a call is active, otherwise the shared state. */
 static boolean shown(boolean callActive, boolean callMuted, boolean shared) {
  return callActive ? callMuted : shared;
 }

 /** The existing voice command that applies a requested state to a live call. */
 static String command(boolean muted) { return muted ? MUTE : UNMUTE; }

 /** The requested state a voice command carries, or null for any other command. */
 static Boolean requested(String command) {
  return MUTE.equals(command) ? Boolean.TRUE : UNMUTE.equals(command) ? Boolean.FALSE : null;
 }

 /**
  * Keeps the shared state and the voice page's own mute in step, from the page's state reports. A call that becomes
  * ready is brought to the shared state (APPLY); after that, a change the page made itself (its own mute button) is
  * the user's and is kept (PERSIST). While an apply or a requested change is on its way to the page, reports that
  * still carry the old value are stale and change nothing.
  */
 static final class Sync {
  static final int NONE = 0, APPLY_MUTED = 1, APPLY_UNMUTED = 2, PERSIST_MUTED = 3, PERSIST_UNMUTED = 4;
  private Boolean pending;

  /** A change was just requested of the live page (a command): reports until it shows are stale. */
  void expect(boolean muted) { pending = muted; }

  int report(boolean wasLive, boolean live, boolean pageMuted, boolean shared) {
   if (!live) { pending = null; return NONE; }
   if (!wasLive) {
    if (pageMuted == shared) { pending = null; return NONE; }
    pending = shared;
    return shared ? APPLY_MUTED : APPLY_UNMUTED;
   }
   if (pending != null) {
    if (pageMuted == pending) pending = null;
    return NONE;
   }
   if (pageMuted != shared) return pageMuted ? PERSIST_MUTED : PERSIST_UNMUTED;
   return NONE;
  }

  boolean waiting() { return pending != null; }
 }

}
