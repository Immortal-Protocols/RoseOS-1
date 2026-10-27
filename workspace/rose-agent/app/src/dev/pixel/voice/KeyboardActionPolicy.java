package dev.pixel.voice;
/** Select only the active same-app editor's advertised action. No app-specific rules. */
final class KeyboardActionPolicy {
 static int select(String targetPackage,String editorPackage,boolean started,int imeOptions,int actionId,String actionLabel){
  if(!started||targetPackage==null||!targetPackage.equals(editorPackage))return -1;
  if(actionId>0&&actionLabel!=null&&!actionLabel.trim().isEmpty())return actionId;
  int action=imeOptions&0xff;
  return action>=2&&action<=7?action:-1;
 }
}
