package dev.pixel.voice;

/** Preserve an explicit End across brief profile reconnects. */
final class GlassesAutoStartPolicy {
 static final long NEW_WEARING_GAP_MS=10000;
 private boolean suppressed;
 private long disconnectedAt=-1;
 private boolean sawClosed;
 GlassesAutoStartPolicy(boolean suppressed){this.suppressed=suppressed;}
 void userEnded(){suppressed=true;sawClosed=false;disconnectedAt=-1;}
 void userStarted(){suppressed=false;sawClosed=false;disconnectedAt=-1;}
 void disconnected(long now){if(disconnectedAt<0)disconnectedAt=now;}
 boolean connected(long now){
  if(suppressed&&disconnectedAt>=0&&now-disconnectedAt>=NEW_WEARING_GAP_MS)suppressed=false;
  disconnectedAt=-1;return !suppressed;
 }
 void hinge(boolean open){if(!open)sawClosed=true;else if(sawClosed){userStarted();}}
 boolean suppressed(){return suppressed;}
}
