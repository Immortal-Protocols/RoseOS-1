// Source checks for the launcher Home page; no device needed.
import fs from 'node:fs';
import assert from 'node:assert/strict';
import test from 'node:test';
import crypto from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const read=path=>fs.readFileSync(new URL(path,import.meta.url),'utf8');
const main=read('../app/src/dev/pixel/voice/MainActivity.java');
const home=read('../app/src/dev/pixel/voice/RoseHomeView.java');
const layout=read('../app/src/dev/pixel/voice/HomeAppLayout.java');
const homeTray=read('../app/src/dev/pixel/voice/HomeTray.java');
const homeFrame=read('../app/src/dev/pixel/voice/HomeFrame.java');
const homeRose=read('../app/src/dev/pixel/voice/HomeRose.java');
const homeOverview=read('../app/src/dev/pixel/voice/HomeOverview.java');
const cell=read('../app/src/dev/pixel/voice/HomeAppTile.java');
const manifest=read('../app/AndroidManifest.xml');

test('the HOME activity identity is unchanged',()=>{
 assert.match(manifest,/package="dev\.pixel\.voice"/);
 const activity=manifest.match(/<activity android:name="\.MainActivity"[\s\S]*?<\/activity>/)[0];
 assert.match(activity,/launchMode="singleTask"/);
 assert.match(activity,/android\.intent\.category\.HOME/);
 assert.match(activity,/android\.intent\.category\.LAUNCHER/);
 // Released 2026102701 bundles 298; a changed app must be newer or an installed 298 keeps winning.
 assert.ok(Number(manifest.match(/android:versionCode="(\d+)"/)[1])>298);
});

test('HOME and the app icon land on Rose Home; the icon opens the Rose conversation; other opens keep their page',()=>{
 // Page indices are named and set from the built page list (HomePages): Home, then the apps (#223: no Widgets or voice page).
 assert.match(main,/int homePage=0,ourAppsPage=1;boolean voiceControls;/);
 assert.match(main,/homePage=HomePages\.HOME;ourAppsPage=count>HomePages\.FIRST_APPS\?HomePages\.FIRST_APPS:HomePages\.HOME;\n/,'with no app page, Apps is Home');
 assert.match(main,/int appPages=appEntries\.isEmpty\(\)&&!entries\.isEmpty\(\)\?0:HomeAppLayout\.pages\(appEntries\.size\(\),appPerPage\);/,'no empty overflow page when Home holds every app');
 assert.ok(!/voicePage|widgetsPage|HomePages\.(voice|widgets)\(/.test(main),'no legacy page index remains');
 // Cold start routes only a fresh intent; recreation (saved state) keeps the saved page, never reopening the conversation.
 assert.match(main,/home\(\);showHomeFor\(getIntent\(\),b==null\);showSettingsFor\(getIntent\(\)\);if\(b!=null&&b\.containsKey\("home-page"\)&&launcherPager!=null\)launcherPager\.showPage\(HomePages\.restore\(b\.getInt\("home-page"\),launcherPager\.getChildCount\(\)\),false\);/);
 assert.match(main,/onNewIntent\(Intent i\)\{super\.onNewIntent\(i\);int previous=launcherPager==null\?-1:launcherPager\.currentPage\(\);boolean homeShown=!homeStopped;if\(!reuseHome\(i\)\)home\(\);showHomeFor\(i,true,previous,homeShown\);showSettingsFor\(i\);gestureContract\(i,homeShown\);\}/);
 const route=main.slice(main.indexOf('void showHomeFor(Intent intent,boolean fresh,int previous,boolean homeShown)'));
 assert.match(route,/^void showHomeFor\(Intent intent,boolean fresh,int previous,boolean homeShown\)\{if\(intent==null\)return;HomeEntry entry=HomeEntry\.of\(Intent\.ACTION_MAIN\.equals\(intent\.getAction\(\)\),intent\.hasCategory\(Intent\.CATEGORY_HOME\),intent\.hasCategory\(Intent\.CATEGORY_LAUNCHER\),fresh,\(intent\.getFlags\(\)&Intent\.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY\)!=0,DeviceIdentity\.paired\(this\)\);if\(entry\.showHome\)\{closeVoiceControls\(\);closeSystemFolder\(\);\}if\(entry\.showHome&&launcherPager!=null\)launcherPager\.showPage\(HomePages\.target\(true,!intent\.hasCategory\(Intent\.CATEGORY_HOME\),homeShown,previous,launcherPager\.currentPage\(\),launcherPager\.getChildCount\(\)\),false\);if\(entry\.openConversation\)openRoseConversation\(\);\}/);
 assert.match(main,/void showHomeFor\(Intent intent,boolean fresh\)\{showHomeFor\(intent,fresh,-1,true\);\}/);
 // "Home shown" means only paused, never stopped: an app or the conversation over Home stops it.
 assert.match(main,/@Override protected void onStop\(\)\{super\.onStop\(\);homeStopped=true;\}/);
 assert.match(main,/screenHostActive=true;homeStopped=false;/);
 assert.match(main,/onSaveInstanceState\(Bundle out\)\{super\.onSaveInstanceState\(out\);if\(launcherPager!=null\)out\.putInt\("home-page",launcherPager\.currentPage\(\)\);\}/);
 // The same explicit Intent the Rose presence uses; routing never touches voice, the HOME role or the wallpaper.
 assert.match(main,/void openRoseConversation\(\)\{startActivity\(new Intent\(this,RoseConversationActivity\.class\)\);\}/);
 for(const absent of ['RoleManager','ROLE_HOME','WallpaperManager','setPreferredActivity','clearPackagePreferredActivities'])assert.ok(!main.includes(absent),absent);
 assert.doesNotMatch(route.slice(0,route.indexOf('\n')),/VoiceService|talk|endConversation|mute/i);
 // Rebuilding for menu actions or package changes keeps the current page while it exists; a fresh build opens on
 // Home (#223: before, it opened on the legacy voice page, so a rebuild or a non-HOME entry showed "Tap to talk").
 assert.match(main,/int selected=preservePage&&launcherPager!=null\?launcherPager\.currentPage\(\):-1;\n/);
 assert.match(main,/selected=HomePages\.restore\(selected,count\);/);
});

test('Home comes first, the app pages follow it, and nothing else is a page (OS-HIFI-HOME-001, #223)',()=>{
 const pages=main.slice(main.indexOf('void attachLauncherPages(boolean'),main.indexOf('/** A small square'));
 const order=['titles.add("Home")','HomeAppLayout.title(i,appPages)'].map(t=>pages.indexOf(t));
 // #223: the legacy Widgets and voice ("Tap to talk") pages are not pages any more, and no widget is built.
 for(const gone of ['titles.add("Widgets")','titles.add("Voice home")','new ClockWidgetView','new WeatherWidgetView','new GalleryWidgetView','launcherPager.addView(page)'])
  assert.ok(!pages.includes(gone),gone);
 // The app pages hold one continuous real inventory: our apps first, then Play Store and other installs. The system and
 // duplicate apps are in the System folder (#403), whose cell is Home's last (#410), and are still Home's default apps.
 assert.match(pages,/for\(ArrayList<ResolveInfo> group:Arrays\.asList\(ourApps,storeApps,otherApps\)\)launchable\.addAll\(group\);/);
 assert.match(pages,/ArrayList<ResolveInfo> entries=new ArrayList<>\(launchable\);\n/);
 assert.ok(!/entries\.add\(null\)/.test(pages),'no folder cell on the app pages');
 assert.match(pages,/folderApps=systemApps;launchable\.addAll\(systemApps\);/);
 assert.deepEqual([...order].sort((a,b)=>a-b),order);
 assert.ok(order.every(i=>i>=0));
 assert.ok(pages.indexOf('launcherPager.addView(roseHome=new RoseHomeView(')<pages.indexOf('for(int number=HomePages.FIRST_APPS;number<count;number++)launcherPager.addView(appPage('));
 // Long-pressing Home opens the Home options; the legacy voice controls open over Home only from there or the
 // conversation's Settings, and only they run the voice refresh (drawer is false while they are shown).
 assert.match(pages,/roseHome\.setOnLongClickListener\(v->\{homeOptions\(v\);return true;\}\);/);
 assert.match(pages,/setContentView\(homeFrame\);homeBars\(\);drawer=true;if\(voiceControls\)attachVoiceControls\(\);/);
 assert.match(main,/void showVoiceControls\(\)\{voiceControls=true;attachVoiceControls\(\);\}/);
 assert.match(main,/void closeVoiceControls\(\)\{voiceControls=false;drawer=true;/);
 assert.match(main,/if\(!voiceControls\)settingsOption\(menu,"Voice controls"\)\.setOnMenuItemClickListener\(i->\{showVoiceControls\(\);return true;\}\);/);
 assert.match(main,/else if\(voiceControls\)closeVoiceControls\(\);/);
 assert.match(read('../app/src/dev/pixel/voice/RoseConversationActivity.java'),/add\("Settings"\)\.setOnMenuItemClickListener\(i->\{startActivity\(new Intent\(MainActivity\.ACTION_SETTINGS\)\.setClass\(this,MainActivity\.class\)\);return true;\}\);/);
 // The pager sits in a HomeFrame with the fixed tray; the frame, not the pager, is the content view.
 assert.match(pages,/homeTray=new HomeTray\(this,this::openRoseFromHome,this::toggleRoseMic,this::openOverview,MainActivity::overviewAvailable\);homeFrame=new HomeFrame\(this,launcherPager,homeTray,fixedDots\);\n  setContentView\(homeFrame\);homeBars\(\);/);
});

test('app pages sit on the shared wallpaper with one fixed row of dots and no drawer headings (OS-PAGING-158-M001)',()=>{
 const pages=main.slice(main.indexOf('void attachLauncherPages(boolean'),main.indexOf('/** A small square'));
 // One Oxblood field behind the whole pager (drawn at the scroll position), not a per-page or drawer background (#410).
 assert.match(pages,/launcherPager\.setBackground\(new OxbloodField\(OxbloodField\.HOME\)\);/);
 assert.ok(!/protected void onDraw|onSizeChanged/.test(home),'Home draws no wallpaper of its own, so it cannot slide');
 // App pages: the measured grid, no visible heading or subtitle, the page name as its accessible name.
 const appPage=main.slice(main.indexOf(' View appPage('),main.indexOf(' TextView appLabel('));
 assert.match(appPage,/HomeAppPage grid=new HomeAppPage\(this,appColumns,this::appPageMeasured\);for\(ResolveInfo app:apps\)grid\.addView\(appTile\(app\)\);/);
 assert.match(appPage,/result\.setContentDescription\(title\);/);
 assert.match(appPage,/No other apps are installed\./);
 for(const gone of ['setAccessibilityHeading','Your everyday apps','Included with this operating system','setBackground'])assert.ok(!appPage.includes(gone),gone);
 for(const gone of ['suiteGrid','arrangeSuiteRows','addAppGroup','"Our apps"','GridLayout grid'])assert.ok(!main.includes(gone),gone);
 // Columns come from the width only (HomeAppLayout), never from the longest label.
 assert.ok(!/measureText/.test(main.slice(main.indexOf(' void planAppPages('),main.indexOf(' View appPage('))),'no label-driven columns');
 assert.match(main,/appColumns=HomeAppLayout\.columns\(width\/density\);appPerPage=HomeAppLayout\.perPage\(width,height,cell,density\);/);
 // Labels wrap to two lines in the measured cell (HomeAppTile); the cell height is what HomeAppLayout plans with.
 assert.match(cell,/name\.setMaxLines\(HomeAppLayout\.LABEL_LINES\);/);
 assert.match(cell,/new LinearLayout\.LayoutParams\(-1, HomeAppLayout\.LABEL_LINES \* name\.getLineHeight\(\)\)/);
 // One row of dots, fixed in the frame above the edge, and the pages' bottom inset is the band above both (#410).
 assert.equal((main.match(/makePageDots\(titles\)/g)||[]).length,1);
 assert.match(homeFrame,/tray\.heightPx\(\) \+ Math\.round\(HomeAppLayout\.BAND_BOTTOM_DP \* tray\.density\)\)\);/);
 assert.match(homeFrame,/addView\(dots, dotsParams\);/);
 // A re-measure re-plans once, then only when the real capacity changes.
 assert.match(main,/if\(!HomeAppLayout\.replan\(appColumns,appPerPage,width,height,cell,getResources\(\)\.getDisplayMetrics\(\)\.density\)\)return;\n  if\(width==appAreaWidth&&height==appAreaHeight&&cell==appCellHeight\)return;/);
 // Launch and return are unchanged: every tile opens its own app through WorkspaceLauncher (closing the folder first).
 assert.match(main,/tile\.setOnClickListener\(v->\{closeSystemFolder\(\);WorkspaceLauncher\.open\(this,app,label\);\}\);/);
 // The pager's gesture decisions are HomePages' tested ones.
 const pager=read('../app/src/dev/pixel/voice/LauncherPager.java');
 assert.match(pager,/int owner=HomePages\.owner\(event\.getX\(\)-downX,event\.getY\(\)-downY,slop\);/);
 assert.match(pager,/finishGesture\(HomePages\.settle\(downPage,dx,vx,getWidth\(\),flingThreshold,/);
});

test('Home layout, page/gesture and overview Java checks run against the production classes',()=>{
 // Executed, not matched: compile each production helper with its Java test and run it.
 const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
 for(const name of ['HomeAppLayout','HomePages']){
  const out=fs.mkdtempSync(path.join(os.tmpdir(),'home-java-'));
  try{
   execFileSync('java',['-m','jdk.compiler/com.sun.tools.javac.Main','-source','8','-target','8','-Xlint:-options','-d',out,
    path.join(root,`app/src/dev/pixel/voice/${name}.java`),path.join(root,`app/tests/${name}Test.java`)],{stdio:'pipe'});
   const result=execFileSync('java',['-cp',out,`dev.pixel.voice.${name}Test`],{encoding:'utf8',stdio:['ignore','pipe','pipe']});
   assert.match(result,new RegExp(`${name}Test: \\d+ checks passed`));
  }finally{fs.rmSync(out,{recursive:true,force:true});}
 }
});

test('the Rose entry opens App\'s conversation activity and only navigates',()=>{
 assert.match(main,/new RoseHomeView\(this,homeEntries,homeColumns,this::homeMeasured\)\);/,'Home fills its measured rows (#410 owner density correction)');
 // Home's columns come from the same planned/measured app area as the app pages (#410: four unlabelled 60dp cells).
 assert.match(main,/homeColumns=HomeAppLayout\.homeColumns\(homeWidth\/density\);homeCapacity=HomeAppLayout\.homeCapacity\(homeWidth,homeHeight,homeCell,density\);/);
 // The tray's Rose well is the plain navigation hook and nothing else.
 assert.match(homeTray,/rose\.setOnClickListener\(v -> openRose\.run\(\)\);/);
 assert.match(homeTray,/rose\.setContentDescription\("Open Rose"\);/);
 // APP-P2-002-HOOK-001: plain explicit Intent, same task, no flags or extras.
 const hook=main.match(/void openRoseConversation\(\)\{([^}]*)\}/)[1];
 assert.equal(hook,'startActivity(new Intent(this,RoseConversationActivity.class));');
 const activity=manifest.match(/<activity android:name="\.RoseConversationActivity"[^>]*\/>/)[0];
 assert.match(activity,/android:exported="false"/);
 assert.ok(!/launchMode|taskAffinity/.test(activity),'stays in the launcher task so HOME and Back return Home');
 for(const forbidden of ['VoiceService','command(','toggleVoice','startForegroundService','"talk"','"end"','"mute"'])
  for(const [name,src] of [['RoseHomeView',home],['HomeTray',homeTray],['HomeFrame',homeFrame],['HomeRose',homeRose]])assert.ok(!src.includes(forbidden),name+': '+forbidden);
 // The conversation surface sends voice commands only from its own controls, never on open.
 const conversation=read('../app/src/dev/pixel/voice/RoseConversationActivity.java');
 for(const hook of ['onCreate','onResume']){
  // Tap listeners (setOnClickListener(v->...)) run on the owner's tap, not on open.
  const body=conversation.slice(conversation.indexOf(`void ${hook}(`)).split('\n @Override')[0].split('\n void ')[0]
   .replace(/setOnClickListener\(v->[^;]*;/g,'');
  assert.ok(!/send\(|startForegroundService|setAction\(/.test(body),hook);
 }
});

test('Home\'s Rose control opens Rose with the platform clip-reveal, one launch at a time (#410)',()=>{
 const open=main.slice(main.indexOf(' void openRoseFromHome(){'),main.indexOf('\n }',main.indexOf(' void openRoseFromHome(){')));
 assert.match(open,/if\(!roseLaunch\.tap\(SystemClock\.uptimeMillis\(\)\)\)return;/,'a repeated or interrupted tap opens nothing extra');
 // The same plain explicit Intent as the hook (no flags or extras); only the window animation differs.
 assert.match(open,/Intent i=new Intent\(this,RoseConversationActivity\.class\);/);
 assert.ok(!/addFlags|setFlags|putExtra/.test(open),'no flags or extras');
 assert.match(open,/startActivity\(i,ActivityOptions\.makeClipRevealAnimation\(from,0,0,from\.getWidth\(\),from\.getHeight\(\)\)\.toBundle\(\)\);/);
 assert.match(open,/HomeRoseLaunch\.animate\(scale\)/,'animations off: a plain, instant launch');
 assert.match(open,/else startActivity\(i\);/);
 for(const forbidden of ['VoiceService','command(','startForegroundService','"talk"','"end"','"mute"'])assert.ok(!open.includes(forbidden),'navigation only: '+forbidden);
 assert.match(main,/onPause\(\)\{refreshHomeRose\(false\);homeOverview\.homeLeft\(\);roseLaunch\.reset\(\);/);
 assert.match(main,/screenHostActive=true;homeStopped=false;roseLaunch\.reset\(\);/);
});

test('Home shows the default apps through the app pages\' real tile and launch path',()=>{
 // Home's cells are MainActivity.appTile: the installed icon/label, AppLabels and WorkspaceLauncher, as on the app pages.
 assert.match(home,/for \(ResolveInfo app : entries\) grid\.addView\(app == null \? activity\.folderTile\(false\) : activity\.appTile\(app, false\)\);/);
 const tile=main.slice(main.indexOf(' View appTile(ResolveInfo app,boolean labelled){'),main.indexOf('\n }',main.indexOf(' View appTile(ResolveInfo app,boolean labelled){')));
 assert.match(tile,/tile\.setOnClickListener\(v->\{closeSystemFolder\(\);WorkspaceLauncher\.open\(this,app,label\);\}\);/);
 assert.match(tile,/AppLabels\.label\(getPackageManager\(\),app\.activityInfo\.packageName/);
 assert.match(tile,/View tile=HomeAppTile\.tile\(this,mono\?new HomeGlyph\(glyph\):appMark\(app\),label,mono,labelled\);/);
 assert.match(cell,/tile\.setContentDescription\(label\);/);
 assert.match(home,/pm\.resolveActivity\(role, PackageManager\.MATCH_DEFAULT_ONLY\)/);
 // A chooser ("android") or Rose Agent itself is never a dock app; only launchable activities are used.
 assert.match(home,/"android"\.equals\(pkg\) \|\| pkg\.equals\(self\)/);
 assert.match(home,/for \(ResolveInfo app : launchable\) if \(pkg\.equals\(app\.activityInfo\.packageName\)\)/);
 assert.ok(!/"(com|dev|org)\.[a-z0-9_.]+"/.test(home),'no hard-coded package names');
});

test('one persistent control bar: Home has no second shortcut dock over the tray (#157, APP-OS-VISUAL-1030)',()=>{
 // The selected HiFi Home is apps on the wallpaper above the dots and the one tray; the old card dock stacked a
 // second bar (its own panel, an "All apps" tile) directly over HomeTray.
 for(const old of ['GradientDrawable','setCornerRadius','0xd9fafcf4','"All apps"','▦','dockTile','DockLabelView','LinearLayout'])
  assert.ok(!home.includes(old),'RoseHomeView still builds the old dock: '+old);
 // Home's grid is the app pages' HomeAppPage in Home's cells (#410), filling below the date down to the fixed dots/edge;
 // its measurement re-plans Home's capacity (#410 owner density correction).
 assert.match(home,/HomeAppPage grid = new HomeAppPage\(activity, columns, HomeAppLayout\.HOME_CELL_DP, HomeAppLayout\.HOME_COL_GAP_DP, fit\);/);
 assert.match(home,/addView\(grid, new LayoutParams\(-1, -1\)\);/);
 // Its bottom padding is only the frame's inset (system bar + tray + dots), like the app pages: nothing is drawn under the tray.
 assert.match(home,/side \+ i\.getSystemWindowInsetRight\(\), i\.getSystemWindowInsetBottom\(\)\);/);
 const page=main.slice(main.indexOf(' View appPage('),main.indexOf(' TextView appLabel('));
 assert.match(page,/dp\(HomeAppLayout\.MARGIN_DP\)\+i\.getSystemWindowInsetRight\(\),i\.getSystemWindowInsetBottom\(\)\);/);
 // The edge and the one row of dots stay the frame's only bottom controls; every other app is a swipe away, and the
 // pager names the page and offers scroll actions (the dots are an indicator only, as in the reference, #410).
 assert.equal((main.match(/new HomeTray\(/g)||[]).length,1);
 assert.match(main,/dots\.setImportantForAccessibility\(View\.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS\);/);
 const pager=read('../app/src/dev/pixel/voice/LauncherPager.java');
 assert.match(pager,/AccessibilityAction\.ACTION_SCROLL_FORWARD/);assert.match(pager,/AccessibilityAction\.ACTION_SCROLL_BACKWARD/);
 assert.match(main,/void apps\(\)\{closeVoiceControls\(\);if\(launcherPager!=null\)launcherPager\.showPage\(ourAppsPage,true\);\}/);
});

test('an unchanged HOME or app-icon return reuses the built Home; changes rebuild it (OS-HOME-REUSE-001)',()=>{
 // The route is decided after reuse, so HOME still lands on Home and the app icon still opens the conversation.
 assert.match(main,/if\(!reuseHome\(i\)\)home\(\);showHomeFor\(i,true,previous,homeShown\);/);
 // Only Home entries (HOME, MAIN\/LAUNCHER) qualify, judged by the same HomeEntry rule as routing.
 assert.match(main,/boolean reuseHome\(Intent i\)\{return i!=null&&HomeReuse\.reuse\(HomeEntry\.of\(Intent\.ACTION_MAIN\.equals\(i\.getAction\(\)\),i\.hasCategory\(Intent\.CATEGORY_HOME\),i\.hasCategory\(Intent\.CATEGORY_LAUNCHER\),true,false,true\)\.showHome,/);
 // Whatever home() resets still forces it: chat mode, an open visual or Mac screen, a changed Home setting, another view in place.
 assert.match(main,/homeFrame!=null&&homeFrame\.getParent\(\)!=null,builtHomeStamp,homeStamp\(\),chatMode,visualDetail!=null\|\|screenDetail!=null,experimentalHome!=getPreferences\(0\)\.getBoolean\("conversation-home",true\)\);\}/);
 // The stamp is recorded by every catalog build and covers packages, configuration and the dock's default apps.
 assert.match(main,/homeFrame\.requestApplyInsets\(\);\n  builtHomeStamp=homeStamp\(\);\n \}/);
 assert.match(main,/public void onReceive\(Context context,Intent intent\)\{packageGeneration\+\+;if\(launcherPager!=null&&!isFinishing\(\)\)attachLauncherPages\(true\);\}/);
 for(const field of ['screenWidthDp','screenHeightDp','densityDpi','orientation','fontScale','uiMode','getLocales().toLanguageTags()'])assert.ok(main.includes('c.'+field),field);
 assert.match(main,/RoseHomeView\.dockRolePackages\(getPackageManager\(\),getPackageName\(\)\)\);\}/);
 // Home is built from the same role list the stamp compares.
 assert.match(home,/ArrayList<String> wanted = new ArrayList<>\(dockRolePackages\(pm, self\)\);/);
});

test('Home\'s edge stays fixed over the swiping pages and reaches the screen bottom (OS-HIFI-HOME-001, #410)',()=>{
 // The edge is the frame's own child, outside the pager, so it never scrolls with a page.
 assert.match(homeFrame,/addView\(pager, new LayoutParams\(-1, -1\)\);/);
 assert.match(homeFrame,/addView\(tray, new LayoutParams\(-1, tray\.heightPx\(\), Gravity\.BOTTOM\)\);/);
 // Pages get the band above the edge as their bottom inset; the edge grows by the tappable navigation inset only.
 assert.match(homeFrame,/int tappable = insets\.getTappableElementInsets\(\)\.bottom;/);
 assert.match(homeTray,/int heightPx\(\) \{ return Math\.round\(HEIGHT_DP \* density\) \+ navigationExtra; \}/);
 // Production status and navigation bars stay: nothing hides system bars.
 for(const src of [homeFrame,homeTray])for(const hide of ['SYSTEM_UI_FLAG','hide(WindowInsets','setDecorFitsSystemWindows','FLAG_FULLSCREEN'])assert.ok(!src.includes(hide),hide);
 // #410 (oxblood.css:371-380,487-496): a 92dp edge; Rose 103 and switcher 106 wide, 44dp tall, 14 apart; the 16dp pearl.
 const edge=read('../app/src/dev/pixel/voice/OxbloodEdge.java');
 assert.match(edge,/static final int HEIGHT_DP = 92, FLAT_TOP_DP = 34, FILLET_DP = 34;/);
 assert.match(edge,/static final int ROSE_W_DP = 103, SWITCHER_W_DP = 106, CONTROL_H_DP = 44, GAP_DP = 14, PEARL_DP = 16;/);
 assert.match(homeTray,/setBackground\(new OxbloodEdge\.Plate\(density\)\);/);
 // #410 wave item 6: Rose's microphone-input mute well between Rose and the switcher (the HiFi's tray-mic). It is
 // Rose's own input only, never an OS-wide mute: no AudioManager microphone mute, Sensor Privacy, output or call action.
 assert.match(homeTray,/micControl\.setOnClickListener\(v -> toggleMic\.run\(\)\);/);
 assert.match(homeTray,/String spoken = muted \? "Rose’s microphone is muted\. Unmute" : "Mute Rose’s microphone";/);
 assert.match(homeTray,/pearl\.set\(shownMicMuted, false, -1\);/,'the pearl follows the one mute');
 assert.ok(!/tray_mute|"mute"|setMicrophoneMute|SensorPrivacy/.test(homeTray),'the well only asks MainActivity');
 assert.match(main,/void toggleRoseMic\(\)\{RoseMicState\.request\(this,!roseMicShown\(\)\);/);
 assert.match(main,/boolean roseMicShown\(\)\{ConversationState c=conversationState\(\);return RoseMicInput\.shown\(c\.active\(\),c\.muted,RoseMicState\.muted\(this\)\);\}/);
 const micState=read('../app/src/dev/pixel/voice/RoseMicState.java'),micRules=read('../app/src/dev/pixel/voice/RoseMicInput.java');
 const code=src=>src.replace(/\/\*[\s\S]*?\*\//g,'').replace(/\/\/.*$/gm,'');
 for(const src of [micState,micRules])assert.ok(!/setMicrophoneMute|SensorPrivacy|AudioManager|startService|startForegroundService|"end"|"talk"|"disconnect"/.test(code(src)),'input-only: no OS mute, output, start or end');
 assert.match(micState,/if \(voice != null\) voice\.command\(RoseMicInput\.command\(muted\), "mic_input"\);/,'a running voice service is told (live or connecting); none: nothing is started');
 assert.ok(!/Sync/.test(code(micRules)),'one sync rule, the backend\'s (App Core)');
 // Home's store and commands are the very ones App Core's capture reads and writes (MicInputCapture, #520).
 const capture=read('../app/src/dev/pixel/voice/MicInputCapture.java');
 assert.match(capture,/static final String PREFS = "rose-mic-input", KEY = "muted";/);
 assert.match(micState,/static final String PREFS = "rose-mic-input", KEY = "muted";/,'one store, shared with the capture backend');
 assert.match(capture,/static final String MUTE = "tray_mute", UNMUTE = "tray_unmute";/);
 assert.match(micRules,/static final String MUTE = "tray_mute", UNMUTE = "tray_unmute";/,'the commands the capture backend stores');
 const tray=read('../app/src/dev/pixel/voice/AgentTrayService.java');
 assert.match(tray,/if\(m\.what==MUTE\|\|m\.what==UNMUTE\)\{RoseMicState\.request\(this,m\.what==MUTE\);publish\(true\);return;\}/,'the tray mute is the same state and never starts voice');
 // The tray's caller check is unchanged: SystemUI only (any widening needs review, #410).
 assert.match(tray,/return packages!=null&&Arrays\.asList\(packages\)\.contains\("com\.android\.systemui"\)&&\n    pm\.checkSignatures\("android","com\.android\.systemui"\)==PackageManager\.SIGNATURE_MATCH;/);
 assert.ok(!/recents|launcher3|config_recentsComponentName/i.test(tray),'no new trusted caller');
 assert.ok(!home.includes('sessionMark')&&!home.includes('showSession')&&!home.includes('RoseMark'));
});

test('the overview well asks for the real system overview through the one permitted route (OS-OVERVIEW-156-M001)',()=>{
 // The selected glyph stays; the well is one focusable, clickable action whose only click is the overview callback.
 const well=homeTray.slice(homeTray.indexOf('FrameLayout switcher = control(activity);'),homeTray.indexOf('addView(switcher'));
 assert.match(well,/glyph\.setBackground\(new OxbloodEdge\.SwitcherGlyph\(density\)\);/);
 assert.match(well,/switcher\.setOnClickListener\(v -> openOverview\.run\(\)\);/);
 assert.ok(!well.includes('setEnabled(false)')&&!homeTray.includes('not available yet'),'the obsolete disabled placeholder is gone');
 for(const fake of ['GLOBAL_ACTION','startActivity','Intent','WorkspaceLauncher','apps'])assert.ok(!well.includes(fake),fake);
 // Each refresh shows whether the route is available, dimmed with the reason when it is not.
 assert.match(homeTray,/boolean available = overviewAvailable\.getAsBoolean\(\);\n  if \(available != shownOverviewAvailable\) applyOverview\(available\);/);
 assert.match(homeTray,/overview\.setContentDescription\(HomeOverview\.describe\(available\)\);/);
 // MainActivity: one HomeOverview per activity (tray rebuilds keep it), cleared when Home leaves the foreground.
 assert.match(main,/final HomeOverview homeOverview=new HomeOverview\(\);/);
 assert.match(main,/homeTray=new HomeTray\(this,this::openRoseFromHome,this::toggleRoseMic,this::openOverview,MainActivity::overviewAvailable\);/);
 assert.match(main,/protected void onPause\(\)\{refreshHomeRose\(false\);homeOverview\.homeLeft\(\);/);
 // The route: only the owner-enabled phone-controls service's GLOBAL_ACTION_RECENTS; null (unavailable) when it is off.
 const open=main.slice(main.indexOf(' void openOverview(){'),main.indexOf('\n',main.indexOf(' void openOverview(){')));
 assert.match(main,/static boolean overviewAvailable\(\)\{return PhoneAccessibility\.active!=null;\}/);
 assert.match(open,/PhoneAccessibility a=PhoneAccessibility\.active;return a==null\?null:a\.performGlobalAction\(android\.accessibilityservice\.AccessibilityService\.GLOBAL_ACTION_RECENTS\);/);
 // Never a new authority, a Settings detour or an invented recents intent.
 for(const banned of ['startActivity','ACCESSIBILITY','Settings','SHOW_RECENTS','STATUS_BAR','toggleRecentApps','RecentsActivity','setResult','finish('])assert.ok(!open.includes(banned),banned);
 assert.equal((main.match(/GLOBAL_ACTION_RECENTS/g)||[]).length,1);
 assert.ok(!/SHOW_RECENTS|toggleRecentApps|android\.permission\.STATUS_BAR"/.test(main+manifest));
 for(const page of ['launcherPager','home()','attachLauncherPages','VoiceService','conversation'])assert.ok(!open.includes(page),page);
 assert.ok(!/import android\./.test(homeOverview),'HomeOverview is pure logic');
});

test('HomeOverview runs: one tap one request, repeats in flight, unavailable/refused, return and recreation',()=>{
 // Executed, not matched: compile the production helper with its Java test and run it.
 const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
 const out=fs.mkdtempSync(path.join(os.tmpdir(),'home-overview-'));
 try{
  execFileSync('java',['-m','jdk.compiler/com.sun.tools.javac.Main','-source','8','-target','8','-Xlint:-options','-d',out,
   path.join(root,'app/src/dev/pixel/voice/HomeOverview.java'),path.join(root,'app/tests/HomeOverviewTest.java')],{stdio:'pipe'});
  const result=execFileSync('java',['-cp',out,'dev.pixel.voice.HomeOverviewTest'],{encoding:'utf8',stdio:['ignore','pipe','pipe']});
  assert.match(result,/HomeOverviewTest: \d+ checks passed/);
 }finally{fs.rmSync(out,{recursive:true,force:true});}
});

test('the tray Rose reads real state once per tick and stops when Home is hidden, rebuilt or gone (M004)',()=>{
 // One read of VoiceService's published levels per existing refresh tick, through the merged RoseVisualState mapping.
 assert.match(main,/void refreshHomeRose\(boolean shown\)\{if\(homeTray==null\)return;VoiceService s=VoiceService\.instance;ConversationState c=conversationState\(\);homeTray\.show\(c,HomeRose\.state\(c,s==null\?Float\.NaN:s\.trayInputLevel,s==null\?Float\.NaN:s\.trayOutputLevel,s==null\?0L:s\.trayAudioObservedAt,SystemClock\.elapsedRealtime\(\),HomeRose\.reducedMotion\(android\.provider\.Settings\.Global\.getFloat\(getContentResolver\(\),android\.provider\.Settings\.Global\.ANIMATOR_DURATION_SCALE,1f\)\),shown\),RoseMicInput\.shown\(c\.active\(\),c\.muted,RoseMicState\.muted\(this\)\)\);\}/);
 assert.match(homeRose,/RoseVisualState\.from\(conversation, RoseVisualState\.fresh\(inputLevel, observedAtMs, nowMs\),\n    RoseVisualState\.fresh\(outputLevel, observedAtMs, nowMs\), reducedMotion, homeShown\);/);
 assert.equal((main.match(/refreshHomeRose\(/g)||[]).length,3,'defined, called by refresh and by onPause only');
 // Pause renders hidden first; a rebuilt tray and a destroyed activity release the old one, which then ignores frames.
 assert.match(main,/protected void onPause\(\)\{refreshHomeRose\(false\);/);
 assert.match(main,/if\(homeTray!=null\)homeTray\.release\(\);homeTray=new HomeTray\(this,this::openRoseFromHome,this::toggleRoseMic,this::openOverview,MainActivity::overviewAvailable\);/);
 assert.match(main,/protected void onDestroy\(\)\{closeSystemFolder\(\);if\(homeTray!=null\)homeTray\.release\(\);/);
 assert.match(homeTray,/void show\(ConversationState conversation, RoseVisualState visual, boolean micMuted\) \{\n  if \(released\) return;/);
 assert.match(homeTray,/void release\(\) \{\n  released = true;\n \}/);
 // One mount point for the pearl; no second poller, timer or animation of Home's own (#410: Home's pearl is static).
 assert.equal((homeTray.match(/void applyVisual\(RoseVisualState visual\)/g)||[]).length,1);
 assert.equal((homeTray.match(/new RosePearl\(/g)||[]).length,1);
 for(const src of [homeTray,homeRose])for(const timer of ['postDelayed','Handler','ValueAnimator','Choreographer','Timer','schedule'])assert.ok(!src.includes(timer),timer);
 // No duplicate renderer: Home draws no WebGL, shader or surface of its own.
 for(const src of [homeTray,homeRose])for(const gl of ['GLSurfaceView','GLES','Shader','TextureView','SurfaceView'])assert.ok(!src.includes(gl),gl);
});

test('Home labels are the app pages\' labels: whole names over two lines, never a one-line cut (M008)',()=>{
 // 302/304 forced one line with an end ellipsis (Messaging, Camera (system) cut at 720 px). Home now uses appLabel.
 assert.match(main,/TextView appLabel\(String label\)\{return HomeAppTile\.label\(this,label\);\}/);
 const label=cell.slice(cell.indexOf(' static TextView label('));
 assert.doesNotMatch(label,/setSingleLine\(true\)/);
 assert.match(label,/name\.setMaxLines\(HomeAppLayout\.LABEL_LINES\);/);
 assert.match(cell,/new LinearLayout\.LayoutParams\(-1, HomeAppLayout\.LABEL_LINES \* name\.getLineHeight\(\)\)/);
});

test('the wallpaper is the selected HiFi image, drawn like its .wall: cover at 62% 30% (APP-OS-VISUAL-1030-M002)',()=>{
 const jpeg=fs.readFileSync(new URL('../app/res/drawable-nodpi/rose_home_wallpaper.jpg',import.meta.url));
 const sha=crypto.createHash('sha256').update(jpeg).digest('hex');
 // Exact bytes of prototypes/rose-hifi/src/assets/wallpaper.jpg at ce126f56 (Git blob 1d99a767…9720), which Rose-HiFi.html
 // embeds unchanged as --wallpaper; scripts/rose-verify.py pins the same fixed SHA-256. 1024 x 1536, 286252 bytes.
 assert.equal(sha,'67e3975453b7caa1fc344b12f1f82088747c3aa5730621af72b7ecf02d0463c5');
 assert.notEqual(sha,'409c0dbfa309dbd56f97a59b04223532e9931ae5c5c4625d3567b77a9c56d8c5','not the earlier Rose-Fold-v3 image');
 assert.equal(jpeg.length,286252);
 const sof=jpeg.indexOf(Buffer.from([0xff,0xc0]));assert.ok(sof>0);
 assert.deepEqual([jpeg.readUInt16BE(sof+7),jpeg.readUInt16BE(sof+5)],[1024,1536],'width x height');
 assert.match(read('../../../scripts/rose-verify.py'),/'wallpaperSha256': '67e3975453b7caa1fc344b12f1f82088747c3aa5730621af72b7ecf02d0463c5'/);
 // #410: Home now draws the Oxblood field (OxbloodField); this image remains the reading surface's (ConversationDrawing)
 // and stays pinned for rose-verify. No owner wallpaper preference exists to reset.
 assert.ok(!/rose_home_wallpaper/.test(home),'Home no longer draws the HiFi image');
 assert.match(layout,/WALL_X = \.62f, WALL_Y = \.30f, IVORY_X = \.80f, IVORY_Y = \.20f;/);
 const drawing=read('../app/src/dev/pixel/voice/ConversationDrawing.java');
 assert.match(drawing,/HomeAppLayout\.cover\(wallpaper\.getWidth\(\),wallpaper\.getHeight\(\),w\*1\.2f,h\*1\.2f,HomeAppLayout\.IVORY_X,HomeAppLayout\.IVORY_Y\)/,'the reading surface crops too, never stretches the image');
 assert.ok(!/new Rect\(0,0,wallpaper\.getWidth\(\),wallpaper\.getHeight\(\)\)/.test(drawing),'no whole-image stretch');
 // The Oxblood field is placed when the bounds change (cover, centre top), not per frame.
 const field=read('../app/src/dev/pixel/voice/OxbloodField.java');
 assert.match(field,/scale = Math\.max\(b\.width\(\) \/ VIEW_W, b\.height\(\) \/ VIEW_H\);/);
 assert.match(field,/dy = b\.top; \/\/ background-position: center top/);
});

test('one tray material for Home and the conversation, and the HiFi-sized tray Rose (APP-OS-VISUAL-1030)',()=>{
 const material=read('../app/src/dev/pixel/voice/TrayMaterial.java');
 const conversation=read('../app/src/dev/pixel/voice/RoseConversationActivity.java');
 // Both trays and every well use the one material; no hand-rolled gradients are left behind.
 // #410: Home's edge is the Oxblood edge (OxbloodEdge); TrayMaterial remains the conversation's until its Oxblood slice.
 assert.match(homeTray,/control\.setBackground\(new OxbloodEdge\.Well\(activity\.getResources\(\)\.getDisplayMetrics\(\)\.density\)\);/);
 // #410: the conversation's controls are Oxblood too (OxbloodRose): the single Connect/Disconnect plate and the work entry.
 assert.match(conversation,/connect\.setBackground\(new OxbloodRose\.Pill\(density\)\);/);
 assert.match(conversation,/work=new FrameLayout\(this\);workGlyph=new OxbloodRose\.WorkButton\(density\);work\.setBackground\(workGlyph\);/);
 assert.ok(!/GradientDrawable/.test(homeTray),'HomeTray draws no private gradient');
 assert.ok(!/0xfaffffff,0x8cffffff|0xfff6f7f9,0xffe3e7eb/.test(conversation),'the conversation has no copy of the material');
 // #157: values measured from the selected image's pixels (alpha x 255, rounded). The bar is mid silver (top #c1c3c9,
 // middle #c3c5ca, bottom #c6c8cd) with small corners; the wells are clear oval glass over the metal (interior #abb3bd).
 const argb=(a,rgb)=>'0x'+Math.round(a*255).toString(16).padStart(2,'0')+rgb;
 assert.match(material,/109faa230ca63744dc393a65d03ae525ca1e0d77bf98e0db5a423c670bccd1b4/,'the reference image is named by its hash');
 assert.match(material,/METAL = \{0xffc4c6cc, 0xffc1c3c9, 0xffc3c5ca, 0xffc6c8cd\};/);assert.match(material,/METAL_STOPS = \{0f, \.38f, \.62f, 1f\};/);
 assert.match(material,/static final int GRAIN = 2;/);
 assert.ok(material.includes(`SPECULAR = {0x00ffffff, ${argb(.2,'ffffff')}, 0x00ffffff}`));assert.match(material,/SPECULAR_STOPS = \{\.28f, \.44f, \.60f\};/);
 for(const [name,a,rgb] of [['TINT',.18,'2a3c5a'],['LIGHT',.35,'ffffff'],['RIM',.7,'ffffff'],['RIM_SHADOW',.3,'28374f'],['LOWER_SHADE',.22,'465c7d'],
   ['SHEEN',.3,'ffffff'],['PRESSED_SHADE',.3,'3c506e'],['OUTER_SHADOW',.25,'28374f'],['LOWER_RIM',.4,'3c4b5f']])
  assert.ok(material.includes(`${name} = ${argb(a,rgb)}`),name);
 // The lens is an ellipse, not a pill, and every glass layer is an oval.
 assert.match(material,/lensPath\.addOval\(lens, android\.graphics\.Path\.Direction\.CW\);/);
 assert.ok(!/drawRoundRect\(lens/.test(material),'no pill-shaped glass');
 assert.match(material,/POCKET_DP = 5;/);assert.match(material,/RADIUS_DP = 12;/);
 // The pocket paints only outside the lens (CSS box-shadow spreads): host-rendered native output showed the dark
 // pocket fills through the translucent glass, 36-49 levels darker than the reference, until they were clipped out.
 assert.match(material,/int save = canvas\.save\(\);\s*canvas\.clipOutPath\(lensPath\);[\s\S]*?canvas\.restoreToCount\(save\);\s*\/\/ The glass\./);
 // The pressed glass follows the real pressed state; the drawables are decorative (no listeners, text or names).
 assert.match(material,/if \(s == android\.R\.attr\.state_pressed\) now = true;/);
 assert.ok(!/setOnClickListener|setContentDescription|drawText/.test(material));
 // Home's Rose is the 16dp pearl (#410); the conversation keeps the shared sphere renderer until its slice.
 assert.match(homeTray,/pearl = new RosePearl\(density, PEARL_DP\);/);
 assert.match(read('../app/src/dev/pixel/voice/RosePresenceView.java'),/float r=Math\.min\(getWidth\(\),getHeight\(\)\)\/\(2f\*RosePresenceModel\.VIEW_RADII\);/);
 assert.ok(!/TrayMaterial/.test(conversation),'no HiFi tray left on the conversation');
});

test('the overview well draws the HiFi three-bar glyph with its gradients and rings (APP-OS-VISUAL-1030)',()=>{
 const glyph=read('../app/src/dev/pixel/voice/TrayMaterial.java').slice(read('../app/src/dev/pixel/voice/TrayMaterial.java').indexOf('static final class Overview extends Drawable'));
 // #410: Home's switcher control draws the two offset task-card pairs (OxbloodEdge.SwitcherGlyph); this three-bar glyph
 // stays TrayMaterial's for any surface still on the HiFi tray.
 assert.ok(!homeTray.includes('TrayMaterial.Overview'),'Home uses the Oxblood switcher glyph');
 // 11-wide bars, 4 apart, 5 radius; #157: the outer bars are 24 tall beside the middle 34 (about 70%, as in the image).
 assert.match(glyph,/float width = 11 \* unit, gap = 4 \* unit/);assert.match(glyph,/float half = \(i == 1 \? 17 : 12\) \* unit/);
 assert.match(glyph,/float radius = 5 \* unit;/);
 const argb=(a,rgb)=>'0x'+Math.round(a*255).toString(16).padStart(2,'0')+rgb;
 assert.match(glyph,/BAR = \{0xffffffff, 0xffe2e8f0\}, ACCENT = \{0xff6aa2ff, 0xff2f6be6\};/);
 for(const [name,a,rgb] of [['BAR_RING',.55,'788caa'],['ACCENT_RING',.6,'1e50c8'],['BAR_SHADE',.25,'506482'],['ACCENT_LIGHT',.45,'ffffff']])
  assert.ok(glyph.includes(`${name} = ${argb(a,rgb)}`),name);
 // Gradients are built per size; drawing allocates nothing. Still decorative: no listener or spoken name of its own.
 const draw=glyph.slice(glyph.indexOf('@Override public void draw'),glyph.indexOf('@Override public void setAlpha'));
 assert.ok(!draw.includes('new '),'no allocation while drawing');
 assert.ok(!/setOnClickListener|setContentDescription/.test(glyph));
});

test('app labels and page dots follow the Oxblood type and dot treatment (#410)',()=>{
 // oxblood.css:43 with style.css:590: 11.5px, weight 500, #d2b9ab, text-shadow 0 1px 4px rgba(0,0,0,.7). Inter falls
 // back to the system UI sans; no font asset is added.
 const label=cell.slice(cell.indexOf(' static TextView label('));
 assert.match(label,/name\.setTextSize\(11\.5f\);\n  name\.setTextColor\(0xffd2b9ab\);\n  name\.setTypeface\(Typeface\.create\(Typeface\.SANS_SERIF, 500, false\)\);/);
 assert.match(label,/name\.setShadowLayer\(dp\(density, 2\), 0, dp\(density, 1\), 0xb3000000\);/);
 assert.match(cell,/static int dp\(float density, float value\) \{ return \(int\) \(value \* density\); \}/,'MainActivity.dp\'s truncation, so cells keep their sizes');
 assert.ok(!/chatFont\(800\)/.test(label),'not the rounded 800 face');
 // oxblood.css:364-367,514-515: indicator dots 19dp apart; the current page is the view's selected state.
 const dots=main.slice(main.indexOf(' LinearLayout makePageDots('),main.indexOf(' View appTile('));
 assert.match(dots,/TextView dot=new TextView\(this\);dot\.setBackground\(new HomeDot\(getResources\(\)\.getDisplayMetrics\(\)\.density\)\);dot\.setTag\(i\);dot\.setSelected\(i==0\);/);
 assert.match(dots,/new LinearLayout\.LayoutParams\(dp\(19\),dp\(HomeFrame\.DOTS_DP\)\)/);
 assert.ok(!/setOnClickListener/.test(dots),'the dots are an indicator only');
 assert.ok(!/●/.test(dots)&&!/dot\.setAlpha/.test(main),'no glyph dots or alpha dimming');
 assert.match(main,/for\(TextView dot:pageDots\)dot\.setSelected\(\(\(Integer\)dot\.getTag\(\)\)==index\);/);
 const dot=read('../app/src/dev/pixel/voice/HomeDot.java');
 assert.match(dot,/DOT_DP = 7, CURRENT_DP = 7\.5f, GLOW_DP = 4;/);
 assert.match(dot,/IDLE = 0x73c8beba, CURRENT = 0xfff4ede0, GLOW = 0x73ffebdc;/);
 assert.match(dot,/if \(s == android\.R\.attr\.state_selected\) now = true;/);
 const draw=dot.slice(dot.indexOf('@Override public void draw('),dot.indexOf('@Override public boolean isStateful'));
 assert.ok(!draw.includes('new '),'no allocation while drawing');
});

test('app tiles are the Oxblood glass with the reference marks; other apps keep their own icons (#410, #403)',()=>{
 // #410 (oxblood.css:38,215,354-362): a 59dp faint warm glass tile, radius 14; Rose's suite and the System folder draw
 // the reference's monoline marks (icons.js MONO); every other installed app keeps its own icon, unrestyled.
 const glass=read('../app/src/dev/pixel/voice/HomeGlassTile.java');
 const tile=main.slice(main.indexOf(' View appTile(ResolveInfo app,boolean labelled){'),main.indexOf('\n }',main.indexOf(' View appTile(ResolveInfo app,boolean labelled){')));
 assert.match(cell,/iconFrame\.setBackground\(new HomeGlassTile\(density\)\);/);
 assert.ok(!/raised\(/.test(tile),'no cream raised tile');
 assert.match(glass,/RADIUS_DP = 14;/);
 assert.match(glass,/FILL = 0x0effe8de, RING = 0x12ffe8de, TOP_EDGE = 0x1affe8de;/);
 for(const absent of ['0x9effffff','0xf2ffffff','7896be','0xf05e1b26'])assert.ok(!glass.includes(absent),'no earlier tile: '+absent);
 // Decorative: the cell is still the one spoken, clickable target; the drawables hold no listener or text.
 for(const src of [glass,read('../app/src/dev/pixel/voice/HomeGlyph.java')])assert.ok(!/setOnClickListener|setContentDescription|drawText/.test(src));
 assert.match(cell,/tile\.setContentDescription\(label\);\n  tile\.setClickable\(true\);\n  tile\.setFocusable\(true\);/);
 assert.match(cell,/image\.setImportantForAccessibility\(View\.IMPORTANT_FOR_ACCESSIBILITY_NO\);/);
 assert.match(cell,/name\.setImportantForAccessibility\(View\.IMPORTANT_FOR_ACCESSIBILITY_NO\);/);
 // The mark: our suite's glyph (HomeAppGroups.glyph); any other app's own icon, never redrawn or tinted.
 assert.match(tile,/HomeAppGroups\.glyph\(app\.activityInfo\.packageName,AppLabels\.preferredCamera\(getPackageManager\(\)\)\)/);
 const mark=main.slice(main.indexOf(' android.graphics.drawable.Drawable appMark('),main.indexOf(' ArrayList<ResolveInfo> folderApps'));
 assert.match(mark,/return app\.loadIcon\(getPackageManager\(\)\);\n \}/);
 assert.ok(!/themed|getMonochrome|setTint/.test(mark),'store apps are not tinted');
 assert.ok(!main.includes('AppLabels.icon('),'the illustrated suite PNGs are no longer Home icons');
 // The reference's own path data, parsed unchanged.
 const glyph=read('../app/src/dev/pixel/voice/HomeGlyph.java');
 assert.ok(glyph.includes('stroke("M14.5 9.5c1.6-.9 3.4-.4 4.2 1.1l2.6 4.7'),'the reference phone');
 assert.ok(glyph.includes('fill("M37 11 12.5 22.6l10.3 2.6 2.6 10.3z")'),'the reference direction arrow');
 assert.match(glyph,/static final int CREAM = 0xffecdccb;/);
});

test('system and duplicate apps leave the app pages for one "…" System folder (#403)',()=>{
 const pages=main.slice(main.indexOf('void attachLauncherPages(boolean'),main.indexOf('/** A small square'));
 // The grouping is HomeAppGroups over the launcher's own facts; no other app is hidden, nothing about access changes.
 assert.match(pages,/int group=HomeAppGroups\.group\(packageName,AppLabels\.suiteIndex\(packageName,cameraPackage\)>=0,ourBuild\(packageName\),preinstalled\(app\),cameraPackage\);/);
 assert.match(pages,/else \(group==HomeAppGroups\.SYSTEM\?systemApps:fromPlayStore\(packageName\)\?storeApps:otherApps\)\.add\(app\);/);
 const groups=read('../app/src/dev/pixel/voice/HomeAppGroups.java');
 assert.match(groups,/KEEP_OUT = \{"com\.anthropic\.claude", "com\.openai\.chatgpt", "com\.amazon\.mShop\.android\.shopping"\};/);
 assert.match(groups,/if \(has\(KEEP_OUT, packageName\)\) return MAIN;\n  if \(suite\) return SUITE;/);
 // Home's default apps come from every launchable app, the folder's included.
 assert.match(pages,/RoseHomeView\.homeApps\(getPackageManager\(\),getPackageName\(\),launchable,false\)/);
 assert.match(pages,/int\[\] share=HomeAppLayout\.homeShare\(curated\.size\(\),!folderApps\.isEmpty\(\),rest\.size\(\),homeCapacity\);/);
 assert.match(pages,/if\(!folderApps\.isEmpty\(\)\)homeEntries\.add\(null\);/,'the System folder is Home\'s last cell');
 assert.match(pages,/for\(ResolveInfo app:entries\)if\(!onHome\.contains\(componentKey\(app\)\)\)appEntries\.add\(app\);/,'no app is placed twice to fill space');
 assert.match(pages,/HomeAppLayout\.slice\(appEntries\.size\(\),appPerPage,i\)/,'the app pages continue the inventory after Home');
 assert.ok(pages.indexOf('launchable.addAll(systemApps)')<pages.indexOf('RoseHomeView.homeApps('));
 // The folder: one named cell, a dialog with the real cells; launching, HOME, a rebuild or teardown closes it.
 const folder=main.slice(main.indexOf(' View folderTile(boolean labelled){'),main.indexOf(' void closeSystemFolder(){'));
 assert.match(folder,/HomeAppTile\.tile\(this,new HomeGlyph\("system"\),HomeAppGroups\.FOLDER_LABEL,true,labelled\)/);
 assert.match(folder,/tile\.setContentDescription\(HomeAppGroups\.folderDescription\(folderApps\.size\(\)\)\);/);
 assert.match(folder,/for\(ResolveInfo app:folderApps\)tiles\.add\(appTile\(app\)\);/);
 assert.match(folder,/new HomeFolder\(this,HomeAppGroups\.FOLDER_LABEL,tiles,appColumns,this::closeSystemFolder\)/);
 assert.match(pages,/HashSet<String> seen=new HashSet<>\(\),suiteSeen=new HashSet<>\(\);\n  closeSystemFolder\(\);/);
 // No stored preference, wallpaper or background-work call is touched by the folder.
 for(const absent of ['getPreferences','putBoolean','VoiceService','WallpaperManager'])assert.ok(!folder.includes(absent),absent);
});

test('no conversation-page check uses a literal page index',()=>{
 assert.ok(!/currentPage\(\)\s*==\s*\d/.test(main),'use homePage/ourAppsPage, not a number');
 // Completed-task details auto-open only while the voice controls are shown (#223: they are no longer a page).
 assert.match(main,/\(launcherPager==null\|\|voiceControls\)&&visualDetail==null&&screenDetail==null/);
});

test('the tray Rose renders and speaks the same authoritative state as the tray service and conversation',()=>{
 const snapshot=src=>src.match(/ConversationState\.of\(false[^;]*;/)[0].replace(/\s+/g,'');
 const tray=read('../app/src/dev/pixel/voice/AgentTrayService.java');
 const conversation=read('../app/src/dev/pixel/voice/RoseConversationActivity.java');
 // Same fields, same order, same "no service is inactive" rule as App's two surfaces.
 assert.equal(snapshot(main).replace(/s\./g,'v.'),snapshot(tray));
 assert.equal(snapshot(main),snapshot(conversation));
 assert.match(main,/static ConversationState conversationState\(\)\{VoiceService s=VoiceService\.instance;return s==null\?/);
 // Updated on every refresh tick, before the conversation-page early return, so the fixed Home tray is live too.
 assert.match(main,/void refresh\(\)\{\n  refreshHomeRose\(HomeRose\.shown\(screenHostActive,homeFrame!=null&&homeFrame\.getParent\(\)!=null\)\);\n  if\(drawer\|\|micButton==null\)return;/);
 // The tick starts on resume and stops on pause, so returning to Home reads fresh state.
 assert.match(main,/onResume\(\)\{[^\n]*handler\.removeCallbacks\(tick\);handler\.post\(tick\);\}/);
 assert.match(main,/onPause\(\)\{[^\n]*handler\.removeCallbacks\(tick\);/);
 // The pearl draws it (silver only for a connected, muted call); the control alone is focusable and speaks App's
 // description of the same state.
 assert.match(homeTray,/pearlView\.setImportantForAccessibility\(IMPORTANT_FOR_ACCESSIBILITY_NO\);/);
 // The pearl is silver while Rose's microphone input is muted: the one mute every surface shows (RoseMicInput.shown).
 assert.match(homeTray,/void applyMic\(boolean muted\) \{\n  shownMicMuted = muted;\n  micGlyph\.set\(muted\);/);
 assert.match(homeTray,/"Open Rose\. "\)\n    \+ RosePresenceModel\.describe\(visual\);/);
 for(const src of [home,homeTray,homeRose])for(const guess of ['inputLevel)','outputLevel)','.status','.live','.connected'])assert.ok(!src.includes(guess),guess);
});

test('Back from the conversation or app pages returns Home',()=>{
 assert.match(main,/else if\(drawer&&launcherPager!=null\)launcherPager\.showPage\(homePage,true\);else if\(experimentalHome&&chatMode\)\{chatMode=false;home\(\);\}else if\(voiceControls\)closeVoiceControls\(\);else if\(launcherPager!=null\)launcherPager\.showPage\(homePage,true\);\}/);
 // #223: Back first leaves the conversation mode inside the voice controls, then closes them back to Home.
});

test('#410 gesture_nav_contract_v1: a RectF-only reply to the verified recents component, failing closed',()=>{
 const g=main.slice(main.indexOf(' void gestureContract(Intent i,boolean homeShown){'),main.indexOf(' static String recentsPackage(){'));
 assert.ok(g.length>0,'the handler exists');
 assert.match(main,/showSettingsFor\(i\);gestureContract\(i,homeShown\);\}/,'after the existing navigation, which it never changes');
 assert.match(g,/GestureContract\.entry\(getClass\(\)==MainActivity\.class,Build\.VERSION\.SDK_INT,homeIntent,homeShown\)/,'MainActivity itself, SDK 35+, HOME, Home not showing');
 assert.ok(g.indexOf('getCurrentCaller()')>0&&g.indexOf('getCurrentCaller()')<g.indexOf('getBundleExtra('),'the caller is checked before any extra is read');
 assert.match(g,/GestureContract\.caller\(caller\.getPackage\(\),caller\.getUid\(\),recentsPackage\(\)\)/);
 assert.match(g,/callback\.replyTo==null\|\|!\(callback\.obj instanceof ParcelUuid\)\|\|!android\.os\.Process\.myUserHandle\(\)\.equals\(user\)/,'well-formed, this user');
 assert.match(g,/catch\(RuntimeException unreadable\)\{\}/,'a malformed contract is no contract');
 assert.match(g,/data\.putParcelable\(GestureContract\.ICON_POSITION,target\);reply\.setData\(data\);/,'the rect is the only data');
 assert.doesNotMatch(g,/SurfaceControl|surface_control|finish_callback|SurfaceView/,'never a surface or a finish callback');
 assert.doesNotMatch(g,/Log\.|showPage\(|startActivity|startService|command\(/,'no logging, navigation, voice or task action');
 assert.match(main,/tile\.setTag\(GestureContract\.tag\(app\.activityInfo\.packageName,app\.activityInfo\.name\)\);/,'tiles carry their exact component');
 assert.match(main,/tile\.setTag\(GestureContract\.FOLDER_TAG\);/);
 // Review of beb371c: the listener lives on the window's observer (never a detachable pager's), and the send waits
 // for that frame's measure callbacks and is skipped while they re-plan Home.
 assert.doesNotMatch(g,/pager\.getViewTreeObserver\(\)/,'never the pager\'s observer');
 assert.equal((g.match(/decor\.getViewTreeObserver\(\)\.removeOnPreDrawListener\(once\[0\]\)/g)||[]).length,2,'both removals on the window observer');
 assert.match(g,/handler\.post\(\(\)->sendGestureReply\(pager,tag,folder,reply,to\)\)/,'one message after the pre-draw');
 assert.match(main,/if\(pager!=launcherPager\|\|replanPending\|\|isFinishing\(\)\)return;/,'no target while Home is about to be rebuilt');
 assert.equal((main.match(/Height=cell;replanPending=true;\n  handler\.post\(\(\)->\{if\(launcherPager!=null&&!isFinishing\(\)\)attachLauncherPages\(true\);\}\);/g)||[]).length,2,'both re-plans mark it');
 assert.match(main,/void attachLauncherPages\(boolean preservePage\)\{\n  replanPending=false;/,'a rebuild clears it');
});

// The Settings intent must not silently disappear when the Home anchor is absent/replaced.
test('Settings opens a window-owned dialog without depending on the launcher frame',()=>{
 const route=main.slice(main.indexOf('void showSettingsFor(Intent intent)'),main.indexOf('/** HOME returning'));
 assert.ok(!route.includes('homeFrame'));
 assert.match(route,/getWindow\(\)\.getDecorView\(\)\.post/);
 assert.match(main,/SettingsDialog\.show\(this,menu\.getMenu\(\)\);/);
});
