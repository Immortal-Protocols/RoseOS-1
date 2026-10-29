package dev.pixel.voice;

/** What the Rose presence shows, as three independent axes (the Rose three-dimensions study):
 * connection is the split, the microphone is the color, Rose's voice is the surround. Muting changes
 * only the color and the surround; Rose can still be speaking while muted. Derived from ConversationState
 * and the measured audio levels VoiceService already publishes; pure logic with no Android types, and it
 * sends no command, emits no notice and touches no task. */
final class RoseVisualState {
 enum Connection { JOINED, NARROW_GAP, WIDE_GAP }
 enum Mic { ROSE, SILVER }
 enum Surround { QUIET, LISTENING, SPEAKING }
 /** Levels older than this are not evidence of current audio. */
 static final long STALE_MS=1000;
 /** Same threshold as the voice page's assistantSpeaking. */
 static final float SPEAKING_LEVEL=.008f;

 final Connection connection; final Mic mic; final Surround surround;
 /** ConversationState is ERROR; its failureText() stays the source of the words. */
 final boolean failed;
 /** False when the levels were missing, stale or not finite: nothing is inferred from them. */
 final boolean levelsKnown;
 /** 0..1: Rose's output while speaking, the microphone input while listening, otherwise 0. */
 final float amplitude;
 /** False under reduced motion or while hidden; the semantic axes above are unchanged. */
 final boolean animate;

 private RoseVisualState(Connection connection,Mic mic,Surround surround,boolean failed,boolean levelsKnown,float amplitude,boolean animate){
  this.connection=connection;this.mic=mic;this.surround=surround;this.failed=failed;this.levelsKnown=levelsKnown;this.amplitude=amplitude;this.animate=animate;
 }

 /** inputLevel and outputLevel are 0..1 measured levels, or NaN when unknown (see fresh). */
 static RoseVisualState from(ConversationState s,float inputLevel,float outputLevel,boolean reducedMotion,boolean visible){
  boolean active=s.active(),known=Float.isFinite(inputLevel)&&Float.isFinite(outputLevel);
  Connection connection=active?Connection.JOINED:s.phase==ConversationState.Phase.CONNECTING?Connection.NARROW_GAP:Connection.WIDE_GAP;
  Mic mic=active&&!s.muted?Mic.ROSE:Mic.SILVER;
  float input=bounded(inputLevel),output=bounded(outputLevel);
  Surround surround=!active?Surround.QUIET:known&&output>SPEAKING_LEVEL?Surround.SPEAKING:s.muted?Surround.QUIET:Surround.LISTENING;
  boolean animate=!reducedMotion&&visible;
  float amplitude=!animate||!known?0f:surround==Surround.SPEAKING?output:surround==Surround.LISTENING?input:0f;
  return new RoseVisualState(connection,mic,surround,s.phase==ConversationState.Phase.ERROR,known,amplitude,animate);
 }

 /** A published level, or NaN when it was never observed (observedAtMs 0) or is older than STALE_MS. */
 static float fresh(float level,long observedAtMs,long nowMs){
  return observedAtMs<=0||nowMs-observedAtMs>STALE_MS||nowMs<observedAtMs?Float.NaN:level;
 }

 private static float bounded(float level){return Float.isFinite(level)?Math.max(0f,Math.min(1f,level)):0f;}
}
