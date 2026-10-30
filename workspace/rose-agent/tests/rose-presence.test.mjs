// APP-HIFI-CONVERSATION-1029-P: the conversation screen draws the Rose presence from Core's RoseVisualState
// with real VoiceService observations, replaces the old petal Presence, and keeps every existing control.
// Source checks of the real Android files; RosePresenceModelTest executes the mapping. No device.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const dir=new URL('../app/src/dev/pixel/voice/',import.meta.url);
const source=name=>fs.readFileSync(new URL(name,dir),'utf8');
const activity=source('RoseConversationActivity.java'),view=source('RosePresenceView.java'),model=source('RosePresenceModel.java');

test('the presence is Core\'s RoseVisualState drawn from real, fresh VoiceService levels',()=>{
 const refresh=activity.slice(activity.indexOf('void refresh(){'),activity.indexOf('void renderTask('));
 assert.match(refresh,/RoseVisualState visual=RoseVisualState\.from\(state,/,'one call into Core\'s adapter; no mapping duplicated here');
 assert.match(refresh,/presence\.show\(visual\);caption\.setText\(RosePresenceModel\.caption\(visual\)\);/,'presence and caption draw the same state');
 assert.match(refresh,/RoseVisualState\.fresh\(s\.trayInputLevel,seen,now\)/);assert.match(refresh,/RoseVisualState\.fresh\(s\.trayOutputLevel,seen,now\)/);
 assert.match(refresh,/seen=s==null\?0L:s\.trayAudioObservedAt/,'levels are dated by VoiceService\'s own observation time');
 assert.match(refresh,/s==null\?Float\.NaN:/,'no service, no levels: nothing is inferred');
 assert.match(refresh,/reducedMotion\(\),resumed\);/,'reduced motion and visibility come from the activity');
 for(const text of [view,model])assert.ok(!/ConversationState|\.muted\b|trayOutputLevel|trayInputLevel|SPEAKING_LEVEL/.test(text),
  'the renderer reads only RoseVisualState: no second state mapping');
});

test('the old petal presence is gone, not kept as an alternative',()=>{
 assert.ok(!/class Presence\b|petal|translucent petals|ValueAnimator/.test(activity),'Presence class and its animation removed');
 assert.match(activity,/RosePresenceView presence;/);assert.match(activity,/presence=new RosePresenceView\(this\)/);
});

test('the renderer is visual only and suspends when not visible',()=>{
 assert.ok(!/send\(|startForegroundService|VoiceService\.|task\/stop|new Intent|evaluateJavascript|WebView|GLSurfaceView|EGL/.test(view),'no command, task, bridge or GL dependency');
 assert.match(view,/onDetachedFromWindow\(\)\{running=false;/);assert.match(view,/onWindowVisibilityChanged[^]*?running=false/);
 assert.match(view,/if\(model\.step\(dt\)\)postInvalidateOnAnimation\(\);else running=false;/,'frames only while changing');
 assert.match(view,/setContentDescription\(d\)/,'state is described for accessibility');
 assert.match(activity,/onPause\(\)\{resumed=false;[^}]*presence\.show\(RoseVisualState\.from\(state,Float\.NaN,Float\.NaN,true,false\)\)/,'leaving the screen stops the animation');
 // The study's state meaning, recorded once in the model.
 assert.match(model,/GAP_JOINED=\.012f,GAP_NARROW=\.10f,GAP_WIDE=\.38f/);assert.match(model,/RING_CLOSE=1\.075f,RING_BROAD=1\.55f,RING_ENERGY=\.12f/);
});

test('voice, results, Watch/Stop, notices and Update Rose Agent are unchanged',()=>{
 assert.match(activity,/ConversationPresentation\.Glyph glyph=ConversationPresentation\.primary\(state\);/);
 assert.match(activity,/primary\.show\(glyph,state\.muted\?"Unmute":"Mute",state\.muted\?"Microphone muted\. Unmute":"Microphone on\. Mute"\)/);
 assert.match(activity,/primary\.show\(glyph,"Speak","Start voice conversation"\)/);assert.match(activity,/primary\.show\(glyph,"Cancel","Cancel voice connection"\)/);assert.match(activity,/end\.available\(state\.canEnd\(\)\)/);
 assert.match(activity,/setOnClickListener\(v->openWorkspace\(\)\)/);assert.match(activity,/setPositiveButton\("Stop task",\(d,w\)->sendStop\(id\)\)/);
 assert.match(activity,/add\("Update Rose Agent"\)\.setOnMenuItemClickListener\(i->\{checkForUpdate\(\);return true;\}\)/);
 assert.match(activity,/presentedScroll\.setVisibility\(t\.visible\?View\.VISIBLE:View\.GONE\)/,'results stay empty when absent');
 assert.ok(!/Interrupt|EditText|Listening…|Speaking…/.test(activity),'no composer, Interrupt or listening/speaking notice');
});

test('HiFi composition: header, caption, results type and the three-well tray with the real workspace route',()=>{
 assert.match(activity,/head\.setText\("Rose"\);head\.setAccessibilityHeading\(true\)/);
 assert.match(activity,/caption\.setImportantForAccessibility\(View\.IMPORTANT_FOR_ACCESSIBILITY_NO\)/,'the caption is not a notice or a second announcement');
 assert.match(activity,/ConversationPresentation\.of\([^;]*,t\.visible,cfg\.fontScale\)/,'Rose is large without a result and compact beside its caption with one (ConversationPresentationTest)');
 assert.match(activity,/taskTitle=presentedText\(ConversationPresentation\.TITLE_SP,ink\(\)/);assert.match(activity,/taskBody=presentedText\(ConversationPresentation\.BODY_SP,ink2\(\)/);
 assert.match(activity,/agents\.show\(ConversationPresentation\.Glyph\.APPS,"Rose's apps","View this task's background workspace"\);agents\.setOnClickListener\(v->openWorkspace\(\)\);agents\.available\(false\)/,
  'Rose\'s apps opens the existing bound workspace route and starts disabled');
 assert.match(activity,/agents\.available\(canWatch\)/);assert.match(activity,/if\(!t\.visible\)\{shownTaskId="";agents\.available\(false\);return;\}/,'enabled only for a watchable shown task');
 assert.ok(!/Interrupt|EditText/.test(activity),'no composer or Interrupt in the tray');
});

test('#159: ivory reading surface, shared layout decisions and legible drawn tray glyphs',()=>{
 const drawing=source('ConversationDrawing.java'),presentation=source('ConversationPresentation.java');
 assert.match(activity,/if\(ivory\)root\.setBackground\(new ConversationDrawing\.IvoryBackground\(this\)\);else root\.setBackgroundColor\(paper\(\)\);/,'HiFi ivory surface in light mode, dark unchanged');
 assert.match(drawing,/0xfff7f9fc,0xffeef3f9/,'HiFi .ivory-bg gradient');assert.match(drawing,/R\.drawable\.rose_home_wallpaper/,'existing wallpaper reused read-only, not duplicated');
 assert.match(activity,/ConversationPresentation lay=ConversationPresentation\.of\(/,'stage and type come from the tested layout');
 assert.match(activity,/int size=dp\(lay\.stage\)/);assert.match(activity,/taskTitle\.setTextSize\(lay\.titleSp\);taskBody\.setTextSize\(lay\.bodySp\);/);
 assert.match(activity,/cp\.bottomMargin=dp\(ConversationPresentation\.reservedBottom\(\)\)/,'results end above the tray');
 assert.match(activity,/end\.show\(ConversationPresentation\.Glyph\.END,"End call","End voice conversation"\)/);
 assert.ok(![...activity.matchAll(/(?:primary|end|agents)\.show\(([^,]+),/g)].some(m=>/"/.test(m[1])),'tray wells take a drawn glyph, never an emoji string');
 for(const g of ['MIC','MIC_OFF','END','CANCEL','APPS'])assert.match(drawing,new RegExp('case '+g+'[:,]'),g+' is drawn');
 assert.match(drawing,/setImportantForAccessibility\(IMPORTANT_FOR_ACCESSIBILITY_NO\)/,'icons are decorative; wells keep their spoken names');
 assert.match(presentation,/HEAD_SP=30,TITLE_SP=44,BODY_SP=21,CAPTION_SP=15/);
});

test('APP-OS-VISUAL-1030: the shared Rose draws the HiFi renderer\'s rendered metal and camera geometry',()=>{
 // Tones come from the model for the eased microphone mix, never the raw #f27a87/#a6bacf base painted directly.
 // Each shell has its own run so the step to dark sits on the perspective seam (native render, M003).
 assert.match(view,/new LinearGradient\(cx,upper-r,cx,upper\+flat,model\.upperTones\(\),RosePresenceModel\.UPPER_STOPS,Shader\.TileMode\.CLAMP\)/);
 assert.match(view,/new LinearGradient\(cx,lower,cx,lower\+r,model\.lowerTones\(\),RosePresenceModel\.LOWER_STOPS,Shader\.TileMode\.CLAMP\)/);
 // No contact shadow (the reference canvas has none) and the renderer's thin seam.
 assert.ok(!/0x33283e39/.test(view),'no contact shadow');
 assert.match(view,/ring\.setStrokeWidth\(Math\.max\(1f,r\*\.012f\)\);ring\.setColor\(0x42000000\);/);
 assert.ok(!/model\.metal\(\)/.test(view),'no raw base fill');
 // Rims and the cut face are ellipses flattened by the camera tilt; the face uses the shader-derived colour.
 assert.match(view,/flat=RosePresenceModel\.TILT\*r/);
 assert.match(view,/paint\.setShader\(null\);paint\.setColor\(model\.faceColor\(\)\);/);
 assert.match(view,/half\.arcTo\(box,180f,180f,false\);\s*box\.set\(cx-r,upper-flat,cx\+r,upper\+flat\);half\.arcTo\(box,0f,180f,false\);half\.close\(\);/);
 // Highlights stay inside the upper shell; the seam is the renderer's 26% darkening.
 assert.match(view,/c\.save\(\);c\.clipPath\(half\);\s*for\(float\[\] h:RosePresenceModel\.HIGHLIGHTS\)/);
 assert.match(view,/ring\.setColor\(0x42000000\)/);
 // Regression: no statement hides behind a line comment (the cut face once lost its colour this way).
 for(const line of view.split('\n')){const i=line.indexOf('//');if(i>=0)assert.ok(!/;\s*\S/.test(line.slice(i))||!/paint\.|ring\.|c\.draw/.test(line.slice(i)),'code after a comment: '+line.trim());}
 // State meaning is unchanged: gap, ring and accessibility still come from the same model and description.
 assert.match(view,/float lift=model\.gap\*r/);assert.match(view,/float a=model\.ringAlpha\(\);/);
 assert.match(view,/String d=RosePresenceModel\.describe\(v\)/);
});

test('APP-OS-VISUAL-1030: the compact result Rose and caption sit at the HiFi positions',()=>{
 // .screen[data-result=true]: the 220 stage scaled .4 (88) ends where the caption starts (margin + 84); no extra inset.
 assert.match(activity,/stage\.setPadding\(0,0,0,0\);caption\.setGravity\(t\.visible\?Gravity\.START\|Gravity\.CENTER_VERTICAL:Gravity\.CENTER\);/);
 assert.ok(!/stage\.setPadding\(t\.visible\?dp\(12\)/.test(activity),'no 12dp inset before the compact Rose');
});

