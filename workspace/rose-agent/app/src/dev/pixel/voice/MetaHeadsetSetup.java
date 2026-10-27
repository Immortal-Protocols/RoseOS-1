package dev.pixel.voice;
final class MetaHeadsetSetup {
 static boolean use(int type,String name,String address,boolean compatibility){
  if(type!=7)return false;
  if(GlassesReconnect.ADDRESS.equalsIgnoreCase(address))return true;
  String label=name==null?"":name.toLowerCase(java.util.Locale.ROOT);
  return compatibility&&(label.startsWith("rb meta")||label.startsWith("ray-ban meta")||label.startsWith("oakley meta"));
 }
}
