package dev.pixel.voice;

/** The Home tray's Rose state, read once per existing Home refresh tick (OS-HIFI-HOME-001 M004). It is the
 * merged RoseVisualState mapping fed with the levels the voice service already publishes; Home adds only whether it is
 * shown and the owner's reduced-motion setting. Missing or stale levels stay unknown, never guessed; nothing
 * here sends a voice or task command. Pure logic, no Android types. */
final class HomeRose {
 private HomeRose() {}

 /** observedAtMs is the voice service's trayAudioObservedAt (elapsedRealtime, 0 when never observed). */
 static RoseVisualState state(ConversationState conversation, float inputLevel, float outputLevel, long observedAtMs,
                              long nowMs, boolean reducedMotion, boolean homeShown) {
  return RoseVisualState.from(conversation, RoseVisualState.fresh(inputLevel, observedAtMs, nowMs),
    RoseVisualState.fresh(outputLevel, observedAtMs, nowMs), reducedMotion, homeShown);
 }

 /** The same reduced-motion rule the conversation uses: the owner turned animations off. */
 static boolean reducedMotion(float animatorDurationScale) {
  return animatorDurationScale == 0f;
 }

 /** Home is shown while the activity is resumed and the Home frame, not another surface, is its content. */
 static boolean shown(boolean resumed, boolean frameIsContent) {
  return resumed && frameIsContent;
 }
}
