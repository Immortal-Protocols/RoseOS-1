package dev.pixel.voice;

/** Opening Camera's voice entry must not change an existing session's mute state. */
final class CameraEntryPolicy {
 static boolean shouldStart(boolean serviceExists,boolean live,boolean connected,boolean communicating){
  return !serviceExists||!(live||connected||communicating);
 }
 private CameraEntryPolicy(){}
}
