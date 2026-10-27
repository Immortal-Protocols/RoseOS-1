package dev.pixel.voice;
final class GlassesButtonGate {
 private int key=-1;private long down=-1,at=-10000;
 private boolean zeroPressed=false;
 boolean accept(int k,int action,int repeats,long downTime,long now){
  if((action!=0&&action!=1)||repeats!=0)return false;
  // AVRCP may omit timestamps entirely. Zero is not a gesture identifier.
  if(downTime==0){
   if(action==1&&zeroPressed&&k==key){zeroPressed=false;return false;}
   if(k==key&&now-at<400)return false;
   zeroPressed=action==0;key=k;down=0;at=now;return true;
  }
  zeroPressed=false;
  if(k==key&&downTime==down&&now-at<2000)return false;
  key=k;down=downTime;at=now;return true;
 }
}
