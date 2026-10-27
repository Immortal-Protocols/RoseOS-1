package dev.pixel.voice;
import android.media.*;
import android.util.Base64;
import java.io.*;

/** Exact book audio, sharing the existing conversation route. */
final class BookNarrator {
 private final VoiceService owner;
 private MediaPlayer player;
 private int generation;
 BookNarrator(VoiceService owner){this.owner=owner;}
 void event(String type){owner.web.evaluateJavascript("window.readerAudioEvent("+org.json.JSONObject.quote(type)+")",null);}
 void play(String encoded){stop();final int version=generation;try{
  File file=new File(owner.getCacheDir(),"book-passage.mp3");try(FileOutputStream out=new FileOutputStream(file)){out.write(Base64.decode(encoded,Base64.DEFAULT));}
  MediaPlayer p=new MediaPlayer();player=p;p.setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build());p.setDataSource(file.getAbsolutePath());
  p.setOnPreparedListener(mp->{if(version!=generation||player!=mp)return;mp.start();event("playing");});
  p.setOnCompletionListener(mp->{if(version==generation)event("ended");});
  p.setOnErrorListener((mp,what,extra)->{if(version==generation)event("error");return true;});p.prepareAsync();
 }catch(Exception e){event("error");}}
 boolean resume(){try{if(player==null)return false;player.start();event("playing");return true;}catch(Exception e){return false;}}
 void pause(){try{if(player!=null&&player.isPlaying())player.pause();}catch(Exception ignored){}}
 void stop(){generation++;if(player!=null){try{player.release();}catch(Exception ignored){}player=null;}}
 boolean playing(){try{return player!=null&&player.isPlaying();}catch(Exception e){return false;}}
}
