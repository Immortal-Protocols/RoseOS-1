package dev.pixel.voice;

/**
 * F08 (PM-PHONE-FEEDBACK-1104-001): when Rose Agent's own microphone-input mute gives way to the OS Rose chin.
 *
 * The chin is the swipe-up edge's microphone slot (OS Core's Shell RoseEdgeMicSlot), which sends the same MUTE/UNMUTE
 * to AgentTrayService, so it changes the same input-only mute (RoseMicState): speech output and background work are
 * never touched by either control. The conversation runs in its own standard task, so the chin can be raised over it.
 *
 * The in-app mute is hidden only while the chin is reachable: a trusted SystemUI tray client (AgentTrayService's
 * unchanged caller check) is registered and its REGISTER declared the chin available (arg2 == AVAILABLE). Whether the
 * chin happens to be on screen now (the mic slot's own REGISTER, arg1 == 1) never counts: that is visibility, not
 * reachability. No declaration, an older OS, an unregistered or dead client: the in-app mute stays. Pure logic.
 */
final class RoseChin {
 private RoseChin() {}

 /** AgentTrayService protocol v1 REGISTER (equal to AgentTrayService.REGISTER; kept here for the host tests). */
 static final int REGISTER = 1;
 /** REGISTER arg2: the chin can be raised over a standard app on this device. */
 static final int AVAILABLE = 1;

 /** Whether a trusted REGISTER (what, arg2) declares the chin reachable. */
 static boolean declaresReachable(int what, int arg2) {
  return what == REGISTER && arg2 == AVAILABLE;
 }

 /** Whether the conversation shows its own mute: always, unless the chin is reachable. */
 static boolean showInAppMute(boolean chinReachable) {
  return !chinReachable;
 }
}
