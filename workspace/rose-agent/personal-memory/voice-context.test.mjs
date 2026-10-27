import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

test('voice receives memory in initial history, updates once, clears when disabled, and keeps recent conversation',async()=>{
 const initial={enabled:true,revision:'one',records:[{id:'a',text:'Prefers blue.'}],text:'Initial memory'};
 const updates=[],requests=[];let options;let response={revision:1,messages:[],personalMemory:initial};
 const elements=new Map(),element=()=>({textContent:'',value:'',disabled:false,children:[],classList:{add(){},remove(){}},append(){},replaceChildren(){}});
 const conversation={sendContextualUpdate:t=>updates.push(t),getInputVolume:()=>0,getOutputVolume:()=>0,endSession:async()=>{},setMicMuted:()=>{}};
 const context={console,JSON,Date,Promise,Map,Set,encodeURIComponent,performance:{now:()=>1},crypto:{randomUUID:()=> 'test-request'},navigator:{},localStorage:{getItem:()=>null},setTimeout:()=>1,clearTimeout(){},setInterval:()=>1,clearInterval(){},document:{getElementById:id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);},createElement:element}};
 context.window=context;
 context.Native={startPhonePush(){},voiceBoostEnabled:()=>false,voiceBoostState(){},audio(){},audioPrepared:()=>true,timing(){},endpoint:()=>'',state(){},readyChime(){},lastButton:()=>'',audioRoute:()=>'',assistantSpeaking(){},request(id,method,route,body){requests.push({method,route,body:JSON.parse(body)});const value=route==='/voice/config'?{provider:'elevenlabs'}:route==='/eleven/session'?{token:'test',sessionId:'s',history:'{"recent_conversation":[{"role":"user","text":"Garden is Marigold"}],"personal_memory_context":"Initial memory"}',personalMemory:initial}:route.startsWith('/conversation')?response:route==='/apps'?{apps:[]}:route==='/phone/commands'?{commands:[]}:route==='/health'?{ready:true}:{events:[],epoch:'test'};queueMicrotask(()=>context.nativeResponse(id,200,JSON.stringify(value)));}};
 context.ElevenConversation={startSession:async o=>{options=o;o.onConversationCreated(conversation);o.onConnect();return conversation;}};
 vm.createContext(context);
 for(const name of ['media-tool.js','messages-tool.js','clock-tool.js','phone-command-delivery.js','task-status-transport.js'])vm.runInContext(fs.readFileSync(new URL('../app/assets/'+name,import.meta.url),'utf8'),context);
 vm.runInContext(fs.readFileSync(new URL('../voice-providers/voice-providers.js',import.meta.url),'utf8'),context);vm.runInContext(fs.readFileSync(new URL('../app/assets/index.html',import.meta.url),'utf8').split('<script>')[1].split('</script>')[0],context);
 context.bookControl=()=>{};context.bookConnected=()=>{};context.bookDisconnected=()=>{};
 await context.start();assert.ok(options, elements.get('status')?.textContent);assert.match(options.dynamicVariables.recent_history,/Garden is Marigold/);assert.equal(updates.length,0);
 response={...response,personalMemory:{...initial,revision:'two',text:'Updated memory'}};
 await context.pollConversation();await context.pollConversation();assert.deepEqual(updates,['Updated memory']);
 response={...response,personalMemory:{enabled:false,revision:'off',records:[],text:'Ignore previous trial memory'}};
 await context.pollConversation();assert.deepEqual(updates,['Updated memory','Ignore previous trial memory']);
 assert.ok(requests.some(r=>r.route==='/memory/delivered'&&r.body.revision==='two'));
});
