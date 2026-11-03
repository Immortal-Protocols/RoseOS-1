// Source checks of Rose Agent's exported components (#410 security review, comment 6593742638); no device needed.
import fs from 'node:fs';
import assert from 'node:assert/strict';
import test from 'node:test';
const read=path=>fs.readFileSync(new URL(path,import.meta.url),'utf8');
const manifest=read('../app/AndroidManifest.xml');
const components=[...manifest.matchAll(/<(activity|activity-alias|service|receiver|provider)\b([^>]*)>/g)].map(([,kind,attrs])=>({
 kind,attrs,name:/android:name="([^"]+)"/.exec(attrs)[1],
 exported:/android:exported="true"/.test(attrs),permission:/android:permission="/.test(attrs)}));
const component=name=>{const c=components.find(c=>c.name===name);assert.ok(c,`${name} is declared`);return c;};

test('the temporary Bluetooth diagnostic cannot be started by another app', () => {
 // Started, it ends Rose's live call or stops VoiceService, pauses glasses reconnect for 5 minutes and logs nearby BLE
 // devices' addresses and advertisements. Nothing in Rose Agent starts it.
 const probe=component('.BluetoothProbeActivity');
 assert.match(probe.attrs,/android:exported="false"/);
 assert.ok(!/<intent-filter/.test(manifest.slice(manifest.indexOf('.BluetoothProbeActivity'),manifest.indexOf('/>',manifest.indexOf('.BluetoothProbeActivity')))),
  'no intent filter could re-export it');
 for(const file of fs.readdirSync(new URL('../app/src/dev/pixel/voice/',import.meta.url)))
  if(file!=='BluetoothProbeActivity.java')
   assert.ok(!read(`../app/src/dev/pixel/voice/${file}`).includes('BluetoothProbeActivity'),`${file} never starts the diagnostic`);
});

test('the separate-task app picker cannot be started by another app', () => {
 // It is MainActivity in its own Recents task (own affinity, resizeable, adjacent launch). Nothing in Rose Agent starts it;
 // its only caller was the unshipped agent-split controller (releases build --viewer pip).
 const picker=component('.WorkspaceAppsActivity');
 assert.match(picker.attrs,/android:exported="false"/);
 assert.match(picker.attrs,/android:taskAffinity="dev\.pixel\.voice\.workspaceapps"/,'otherwise unchanged');
 assert.ok(!/startActivity\([^;]*WorkspaceAppsActivity/.test(read('../app/src/dev/pixel/voice/WorkspaceLauncher.java')));
});

test('no other exported component without a permission appears unreviewed', () => {
 // The components another app can reach with no permission, as of this review. Each guards itself in code or is a
 // public entry (launcher/HOME, pairing); adding one, or dropping a permission, must update this list on purpose.
 const open=components.filter(c=>c.exported&&!c.permission).map(c=>`${c.kind} ${c.name}`).sort();
 assert.deepEqual(open,[
  'activity .LocalRosePairActivity',
  'activity .MainActivity',
  'provider .GlassesPhotoProvider',
  'provider .SettingsAgentProvider',
  'receiver .OwnerStopReceiver',
 ]);
});
