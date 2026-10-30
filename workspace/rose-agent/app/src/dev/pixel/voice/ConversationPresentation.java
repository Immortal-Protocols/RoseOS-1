package dev.pixel.voice;

/** Layout and control glyphs of the Rose conversation screen, from the selected HiFi reference
 * (prototypes/rose-hifi at ce126f56: src/style.css .rose-head/.rose-stage/.rose-caption/.rose-output/.tray,
 * src/icons.js mic/micOff/end/grid). CSS pixels on its 384×832 screen are read as dp and adapted to the actual
 * height and font scale rather than copied: the tray always stays reachable and nothing overlaps it. Pure logic
 * (no Android types) so the production layout decisions are testable; RoseConversationActivity applies them. */
final class ConversationPresentation {
 static final int MARGIN=24,TRAY_HEIGHT=92,TRAY_BOTTOM=34,FAILURE_LINE=28,HEAD_HEIGHT=48,STAGE=220,COMPACT=88,MIN_STAGE=96;
 /** HiFi .rose-head h2 30px, .rose-output h3 44px and p 21px, .rose-caption 15px. */
 static final int HEAD_SP=30,TITLE_SP=44,BODY_SP=21,CAPTION_SP=15,SMALL_TITLE_SP=34,SMALL_BODY_SP=19;

 /** dp above the screen's bottom edge that the tray and the failure line occupy, plus a breathing gap. */
 static int reservedBottom(){return TRAY_BOTTOM+TRAY_HEIGHT+FAILURE_LINE+16;}
 /** dp below the status inset that the header occupies. */
 static int reservedTop(){return HEAD_HEIGHT+8;}

 final int stage;final boolean compact;final int titleSp,bodySp;
 private ConversationPresentation(int stage,boolean compact,int titleSp,int bodySp){this.stage=stage;this.compact=compact;this.titleSp=titleSp;this.bodySp=bodySp;}

 /** heightDp: the activity's content height without the status inset; fontScale: the system font scale. */
 static ConversationPresentation of(int heightDp,boolean result,float fontScale){
  int available=Math.max(0,heightDp-reservedTop()-reservedBottom());
  boolean tight=heightDp<640||fontScale>1.3f;
  int titleSp=tight?SMALL_TITLE_SP:TITLE_SP,bodySp=tight?SMALL_BODY_SP:BODY_SP;
  if(result)return new ConversationPresentation(COMPACT,true,titleSp,bodySp);
  // Empty: Rose large and centred with its caption; shrink only when the screen (or larger text) leaves no room.
  int caption=Math.round(24*Math.max(1f,fontScale));
  int stage=Math.max(MIN_STAGE,Math.min(STAGE,available-caption-32));
  return new ConversationPresentation(stage,false,titleSp,bodySp);
 }

 /** Line glyphs drawn in the tray wells (HiFi icons.js); one per meaning so the three wells read distinctly. */
 enum Glyph { MIC, MIC_OFF, CANCEL, END, APPS }
 /** The microphone well: Speak (mic) when idle, Cancel while connecting, Mute (mic) / Unmute (mic off) in a call. */
 static Glyph primary(ConversationState s){
  if(s.active())return s.muted?Glyph.MIC_OFF:Glyph.MIC;
  return s.phase==ConversationState.Phase.CONNECTING?Glyph.CANCEL:Glyph.MIC;
 }
}
