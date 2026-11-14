package dev.pixel.voice;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import rose.models.composer.LocalInference;
import rose.models.composer.Refused;

/**
 * Project 2: the page's starter layout, labelled NOT A MODEL. Until Models' on-phone runtime is on the phone,
 * Arrange and typed requests go through Models' real composer (rose.models.composer.Composer), with its
 * context selection, aliases, validation and typed failures, and this stands in for the model call only. It
 * ignores the request's words and always answers the same way:
 * <ul>
 *   <li>a grid of the first apps it is offered (2 to 8);</li>
 *   <li>a row of up to 4 direct actions (shortcuts, intent templates and launches).</li>
 * </ul>
 * It never offers a Rose task, so nothing that asks first appears without a model choosing it. The page
 * labels every layout it makes as "Starter layout · no on-phone model yet", and WorkspaceTiming records its
 * results as not a model's.
 */
final class WorkspaceStarter implements LocalInference {
 static final String ID="starter";
 static final String LABEL="Starter layout · no on-phone model yet";
 static final String TITLE="Your apps";
 private static final int GRID=8,ROW=4;

 @Override public Map<String,String> info(){
  Map<String,String> m=new LinkedHashMap<>();
  m.put("id",ID);m.put("revision","n/a");m.put("quantization","n/a");m.put("runtime","none (no model on this phone)");
  return m;
 }

 @Override public Generation generate(Request request)throws Refused{
  if(request.cancellation.isCancelled())throw new Refused("cancelled");
  List<String> apps=new ArrayList<>(),actions=new ArrayList<>();
  boolean items=false;
  for(String line:request.user.split("\n")){
   if(line.startsWith("Items:")){items=true;continue;}
   if(line.startsWith("Request:"))break;
   if(!items)continue;
   String t=line.trim();int space=t.indexOf(' ');if(space<2)continue;
   String alias=t.substring(0,space);
   if(alias.matches("a\\d+")){if(apps.size()<GRID)apps.add(alias);}
   else if(alias.matches("x\\d+")&&actions.size()<ROW&&direct(t))actions.add(alias);
  }
  StringBuilder b=new StringBuilder("{\"title\":\""+TITLE+"\",\"sections\":[");
  boolean any=false;
  if(apps.size()>=2){b.append(section("grid","Apps",apps,true));any=true;}
  if(!actions.isEmpty()){if(any)b.append(',');b.append(section("button","Quick actions",actions,false));any=true;}
  if(!any)throw new Refused("empty_context");
  b.append("]}");
  return new Generation(b.toString(),null);
 }

 /**
  * A shortcut, intent template or launch: an action that acts at once. Rose tasks and anything else are left
  * out. The kind is the bracket that ends the line (before any "-> target"), so a label cannot pose as one.
  */
 static boolean direct(String line){
  int arrow=line.lastIndexOf(" -> ");String head=arrow>=0?line.substring(0,arrow):line;
  return head.endsWith("[shortcut]")||head.endsWith("[deeplink]")||head.endsWith("[launch]");
 }

 private static String section(String component,String title,List<String> aliases,boolean withActions){
  StringBuilder b=new StringBuilder("{\"component\":\""+component+"\",\"title\":\""+title+"\",\"items\":[");
  for(int i=0;i<aliases.size();i++){if(i>0)b.append(',');b.append('"').append(aliases.get(i)).append('"');}
  b.append(']');if(withActions)b.append(",\"actions\":[]");
  return b.append('}').toString();
 }
}
