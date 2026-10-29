package dev.pixel.voice;

/** Geometry and color of the Rose presence for one RoseVisualState, following the Rose three-dimensions study
 * (prototypes/rose-three-dimensions, version 0) without re-deriving its meaning: the connection axis is the
 * vertical gap between two metal hemispheres (joined .012, connecting .10, disconnected .38 of the radius), the
 * microphone axis is the metal (rose when on, silver when muted), and the voice axis is a camera-facing ring
 * (none when quiet, close at 1.075 radii while listening, broad at 1.55 radii plus speech energy while
 * speaking). Values ease toward their targets frame by frame; with animation off they snap and the ring holds
 * still. Pure logic (no Android types) so it is testable; it never creates session state. */
final class RosePresenceModel {
 static final float GAP_JOINED=.012f,GAP_NARROW=.10f,GAP_WIDE=.38f;
 static final float RING_CLOSE=1.075f,RING_BROAD=1.55f,RING_ENERGY=.12f;
 static final int ROSE=0xfff27a87,SILVER=0xffa6bacf,LIGHT=0xffa3c4ff;
 /** Easing rate per second, as in the study's per-frame approach to its target. */
 static final float RATE=6f;

 float gap=GAP_WIDE,mic=0f,voice=0f,energy=0f;
 private float targetGap=GAP_WIDE,targetMic=0f,targetVoice=0f,targetEnergy=0f;private boolean animate;

 static float gapFor(RoseVisualState.Connection c){return c==RoseVisualState.Connection.JOINED?GAP_JOINED:c==RoseVisualState.Connection.NARROW_GAP?GAP_NARROW:GAP_WIDE;}
 static float voiceFor(RoseVisualState.Surround s){return s==RoseVisualState.Surround.SPEAKING?2f:s==RoseVisualState.Surround.LISTENING?1f:0f;}

 /** Sets the targets; without animation the presence shows them at once. */
 void set(RoseVisualState v){
  targetGap=gapFor(v.connection);targetMic=v.mic==RoseVisualState.Mic.ROSE?1f:0f;targetVoice=voiceFor(v.surround);
  targetEnergy=v.surround==RoseVisualState.Surround.SPEAKING?v.amplitude:0f;animate=v.animate;
  if(!animate){gap=targetGap;mic=targetMic;voice=targetVoice;energy=0f;}
 }
 /** Advances dt seconds; returns true while another frame is needed. */
 boolean step(float dt){
  if(!animate)return false;
  float k=1f-(float)Math.exp(-RATE*Math.max(0f,Math.min(dt,.05f)));
  gap+=(targetGap-gap)*k;mic+=(targetMic-mic)*k;voice+=(targetVoice-voice)*k;energy+=(targetEnergy-energy)*k;
  boolean settled=Math.abs(targetGap-gap)<.001f&&Math.abs(targetMic-mic)<.002f&&Math.abs(targetVoice-voice)<.002f&&Math.abs(targetEnergy-energy)<.002f;
  if(settled){gap=targetGap;mic=targetMic;voice=targetVoice;energy=targetEnergy;}
  return !settled||voice>0f; // a lit ring keeps breathing while visible
 }
 /** Ring opacity: zero when quiet, full from listening on. */
 float ringAlpha(){return smooth(0f,.85f,voice);}
 /** Ring radius in sphere radii; time drives the speech envelope only when energy is present. */
 float ringRadius(float time){
  float broad=smooth(1f,2f,voice);
  float envelope=energy*(.48f+.22f*(float)Math.sin(time*3.1)+.17f*(float)Math.sin(time*7.3+1.2)+.09f*(float)Math.sin(time*11.7));
  return RING_CLOSE+(RING_BROAD+RING_ENERGY*envelope-RING_CLOSE)*broad;
 }
 /** The metal's base color for the current microphone mix. */
 int metal(){return mix(SILVER,ROSE,mic);}

 static float smooth(float a,float b,float x){float t=Math.max(0f,Math.min(1f,(x-a)/(b-a)));return t*t*(3f-2f*t);}
 static int mix(int a,int b,float t){
  t=Math.max(0f,Math.min(1f,t));
  int r=Math.round(((a>>16)&255)+(((b>>16)&255)-((a>>16)&255))*t),g=Math.round(((a>>8)&255)+(((b>>8)&255)-((a>>8)&255))*t),bl=Math.round((a&255)+((b&255)-(a&255))*t);
  return 0xff000000|(r<<16)|(g<<8)|bl;
 }
 /** Spoken summary for accessibility; the visible notices stay the four conversation notices. */
 static String describe(RoseVisualState v){
  String c=v.connection==RoseVisualState.Connection.JOINED?"connected":v.connection==RoseVisualState.Connection.NARROW_GAP?"connecting":"not connected";
  String m=v.mic==RoseVisualState.Mic.ROSE?"microphone on":"microphone off";
  String s=v.surround==RoseVisualState.Surround.SPEAKING?", Rose speaking":v.surround==RoseVisualState.Surround.LISTENING?", listening":"";
  return "Rose: "+c+", "+m+s;
 }
}
