package dev.pixel.voice;

/** Geometry and color of the Rose presence for one RoseVisualState, following the Rose three-dimensions study
 * (prototypes/rose-three-dimensions, version 0) without re-deriving its meaning: the connection axis is the
 * vertical gap between two metal hemispheres (joined .012, connecting .10, disconnected .38 of the radius), the
 * microphone axis is the metal (rose when on, silver when muted), and the voice axis is a camera-facing ring
 * (none when quiet, close at 1.075 radii while listening, broad at 1.55 radii plus speech energy while
 * speaking). Values ease toward their targets frame by frame; with animation off they snap and the ring holds
 * still. Rendering choices follow the HiFi prototype's shared renderer (prototypes/rose-hifi/src/renderer.js at ce126f56):
 * easing at 5.5 per second, and frames only while Rose speaks or a value is still settling. Pure logic (no Android types) so it is testable; it never creates session state. */
final class RosePresenceModel {
 static final float GAP_JOINED=.012f,GAP_NARROW=.10f,GAP_WIDE=.38f;
 static final float RING_CLOSE=1.075f,RING_BROAD=1.55f,RING_ENERGY=.12f;
 static final int ROSE=0xfff27a87,SILVER=0xffa6bacf,LIGHT=0xffa3c4ff;
 /** Room around the sphere that RosePresenceView keeps for the broadest ring, in radii (its onDraw divisor). */
 static final float VIEW_RADII=RING_BROAD+RING_ENERGY+.12f;
 /** The HiFi Home tray's Rose: a 42dp sphere (measured in the ce126f56 prototype's tray well, 3x DPR), whose
  * 102px canvas overhangs the 66px well. */
 static final float TRAY_SPHERE_DP=42f;
 /** The square view size that draws a sphere of this diameter with room for the broadest ring. */
 static int viewSizeFor(float sphereDiameter){return Math.round(sphereDiameter*VIEW_RADII);}
 /** Easing rate per second (HiFi renderer). */
 static final float RATE=5.5f;

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
  return !settled||voice>1.001f; // only the speaking surround keeps moving once settled
 }
 /** Ring opacity: zero when quiet, full from listening on. */
 float ringAlpha(){return smooth(0f,.85f,voice);}
 /** Ring radius in sphere radii; time drives the speech envelope only when energy is present. */
 float ringRadius(float time){
  float broad=smooth(1f,2f,voice);
  float envelope=energy*(.48f+.22f*(float)Math.sin(time*3.1)+.17f*(float)Math.sin(time*7.3+1.2)+.09f*(float)Math.sin(time*11.7));
  return RING_CLOSE+(RING_BROAD+RING_ENERGY*envelope-RING_CLOSE)*broad;
 }
 /** Rendered tones of the HiFi renderer's metal (renderer.js at ce126f56: environment reflection, Fresnel, tone map and
  * gamma), measured top to bottom down the sphere in the pinned prototype's own output (Chromium, SwiftShader, 3x
  * DPR; prototype pixels, not device output). The renderer's raw bases (ROSE, SILVER) never appear as such: rose
  * metal reads as a muted mauve, silver as blue steel. Stops are fractions of the sphere's height. */
 static final float[] TONE_STOPS={.05f,.15f,.45f,.5f,.6f,.7f,.95f};
 static final int[] ROSE_TONES={0xffd7c6ce,0xffc8b0b9,0xffc2aab3,0xffaf97a2,0xff877683,0xff756b7a,0xff706979};
 static final int[] SILVER_TONES={0xffcbd3da,0xffb8c2ca,0xffb2bcc4,0xff9daab5,0xff768997,0xff657c8d,0xff5c7689};
 /** The two soft specular spots in the upper half, as (x, y, radius) in sphere radii from the centre. */
 static final float[][] HIGHLIGHTS={{-.32f,-.2f,.26f},{.38f,-.2f,.2f}};
 /** The renderer's camera (eye 0,1.8,7.4 looking at 0,.15,0) sits about 12.6 degrees above the equator, so the
  * rims and the cut face appear as ellipses this flat (minor/major = sin of that elevation). */
 static final float TILT=(float)Math.sin(Math.atan2(1.8-.15,7.4));
 /** The cut face's colour: the renderer's base*.16, through its tone map c/(c+.55) and 1/2.2 gamma. */
 static final int ROSE_FACE=face(.95f,.48f,.53f),SILVER_FACE=face(.65f,.73f,.81f);
 static int face(float r,float g,float b){return 0xff000000|(channel(r)<<16)|(channel(g)<<8)|channel(b);}
 private static int channel(float base){double c=base*.16;c=c/(c+.55);return (int)Math.round(Math.pow(c,1/2.2)*255);}
 /** The rendered tones for the current microphone mix. */
 int[] tones(){int[] t=new int[TONE_STOPS.length];for(int i=0;i<t.length;i++)t[i]=mix(SILVER_TONES[i],ROSE_TONES[i],mic);return t;}
 int faceColor(){return mix(SILVER_FACE,ROSE_FACE,mic);}
 /** The metal's base color for the current microphone mix. */
 int metal(){return mix(SILVER,ROSE,mic);}

 static float smooth(float a,float b,float x){float t=Math.max(0f,Math.min(1f,(x-a)/(b-a)));return t*t*(3f-2f*t);}
 static int mix(int a,int b,float t){
  t=Math.max(0f,Math.min(1f,t));
  int r=Math.round(((a>>16)&255)+(((b>>16)&255)-((a>>16)&255))*t),g=Math.round(((a>>8)&255)+(((b>>8)&255)-((a>>8)&255))*t),bl=Math.round((a&255)+((b&255)-(a&255))*t);
  return 0xff000000|(r<<16)|(g<<8)|bl;
 }
 /** The quiet caption under Rose (HiFi .rose-caption): connection and voice in words, from the same state. It is
  * not a notice and is hidden from accessibility (the presence's description says the same). */
 static String caption(RoseVisualState v){
  if(v.connection==RoseVisualState.Connection.NARROW_GAP)return "Connecting…";
  if(v.connection!=RoseVisualState.Connection.JOINED)return "";
  return v.surround==RoseVisualState.Surround.SPEAKING?"Rose speaking":v.mic==RoseVisualState.Mic.SILVER?"Microphone muted":"Listening";
 }
 /** Spoken summary for accessibility; the visible notices stay the four conversation notices. */
 static String describe(RoseVisualState v){
  String c=v.connection==RoseVisualState.Connection.JOINED?"connected":v.connection==RoseVisualState.Connection.NARROW_GAP?"connecting":"not connected";
  String m=v.mic==RoseVisualState.Mic.ROSE?"microphone on":"microphone off";
  String s=v.surround==RoseVisualState.Surround.SPEAKING?", Rose speaking":v.surround==RoseVisualState.Surround.LISTENING?", listening":"";
  return "Rose: "+c+", "+m+s;
 }
}
