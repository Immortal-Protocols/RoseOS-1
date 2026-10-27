import test from 'node:test';
import assert from 'node:assert/strict';
import {appWorkers,appWorkerPolicy} from './app-worker-policy.mjs';
import {PhoneCommands} from '../server/phone.mjs';

test('backend catalog points to the same accepted native music command',()=>{
 const worker=appWorkers.find(w=>w.app==='Apple Music');
 const command=new PhoneCommands().create({action:worker.action,title:'Across the Universe'});
 assert.equal(command.action,'apple_music_run');
 assert.match(appWorkerPolicy('/private/tools/control.py'),/python3 "\/private\/tools\/control.py" apple_music_run/);
});
test('policy preserves intent resolution, action authorization and uncertain-outcome rules',()=>{
 const policy=appWorkerPolicy('/tools/control.py');
 for(const required of ['follow-ups','corrections','current request authorizes','factual question','Artist is optional','resolve the intended title','Never call it and then replay','Albums, playlists'])assert.ok(policy.includes(required),required);
});

test('timer requests use model resolution and the one-shot native worker',()=>{
 const policy=appWorkerPolicy('/tools/control.py');
 const timer=appWorkers.find(w=>w.action==='clock_timer_start');assert.ok(timer);
 assert.match(policy,/clock_timer_start SECONDS/);
 assert.match(policy,/integer seconds between 1 and 86400/);
 assert.match(policy,/uncertain result must not be retried/);
 const command=new PhoneCommands().create({action:timer.action,seconds:150,label:'Tea'});
 assert.equal(command.seconds,150);
});
