package dev.pixel.voice;
import java.util.regex.*;

/** Parses only YouTube's explicit video-result accessibility labels. */
final class YouTubeVideo {
 final String label,title,channel;
 private YouTubeVideo(String label,String title,String channel){this.label=label;this.title=title;this.channel=channel;}
 static boolean matchesMetadata(String description,String title){return description.equals(title)||description.startsWith(title+", ");}
 static YouTubeVideo parse(String label){
  if(label==null||!label.endsWith(" - play video"))return null;
  Matcher title=Pattern.compile("^(.*?) - (?:(?:[0-9,]+ (?:hours?|minutes?|seconds?)(?:, | ))*[0-9,]+ (?:hours?|minutes?|seconds?)|LIVE|Live) - ").matcher(label);
  String name=title.find()?title.group(1):label.substring(0,label.length()-13);
  Matcher channel=Pattern.compile(" - Go to channel (.*?) - ").matcher(label);
  return new YouTubeVideo(label,name,channel.find()?channel.group(1):"");
 }
}
