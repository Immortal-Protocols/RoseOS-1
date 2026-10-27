package dev.pixel.voice;

public final class GlassesDisconnectPolicyTest {
 public static void main(String[] args) {
  if(GlassesDisconnectPolicy.shouldEndVoice(true,false))throw new AssertionError("phone conversation ended");
  if(GlassesDisconnectPolicy.shouldEndVoice(false,false))throw new AssertionError("idle prepared session ended");
  if(GlassesDisconnectPolicy.shouldEndVoice(false,true))throw new AssertionError("inactive stale route ended");
  if(!GlassesDisconnectPolicy.shouldEndVoice(true,true))throw new AssertionError("glasses conversation must end on disconnect");
  System.out.println("PASS: phone and idle sessions survive headset disconnect; active headset session ends");
 }
}
