// Rose Agent's settings (owner request, 2026-11-06): "There's really only one we care about, pairing with the mac ...
// remove the code for all of the settings except for that one and maybe some 'about' information."
// Settings is now Pair with Mac (the established QR pairing flow) and About (app name, version and build). The other
// entries, their screens and their settings-only plumbing are gone; earlier, APP-REMOVE-001-P removed the DeepSeek
// Fast, Luna-first and ARTEMIS trials, and they stay gone. App control while locked is always on, with no option
// (owner decision, 2026-11-07), and stays behind Android's own owner authentication. Source checks; no device.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const dir=new URL('../app/src/dev/pixel/voice/',import.meta.url);
const source=name=>fs.readFileSync(new URL(name,dir),'utf8');
const exists=name=>fs.existsSync(new URL(name,dir));
const main=source('MainActivity.java'),dialog=source('SettingsDialog.java'),screen=source('SettingsScreen.java'),model=source('RoseSettingsModel.java');
const manifest=fs.readFileSync(new URL('../app/AndroidManifest.xml',import.meta.url),'utf8');
const code=text=>text.replace(/\/\*[\s\S]*?\*\//g,'').replace(/\/\/.*$/gm,'');

test('settings is Pair with Mac and About only, in one window-owned dialog',()=>{
 assert.match(main,/void homeOptions\(View anchor\)\{SettingsDialog\.show\(this\);\}/,'the old 21-entry menu is gone');
 assert.doesNotMatch(main,/settingsOption/);
 assert.match(model,/TITLE = "Rose Agent settings", PAIR = "Pair with Mac", ABOUT = "About", APP = "Rose Agent"/);
 // Exactly one action in the body (render-tested in SettingsDialogRenderTest): the Pair with Mac row.
 assert.equal((code(screen).match(/setOnClickListener/g)||[]).length,1);
 assert.match(screen,/setNegativeButton\("Close", null\)/);
 // Both entry points still open it: a long press on Home, and the conversation's Settings (ACTION_SETTINGS).
 assert.match(main,/roseHome\.setOnLongClickListener\(v->\{homeOptions\(v\);return true;\}\);/);
 assert.match(source('RoseConversationActivity.java'),/add\("Settings"\)\.setOnMenuItemClickListener\(i->\{startActivity\(new Intent\(MainActivity\.ACTION_SETTINGS\)\.setClass\(this,MainActivity\.class\)\);return true;\}\)/);
 for(const gone of ['Voice controls','Server connection','Conversation mode','Conversation notes','Location access',
  'General Decisions routing','Live Mac screen','Keep screen on','App control while locked','Classic home','Try conversation home',
  'Visual cards','Reject changed screens','Stable-field typing','Voice boost (+6 dB)','Double tap toggles mute',
  'Set up connected glasses','Talk to a Guardian','Result card style','DeepSeek Fast (trial)','Luna-first voice (trial)','ARTEMIS phone control (trial)'])
  assert.ok(!main.includes(`"${gone}"`)&&!screen.includes(`"${gone}"`)&&!dialog.includes(`"${gone}"`),gone);
});

test('Pair with Mac opens the established QR pairing flow, with the state read safely',()=>{
 // One flow for every install: scan to pair, scan a new code to pair again or change Macs, unpair, or cancel there.
 assert.match(dialog,/activity\.startActivity\(new Intent\(activity, LocalRosePairActivity\.class\)\)/);
 assert.doesNotMatch(code(dialog),/PairingActivity\.class|DeviceIdentity/,'no second client flow');
 // The state line never probes the network: status() only for a paired phone (its unpaired branch checks USB reachability).
 const state=dialog.slice(dialog.indexOf('static String pairingState('),dialog.indexOf('static String version('));
 assert.ok(state.indexOf('if (p == null) return')<state.indexOf('LocalRoseConnection.status(c)'),'unpaired returns before status()');
 assert.doesNotMatch(code(state),/reachable\(|Thread|https?:/);
 // Display only: the Mac's name and the state, never an address, code, key or pin.
 assert.doesNotMatch(code(dialog+model),/\.hosts|\.pin\b|serverId|deviceId|lastHost/);
});

test('retired settings: their screens and plumbing are removed; stored values are left alone',()=>{
 for(const file of ['ServerConnectionActivity.java','ConversationModeActivity.java'])assert.ok(!exists(file),file);
 assert.doesNotMatch(manifest,/ServerConnectionActivity|ConversationModeActivity/);
 assert.doesNotMatch(main,/guardianPicker|toggleLockedControl|keepScreenOnEnabled|macScreenEnabled/);
 // Retired switches whose reader is Home's are no longer read: Rose Home always; the Live Mac screen card stays off.
 for(const key of ['"conversation-home"','"mac-screen-enabled"','"text-input-guard"','"glasses-controls"','"voice-output-boost"','"screen-change-guard"'])
  assert.ok(!main.includes(`getPreferences(0).getBoolean(${key}`)&&!main.includes(`getSharedPreferences(${key}`),key);
 assert.match(main,/experimentalHome=true;/);
 // Keep screen on stays the owner's through the Quick Settings tile.
 assert.match(source('ScreenTile.java'),/ScreenPolicy\.setKeepScreenOn\(/);
 // No migration: opening Rose Agent or its settings changes no stored setting.
 assert.doesNotMatch(code(dialog+screen),/getSharedPreferences|getPreferences|\.edit\(\)/);
 assert.doesNotMatch(main,/retireSettings/);
 // Retired trial commands and mirrors stay gone everywhere.
 const files=fs.readdirSync(dir).filter(f=>f.endsWith('.java')).map(f=>[f,source(f)]);
 files.push(['index.html',fs.readFileSync(new URL('../app/assets/index.html',import.meta.url),'utf8')]);
 for(const [name,text] of files){
  for(const command of ['toggle_deepseek_trial','toggle_luna_first','toggle_artemis_trial'])assert.ok(!text.includes(command),`${name} still names ${command}`);
  for(const mirror of ['"deepseek-trial"','"voice-routing"','"artemis-trial"'])assert.ok(!text.includes(mirror),`${name} still touches ${mirror}`);
 }
});

test('normal operation is kept: updates, apps, location ask and the voice backend',()=>{
 assert.match(source('RoseConversationActivity.java'),/add\("Update Rose Agent"\)\.setOnMenuItemClickListener\(i->\{checkForUpdate\(\);return true;\}\)/);
 assert.match(main,/void apps\(\)\{/,'Your apps stays reachable from Home');
 assert.match(main,/requestLocationContext\(\);\}/,'the first-launch location ask stays');
 const voice=source('VoiceService.java');
 for(const kept of ['"toggle_conversation_mode"','"toggle_decisions_routing"','setServerEndpoint('])assert.ok(voice.includes(kept),kept);
});

test('app control while locked is always on, with no option, and still needs the owner\'s recent unlock',()=>{
 const locked=code(source('LockedControl.java')),phone=source('PhoneControl.java');
 // No switch, no arming prompt and no stored on/off flag; nothing in the app turns it off.
 assert.doesNotMatch(locked,/getSharedPreferences|ENABLED|BiometricPrompt|static void (enable|disable)\(/);
 for(const text of [main,dialog,screen,model])assert.ok(!/LockedControl\.|App control while locked/.test(code(text)));
 // Away from the phone, start and command requests always meet the same check, now with no opt-in step first.
 assert.match(phone,/LockedControl\.gates\(command\.getString\("operation"\)\)&&LockedControl\.ownerAway\(context\)\)\{JSONObject refusal=LockedControl\.refusal\(context\);if\(refusal!=null\)return refusal;\}/);
 const refusal=locked.slice(locked.indexOf('static JSONObject refusal('),locked.indexOf('static PrivateKey key('));
 assert.match(refusal,/SDK_INT<30\)return locked\("unsupported"/);
 assert.match(refusal,/isDeviceSecure\(\)\)return locked\("no_secure_lock"/);
 assert.match(refusal,/catch\(UserNotAuthenticatedException expired\)\{return locked\("authentication_expired"/);
 // The key is made on first use or after Android invalidates it, and is bound to a PIN, pattern, password or strong
 // biometric within the last 4 hours (the OS patch's own max age); a new key signs nothing before that.
 assert.match(locked,/AUTH_WINDOW_SECONDS=4\*60\*60;/);
 assert.match(locked,/\.setUserAuthenticationRequired\(true\)\.setUserAuthenticationParameters\(AUTH_WINDOW_SECONDS,KeyProperties\.AUTH_DEVICE_CREDENTIAL\|KeyProperties\.AUTH_BIOMETRIC_STRONG\)/);
 assert.match(locked,/\.setInvalidatedByBiometricEnrollment\(true\)/);
 assert.match(refusal,/catch\(KeyPermanentlyInvalidatedException invalidated\)\{[^}]*store\.deleteEntry\(KEY\);sign\(key\(store\)\);/);
 assert.match(fs.readFileSync(new URL('../../../os/patches/locked-control-frameworks-base.patch',import.meta.url),'utf8'),/config_pixelComputerControlLockedAuthMaxAgeMs">14400000</);
 // Never around the lock screen: no keyguard dismissal, show-when-locked window or credential handling of its own.
 assert.doesNotMatch(locked,/requestDismissKeyguard|DISMISS_KEYGUARD|setShowWhenLocked|SHOW_WHEN_LOCKED|setTurnScreenOn|createConfirmDeviceCredentialIntent/);
});
