// Source checks for the app builder's refresh lifecycle; no device needed. A build that finished or was repaired on
// the server while the builder sat in the background, or while its library was showing, must show as it is now
// without the owner opening a project or tapping All apps (Nathan's first apps: ready on the server, stale on phone).
import fs from 'node:fs';
import assert from 'node:assert/strict';
import test from 'node:test';
const read=p=>fs.readFileSync(new URL(p,import.meta.url),'utf8');
const screen=read('../app/src/dev/pixel/builder/BuilderScreen.java');
const agent=read('../app/src/dev/pixel/builder/BuilderActivity.java');
const manifest=read('../app/AndroidManifest.xml');
const body=(src,sig)=>{const i=src.indexOf(sig);assert.ok(i>=0,'missing '+sig);let d=0;for(let j=src.indexOf('{',i);j<src.length;j++){if(src[j]==='{')d++;else if(src[j]==='}'&&--d===0)return src.slice(i,j+1);}throw new Error('unbalanced '+sig);};

test('the builder stays one live activity, so coming back to it re-uses what it last drew',()=>{
 const a=manifest.match(/<activity android:name="dev\.pixel\.builder\.BuilderActivity"[^>]*>/)[0];
 assert.match(a,/launchMode="singleTop"/);
 assert.match(a,/android:exported="false"/);
});

test('coming back to the builder reads the library or project on screen again',()=>{
 const r=body(screen,'protected void onResume()');
 assert.match(r,/super\.onResume\(\)/);
 assert.match(r,/resumeConnect\(\)/,'a reconnect is the subclass\'s first');
 assert.match(r,/Refresh\.onResume\(shown, first, api != null\)/);
 assert.match(r,/case LIBRARY: refreshLibrary\(\)/);
 assert.match(r,/case PROJECT: if \(openProject != null\) showProject\(openProject\)/);
 // The Agent's reconnect-on-resume is the hook, not a second onResume that would skip the refresh.
 assert.doesNotMatch(agent,/protected void onResume\(\)/);
 assert.match(body(agent,'boolean resumeConnect()'),/showConnected\(\); return true;/);
});

test('the library polls while a build in it runs, and polling stops when the builder is not seen',()=>{
 const d=body(screen,'void drawLibrary(');
 assert.match(d,/Refresh\.libraryPolls\(projects\)\) main\.postDelayed\(poll, POLL_MS\)/);
 assert.match(body(screen,'public void run()'),/else if \(shown == Refresh\.Shown\.LIBRARY\) refreshLibrary\(\)/);
 assert.match(body(screen,'protected void onPause()'),/main\.removeCallbacks\(poll\)/);
});

test('each screen says what it is, so resume never refetches over what the owner is typing',()=>{
 assert.match(body(screen,'void showLibrary()'),/shown = Refresh\.Shown\.LIBRARY/);
 assert.match(body(screen,'void showProject(String id)'),/shown = Refresh\.Shown\.PROJECT/);
 assert.match(body(screen,'void showCreate('),/shown = Refresh\.Shown\.OTHER/);
 assert.match(body(screen,'void showSignIn('),/shown = Refresh\.Shown\.OTHER/);
 assert.match(body(agent,'void showPair(String error)'),/shown = Refresh\.Shown\.OTHER/);
});
