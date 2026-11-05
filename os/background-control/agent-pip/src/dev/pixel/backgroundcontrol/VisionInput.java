package dev.pixel.backgroundcontrol;

/** Text through the session's virtual input device, into the visually focused editor. */
final class VisionInput {
 static void validate(String text){
  if(text==null||text.length()>4000)throw new IllegalArgumentException("Text exceeds limit");
 }
 static void insert(Object session,String text,boolean replace)throws Exception{
  validate(text);
  // The last argument is commit: false never presses the editor's Send/Search/Enter action.
  session.getClass().getMethod("insertText",String.class,boolean.class,boolean.class)
   .invoke(session,text,replace,false);
 }
}
