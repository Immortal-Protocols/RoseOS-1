const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const elements = new Map();
const frames = [], speaking = [], intervals = [];
function element() { return {textContent:'',value:'',disabled:false,children:[],classList:{add(){},remove(){}},append(){},replaceChildren(){}}; }
let input=0,output=0,failInput=false,failOutput=false;
const conv={
  inputLevel(){if(failInput)throw Error('track detached');return input;},
  outputLevel(){if(failOutput)throw Error('track detached');return output;},
  setMuted(){},close:async()=>{},
};
const context={
  window:{},document:{getElementById:id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);},createElement:element},
  Native:{endpoint:()=>'',request(){},trayAudio:(a,b)=>frames.push([a,b]),assistantSpeaking:v=>speaking.push(v),audio(){},audioRoute:()=>'',sessionCue(){}},
  navigator:{},localStorage:{getItem:()=>null},performance:{now:()=>1},crypto:{randomUUID:()=> 'test'},
  console,Promise,Map,Set,setInterval:(fn,ms)=>{intervals.push({fn,ms});return intervals.length;},clearInterval(){},setTimeout:()=>1,clearTimeout(){},
  bookDisconnected(){},conv,
};
context.window=context;
vm.createContext(context);
const html=fs.readFileSync(path.join(__dirname,'../app/assets/index.html'),'utf8');
vm.runInContext(html.split('<script>')[1].split('</script>')[0],context);
vm.runInContext('conversation=conv;ready=true;muted=false',context);
function sample(a,b){input=a;output=b;context.publishAudioLevels();return frames.at(-1);}
assert.deepEqual(sample(.18,.29),[.18,.29],'native bridge gets raw SDK normalized levels, not UI gain');
assert.deepEqual(sample(0,0),[0,0],'connected silence remains zero');
assert.equal(speaking.at(-1),false);
assert.deepEqual(sample(.8,0),[.8,0],'microphone and assistant remain independent');
assert.deepEqual(sample(0,.7),[0,.7]);
assert.equal(speaking.at(-1),true);
context.toggleMute();
assert.deepEqual(sample(.6,.3),[0,.3],'mute flattens only user waveform');
context.toggleMute();
assert.deepEqual(sample(.4,.2),[.4,.2],'unmute follows current SDK readings');
assert.deepEqual(sample(NaN,Infinity),[0,0],'invalid readings cannot poison rendering');
assert.deepEqual(sample(-1,2),[0,1],'normalized bounds enforced');
failInput=true;assert.deepEqual(sample(.4,.2),[0,.2],'detached input does not stop output updates');
failInput=false;failOutput=true;assert.deepEqual(sample(.4,.2),[.4,0]);failOutput=false;
context.cleanup();
assert.deepEqual(frames.at(-1),[0,0],'disconnect clears native levels immediately');
assert.deepEqual(sample(.9,.9),[0,0],'stale reads cannot animate disconnected session');
vm.runInContext('conversation=conv;ready=false',context);
assert.deepEqual(sample(.9,.9),[0,0],'connecting is not audio activity');
assert.match(html,/publishAudioLevels\(\);meterTimer=setInterval\([\s\S]*?\},80\)/,'active meter sends at most 12.5 updates per second');
console.log('Status audio bridge passed: measured independent levels; true silence; mute/unmute; invalid/detached tracks; disconnect and connecting resets; bounded 80 ms metering.');
