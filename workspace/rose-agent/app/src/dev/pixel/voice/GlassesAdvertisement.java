package dev.pixel.voice;

/** Observed on RB Meta 05XH firmware; unknown formats must not trigger actions. */
final class GlassesAdvertisement {
 static int hinge(byte[] manufacturerData) {
  if(manufacturerData==null||manufacturerData.length<5)return -1;
  if((manufacturerData[0]&255)!=0x80||(manufacturerData[1]&255)!=0x0a||
     (manufacturerData[2]&255)!=0x01||(manufacturerData[3]&255)!=0x33)return -1;
  int state=manufacturerData[4]&255;
  return state==0x04?0:state==0x14?1:-1;
 }
}
