package dev.pixel.backgroundcontrol;

/** Real reflection call against the platform method's signature; no Android or accessibility. */
public final class VisionInputTest {
 public static final class Session {
  String editor="existing";int calls;boolean submitted;
  public void insertText(String text,boolean replace,boolean commit){
   calls++;editor=replace?text:editor+text;submitted|=commit;
  }
 }
 public static void main(String[] args)throws Exception{
  Session s=new Session();
  VisionInput.insert(s,"Feedback\nSecond line \uD83C\uDF39",true);
  if(!s.editor.equals("Feedback\nSecond line \uD83C\uDF39")||s.submitted)throw new AssertionError("Literal draft text, never submit");
  VisionInput.insert(s," appended",false);
  if(!s.editor.endsWith(" appended")||s.calls!=2||s.submitted)throw new AssertionError("Append preserves text without submission");
  for(String invalid:new String[]{null,new String(new char[4001])}){
   try{VisionInput.insert(s,invalid,true);throw new AssertionError("Expected length rejection");}
   catch(IllegalArgumentException expected){}
  }
  if(s.calls!=2)throw new AssertionError("Invalid input reached the platform");
  System.out.println("VisionInputTest passed");
 }
}
