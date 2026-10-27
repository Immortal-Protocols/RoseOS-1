// Source checks for the launcher Home page; no device needed.
import fs from 'node:fs';
import assert from 'node:assert/strict';
import test from 'node:test';
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
});

test('HOME intents land on the Home page; other opens keep the conversation page',()=>{
 assert.match(main,/int homePage=0,voicePage=1,widgetsPage=2,ourAppsPage=3;/);
 assert.match(main,/home\(\);showHomeFor\(getIntent\(\)\);/);
 assert.match(main,/onNewIntent\(Intent i\)\{super\.onNewIntent\(i\);home\(\);showHomeFor\(i\);\}/);
 assert.match(main,/void showHomeFor\(Intent intent\)\{if\(intent!=null&&intent\.hasCategory\(Intent\.CATEGORY_HOME\)&&launcherPager!=null\)launcherPager\.showPage\(homePage,false\);\}/);
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

test('the Rose entry only navigates; it never starts, mutes or ends voice',()=>{
 assert.match(main,/new RoseHomeView\(this,RoseHomeView\.dockApps\(getPackageManager\(\),getPackageName\(\),launchable\),\(\)->launcherPager\.showPage\(voicePage,true\),this::apps,/);
 for(const forbidden of ['VoiceService','command(','toggleVoice','startForegroundService','"talk"','"end"','"mute"'])
  assert.ok(!home.includes(forbidden),forbidden);
});

test('the dock shows real installed apps through the existing launch path',()=>{
 assert.match(home,/tray\.addView\(activity\.appTile\(app, false\)/);
 assert.match(home,/pm\.resolveActivity\(role, PackageManager\.MATCH_DEFAULT_ONLY\)/);
 // A chooser ("android") or Rose Agent itself is never a dock app; only launchable activities are used.
 assert.match(home,/"android"\.equals\(pkg\) \|\| pkg\.equals\(self\)/);
 assert.match(home,/for \(ResolveInfo app : launchable\) if \(pkg\.equals\(app\.activityInfo\.packageName\)\)/);
 assert.ok(!/"(com|dev|org)\.[a-z0-9_.]+"/.test(home),'no hard-coded package names');
});

test('Back from the conversation or app pages returns Home',()=>{
 assert.match(main,/else if\(drawer&&launcherPager!=null\)launcherPager\.showPage\(homePage,true\);else if\(experimentalHome&&chatMode\)\{chatMode=false;home\(\);\}else if\(launcherPager!=null\)launcherPager\.showPage\(homePage,true\);\}/);
});
