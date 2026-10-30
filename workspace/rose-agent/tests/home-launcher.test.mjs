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
 // Page indices are named and set from the built page list (HomePages): Home, apps, Widgets, legacy voice last.
 assert.match(main,/int homePage=0,voicePage=1,widgetsPage=2,ourAppsPage=3;/);
 assert.match(main,/homePage=HomePages\.HOME;ourAppsPage=HomePages\.FIRST_APPS;widgetsPage=HomePages\.widgets\(count\);voicePage=HomePages\.voice\(count\);/);
 // Cold start routes only a fresh intent; recreation (saved state) keeps the saved page, never reopening the conversation.
 assert.match(main,/home\(\);showHomeFor\(getIntent\(\),b==null\);if\(b!=null&&b\.containsKey\("home-page"\)&&launcherPager!=null\)launcherPager\.showPage\(HomePages\.restore\(b\.getInt\("home-page"\),launcherPager\.getChildCount\(\)\),false\);/);
 assert.match(main,/onNewIntent\(Intent i\)\{super\.onNewIntent\(i\);int previous=launcherPager==null\?-1:launcherPager\.currentPage\(\);boolean homeShown=!homeStopped;if\(!reuseHome\(i\)\)home\(\);showHomeFor\(i,true,previous,homeShown\);\}/);
 const route=main.slice(main.indexOf('void showHomeFor(Intent intent,boolean fresh,int previous,boolean homeShown)'));
 assert.match(route,/^void showHomeFor\(Intent intent,boolean fresh,int previous,boolean homeShown\)\{if\(intent==null\)return;HomeEntry entry=HomeEntry\.of\(Intent\.ACTION_MAIN\.equals\(intent\.getAction\(\)\),intent\.hasCategory\(Intent\.CATEGORY_HOME\),intent\.hasCategory\(Intent\.CATEGORY_LAUNCHER\),fresh,\(intent\.getFlags\(\)&Intent\.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY\)!=0,DeviceIdentity\.paired\(this\)\);if\(entry\.showHome&&launcherPager!=null\)launcherPager\.showPage\(HomePages\.target\(true,!intent\.hasCategory\(Intent\.CATEGORY_HOME\),homeShown,previous,launcherPager\.currentPage\(\),launcherPager\.getChildCount\(\)\),false\);if\(entry\.openConversation\)openRoseConversation\(\);\}/);
 assert.match(main,/void showHomeFor\(Intent intent,boolean fresh\)\{showHomeFor\(intent,fresh,-1,true\);\}/);
 // "Home shown" means only paused, never stopped: an app or the conversation over Home stops it.
 assert.match(main,/@Override protected void onStop\(\)\{super\.onStop\(\);homeStopped=true;\}/);
 assert.match(main,/screenHostActive=true;homeStopped=false;/);
 assert.match(main,/onSaveInstanceState\(Bundle out\)\{super\.onSaveInstanceState\(out\);if\(launcherPager!=null\)out\.putInt\("home-page",launcherPager\.currentPage\(\)\);\}/);
 // The same explicit Intent the Rose presence uses; routing never touches voice, the HOME role or the wallpaper.
 assert.match(main,/void openRoseConversation\(\)\{startActivity\(new Intent\(this,RoseConversationActivity\.class\)\);\}/);
 for(const absent of ['RoleManager','ROLE_HOME','WallpaperManager','setPreferredActivity','clearPackagePreferredActivities'])assert.ok(!main.includes(absent),absent);
 assert.doesNotMatch(route.slice(0,route.indexOf('\n')),/VoiceService|talk|endConversation|mute/i);
 // Rebuilding for menu actions or package changes keeps the current page, and the legacy voice page if shown.
 assert.match(main,/int selected=preservePage&&launcherPager!=null\?launcherPager\.currentPage\(\):-1;boolean onVoice=selected>=0&&selected==voicePage;/);
 assert.match(main,/if\(selected<0\|\|onVoice\)selected=voicePage;/);
});

test('Home comes first, the app pages follow it, and the legacy voice page is last (OS-HIFI-HOME-001)',()=>{
 const pages=main.slice(main.indexOf('void attachLauncherPages(boolean'),main.indexOf('/** A small square'));
 const order=['titles.add("Home")','HomeAppLayout.title(i,appPages)','titles.add("Widgets")','titles.add("Voice home")'].map(t=>pages.indexOf(t));
 // The app pages hold one continuous real inventory: our apps first, then preinstalled, Play Store and other installs.
 assert.match(pages,/for\(ArrayList<ResolveInfo> group:Arrays\.asList\(ourApps,systemApps,storeApps,otherApps\)\)launchable\.addAll\(group\);/);
 assert.deepEqual([...order].sort((a,b)=>a-b),order);
 assert.ok(order.every(i=>i>=0));
 assert.ok(pages.indexOf('launcherPager.addView(roseHome=new RoseHomeView(')<pages.indexOf('if(number==voicePage){launcherPager.addView(page);continue;}'));
 assert.match(pages,/for\(int number=HomePages\.FIRST_APPS;number<count;number\+\+\)\{\n   if\(number==voicePage\)\{launcherPager\.addView\(page\);continue;\}/);
 // Only the conversation page runs the voice refresh and the swipe-up gesture.
 assert.match(pages,/drawer=index!=voicePage;/);
 // The pager sits in a HomeFrame with the fixed tray; the frame, not the pager, is the content view.
 assert.match(pages,/homeTray=new HomeTray\(this,this::openRoseConversation,this::openOverview,MainActivity::overviewAvailable\);homeFrame=new HomeFrame\(this,launcherPager,homeTray,fixedDots\);\n  setContentView\(homeFrame\);/);
});

test('app pages sit on the shared wallpaper with one fixed row of dots and no drawer headings (OS-PAGING-158-M001)',()=>{
 const pages=main.slice(main.indexOf('void attachLauncherPages(boolean'),main.indexOf('/** A small square'));
 // One wallpaper behind the whole pager (drawn at the scroll position), not a per-page or drawer background.
 assert.match(pages,/launcherPager\.setBackground\(new RoseHomeView\.Wallpaper\(getResources\(\)\)\);/);
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
 // Labels wrap to two lines in the measured cell; the cell height is what HomeAppLayout plans with.
 assert.match(main,/name\.setMaxLines\(HomeAppLayout\.LABEL_LINES\);/);
 assert.match(main,/new LinearLayout\.LayoutParams\(-1,HomeAppLayout\.LABEL_LINES\*name\.getLineHeight\(\)\)/);
 // One row of dots, fixed in the frame above the tray, and the pages' bottom inset clears both.
 assert.equal((main.match(/makePageDots\(titles\)/g)||[]).length,1);
 assert.match(homeFrame,/reserved = activity\.dp\(HomeTray\.HEIGHT_DP\) \+ 2 \* gap \+ dotsHeight;/);
 assert.match(homeFrame,/addView\(dots, dotsParams\);/);
 // A re-measure re-plans once, then only when the real capacity changes.
 assert.match(main,/if\(!HomeAppLayout\.replan\(appColumns,appPerPage,width,height,cell,getResources\(\)\.getDisplayMetrics\(\)\.density\)\)return;\n  if\(width==appAreaWidth&&height==appAreaHeight&&cell==appCellHeight\)return;/);
 // Launch and return are unchanged: every tile opens its own app through WorkspaceLauncher.
 assert.match(main,/tile\.setOnClickListener\(v->WorkspaceLauncher\.open\(this,app,label\)\);/);
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
 assert.match(main,/new RoseHomeView\(this,RoseHomeView\.dockApps\(getPackageManager\(\),getPackageName\(\),launchable\),homeColumns\)\);/);
 // Home's columns come from the same planned/measured app area and cell as the app pages.
 assert.match(main,/appPerPage=HomeAppLayout\.perPage\(width,height,cell,density\);homeColumns=HomeAppLayout\.homeColumns\(width,height,cell,density\);/);
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

test('Home shows the default apps through the app pages\' real tile and launch path',()=>{
 // Home's cells are MainActivity.appTile: the installed icon/label, AppLabels and WorkspaceLauncher, as on the app pages.
 assert.match(home,/for \(ResolveInfo app : dock\) grid\.addView\(activity\.appTile\(app\)\);/);
 const tile=main.slice(main.indexOf(' View appTile(ResolveInfo app){'),main.indexOf('\n }',main.indexOf(' View appTile(ResolveInfo app){')));
 assert.match(tile,/tile\.setOnClickListener\(v->WorkspaceLauncher\.open\(this,app,label\)\);/);
 assert.match(tile,/AppLabels\.label\(getPackageManager\(\),app\.activityInfo\.packageName/);
 assert.match(tile,/tile\.setContentDescription\(label\);/);
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
 // Home's grid is the app pages' HomeAppPage, bottom-aligned above the fixed dots/tray, with no re-plan callback.
 assert.match(home,/HomeAppPage grid = new HomeAppPage\(activity, columns, null\);/);
 assert.match(home,/addView\(grid, new LayoutParams\(-1, -1\)\);/);
 // Its bottom padding is only the frame's inset (system bar + tray + dots), like the app pages: nothing is drawn under the tray.
 assert.match(home,/side \+ i\.getSystemWindowInsetRight\(\), i\.getSystemWindowInsetBottom\(\)\);/);
 const page=main.slice(main.indexOf(' View appPage('),main.indexOf(' TextView appLabel('));
 assert.match(page,/dp\(HomeAppLayout\.MARGIN_DP\)\+i\.getSystemWindowInsetRight\(\),i\.getSystemWindowInsetBottom\(\)\);/);
 // The tray and the one row of dots stay the frame's only bottom controls; every other app is a swipe or a dot away.
 assert.equal((main.match(/new HomeTray\(/g)||[]).length,1);
 assert.match(main,/dot\.setContentDescription\("Go to "\+titles\.get\(i\)\.toLowerCase\(Locale\.ROOT\)\);dot\.setFocusable\(true\);/);
 assert.match(main,/void apps\(\)\{if\(launcherPager!=null\)launcherPager\.showPage\(ourAppsPage,true\);\}/);
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
 // The dock is built from the same role list the stamp compares.
 assert.match(home,/for \(String pkg : dockRolePackages\(pm, self\)\)/);
});

test('the Home tray stays fixed over the swiping pages, above system insets (OS-HIFI-HOME-001)',()=>{
 // The tray is the frame's own child, outside the pager, so it never scrolls with a page.
 assert.match(homeFrame,/addView\(pager, new LayoutParams\(-1, -1\)\);/);
 assert.match(homeFrame,/LayoutParams trayParams = new LayoutParams\(-1, activity\.dp\(HomeTray\.HEIGHT_DP\), Gravity\.BOTTOM\);/);
 // Pages get the tray's height as extra bottom inset, so dots and grids stay above it; the tray sits above the nav bar.
 assert.match(homeFrame,/pager\.dispatchApplyWindowInsets\(insets\.replaceSystemWindowInsets\(left, insets\.getSystemWindowInsetTop\(\), right,\n    bottom \+ reserved\)\);/);
 assert.match(homeFrame,/trayParams\.setMargins\(side \+ left, 0, side \+ right, gap \+ bottom\);/);
 // Production status and navigation bars stay: nothing hides system bars.
 for(const src of [homeFrame,homeTray])for(const hide of ['SYSTEM_UI_FLAG','hide(WindowInsets','setDecorFitsSystemWindows','FLAG_FULLSCREEN'])assert.ok(!src.includes(hide),hide);
 // The selected HiFi composition (ce126f56): a 92dp bar with 22dp corners and two 104x66dp wells at its ends.
 assert.match(homeTray,/static final int HEIGHT_DP = 92, GAP_DP = 16, SIDE_DP = 24, WELL_W_DP = 104, WELL_H_DP = 66;/);
 // APP-OS-VISUAL-1030: the shared TrayMaterial (22dp-corner brushed tray); each well view is its 104x66 glass plus
 // the pocket on every side, and the end padding keeps the glass 22dp from the tray's ends (.tray.two).
 assert.match(homeTray,/setBackground\(new TrayMaterial\.Tray\(activity\.getResources\(\)\.getDisplayMetrics\(\)\.density\)\);/);
 assert.match(homeTray,/setPadding\(activity\.dp\(22 - TrayMaterial\.POCKET_DP\), 0, activity\.dp\(22 - TrayMaterial\.POCKET_DP\), 0\);/);
 assert.equal((homeTray.match(/new LayoutParams\(activity\.dp\(WELL_W_DP \+ 2 \* TrayMaterial\.POCKET_DP\), activity\.dp\(WELL_H_DP \+ 2 \* TrayMaterial\.POCKET_DP\)\)/g)||[]).length,2);
 assert.match(homeTray,/addView\(new View\(activity\), new LayoutParams\(0, 1, 1\)\);/);
 assert.ok(!home.includes('sessionMark')&&!home.includes('showSession')&&!home.includes('RoseMark'));
});

test('the overview well asks for the real system overview through the one permitted route (OS-OVERVIEW-156-M001)',()=>{
 // The selected glyph stays; the well is one focusable, clickable action whose only click is the overview callback.
 const well=homeTray.slice(homeTray.indexOf('FrameLayout overviewWell = well(activity);'),homeTray.indexOf('addView(overviewWell'));
 assert.match(well,/overviewWell\.addView\(new OverviewGlyph\(activity\), new FrameLayout\.LayoutParams\(-1, -1\)\);/);
 assert.match(well,/overviewWell\.setOnClickListener\(v -> openOverview\.run\(\)\);/);
 assert.ok(!well.includes('setEnabled(false)')&&!homeTray.includes('not available yet'),'the obsolete disabled placeholder is gone');
 for(const fake of ['GLOBAL_ACTION','startActivity','Intent','WorkspaceLauncher','apps'])assert.ok(!well.includes(fake),fake);
 // Each refresh shows whether the route is available, dimmed with the reason when it is not.
 assert.match(homeTray,/boolean available = overviewAvailable\.getAsBoolean\(\);\n  if \(available != shownOverviewAvailable\) applyOverview\(available\);/);
 assert.match(homeTray,/overview\.setContentDescription\(HomeOverview\.describe\(available\)\);/);
 // MainActivity: one HomeOverview per activity (tray rebuilds keep it), cleared when Home leaves the foreground.
 assert.match(main,/final HomeOverview homeOverview=new HomeOverview\(\);/);
 assert.match(main,/homeTray=new HomeTray\(this,this::openRoseConversation,this::openOverview,MainActivity::overviewAvailable\);/);
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
 assert.match(main,/void refreshHomeRose\(boolean shown\)\{if\(homeTray==null\)return;VoiceService s=VoiceService\.instance;ConversationState c=conversationState\(\);homeTray\.show\(c,HomeRose\.state\(c,s==null\?Float\.NaN:s\.trayInputLevel,s==null\?Float\.NaN:s\.trayOutputLevel,s==null\?0L:s\.trayAudioObservedAt,SystemClock\.elapsedRealtime\(\),HomeRose\.reducedMotion\(android\.provider\.Settings\.Global\.getFloat\(getContentResolver\(\),android\.provider\.Settings\.Global\.ANIMATOR_DURATION_SCALE,1f\)\),shown\)\);\}/);
 assert.match(homeRose,/RoseVisualState\.from\(conversation, RoseVisualState\.fresh\(inputLevel, observedAtMs, nowMs\),\n    RoseVisualState\.fresh\(outputLevel, observedAtMs, nowMs\), reducedMotion, homeShown\);/);
 assert.equal((main.match(/refreshHomeRose\(/g)||[]).length,3,'defined, called by refresh and by onPause only');
 // Pause renders hidden first; a rebuilt tray and a destroyed activity release the old one, which then ignores frames.
 assert.match(main,/protected void onPause\(\)\{refreshHomeRose\(false\);/);
 assert.match(main,/if\(homeTray!=null\)homeTray\.release\(\);homeTray=new HomeTray\(this,this::openRoseConversation,this::openOverview,MainActivity::overviewAvailable\);/);
 assert.match(main,/protected void onDestroy\(\)\{if\(homeTray!=null\)homeTray\.release\(\);/);
 assert.match(homeTray,/void show\(ConversationState conversation, RoseVisualState visual\) \{\n  if \(released\) return;/);
 assert.match(homeTray,/applyVisual\(RoseVisualState\.from\(shownConversation, Float\.NaN, Float\.NaN, true, false\)\)/);
 // One mount point for App's renderer; no second poller, timer or animation of Home's own.
 assert.equal((homeTray.match(/void applyVisual\(RoseVisualState visual\)/g)||[]).length,1);
 assert.equal((homeTray.match(/new RosePresenceView\(/g)||[]).length,1);
 for(const src of [homeTray,homeRose])for(const timer of ['postDelayed','Handler','ValueAnimator','Choreographer','Timer','schedule'])assert.ok(!src.includes(timer),timer);
 // No duplicate renderer: Home draws no WebGL, shader or surface of its own.
 for(const src of [homeTray,homeRose])for(const gl of ['GLSurfaceView','GLES','Shader','TextureView','SurfaceView'])assert.ok(!src.includes(gl),gl);
});

test('Home labels are the app pages\' labels: whole names over two lines, never a one-line cut (M008)',()=>{
 // 302/304 forced one line with an end ellipsis (Messaging, Camera (system) cut at 720 px). Home now uses appLabel.
 const label=main.slice(main.indexOf(' TextView appLabel('),main.indexOf(' LinearLayout makePageDots('));
 assert.doesNotMatch(label,/setSingleLine\(true\)/);
 assert.match(label,/name\.setMaxLines\(HomeAppLayout\.LABEL_LINES\);/);
 assert.match(main,/new LinearLayout\.LayoutParams\(-1,HomeAppLayout\.LABEL_LINES\*name\.getLineHeight\(\)\)/);
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
 // One resource, same name: Home and the reading surface share it; no owner wallpaper preference exists to reset.
 assert.match(home,/BitmapFactory\.decodeResource\(resources, R\.drawable\.rose_home_wallpaper\)/);
 assert.match(home,/HomeAppLayout\.cover\(wallpaper\.getWidth\(\), wallpaper\.getHeight\(\), bounds\.width\(\), bounds\.height\(\),\s+HomeAppLayout\.WALL_X, HomeAppLayout\.WALL_Y\);/);
 assert.match(layout,/WALL_X = \.62f, WALL_Y = \.30f, IVORY_X = \.80f, IVORY_Y = \.20f;/);
 const drawing=read('../app/src/dev/pixel/voice/ConversationDrawing.java');
 assert.match(drawing,/HomeAppLayout\.cover\(wallpaper\.getWidth\(\),wallpaper\.getHeight\(\),w\*1\.2f,h\*1\.2f,HomeAppLayout\.IVORY_X,HomeAppLayout\.IVORY_Y\)/,'the reading surface crops too, never stretches the image');
 assert.ok(!/new Rect\(0,0,wallpaper\.getWidth\(\),wallpaper\.getHeight\(\)\)/.test(drawing),'no whole-image stretch');
 // The crop is prepared when the bounds change, not allocated per frame.
 const draw=home.slice(home.indexOf('@Override public void draw(Canvas canvas)'));
 assert.ok(!draw.slice(0,draw.indexOf('\n  }')).includes('new '),'no allocation while drawing the wallpaper');
 assert.match(home,/@Override protected void onBoundsChange\(Rect bounds\)/);
});

test('one tray material for Home and the conversation, and the HiFi-sized tray Rose (APP-OS-VISUAL-1030)',()=>{
 const material=read('../app/src/dev/pixel/voice/TrayMaterial.java');
 const conversation=read('../app/src/dev/pixel/voice/RoseConversationActivity.java');
 // Both trays and every well use the one material; no hand-rolled gradients are left behind.
 assert.match(homeTray,/well\.setBackground\(new TrayMaterial\.Well\(activity\.getResources\(\)\.getDisplayMetrics\(\)\.density\)\);/);
 assert.match(conversation,/icon\.setBackground\(new TrayMaterial\.Well\(getResources\(\)\.getDisplayMetrics\(\)\.density\)\);icon\.setDuplicateParentStateEnabled\(true\);/);
 assert.match(conversation,/row\.setBackground\(new TrayMaterial\.Tray\(getResources\(\)\.getDisplayMetrics\(\)\.density\)\);row\.setElevation\(dp\(12\)\);/);
 assert.ok(!/GradientDrawable/.test(homeTray),'HomeTray draws no private gradient');
 assert.ok(!/0xfaffffff,0x8cffffff|0xfff6f7f9,0xffe3e7eb/.test(conversation),'the conversation has no copy of the material');
 // Effective style.css values at ce126f56 (review overrides applied): alpha x 255, rounded.
 const argb=(a,rgb)=>'0x'+Math.round(a*255).toString(16).padStart(2,'0')+rgb;
 assert.ok(material.includes(`GLASS = {${argb(.98,'ffffff')}, ${argb(.55,'ffffff')}, ${argb(.35,'d6e2f0')}, ${argb(.5,'becee0')}}`),'well glass');
 assert.match(material,/GLASS_STOPS = \{0f, \.34f, \.68f, 1f\};/);
 assert.match(material,/ellipse\.setScale\(1\.1f \* w, \.8f \* h\);/);assert.match(material,/ellipse\.postTranslate\(lens\.left \+ \.32f \* w, lens\.top \+ \.18f \* h\);/);
 for(const [name,a,rgb] of [['INNER_EDGE',.65,'ffffff'],['LOWER_SHADE',.22,'465c7d'],['SHEEN',.85,'ffffff'],['PRESSED_SHADE',.3,'3c506e'],
   ['POCKET_EDGE',.42,'5f738e'],['COLLAR',.9,'c6ced7'],['COLLAR_LINE',.35,'78879b'],['LIP',.75,'ffffff'],['LOWER_RIM',.28,'3c4b5f']])
  assert.ok(material.includes(`${name} = ${argb(a,rgb)}`),name);
 assert.match(material,/METAL = \{0xfff6f7f9, 0xffe3e7eb, 0xffcfd5db, 0xffe8ebee\};/);assert.match(material,/METAL_STOPS = \{0f, \.38f, \.62f, 1f\};/);
 assert.ok(material.includes(`SPECULAR = {0x00ffffff, ${argb(.55,'ffffff')}, 0x00ffffff}`));assert.match(material,/SPECULAR_STOPS = \{\.28f, \.44f, \.60f\};/);
 assert.match(material,/POCKET_DP = 5;/);assert.match(material,/RADIUS_DP = 22;/);
 // The pressed glass follows the real pressed state; the drawables are decorative (no listeners, text or names).
 assert.match(material,/if \(s == android\.R\.attr\.state_pressed\) now = true;/);
 assert.ok(!/setOnClickListener|setContentDescription|drawText/.test(material));
 // The tray Rose is sized from the measured HiFi sphere through the shared renderer's own geometry.
 assert.match(homeTray,/int roseSize = activity\.dp\(RosePresenceModel\.viewSizeFor\(RosePresenceModel\.TRAY_SPHERE_DP\)\);/);
 assert.match(homeTray,/rose\.addView\(roseView, new FrameLayout\.LayoutParams\(roseSize, roseSize, Gravity\.CENTER\)\);/);
 assert.match(read('../app/src/dev/pixel/voice/RosePresenceView.java'),/float r=Math\.min\(getWidth\(\),getHeight\(\)\)\/\(2f\*RosePresenceModel\.VIEW_RADII\);/);
 // The conversation's wells keep the reference 92x66 where they fit and narrow on a small screen (ConversationPresentation).
 assert.match(conversation,/int lens=ConversationPresentation\.wellWidth\(getResources\(\)\.getConfiguration\(\)\.screenWidthDp,TrayMaterial\.POCKET_DP\);/);
});

test('app tiles are the HiFi frosted glass, on Home and the app pages alike (APP-OS-VISUAL-1030)',()=>{
 const glass=read('../app/src/dev/pixel/voice/HomeGlassTile.java');
 const tile=main.slice(main.indexOf(' View appTile(ResolveInfo app){'),main.indexOf('\n }',main.indexOf(' View appTile(ResolveInfo app){')));
 assert.match(tile,/iconFrame\.setBackground\(new HomeGlassTile\(getResources\(\)\.getDisplayMetrics\(\)\.density\)\);iconFrame\.setElevation\(dp\(4\)\);/);
 assert.ok(!/raised\(/.test(tile),'no cream raised tile');
 // style.css .tile at ce126f56: 18px radius; linear-gradient(160deg, rgba(255,255,255,.62), rgba(255,255,255,.18) 55%,
 // rgba(225,236,248,.32)); rings rgba(120,150,190,.32) and inset rgba(255,255,255,.5); top inset rgba(255,255,255,.95);
 // inset 0 -2px 6px rgba(60,90,130,.18); ::after sheen rgba(255,255,255,.55) over the top half.
 const argb=(a,rgb)=>'0x'+Math.round(a*255).toString(16).padStart(2,'0')+rgb;
 assert.match(glass,/RADIUS_DP = 18;/);
 assert.ok(glass.includes(`FILL = {${argb(.62,'ffffff')}, ${argb(.18,'ffffff')}, ${argb(.32,'e1ecf8')}}`),'fill');
 assert.match(glass,/FILL_STOPS = \{0f, \.55f, 1f\};/);assert.match(glass,/Math\.toRadians\(160\)/);
 for(const [name,a,rgb] of [['OUTER_RING',.32,'7896be'],['INNER_RING',.5,'ffffff'],['TOP_EDGE',.95,'ffffff'],['LOWER_SHADE',.18,'3c5a82'],['SHEEN',.55,'ffffff']])
  assert.ok(glass.includes(`${name} = ${argb(a,rgb)}`),name);
 // Decorative: the tile is still the one spoken, clickable target; the drawable holds no listener or text.
 assert.ok(!/setOnClickListener|setContentDescription|drawText/.test(glass));
 assert.match(tile,/tile\.setContentDescription\(label\);tile\.setClickable\(true\);tile\.setFocusable\(true\);/);
});

test('no conversation-page check uses a literal page index',()=>{
 assert.ok(!/currentPage\(\)\s*==\s*\d/.test(main),'use voicePage/homePage, not a number');
 // Completed-task details auto-open only while the conversation page is visible.
 assert.match(main,/launcherPager\.currentPage\(\)==voicePage\)&&visualDetail==null&&screenDetail==null/);
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
 // App's shared renderer draws it; the well alone is focusable and speaks App's description of the same state.
 assert.match(homeTray,/roseView = new RosePresenceView\(activity\);\n  roseView\.setImportantForAccessibility\(IMPORTANT_FOR_ACCESSIBILITY_NO\);/);
 assert.match(homeTray,/roseView\.show\(visual\);\n  String spoken = "Open Rose\. " \+ RosePresenceModel\.describe\(visual\);/);
 for(const src of [home,homeTray,homeRose])for(const guess of ['inputLevel)','outputLevel)','.status','.live','.connected'])assert.ok(!src.includes(guess),guess);
});

test('Back from the conversation or app pages returns Home',()=>{
 assert.match(main,/else if\(drawer&&launcherPager!=null\)launcherPager\.showPage\(homePage,true\);else if\(experimentalHome&&chatMode\)\{chatMode=false;home\(\);\}else if\(launcherPager!=null\)launcherPager\.showPage\(homePage,true\);\}/);
});
