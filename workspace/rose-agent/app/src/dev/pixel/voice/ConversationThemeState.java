package dev.pixel.voice;

import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.Map;

/** In-process, per-activity evidence. A preference read or scheduled recreation is not a drawn frame.
 * No activity is retained here: each screen uses an opaque token and removes it on destruction.
 * Old callbacks cannot acknowledge a replacement screen. Nothing is persisted across process death. */
final class ConversationThemeState {
 static final ConversationThemeState LIVE = new ConversationThemeState();
 private static final class Frame { String shown; boolean deferred; }
 private final Map<Object, Frame> screens = new IdentityHashMap<>();
 synchronized void opened(Object token) { screens.put(token, new Frame()); }
 synchronized void drawn(Object token, String theme) { Frame f=screens.get(token);if(f!=null)f.shown=theme; }
 synchronized void deferred(Object token, boolean value) { Frame f=screens.get(token);if(f!=null)f.deferred=value; }
 synchronized void closed(Object token) { screens.remove(token); }
 synchronized Map<String,Object> snapshot(String requested) {
  boolean applied=!screens.isEmpty(), deferred=false,first=true;String shown=null;
  for(Frame f:screens.values()){
   applied &= requested!=null&&requested.equals(f.shown);deferred |= f.deferred;
   if(first){shown=f.shown;first=false;}else if(shown==null||!shown.equals(f.shown))shown=null;
  }
  String state=applied?"applied":screens.isEmpty()?"not_open":deferred?"deferred":"pending";
  Map<String,Object> out=new LinkedHashMap<>();
  out.put("requested",requested);out.put("shown",shown==null?MiniJson.NULL:(Object)shown);
  out.put("applied",applied);out.put("state",state);
  if(!applied)out.put("note",state.equals("deferred")
    ?"Rose will finish the theme change after Return, Stop or Background Work closes."
    :state.equals("not_open")?"The Rose screen is not open; its theme will be checked when it next draws."
    :"The Rose screen has not drawn this theme yet.");
  return out;
 }

 /** Only the settled context of a theme-triggered recreation, never a View, dialog or live callback.
  * Task and Return bindings remain exact. The service still owns the actual work and its current projection. */
 static final class Context {
  final ReturnModel.State returnState;final String taskId,stopMessage;
  Context(ReturnModel.State state,String taskId,String stopMessage){this.returnState=state;this.taskId=taskId;this.stopMessage=stopMessage;}
 }
}
