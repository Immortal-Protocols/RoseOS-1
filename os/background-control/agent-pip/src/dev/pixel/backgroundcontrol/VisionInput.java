package dev.pixel.backgroundcontrol;

/** Text through the session's virtual input device, into the visually focused editor. */
final class VisionInput {
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
}
