package dev.pixel.voice;
import java.util.*;
/** Select the first playable song in Apple Music's ranked search results. */
final class AppleMusicMatch {
 static String norm(String s){return s==null?"":s.trim().replaceAll("\\s+"," ").toLowerCase(Locale.ROOT);}
 static final class Row {
  static final String TITLE="com.apple.android.music:id/title";
  final int target;final String text,artist,viewId;
  Row(int target,String text){this(target,text,null,TITLE);}
  Row(int target,String text,String artist){this(target,text,artist,TITLE);}
  Row(int target,String text,String artist,String viewId){this.target=target;this.text=text;this.artist=artist;this.viewId=viewId;}
 }
 static Row choose(List<Row> rows,String title,String artist){
  for(Row row:rows){
   if(!Row.TITLE.equals(row.viewId)||row.text.trim().isEmpty()||row.text.startsWith("Song • "))continue;
   for(Row credit:rows)if(credit.target==row.target&&credit.text.startsWith("Song • "))
    return new Row(row.target,row.text,credit.text.substring("Song • ".length()).trim());
  }
  return null;
 }
 static String signature(List<Row> rows){
  StringBuilder out=new StringBuilder();
  Set<Integer> seen=new HashSet<>();
  for(Row row:rows){if(!Row.TITLE.equals(row.viewId)||seen.contains(row.target)||row.text.trim().isEmpty()||row.text.startsWith("Song • "))continue;
   for(Row credit:rows)if(credit.target==row.target&&credit.text.startsWith("Song • ")){seen.add(row.target);out.append(row.text).append('\n').append(credit.text).append('\n');break;}}
  return out.toString();
 }
}
