package dev.pixel.backgroundcontrol;

/** Text through the session's virtual input device, into the visually focused editor. */
final class VisionInput {
 /** One slot's own visually declared search. A normal draft never authorizes commit. */
 static final class Search {
  private String query;
  String propose(String text,String purpose,boolean replace){
   validate(text);if(!purpose.equals("text")&&!purpose.equals("search"))throw new IllegalArgumentException("Unknown text purpose");
   if(!purpose.equals("search"))return null;
   String next=replace?text:query==null?null:query+text;
   if(next!=null)validate(next);return next==null||next.trim().isEmpty()?null:next;
  }
  void accept(String next){query=next;}
  void clear(){query=null;}
  void consume(String expected){
   if(query==null||!query.equals(expected))throw new IllegalStateException("No matching visually typed search");
   clear(); // consumed even if the virtual IME reply is lost
  }
 }
 static void validate(String text){
  if(text==null||text.length()>4000)throw new IllegalArgumentException("Text exceeds limit");
  // A line break can act as Enter (a send or submit) in the focused editor even with commit false: never carried.
  for(int i=0;i<text.length();i++){char c=text.charAt(i);
   if(c=='\n'||c=='\r'||c=='\u0085'||c=='\u2028'||c=='\u2029')throw new IllegalArgumentException("Text must be a single line");}
 }
 /** Why vision typing must not reach the session's input focus, or null when it may: found is whether any of the
  * session's windows has an input-focused node; editable and password describe that node. A password field stays the
  * owner's, and with nothing editable focused the text would land nowhere the screenshot showed. */
 static String refusal(boolean found,boolean editable,boolean password){
  if(found&&password)return "Password field requires user";
  if(!found||!editable)return "No focused editable field for vision text";
  return null;
 }
 static void insert(Object session,String text,boolean replace)throws Exception{
  validate(text);
  // The last argument is commit: false never presses the editor's Send/Search/Enter action.
  session.getClass().getMethod("insertText",String.class,boolean.class,boolean.class)
   .invoke(session,text,replace,false);
 }
 static void search(Object session)throws Exception{
  // Search.consume, a fresh screenshot and the non-password input-focus guard
  // precede this call. No accessibility action or physical-screen Enter.
  session.getClass().getMethod("insertText",String.class,boolean.class,boolean.class)
   .invoke(session,"",false,true);
 }
}
