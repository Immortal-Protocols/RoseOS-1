// Source checks for the launcher Home page; no device needed.
import fs from 'node:fs';
import assert from 'node:assert/strict';
import test from 'node:test';
import crypto from 'node:crypto';
const read=path=>fs.readFileSync(new URL(path,import.meta.url),'utf8');
const main=read('../app/src/dev/pixel/voice/MainActivity.java');
const home=read('../app/src/dev/pixel/voice/RoseHomeView.java');
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
 assert.match(main,/int homePage=0,voicePage=1,widgetsPage=2,ourAppsPage=3;/);
 // Cold start routes only a fresh intent; recreation (saved state) keeps Home without reopening the conversation.
 assert.match(main,/home\(\);showHomeFor\(getIntent\(\),b==null\);/);
 assert.match(main,/onNewIntent\(Intent i\)\{super\.onNewIntent\(i\);if\(!reuseHome\(i\)\)home\(\);showHomeFor\(i,true\);\}/);
 const route=main.slice(main.indexOf('void showHomeFor(Intent intent,boolean fresh)'));
 assert.match(route,/^void showHomeFor\(Intent intent,boolean fresh\)\{if\(intent==null\)return;HomeEntry entry=HomeEntry\.of\(Intent\.ACTION_MAIN\.equals\(intent\.getAction\(\)\),intent\.hasCategory\(Intent\.CATEGORY_HOME\),intent\.hasCategory\(Intent\.CATEGORY_LAUNCHER\),fresh,\(intent\.getFlags\(\)&Intent\.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY\)!=0,DeviceIdentity\.paired\(this\)\);if\(entry\.showHome&&launcherPager!=null\)launcherPager\.showPage\(homePage,false\);if\(entry\.openConversation\)openRoseConversation\(\);\}/);
 // The same explicit Intent the Rose presence uses; routing never touches voice, the HOME role or the wallpaper.
 assert.match(main,/void openRoseConversation\(\)\{startActivity\(new Intent\(this,RoseConversationActivity\.class\)\);\}/);
 for(const absent of ['RoleManager','ROLE_HOME','WallpaperManager','setPreferredActivity','clearPackagePreferredActivities'])assert.ok(!main.includes(absent),absent);
 assert.doesNotMatch(route.slice(0,route.indexOf('\n')),/VoiceService|talk|endConversation|mute/i);
 // Rebuilding the pages for menu actions or package changes keeps the conversation (or current) page.
 assert.match(main,/launcherPager\.currentPage\(\):voicePage;/);
});

test('Home comes first and the existing conversation page follows it',()=>{
 const pages=main.slice(main.indexOf('void attachLauncherPages(boolean'),main.indexOf('/** A small square'));
 assert.ok(pages.indexOf('titles.add("Home")')<pages.indexOf('titles.add("Voice home")'));
 assert.ok(pages.indexOf('launcherPager.addView(new RoseHomeView(')<pages.indexOf('launcherPager.addView(page);'));
 assert.match(pages,/for\(int number=voicePage\+1;number<count;number\+\+\)/);
 // Only the conversation page runs the voice refresh and the swipe-up gesture.
 assert.match(pages,/drawer=index!=voicePage;/);
});

test('the Rose entry opens App\'s conversation activity and only navigates',()=>{
 assert.match(main,/new RoseHomeView\(this,RoseHomeView\.dockApps\(getPackageManager\(\),getPackageName\(\),launchable\),this::openRoseConversation,this::apps,/);
 // APP-P2-002-HOOK-001: plain explicit Intent, same task, no flags or extras.
 const hook=main.match(/void openRoseConversation\(\)\{([^}]*)\}/)[1];
 assert.equal(hook,'startActivity(new Intent(this,RoseConversationActivity.class));');
 const activity=manifest.match(/<activity android:name="\.RoseConversationActivity"[^>]*\/>/)[0];
 assert.match(activity,/android:exported="false"/);
 assert.ok(!/launchMode|taskAffinity/.test(activity),'stays in the launcher task so HOME and Back return Home');
 for(const forbidden of ['VoiceService','command(','toggleVoice','startForegroundService','"talk"','"end"','"mute"'])
  assert.ok(!home.includes(forbidden),forbidden);
 // The conversation surface sends voice commands only from its own controls, never on open.
 const conversation=read('../app/src/dev/pixel/voice/RoseConversationActivity.java');
 for(const hook of ['onCreate','onResume']){
  // Tap listeners (setOnClickListener(v->...)) run on the owner's tap, not on open.
  const body=conversation.slice(conversation.indexOf(`void ${hook}(`)).split('\n @Override')[0].split('\n void ')[0]
   .replace(/setOnClickListener\(v->[^;]*;/g,'');
  assert.ok(!/send\(|startForegroundService|setAction\(/.test(body),hook);
 }
});

test('the dock shows real installed apps through the existing launch path',()=>{
 assert.match(home,/WorkspaceLauncher\.open\(activity, app, label\)/);
 assert.match(home,/AppLabels\.label\(activity\.getPackageManager\(\), app\.activityInfo\.packageName/);
 assert.match(home,/pm\.resolveActivity\(role, PackageManager\.MATCH_DEFAULT_ONLY\)/);
 // A chooser ("android") or Rose Agent itself is never a dock app; only launchable activities are used.
 assert.match(home,/"android"\.equals\(pkg\) \|\| pkg\.equals\(self\)/);
 assert.match(home,/for \(ResolveInfo app : launchable\) if \(pkg\.equals\(app\.activityInfo\.packageName\)\)/);
 assert.ok(!/"(com|dev|org)\.[a-z0-9_.]+"/.test(home),'no hard-coded package names');
});

test('an unchanged HOME or app-icon return reuses the built Home; changes rebuild it (OS-HOME-REUSE-001)',()=>{
 // The route is decided after reuse, so HOME still lands on Home and the app icon still opens the conversation.
 assert.match(main,/if\(!reuseHome\(i\)\)home\(\);showHomeFor\(i,true\);/);
 // Only Home entries (HOME, MAIN\/LAUNCHER) qualify, judged by the same HomeEntry rule as routing.
 assert.match(main,/boolean reuseHome\(Intent i\)\{return i!=null&&HomeReuse\.reuse\(HomeEntry\.of\(Intent\.ACTION_MAIN\.equals\(i\.getAction\(\)\),i\.hasCategory\(Intent\.CATEGORY_HOME\),i\.hasCategory\(Intent\.CATEGORY_LAUNCHER\),true,false,true\)\.showHome,/);
 // Whatever home() resets still forces it: chat mode, an open visual or Mac screen, a changed Home setting, another view in place.
 assert.match(main,/launcherPager!=null&&launcherPager\.getParent\(\)!=null,builtHomeStamp,homeStamp\(\),chatMode,visualDetail!=null\|\|screenDetail!=null,experimentalHome!=getPreferences\(0\)\.getBoolean\("conversation-home",true\)\);\}/);
 // The stamp is recorded by every catalog build and covers packages, configuration and the dock's default apps.
 assert.match(main,/launcherPager\.requestApplyInsets\(\);\n  builtHomeStamp=homeStamp\(\);\n \}/);
 assert.match(main,/public void onReceive\(Context context,Intent intent\)\{packageGeneration\+\+;if\(launcherPager!=null&&!isFinishing\(\)\)attachLauncherPages\(true\);\}/);
 for(const field of ['screenWidthDp','screenHeightDp','densityDpi','orientation','fontScale','uiMode','getLocales().toLanguageTags()'])assert.ok(main.includes('c.'+field),field);
 assert.match(main,/RoseHomeView\.dockRolePackages\(getPackageManager\(\),getPackageName\(\)\)\);\}/);
 // The dock is built from the same role list the stamp compares.
 assert.match(home,/for \(String pkg : dockRolePackages\(pm, self\)\)/);
});

test('dock labels are fitted whole, never cut on one line (M008)',()=>{
 const tile=home.slice(home.indexOf('static View dockTile('),home.indexOf('/** Opens the conversation surface'));
 // The 302/304 tile forced one line with an end ellipsis: Messaging and Camera (system) showed cut at 720 px.
 assert.doesNotMatch(tile,/setSingleLine\(true\)/);
 assert.match(tile,/DockLabelView name = new DockLabelView\(activity, label\);/);
 assert.match(tile,/DockLabel fit = DockLabel\.fit\(label, basePx, basePx \* 10 \/ 12, available,/);
 // The full label stays the tile's accessible name and the launch label; the text view is not spoken twice.
 assert.match(tile,/tile\.setContentDescription\(label\);/);
 assert.match(tile,/name\.setImportantForAccessibility\(IMPORTANT_FOR_ACCESSIBILITY_NO\);/);
 // Same 46dp icon and padding; tiles fill the tray height, top-aligned, so icons stay level when a label wraps.
 assert.match(tile,/tile\.addView\(icon, new LinearLayout\.LayoutParams\(activity\.dp\(46\), activity\.dp\(46\)\)\);/);
 assert.match(tile,/tile\.setGravity\(Gravity\.CENTER_HORIZONTAL \| Gravity\.TOP\);/);
 assert.equal((home.match(/new LinearLayout\.LayoutParams\(0, -1, 1\)/g)||[]).length,2);
 // Setters run only on a change, so measuring never loops through requestLayout.
 assert.match(tile,/if \(getMaxLines\(\) != fit\.lines\) setMaxLines\(fit\.lines\);/);
 assert.match(tile,/if \(!fit\.text\.contentEquals\(getText\(\)\)\) setText\(fit\.text\);/);
});

test('the wallpaper is the exact Rose-Fold-v3 narrow Home image, drawn like background-size: cover',()=>{
 const jpeg=fs.readFileSync(new URL('../app/res/drawable-nodpi/rose_home_wallpaper.jpg',import.meta.url));
 // First embedded JPEG of Rose-Fold-v3.html (Library v2, SHA-256 67bf7a50…05ee), 461 x 1024.
 assert.equal(crypto.createHash('sha256').update(jpeg).digest('hex'),'409c0dbfa309dbd56f97a59b04223532e9931ae5c5c4625d3567b77a9c56d8c5');
 assert.match(home,/BitmapFactory\.decodeResource\(getResources\(\), R\.drawable\.rose_home_wallpaper\)/);
 // Shaders and crop are prepared on size change, not allocated per frame.
 assert.ok(!home.slice(home.lastIndexOf('protected void onDraw(')).includes('new '),'no allocation while drawing Home');
});

test('no conversation-page check uses a literal page index',()=>{
 assert.ok(!/currentPage\(\)\s*==\s*\d/.test(main),'use voicePage/homePage, not a number');
 // Completed-task details auto-open only while the conversation page is visible.
 assert.match(main,/launcherPager\.currentPage\(\)==voicePage\)&&visualDetail==null&&screenDetail==null/);
});

test('the Home session mark renders the same authoritative state as the tray and conversation',()=>{
 const snapshot=src=>src.match(/ConversationState\.of\(false[^;]*;/)[0].replace(/\s+/g,'');
 const tray=read('../app/src/dev/pixel/voice/AgentTrayService.java');
 const conversation=read('../app/src/dev/pixel/voice/RoseConversationActivity.java');
 // Same fields, same order, same "no service is inactive" rule as App's two surfaces.
 assert.equal(snapshot(main).replace(/s\./g,'v.'),snapshot(tray));
 assert.equal(snapshot(main),snapshot(conversation));
 assert.match(main,/static ConversationState conversationState\(\)\{VoiceService s=VoiceService\.instance;return s==null\?/);
 // Updated on every refresh tick, before the conversation-page early return, so Home is live too.
 assert.match(main,/void refresh\(\)\{\n  if\(roseHome!=null\)roseHome\.showSession\(conversationState\(\)\);\n  if\(drawer\|\|micButton==null\)return;/);
 // The tick starts on resume and stops on pause, so returning to Home reads fresh state.
 assert.match(main,/onResume\(\)\{[^\n]*handler\.removeCallbacks\(tick\);handler\.post\(tick\);\}/);
 assert.match(main,/onPause\(\)\{[^\n]*handler\.removeCallbacks\(tick\);/);
 // Home chooses its mark only through HomeSessionMark, never from audio levels or status text.
 assert.match(home,/HomeSessionMark\.Mark mark = HomeSessionMark\.of\(state\);/);
 for(const guess of ['inputLevel','outputLevel','status','.live','.connected'])assert.ok(!home.includes(guess),guess);
});

test('Back from the conversation or app pages returns Home',()=>{
 assert.match(main,/else if\(drawer&&launcherPager!=null\)launcherPager\.showPage\(homePage,true\);else if\(experimentalHome&&chatMode\)\{chatMode=false;home\(\);\}else if\(launcherPager!=null\)launcherPager\.showPage\(homePage,true\);\}/);
});
