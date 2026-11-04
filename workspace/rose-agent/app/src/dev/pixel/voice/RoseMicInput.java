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
 * silently unmuting. While a call is live and connected, every surface shows that call's actual input mute. The voice
 * backend (App Core: VoiceService, the voice page, its providers, LocalRoseLink) reads the stored state at every call
 * start and reconnect, writes it only for an acknowledged change, and never for a reset. Pure rules, no Android types;
 * the stored state and requests are RoseMicState.
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


}
