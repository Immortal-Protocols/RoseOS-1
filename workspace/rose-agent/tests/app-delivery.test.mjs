// Automatic /apps catalog loop and backend reachability in app/assets/index.html, with fake
// backend replies. No device, provider or backend is used.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {loadPage} from './web-ui-harness.mjs';

const settle=()=>new Promise(resolve=>setImmediate(resolve));
const calls=(page,name)=>page.calls.filter(c=>c[0]===name).map(c=>c.slice(1));
const SELF='dev.pixel.voice';
const entry=(packageName,label,id)=>({id,label,packageName,versionCode:999,sha256:id,created:1});

test('the automatic catalog loop never offers or installs Rose Agent itself',async()=>{
 const page=loadPage({native:{isSelf:name=>name===SELF,shouldOffer:()=>true}});
 page.window.__apps={apps:[entry(SELF,'Rose Agent','a'.repeat(64)),entry('dev.pixel.calculator.agent','Calculator','b'.repeat(64))]};
 page.run(`api=(method,route)=>route==='/apps'?Promise.resolve(window.__apps):Promise.reject(new Error('unexpected '+route));`);
 page.run('refreshApps()');await settle();
 assert.deepEqual(calls(page,'installApp'),[['b'.repeat(64),'Calculator']],'only the other app is auto-installed');
 assert.deepEqual(page.node('appList').children.map(c=>c.children[0].textContent),['Calculator'],'no Install button for Rose Agent');
});

test('other apps keep their existing automatic offer',async()=>{
 const page=loadPage({native:{isSelf:()=>false,shouldOffer:()=>true}});
 page.window.__apps={apps:[entry('dev.pixel.calculator.agent','Calculator','b'.repeat(64))]};
 page.run(`api=(method,route)=>Promise.resolve(window.__apps);`);
 page.run('refreshApps()');await settle();
 assert.deepEqual(calls(page,'installApp'),[['b'.repeat(64),'Calculator']]);
});

test('check() reports whether the backend answered, for the conversation surface',async()=>{
 const down=loadPage();
 down.run(`api=()=>Promise.reject(new Error('Mac request timed out'));`);
 down.run('check()');await settle();
 assert.deepEqual(calls(down,'backendReachable'),[[false]]);
 assert.equal(down.node('status').textContent,'Cannot reach Mac. Check connection settings.','existing idle text unchanged');

 const up=loadPage();
 up.run(`api=(method,route)=>route==='/health'?Promise.resolve({ready:true}):new Promise(()=>{});`);
 up.run('check()');await settle();
 assert.deepEqual(calls(up,'backendReachable'),[[true]]);
});

test('every voice end reports its typed reason to native code',()=>{
 const page=loadPage();
 page.run(`ready=true;voiceLost('Voice did not connect. Tap Talk to retry.','start_timeout');`);
 page.run(`ready=true;voiceLost('Mac restarted. Tap Talk to reconnect.');`);
 page.run(`ready=true;endConversation();`);
 assert.deepEqual(calls(page,'voiceLifecycle').filter(c=>c[0]==='cleanup').map(c=>c[1]),['start_timeout','other','cleanup']);
});

test('APP-UPDATER-002: explicit self-update is admitted by the service and verified before commit',()=>{
 const dir=new URL('../app/src/dev/pixel/voice/',import.meta.url),src=n=>fs.readFileSync(new URL(n,dir),'utf8');
 const delivery=src('Delivery.java'),voice=src('VoiceService.java'),activity=src('RoseConversationActivity.java'),receiver=src('InstallReceiver.java');
 const generic=delivery.slice(delivery.indexOf('public static void install('),delivery.indexOf('static void installSelf('));
 assert.ok(generic.indexOf('Use Update Rose Agent')>0&&generic.indexOf('Use Update Rose Agent')<generic.indexOf('createSession'),'the generic delivery path never installs Rose Agent itself');
 const self=delivery.slice(delivery.indexOf('static void installSelf('),delivery.indexOf('static boolean sameSigners('));
 const at=s=>{const i=self.indexOf(s);assert.ok(i>0,s);return i;};
 assert.ok(at('SelfUpdate.verify(')<at('createSession'),'identity is verified before an installer session exists');
 assert.ok(at('getPackageArchiveInfo(')<at('SelfUpdate.verify('),'the archive Android parses from the downloaded bytes is checked');
 assert.ok(at('SelfUpdate.beforeCommit(version,a.selfVersion(),a.voiceBusy())')<at('session.commit('),'voice and version are rechecked right before commit');
 assert.ok(at('abandonSession')>0,'a refused or failed attempt abandons its session');
 assert.ok(!/canRequestPackageInstalls|ACTION_MANAGE_UNKNOWN_APP_SOURCES|setInstallerPackageName|grant/i.test(self),'the self path neither checks around nor changes install permission');
 const admit=voice.slice(voice.indexOf('SelfUpdate.Admission installSelf('),voice.indexOf('void recordSelfUpdate('));
 assert.ok(admit.indexOf('SelfUpdate.admit(')<admit.indexOf('installing.compareAndSet(false,true)'),'admission precedes taking the single install slot');
 assert.match(admit,/voiceBusy\(\)/);assert.match(admit,/canRequestPackageInstalls\(\)/);
 assert.match(receiver,/getLongExtra\(SELF_TARGET,-1\)/,'installer outcomes are recorded for the targeted version');
 assert.match(activity,/install\.setOnClickListener\(v->startSelfUpdate\(dialog,install,result\)\)/,'Install runs only from the owner tapping it');
 assert.ok(!/startSelfUpdate\(/.test(activity.slice(0,activity.indexOf('void checkForUpdate('))),'nothing starts an install on its own');
 assert.match(activity,/p\.getLong\("target",-1\)==offered\?SelfUpdate\.attemptText/,'only an attempt for the offered version is shown');
});

test('APP-GITHUB-001: self-update bytes come only from the manifest apkUrl over the anonymous public transport',()=>{
 const delivery=fs.readFileSync(new URL('../app/src/dev/pixel/voice/Delivery.java',import.meta.url),'utf8');
 const self=delivery.slice(delivery.indexOf('static void installSelf('),delivery.indexOf('static final class PublicHttp'));
 const at=s=>{const i=self.indexOf(s);assert.ok(i>0,s);return i;};
 assert.ok(at('m.optString("apkUrl","")')<at('PublicApkDownload.fetch(new PublicHttp(),apkUrl,'),'apkUrl comes from the pinned manifest');
 assert.match(self,/if\(apkUrl\.isEmpty\(\)\)throw new IOException\("No public download is published/,'a missing apkUrl is unavailable, with no fallback');
 assert.ok(!/"\/apk"/.test(self),'the self path no longer downloads from the pinned /apk route');
 assert.ok(at('PublicApkDownload.fetch(')<at('SelfUpdate.verify('),'the existing identity verification still runs on the downloaded bytes');
 assert.match(self,/download\(endpoint\+"\/apps\/"\+id\+"\/idsig",ssl,/,'the optional idsig stays on the pinned same-origin route');
 const http=delivery.slice(delivery.indexOf('static final class PublicHttp'),delivery.indexOf('static boolean sameSigners('));
 assert.match(http,/context\.init\(null,null,null\)/,'system trust and no client KeyManager');
 assert.match(http,/setHostnameVerifier\(HttpsURLConnection\.getDefaultHostnameVerifier\(\)\)/,'hostname verification is the platform default, not trust-all');
 assert.match(http,/setInstanceFollowRedirects\(false\)/);assert.match(http,/setUseCaches\(false\)/);
 assert.match(http,/CookieHandler\.getDefault\(\)!=null\)throw/,'refuses to run with a shared cookie store');
 assert.ok(!/\bssl\b|DeviceIdentity|systemTrustedSsl|identity|setDefault/.test(http),'no device identity, pinned factory or global change');
 const generic=delivery.slice(delivery.indexOf('public static void install('),delivery.indexOf('static void installSelf('));
 assert.match(generic,/download\(endpoint\+"\/apps\/"\+id\+"\/apk",ssl,/,'other apps keep the existing pinned delivery');
});
