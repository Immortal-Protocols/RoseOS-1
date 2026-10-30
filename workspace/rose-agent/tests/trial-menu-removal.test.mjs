// APP-REMOVE-001-P: the Home menu (MainActivity.homeOptions) no longer offers the DeepSeek Fast, Luna-first voice
// or ARTEMIS phone control trials, and nothing replaces them. Every other menu action stays, no code reads or
// rewrites the trials' mirror preferences any more, and their stored bytes are left alone (no clear/remove/reset).
// Source checks of the real Android files; no device or backend.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const dir=new URL('../app/src/dev/pixel/voice/',import.meta.url);
const source=name=>fs.readFileSync(new URL(name,dir),'utf8');
const main=source('MainActivity.java');
const options=main.slice(main.indexOf('void homeOptions(View anchor){'),main.indexOf('menu.show();}',main.indexOf('void homeOptions(View anchor){')));
const labels=[...options.matchAll(/menu\.getMenu\(\)\.add\("([^"]+)"\)/g)].map(m=>m[1]);

test('the three trial entries are gone from the Home menu, not renamed or moved',()=>{
 assert.ok(options.length>0,'found homeOptions');
 for(const gone of ['DeepSeek Fast (trial)','Luna-first voice (trial)','ARTEMIS phone control (trial)'])assert.ok(!labels.includes(gone),gone);
 assert.ok(!labels.some(l=>/deepseek|luna|artemis|advanced/i.test(l)),'no renamed entry or Advanced replacement: '+labels.join(', '));
 // #223: 'Voice controls' (first) is how the legacy voice page opens now that it is not a Home page.
 assert.deepEqual(labels,['Voice controls','Server connection','Conversation mode','Conversation notes','Pair with Mac','Location access','General Decisions routing',
  'Live Mac screen','Keep screen on','App control while locked','Classic home','Try conversation home','Visual cards','Reject changed screens',
  'Stable-field typing','Voice boost (+6 dB)','Double tap toggles mute','Set up connected glasses','Talk to a Guardian','Your apps','Result card style'],
  'every other entry is kept, in order');
});

test('no app code sends a retired trial command or reads or rewrites a trial mirror; stored bytes are untouched',()=>{
 const files=fs.readdirSync(dir).filter(f=>f.endsWith('.java')).map(f=>[f,source(f)]);
 files.push(['index.html',fs.readFileSync(new URL('../app/assets/index.html',import.meta.url),'utf8')]);
 for(const [name,text] of files){
  for(const command of ['toggle_deepseek_trial','toggle_luna_first','toggle_artemis_trial'])assert.ok(!text.includes(command),`${name} still names ${command}`);
  for(const mirror of ['"deepseek-trial"','"voice-routing"','"artemis-trial"'])assert.ok(!text.includes(mirror),`${name} still touches ${mirror}`);
 }
 // Nothing was added to wipe or reset preferences or stop work as part of the removal.
 assert.ok(!/\.clear\(\)|deleteSharedPreferences|stopCurrent|\/task\/stop/.test(options),'the menu performs no reset, wipe or Stop');
});

test('the actions the menu keeps still reach their existing handlers',()=>{
 const voice=source('VoiceService.java');
 assert.match(options,/command\("toggle_conversation_mode"\)/);assert.match(voice,/"toggle_conversation_mode"\.equals\(c\)\?"window\.toggleConversationMode\(\)"/);
 assert.match(options,/command\("toggle_decisions_routing"\)/);assert.match(voice,/"toggle_decisions_routing"\.equals\(c\)\?"window\.toggleDecisionsRouting\(\)"/);
 assert.match(options,/new Intent\(this,ServerConnectionActivity\.class\)/);assert.match(options,/new Intent\(this,PairingActivity\.class\)/);
 assert.match(options,/new Intent\(this,ConversationModeActivity\.class\)/);assert.match(options,/GlassesSetup\.begin\(this\)/);
 assert.match(options,/guardianPicker\(\)/);assert.match(options,/apps\(\)/);assert.match(options,/setResultCardMode\(/);
 assert.match(main,/homeOptions\(brand\)/,'the menu is still opened from the Home brand long-press');
 // Update Rose Agent lives in the conversation surface's menu and is unchanged.
 assert.match(source('RoseConversationActivity.java'),/add\("Update Rose Agent"\)\.setOnMenuItemClickListener\(i->\{checkForUpdate\(\);return true;\}\)/);
});
