package dev.pixel.voice;

public class MediaSnapshotTest {
 static void require(boolean value,String why){if(!value)throw new AssertionError(why);}
 static String session(String pkg,String active,String state,String description){return "    session\n      package="+pkg+"\n      active="+active+"\n      state=PlaybackState {state="+state+", position=123}\n      metadata: size=44, description="+description+"\n";}
 public static void main(String[] args){
  String voice=session("dev.pixel.voice","true","PLAYING(3)","Rose Agent, null, null");
  MediaSnapshot r=MediaSnapshot.parse(voice+session("com.apple.android.music","true","PAUSED(2)","Song, with commas, Artist, Album"),"com.apple.android.music");
  require(r.status.equals("ok")&&!r.playing&&r.state==2,"Selected voice session instead of Apple Music");
  require(r.description.equals("Song, with commas, Artist, Album"),"Ambiguous metadata must not be split into invented title/artist fields");
  r=MediaSnapshot.parse(voice,"com.apple.android.music");require(r.status.equals("unavailable"),"Other app is not Apple Music");
  r=MediaSnapshot.parse(session("com.apple.android.music","false","PLAYING(3)","Old song"),"com.apple.android.music");require(r.status.equals("unavailable"),"Inactive session is not current");
  r=MediaSnapshot.parse(session("com.apple.android.music","true","BUFFERING(6)","Song"),"com.apple.android.music");require(r.status.equals("not_ready"),"Buffering must not claim playing");
  r=MediaSnapshot.parse(session("com.apple.android.music","true","PLAYING(3)","Song")+session("com.apple.android.music","true","PAUSED(2)","Other song"),"com.apple.android.music");require(r.status.equals("ambiguous"),"Multiple active sessions must not be guessed");
  require(MediaSnapshot.parse("permission denied","com.apple.android.music").status.equals("unavailable"),"Denied dumps cannot claim absence or success");
  r=MediaSnapshot.parse(session("com.google.android.youtube","true","PLAYING(3)","Video").replace("position=123", "position=123, speed=2.0"),"com.google.android.youtube");
  require(r.speed==2.0,"Actual media speed should be preserved");
  require(Double.isNaN(MediaSnapshot.parse(voice,"dev.pixel.voice").speed),"Missing speed must remain unknown");
  System.out.println("Media-state selection, ambiguity, comma preservation and playback status checks passed.");
 }
}
