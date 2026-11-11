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
  VisionInput.insert(s,"Feedback, second line \uD83C\uDF39",true);
  if(!s.editor.equals("Feedback, second line \uD83C\uDF39")||s.submitted)throw new AssertionError("Literal draft text, never submit");
  VisionInput.insert(s," appended",false);
  if(!s.editor.endsWith(" appended")||s.calls!=2||s.submitted)throw new AssertionError("Append preserves text without submission");
  for(String invalid:new String[]{null,new String(new char[4001]),"a\nb","sent\r","a\u0085b","a\u2028b","a\u2029b"}){
   try{VisionInput.insert(s,invalid,true);throw new AssertionError("Expected rejection: "+(invalid==null?"null":invalid.length()));}
   catch(IllegalArgumentException expected){}
  }
  if(s.calls!=2)throw new AssertionError("Invalid input reached the platform");
  // The focused-field rule: only a focused, editable, non-password node takes vision text.
  if(VisionInput.refusal(true,true,false)!=null)throw new AssertionError("Focused editor allowed");
  if(!"Password field requires user".equals(VisionInput.refusal(true,true,true)))throw new AssertionError("Password refused");
  if(!"Password field requires user".equals(VisionInput.refusal(true,false,true)))throw new AssertionError("Password refused first");
  if(VisionInput.refusal(false,false,false)==null||VisionInput.refusal(true,false,false)==null)
   throw new AssertionError("Nothing editable focused fails closed");
  VisionInput.Search search=new VisionInput.Search(),other=new VisionInput.Search();
  search.accept(search.propose("ordinary draft","text",true));
  try{search.consume("ordinary draft");throw new AssertionError("Draft became a submission");}catch(IllegalStateException expected){}
  search.accept(search.propose("Sunnyvale","search",true));
  search.accept(search.propose(" cafes","search",false));
  try{other.consume("Sunnyvale cafes");throw new AssertionError("Cross-slot search accepted");}catch(IllegalStateException expected){}
  try{search.consume("different");throw new AssertionError("Mismatched query accepted");}catch(IllegalStateException expected){}
  search.consume("Sunnyvale cafes");VisionInput.search(s);
  if(!s.submitted||!s.editor.endsWith(" appended"))throw new AssertionError("IME search must not rewrite the query");
  try{search.consume("Sunnyvale cafes");throw new AssertionError("Search replay accepted");}catch(IllegalStateException expected){}
  search.accept(search.propose("again","search",true));search.clear();
  try{search.consume("again");throw new AssertionError("Cleared search accepted");}catch(IllegalStateException expected){}
  System.out.println("VisionInputTest passed");
 }
}
