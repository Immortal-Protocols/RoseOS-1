package dev.pixel.voice;

/**
 * The capture side of Rose's microphone-input mute (#410 item 6), as VoiceService applies it. The stored state is
 * Home's (RoseMicState: preferences "rose-mic-input", key "muted"); the backend reads it at every call start and writes
 * it only for an explicit request (tray_mute / tray_unmute, any origin) or a change the live page made itself
 * (the conversation's mic, a glasses tap, talk-while-muted). It never writes a reset: a call ending, cleanup, a lost
 * renderer and a report taken while connecting all leave it as it was. Pure rules, no Android types.
 */
final class MicInputCapture {
 private MicInputCapture() {}

 static final String PREFS = "rose-mic-input", KEY = "muted";
 static final String MUTE = "tray_mute", UNMUTE = "tray_unmute";

 /** The state a voice command requests, or null for any other command. */
 static Boolean requested(String command) {
  return MUTE.equals(command) ? Boolean.TRUE : UNMUTE.equals(command) ? Boolean.FALSE : null;
 }

 /** The page has applied every request it was sent: its reports are then its own (acknowledged by sequence). */
 static boolean settled(int acknowledged, int requested) { return acknowledged >= requested; }

 /**
  * Whether a page report is the page's own change, to be stored: the call is live and connected, every request is
  * acknowledged (an older report is stale), and the reported mute differs from the stored one. Anything else, a
  * reset included, is never stored.
  */
 static boolean storeReported(boolean live, boolean connected, boolean settled, boolean reported, boolean stored) {
  return live && connected && settled && reported != stored;
 }
}
