package dev.pixel.voice;

import android.graphics.Typeface;
import android.text.*;
import android.text.method.LinkMovementMethod;
import android.text.style.*;
import android.widget.TextView;
import java.util.regex.*;

/** Native display formatting, without rewriting or summarizing the result. */
final class TaskText {
 private static final Pattern INLINE=Pattern.compile("\\[([^\\]]+)\\]\\((https?://[^\\s)]+)\\)|\\*\\*([^*]+)\\*\\*|`([^`]+)`");
 private static final Pattern HEADING=Pattern.compile("^#{1,6}\\s+(.+)$"),URL=Pattern.compile("https?://[^\\s<>()]+");
 static void set(TextView view,String source){
  SpannableStringBuilder out=new SpannableStringBuilder();boolean code=false;
  for(String line:source.split("\\n",-1)){
   if(line.trim().startsWith("```")){code=!code;continue;}
   int start=out.length();Matcher heading=HEADING.matcher(line);
   if(code){out.append(line);out.setSpan(new TypefaceSpan("monospace"),start,out.length(),Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);}
   else if(heading.matches()){inline(out,heading.group(1));out.setSpan(new StyleSpan(Typeface.BOLD),start,out.length(),Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);out.setSpan(new RelativeSizeSpan(1.15f),start,out.length(),Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);}
   else inline(out,line.replaceFirst("^\\s*[-*]\\s+","• "));
   out.append('\n');
  }
  while(out.length()>0&&out.charAt(out.length()-1)=='\n')out.delete(out.length()-1,out.length());
  Matcher urls=URL.matcher(out);while(urls.find())if(out.getSpans(urls.start(),urls.end(),URLSpan.class).length==0)out.setSpan(new URLSpan(urls.group()),urls.start(),urls.end(),Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
  view.setText(out);view.setTextIsSelectable(true);view.setMovementMethod(LinkMovementMethod.getInstance());
 }
 private static void inline(SpannableStringBuilder out,String text){
  Matcher m=INLINE.matcher(text);int end=0;
  while(m.find()){
   out.append(text,end,m.start());int start=out.length();
   if(m.group(1)!=null){out.append(m.group(1));out.setSpan(new URLSpan(m.group(2)),start,out.length(),Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);}
   else if(m.group(3)!=null){out.append(m.group(3));out.setSpan(new StyleSpan(Typeface.BOLD),start,out.length(),Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);}
   else{out.append(m.group(4));out.setSpan(new TypefaceSpan("monospace"),start,out.length(),Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);}
   end=m.end();
  }
  out.append(text,end,text.length());
 }
}
