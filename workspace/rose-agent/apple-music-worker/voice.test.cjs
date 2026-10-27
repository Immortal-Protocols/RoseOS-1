const test=require('node:test'),assert=require('node:assert/strict');
const create=require('../app/assets/apple-music-voice.js');
test('voice calls one native routine and returns its verified result',async()=>{
 const requests=[];let time=0;
 const run=create({now:()=>time,delay:async n=>time+=n,api:async(method,path,body)=>{requests.push({method,path,body});return method==='POST'?{id:'one',status:'pending'}:{id:'one',status:'completed',result:{status:'verified_playing',title:'Dreams'}};}});
 const r=JSON.parse(await run({title:'Dreams',artist:'Fleetwood Mac'}));assert.equal(r.status,'verified_playing');assert.equal(requests.filter(r=>r.method==='POST').length,1);assert.equal(requests[0].body.action,'apple_music_run');assert.equal(requests[0].body.pauseAfter,false);
});
test('uncertain outcome never retries playback or delegates',async()=>{
 let posts=0;const run=create({api:async()=>{posts++;throw Error('lost reply');}});
 assert.equal(JSON.parse(await run({title:'Dreams',artist:'Fleetwood Mac'})).status,'outcome_unknown');assert.equal(posts,1);
});
test('title-only playback submits exactly one native command with no guessed artist',async()=>{
 const requests=[];const run=create({api:async(method,path,body)=>{requests.push(body);return {status:'completed',result:{status:'verified_playing',artist:'The Beatles'}};}});
 const result=JSON.parse(await run({title:'Across the Universe'}));assert.equal(result.status,'verified_playing');assert.equal(requests.length,1);assert.equal(requests[0].title,'Across the Universe');assert.equal(requests[0].artist,'');
});
test('invalid title or artist does not send a phone command',async()=>{
 const run=create({api:async()=>assert.fail('No action should be sent')});for(const p of [{title:''},{title:'Dreams',artist:42}])assert.equal(JSON.parse(await run(p)).status,'needs_clarification');
});
