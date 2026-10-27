const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
let now=100000;const messages=[];
const c={console,JSON,Map,Number,Promise,Date:class extends Date{static now(){return now;}},ready:true,starting:false,muted:false,conversation:{sendContext:t=>messages.push(t)},setTimeout,clearTimeout,Native:{podcastCommand(){}}};c.window=c;
vm.createContext(c);vm.runInContext(fs.readFileSync('rose-agent/app/assets/podcast-companion.js','utf8'),c);
const base={app:'Pixel Podcasts',id:'42',active:true,playing:false,foreground:false,title:'Example episode',transcriptStatus:'available',segments:[{startMs:0,endMs:30000,text:'An old podcast passage.'}],positionMs:12000};
const receive=extra=>c.receivePodcastContext({...base,...extra,observedAt:now});
for(let i=0;i<31;i++){receive();now+=1500;}
assert.equal(messages.length,1,'unchanged paused background state sends one status');
assert.doesNotMatch(messages[0],/old podcast passage/,'background status excludes the transcript');
receive({foreground:true});assert.match(messages.at(-1),/old podcast passage/,'returning to the podcast restores its passage');
const count=messages.length;for(let i=0;i<31;i++){now+=1500;receive({foreground:true});}
assert.equal(messages.length,count,'paused foreground passage does not repeat');
receive({foreground:true,playing:true});const resumed=messages.length;
now+=16000;receive({foreground:true,playing:true,positionMs:28000});assert.equal(messages.length,resumed+1,'moving playback position updates context');
receive({foreground:true,playing:true,positionMs:28000,segments:[{startMs:0,endMs:30000,text:'Corrected passage.'}]});assert.match(messages.at(-1),/Corrected passage/,'changed text updates even when segment count is unchanged');
receive();c.podcastDisconnected();c.podcastConnected();assert.doesNotMatch(messages.at(-1),/old podcast passage/,'reconnect does not resurrect paused background text');
console.log('PASS: paused/background deduplication, foreground return, playing position, corrected transcript, reconnect.');
