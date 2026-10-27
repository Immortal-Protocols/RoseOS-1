package dev.pixel.voice;

/** A headset disconnect must not end an unrelated phone or wired conversation. */
final class GlassesDisconnectPolicy {
 static boolean shouldEndVoice(boolean communicating, boolean bluetoothRequested) {
  return communicating && bluetoothRequested;
 }
}
