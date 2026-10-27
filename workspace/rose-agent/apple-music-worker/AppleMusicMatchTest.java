package dev.pixel.voice;
import java.util.*;
public class AppleMusicMatchTest {
 static void require(boolean b){if(!b)throw new AssertionError();}
 public static void main(String[] args){
  List<AppleMusicMatch.Row> rows=Arrays.asList(new AppleMusicMatch.Row(1,"Album title"),new AppleMusicMatch.Row(1,"Album • Adele"),new AppleMusicMatch.Row(2,"Hello (Live)"),new AppleMusicMatch.Row(2,"Song • Adele"),new AppleMusicMatch.Row(3,"Hello"),new AppleMusicMatch.Row(3,"Song • Adele"));
  require(AppleMusicMatch.choose(rows,"Hello","Adele").target==2);
  require(AppleMusicMatch.choose(rows,"Hello","Adele").artist.equals("Adele"));
  rows=Arrays.asList(new AppleMusicMatch.Row(2,"Hallelujah"),new AppleMusicMatch.Row(2,"Song • Jeff Buckley"),new AppleMusicMatch.Row(3,"Hallelujah"),new AppleMusicMatch.Row(3,"Song • Jeff Buckley"));
  require(AppleMusicMatch.choose(rows,"Hallelujah","Jeff Buckley").target==2);
  String signature=AppleMusicMatch.signature(rows);
  require(!signature.equals(AppleMusicMatch.signature(Arrays.asList(new AppleMusicMatch.Row(4,"Different"),new AppleMusicMatch.Row(4,"Song • Someone")))));
  require(AppleMusicMatch.choose(Arrays.asList(new AppleMusicMatch.Row(1,"Hello"),new AppleMusicMatch.Row(2,"Song • Adele")),"Hello","Adele")==null);
  rows=Arrays.asList(new AppleMusicMatch.Row(2,"And I just can't get enough",null,"com.apple.android.music:id/eyebrow"),new AppleMusicMatch.Row(2,"Just Can't Get Enough",null,"com.apple.android.music:id/title"),new AppleMusicMatch.Row(2,"Song • Depeche Mode",null,"com.apple.android.music:id/subtitle"));
  require(AppleMusicMatch.choose(rows,"Just Can't Get Enough","Depeche Mode").text.equals("Just Can't Get Enough"));
  String withLyrics=AppleMusicMatch.signature(rows);
  rows=Arrays.asList(new AppleMusicMatch.Row(2,"Different lyric excerpt",null,"com.apple.android.music:id/eyebrow"),rows.get(1),rows.get(2));
  require(withLyrics.equals(AppleMusicMatch.signature(rows)));
  System.out.println("Top song tests passed: live allowed, duplicate first, album skipped, same-row metadata, result signature.");
 }
}
