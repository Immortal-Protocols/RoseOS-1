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
 assert.match(activity,/install\.setOnClickListener\(v->startSelfUpdate\(dialog,install,r\.offer\)\)/,'Install runs only from the owner tapping it');
 assert.ok(!/startSelfUpdate\(/.test(activity.slice(0,activity.indexOf('void checkForUpdate('))),'nothing starts an install on its own');
 // PR217: attempts are recorded and read through UpdateAttempts (timestamped, recovery-aware); still only the offered version's.
 assert.match(activity,/String selfUpdateText\(long offered,long installed\)\{\n  return UpdateAttempts\.text\(this,offered,installed\);/);
 assert.match(src('UpdateAttempts.java'),/if \(p\.getLong\("target", -1\) != offered\) return "";/,'only an attempt for the offered version is shown');
 assert.match(receiver,/UpdateAttempts\.record\(c,target,/,'installer outcomes go through the same timestamped record');
});

test('APP-GITHUB-002: self-update discovery and download use public GitHub only, with no PixelVoice dependency',()=>{
 const src=n=>fs.readFileSync(new URL('../app/src/dev/pixel/voice/'+n,import.meta.url),'utf8');
 const delivery=src('Delivery.java'),activity=src('RoseConversationActivity.java'),voice=src('VoiceService.java');
 const self=delivery.slice(delivery.indexOf('static void installSelf('),delivery.indexOf('static DirectUpdate.Device localDevice('));
 const at=s=>{const i=self.indexOf(s);assert.ok(i>0,s);return i;};
 assert.match(self,/static void installSelf\(VoiceService a,DirectUpdate\.Offer offer\)/,'the installer takes the validated GitHub offer');
 assert.ok(!/endpoint|\bssl\b|manifest\(|\/apps\//.test(self),'no PixelVoice endpoint, pinned connection, catalog manifest or catalog sidecar route');
 // APP-IDSIG-1030-M004 (#202): the v4 signature comes only from the offer's canonical GitHub sidecar, bound by its own
 // hash and size; without one nothing is downloaded; both files go into the SAME session before commit; both owned
 // temp files are deleted on every path, and recovery deletes both.
 assert.ok(at('if(!offer.hasIdsig())throw new IOException(')<at('PublicApkDownload.fetch('),'an offer without a sidecar is refused before any download');
 assert.ok(at('PublicApkDownload.fetch(http,offer.apkUrl,')<at('PublicApkDownload.fetchSignature(http,offer.idsigUrl,out,offer.idsigSha256,offer.idsigSize,DirectUpdate.MAX_IDSIG)'),'the sidecar is fetched bound to the offer');
 assert.ok(at('PublicApkDownload.fetchSignature(')<at('createSession'),'both files are verified before a session exists');
 assert.match(self,/params\.setSize\(size\+signatureSize\);/,'the session accounts for both files');
 assert.match(self,/try\(PackageInstaller\.Session session=pi\.openSession\(sessionId\)\)\{stage\(session,file,"base\.apk"\);stage\(session,signature,"base\.apk\.idsig"\);\}/,'exactly base.apk and base.apk.idsig, in one session');
 assert.ok(at('stage(session,signature,"base.apk.idsig")')<at('session.commit('),'staged before commit');
 assert.match(self,/finally\{file\.delete\(\);signature\.delete\(\);new File\(file\.getPath\(\)\+"\.idsig"\)\.delete\(\);\}/,'both owned temp files, and any old .idsig sibling, are deleted on every path');
 assert.match(self,/new File\(a\.getCacheDir\(\),UpdateRecovery\.DOWNLOAD\),signature=new File\(a\.getCacheDir\(\),UpdateRecovery\.DOWNLOAD_SIGNATURE\)/,'the temp files are the names recovery owns');
 const attempts=src('UpdateAttempts.java');
 assert.match(attempts,/new File\(c\.getCacheDir\(\), UpdateRecovery\.DOWNLOAD\)\.delete\(\);\s*new File\(c\.getCacheDir\(\), UpdateRecovery\.DOWNLOAD_SIGNATURE\)\.delete\(\);\s*new File\(c\.getCacheDir\(\), UpdateRecovery\.LEGACY_SIGNATURE\)\.delete\(\);/,'recovery deletes every owned temp file, including the old sibling name');
 // #407: the platform's v4 verifier (ApkSignatureSchemeV4Verifier.extractSignature at the pinned frameworks/base) reads
 // <apk>.idsig beside the APK being parsed and then requires fs-verity on that APK, which a cache file never has, so
 // getPackageArchiveInfo fails (null) for a valid update. The downloaded signature is never that sibling, and any old
 // sibling is removed before the archive is parsed; the session still stages exactly base.apk and base.apk.idsig.
 const names=src('UpdateRecovery.java');
 assert.match(names,/DOWNLOAD = "self-update\.apk", DOWNLOAD_SIGNATURE = "self-update\.v4sig", LEGACY_SIGNATURE = "self-update\.apk\.idsig"/);
 assert.ok(!names.includes('DOWNLOAD_SIGNATURE = "self-update.apk.idsig"'),'the signature temp file is never the APK\'s .idsig sibling');
 assert.ok(at('File sibling=new File(file.getPath()+".idsig");if(sibling.exists()&&!sibling.delete())throw new IOException(')<at('getPackageArchiveInfo('),
  'no .idsig sibling exists when Android parses the downloaded archive; one that cannot be removed stops the update');
 assert.ok(at('PublicApkDownload.fetch(http,offer.apkUrl,out,offer.sha256,offer.size,')<at('SelfUpdate.verify('),'bytes from the offer, bound to its hash and size, then verified');
 assert.match(self,/offer\.signerSha256\.equals\(signerSha256\(archive\)\)/,'the archive signer must equal the receipt signer');
 assert.ok(at('DirectUpdate.incompatibility(offer,localDevice(a))')<at('session.commit('),'compatibility is rechecked right before commit');
 assert.ok(at('SelfUpdate.beforeCommit(version,a.selfVersion(),a.voiceBusy())')<at('session.commit('),'voice and version are rechecked right before commit');
 const check=activity.slice(activity.indexOf('void runUpdateCheck('),activity.indexOf('void startSelfUpdate('));
 assert.match(check,/DirectUpdate\.check\(new Delivery\.PublicHttp\(\),device,channel\)/,'the check reads GitHub directly, on the owner\'s channel');
 assert.match(check,/UpdateAttempts\.recover\(this,device\.installed\);/,'an interrupted attempt is recovered before checking');
 assert.ok(!/VoiceService|endpoint|\.ssl|\/apps/.test(check),'the check does not need the voice service, PixelVoice or its catalog');
 const http=delivery.slice(delivery.indexOf('static final class PublicHttp'),delivery.indexOf('static boolean sameSigners('));
 assert.match(http,/context\.init\(null,null,null\)/);assert.match(http,/setInstanceFollowRedirects\(false\)/);
 assert.ok(!/\bssl\b|DeviceIdentity|systemTrustedSsl|setDefault/.test(http),'anonymous transport with no device identity or global change');
 assert.match(voice,/SelfUpdate\.Admission installSelf\(DirectUpdate\.Offer offer\)/,'admission stays in the local service');
 const generic=delivery.slice(delivery.indexOf('public static void install('),delivery.indexOf('static void installSelf('));
 assert.match(generic,/download\(endpoint\+"\/apps\/"\+id\+"\/apk",ssl,/,'other apps keep the existing pinned delivery');
 assert.match(generic,/Use Update Rose Agent/,'and still refuse Rose Agent itself');
});
