const test=require('node:test'),assert=require('node:assert/strict');
const vm=require('node:vm'),fs=require('node:fs');
// The page loads this file as a classic script (workspace/package.json is type:module, so require() would
// treat it as an ES module where `module` is undefined). Load it the same way the page does.
function pageScript(name){const window={};vm.runInThisContext('(function(window){'+require('node:fs').readFileSync(__dirname+'/'+name,'utf8')+'\n})',{filename:name})(window);return window;}
const {create,limiterCurve}=pageScript('output-boost.js').VoiceOutputBoost;
function setup(broken=false){const nodes=[],audio={muted:false},states=[];function node(){const n={connections:0,disconnected:false,connect(){this.connections++},disconnect(){this.disconnected=true;this.connections=0}};nodes.push(n);return n;}const ctx={state:'running',destination:{},createMediaStreamSource(){if(broken)throw Error('Unavailable');return node();},createGain(){return Object.assign(node(),{gain:{value:0}})},createDynamicsCompressor(){return Object.assign(node(),Object.fromEntries(['threshold','knee','ratio','attack','release'].map(x=>[x,{value:0}])))},createWaveShaper:node};return {nodes,audio,states,boost:create(ctx,{},audio,s=>states.push(s))};}
test('boost switches off without layered playback or multiplying the graph on repeat',()=>{const f=setup();f.boost.setDb(6);assert.equal(f.audio.muted,true);assert.equal(f.nodes.length,4);f.boost.setDb(6);assert.equal(f.nodes.length,4);assert.equal(f.nodes[3].connections,1);f.boost.setDb(0);assert.equal(f.audio.muted,false);assert.ok(f.nodes.every(n=>n.disconnected));f.boost.setDb(6);f.boost.close();assert.equal(f.audio.muted,false);assert.ok(f.nodes.every(n=>n.disconnected));});
test('unavailable processing falls back to original playback',()=>{const f=setup(true);f.boost.setDb(6);assert.equal(f.audio.muted,false);assert.equal(f.states.at(-1).status,'unavailable');assert.equal(f.boost.state().active,false);});
test('limiter is monotonic, symmetric and bounded with a linear quiet range',()=>{const c=limiterCurve();for(let i=0;i<c.length;i++){assert.ok(Math.abs(c[i])<.981);assert.ok(Math.abs(c[i]+c[c.length-i-1])<1e-6);if(i)assert.ok(c[i]>=c[i-1]);const x=i*2/(c.length-1)-1;if(Math.abs(x)<.79)assert.ok(Math.abs(c[i]-x)<1e-6);}});
test('reading mute never enables raw audio alongside the boosted signal',()=>{
 const f=setup();f.boost.setDb(6);f.boost.setMuted(false);assert.equal(f.audio.muted,true);assert.ok(f.nodes[1].gain.value>1);
 f.boost.setMuted(true);assert.equal(f.audio.muted,true);assert.equal(f.nodes[1].gain.value,0);
 f.boost.setMuted(false);assert.equal(f.audio.muted,true);assert.ok(f.nodes[1].gain.value>1);
 f.boost.setMuted(true);f.boost.setDb(0);assert.equal(f.audio.muted,true,'switching gain off cannot bypass a reading pause');
 f.boost.setMuted(false);assert.equal(f.audio.muted,false);f.boost.close();
});
