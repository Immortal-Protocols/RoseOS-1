package dev.pixel.voice;

/** The quiet session mark on the Home presence, chosen only from ConversationState (the same state the
 * conversation surface and the SystemUI tray render). Pure logic, no Android types. Unavailable or
 * ended voice shows nothing; microphone state is shown only for an active session, never from audio. */
final class HomeSessionMark {
 enum Mark { NONE, CONNECTING, LISTENING, MUTED }

 private HomeSessionMark() {}

 static Mark of(ConversationState state) {
  if (state == null) return Mark.NONE;
  switch (state.phase) {
   case ACTIVE: return state.muted ? Mark.MUTED : Mark.LISTENING;
   case CONNECTING: return Mark.CONNECTING;
   default: return Mark.NONE;
  }
 }

 /** Spoken with "Open Rose"; empty when no session is shown. */
 static String description(Mark mark) {
  switch (mark) {
   case LISTENING: return "Microphone on";
   case MUTED: return "Microphone muted";
   case CONNECTING: return "Connecting";
   default: return "";
  }
 }
}
