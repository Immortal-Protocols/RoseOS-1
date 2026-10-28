const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm');
// The page loads this as a classic script; workspace/package.json is type:module, so require() would load it as an ES
// module where `module` is undefined and nothing is exported. Load it the way the page does.
function pageScript(name){const window={};vm.runInThisContext('(function(window){'+require('node:fs').readFileSync(__dirname+'/'+name,'utf8')+'\n})',{filename:name})(window);return window;}
const {selection,back}=pageScript('client.js').GuardianVoice;
test('explicit spoken transitions only',()=>{assert.equal(selection("Let me talk to Holland's Guardian."),'Holland');assert.equal(selection('Talk to Alice Smith’s Guardian'),'Alice Smith');assert.equal(selection("What did Holland's Guardian say?"),null);assert.equal(back('Back to Pixel'),true);assert.equal(back('Tell me about going back to Pixel'),false);});
test('Android asset loader permits the Guardian control script',()=>{
 const fs=require('node:fs');const base=__dirname+'/../../rose-agent/';
 assert.match(fs.readFileSync(base+'app/src/dev/pixel/voice/VoiceService.java','utf8'),/p\.equals\("\/guardian-voice\.js"\)/);
 assert.match(fs.readFileSync(base+'app/assets/index.html','utf8'),/<script src="guardian-voice.js"><\/script>/);
});
