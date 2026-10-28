package dev.pixel.voice;

/** Budget for recreating a WebView after its renderer process is gone. All of the app's WebViews share
 * one renderer, so a renderer that dies again right after being recreated must not loop: after `limit`
 * recreations within `windowMs`, the owner stays in a truthful unavailable state until an explicit
 * user action. Pure logic so it is testable. */
final class RendererRecovery {
 private final int limit;private final long windowMs;private final long[] times;private int count;
 RendererRecovery(int limit,long windowMs){this.limit=limit;this.windowMs=windowMs;this.times=new long[limit];}
 /** Records one renderer loss at `now` and says whether the view may be reloaded automatically. */
 boolean allow(long now){
  int recent=0;for(int i=0;i<count;i++)if(now-times[i]<windowMs)times[recent++]=times[i];count=recent;
  if(count>=limit)return false;
  times[count++]=now;return true;
 }
 /** An explicit user action (Speak, reopening a panel) starts a fresh budget. */
 void reset(){count=0;}
}
