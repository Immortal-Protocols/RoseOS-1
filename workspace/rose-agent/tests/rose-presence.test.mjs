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
 // #410: the header pearl and the caption draw the same state (OxbloodRose.caption; speaking only from fresh output).
 assert.match(refresh,/showPearl\(visual\);\n  String cap=OxbloodRose\.caption\(state,visual\);/,'pearl and caption draw the same state');
 assert.match(refresh,/RoseVisualState\.fresh\(s\.trayInputLevel,seen,now\)/);assert.match(refresh,/RoseVisualState\.fresh\(s\.trayOutputLevel,seen,now\)/);
 assert.match(refresh,/seen=s==null\?0L:s\.trayAudioObservedAt/,'levels are dated by VoiceService\'s own observation time');
 assert.match(refresh,/s==null\?Float\.NaN:/,'no service, no levels: nothing is inferred');
 assert.match(refresh,/reducedMotion\(\),resumed\);/,'reduced motion and visibility come from the activity');
 for(const text of [view,model])assert.ok(!/ConversationState|\.muted\b|trayOutputLevel|trayInputLevel|SPEAKING_LEVEL/.test(text),
  'the renderer reads only RoseVisualState: no second state mapping');
 const pearl=source('RosePearl.java');
 assert.ok(!/VoiceService|ConversationState|send\(|Intent/.test(pearl),'the pearl draws only what it is given');
});
test('the old petal presence is gone, not kept as an alternative',()=>{
 assert.ok(!/class Presence\b|petal|translucent petals|ValueAnimator/.test(activity),'Presence class and its animation removed');
 // #410: the conversation shows Rose as the header pearl (RosePearl); its only animation is the pearl's own breath.
 assert.match(activity,/pearl=new RosePearl\(density,16\);/);
 assert.match(activity,/boolean breathing=speaking&&v\.animate;\n  pearl\.breathe\(breathing\);/,'breath only while visible with animations on');
});
test('the renderer is visual only and suspends when not visible',()=>{
 assert.ok(!/send\(|startForegroundService|VoiceService\.|task\/stop|new Intent|evaluateJavascript|WebView|GLSurfaceView|EGL/.test(view),'no command, task, bridge or GL dependency');
 assert.match(view,/onDetachedFromWindow\(\)\{running=false;/);assert.match(view,/onWindowVisibilityChanged[^]*?running=false/);
 assert.match(view,/if\(model\.step\(dt\)\)postInvalidateOnAnimation\(\);else running=false;/,'frames only while changing');
 assert.match(view,/setContentDescription\(d\)/,'state is described for accessibility');
 assert.match(activity,/onPause\(\)\{resumed=false;handler\.removeCallbacks\(tick\);stopBreath\(\);/,'leaving the screen stops the pearl\'s breath');
 // The study's state meaning, recorded once in the model.
 assert.match(model,/GAP_JOINED=\.012f,GAP_NARROW=\.10f,GAP_WIDE=\.38f/);assert.match(model,/RING_CLOSE=1\.075f,RING_BROAD=1\.55f,RING_ENERGY=\.12f/);
});
test('voice, results, Watch/Stop, notices and Update Rose Agent are unchanged',()=>{
 // #410: one Connect/Disconnect control: Connect is talk (after the microphone permission), Disconnect ends or cancels
 // connecting (end); it never mutes. Rose's own call mute stays, named as Rose's, only while the call is live.
 const tap=activity.slice(activity.indexOf(' void connectTapped(){'),activity.indexOf(' /** The header pearl'));
 assert.match(tap,/if\(live\)\{send\("end"\);return;\}/);
 assert.match(tap,/checkSelfPermission\("android\.permission\.RECORD_AUDIO"\)!=PackageManager\.PERMISSION_GRANTED/);
 assert.match(tap,/send\("talk"\);/);assert.ok(!/mute/.test(tap),'Disconnect never mutes');
 assert.match(activity,/mic\.setOnClickListener\(v->\{if\(state!=null&&state\.active\(\)\)send\(state\.muted\?"tray_unmute":"tray_mute"\);\}\);/);
 assert.match(activity,/mic\.setContentDescription\(state\.muted\?"Rose’s microphone is muted\. Unmute":"Mute Rose’s microphone"\);/);
 // F08: still only during a live call, and now only where the OS Rose chin is not reachable (RoseChin).
 assert.match(activity,/mic\.setVisibility\(state\.active\(\)&&RoseChin\.showInAppMute\(AgentTrayService\.chinReachable\(\),navigationMode\(\)\)\?View\.VISIBLE:View\.GONE\);/);
 assert.match(activity,/setOnClickListener\(v->openWorkspace\(\)\)/);assert.match(activity,/setPositiveButton\("Stop task",\(d,w\)->sendStop\(id\)\)/);
 assert.match(activity,/add\("Update Rose Agent"\)\.setOnMenuItemClickListener\(i->\{checkForUpdate\(\);return true;\}\)/);
 assert.match(activity,/presentedScroll\.setVisibility\(t\.visible\?View\.VISIBLE:View\.GONE\)/,'results stay empty when absent');
 assert.ok(!/Interrupt|EditText|Listening…|Speaking…/.test(activity),'no composer, Interrupt or listening/speaking notice');
});
test('Oxblood composition: header pearl and title, upper-right work entry, caption, results type and one Connect/Disconnect (#410)',()=>{
 assert.match(activity,/title\.setText\("Rose"\);title\.setAccessibilityHeading\(true\)/);
 assert.match(activity,/caption\.setImportantForAccessibility\(View\.IMPORTANT_FOR_ACCESSIBILITY_NO\)/,'the caption is not a notice or a second announcement');
 assert.match(activity,/ConversationPresentation\.of\([^;]*,t\.visible,cfg\.fontScale\)/,'type sizes from the tested layout (ConversationPresentationTest)');
 assert.match(activity,/taskTitle=presentedText\(ConversationPresentation\.TITLE_SP,ink\(\)/);assert.match(activity,/taskBody=presentedText\(ConversationPresentation\.BODY_SP,ink2\(\)/);
 assert.match(activity,/work\.setContentDescription\("Open Rose’s background work"\);work\.setOnClickListener\(v->openOverview\(\)\);/);
 assert.ok(!/work\.setEnabled\(false\)/.test(activity),'the work entry is never disabled');
 assert.match(activity,/workGlyph\.set\(ctl\.working\(resumedAt,SystemClock\.uptimeMillis\(\)\),reducedMotion\(\)\);/,'any authoritative session ready, from a current read only');
 assert.doesNotMatch(activity,/workShown|WORK_HOLD/,'never a held last-shown state');
 assert.match(activity,/resumed=true;resumedAt=SystemClock\.uptimeMillis\(\);ctlPolledAt=0;/,'a resume makes older reads stale and starts a fresh read at once');
 assert.match(activity,/\}\)\.withSessions\(summarized\[0\]\?slots:null,startedAt\);/,'the same get_state reply\'s routed sessions summary');
 assert.match(activity,/slots\.add\(new String\[\]\{jsonString\(x,"sessionId"\),jsonString\(x,"status"\)\}\)/,'only each slot\'s id and status are kept');
 assert.doesNotMatch(activity,/workGlyph\.set\([^;]*"launching"/,'the work button never lights while a session is only launching');
 assert.match(activity,/connect\.setBackground\(new OxbloodRose\.Pill\(density\)\);/);
 assert.match(activity,/connect\.setContentDescription\(live\?\(state\.phase==ConversationState\.Phase\.CONNECTING\?"Cancel connecting to Rose":"Disconnect from Rose"\):"Connect to Rose"\);/);
 assert.match(activity,/if\(!t\.visible\)\{shownTaskId="";shownTaskActive=false;return;\}/);
 assert.match(activity,/TaskControl\.WatchState ws=TaskControl\.watchState\(t\.id,t\.active\(\),ctl\);boolean canWatch=!localTask&&ws\.canWatch;/,'the Node task\'s View workspace keeps the classified read; local cards stay read-only');
 assert.match(activity,/boolean canStop=!localTask&&running&&stopPendingFor==null&&!stopInFlight;/,'local task cards never call the unrelated Node task Stop route');
 assert.ok(!/Interrupt|EditText/.test(activity),'no composer or Interrupt');
});
test('#410: the Oxblood Rose field and the shared layout decisions',()=>{
 const presentation=source('ConversationPresentation.java');
 assert.match(activity,/root\.setBackground\(new OxbloodField\(OxbloodField\.ROSE\)\);/,'the Oxblood Rose field in either system theme');
 assert.match(activity,/ConversationPresentation lay=ConversationPresentation\.of\(/,'type comes from the tested layout');
 assert.match(activity,/taskTitle\.setTextSize\(lay\.titleSp\);taskBody\.setTextSize\(lay\.bodySp\);/);
 assert.match(activity,/cp\.bottomMargin=dp\(110\);/,'results end above the Connect control');
 assert.match(presentation,/HEAD_SP=30,TITLE_SP=44,BODY_SP=21,CAPTION_SP=15/);
 assert.ok(!/TrayMaterial|IvoryBackground/.test(activity),'no HiFi tray or ivory surface left');
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

test('#410: the caption and output sit at the Oxblood positions',()=>{
 // oxblood.css:399-400: the caption 88dp under the header's top, the output from 121dp under it, both 30dp in.
 assert.match(activity,/capp\.topMargin=headTop\+dp\(88\);capp\.leftMargin=dp\(30\);/);
 assert.match(activity,/cp\.topMargin=headTop\+dp\(121\);/);
});
