const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const flush=()=>new Promise(r=>setImmediate(r));
function setup(){
 const sent=[],tracks=[{enabled:true,stopped:false,stop(){this.stopped=true;}}];let pc;
 const stream={getAudioTracks:()=>tracks,getTracks:()=>tracks};
 class Peer{constructor(){pc=this;this.iceGatheringState='complete';this.connectionState='new';}addTrack(){}createDataChannel(){this.channel={readyState:'open',send:x=>sent.push(JSON.parse(x)),close(){this.readyState='closed';}};return this.channel;}async createOffer(){return {type:'offer',sdp:'test-offer'};}async setLocalDescription(x){this.localDescription=x;}async setRemoteDescription(x){this.remoteDescription=x;}close(){this.closed=true;}}
 class Audio{play(){return Promise.resolve();}pause(){}}
 const context={console,setTimeout,clearTimeout,setInterval,clearInterval,Float32Array,Map,Set,Promise,Date,JSON,RTCPeerConnection:Peer,Audio,MediaStream:class{},navigator:{mediaDevices:{getUserMedia:async()=>stream}}};context.window=context;
 vm.createContext(context);for(const name of ['voice-commands.js','luna-first.js','voice-providers.js'])vm.runInContext(fs.readFileSync(__dirname+'/'+name,'utf8'),context);
 return {context,sent,tracks,stream,get pc(){return pc;},emit:e=>pc.channel.onmessage({data:JSON.stringify(e)})};
}
test('voice captures app on each utterance and forwards current app with each handoff',async()=>{
 const f=setup(),requests=[],captures=[];let current='YouTube';
 const c=await f.context.VoiceProviders.start({provider:'openai',api:async()=>({session:{id:'s'},transport:{sdp:'answer'}}),captureAppContext:()=>{captures.push(current);return {app:current};},sampleAppContext:()=>({app:current}),tools:{delegate_task:async p=>{requests.push(p);return '{}';}}});
 f.emit({type:'session.input_transcript.delta',start_ms:0,end_ms:1000,delta:'Double the speed'});
 f.emit({type:'session.input_transcript.delta',start_ms:1000,end_ms:1200,delta:' please'});
 f.emit({type:'session.delegation.created',delegation:{id:'first',target:'client'}});await flush();
 current='Apple Music';
 f.emit({type:'session.input_transcript.delta',start_ms:4000,end_ms:5000,delta:'What app am I in?'});
 f.emit({type:'session.delegation.created',delegation:{id:'second',target:'client'}});await flush();
 assert.deepEqual(captures,['YouTube','Apple Music']);assert.equal(requests[0].appContext.app,'YouTube');assert.equal(requests[1].appContext.app,'Apple Music');
 await c.close();
});
test('both providers expose the same lifecycle and context contract',async()=>{
 const f=setup(),calls=[];f.context.ElevenConversation={startSession:async o=>{const raw={endSession:async()=>calls.push('close'),setMicMuted:v=>calls.push(v),sendContextualUpdate:t=>calls.push(t),sendUserMessage:t=>calls.push(t),getInputVolume:()=>.2,getOutputVolume:()=>.3};o.onConversationCreated(raw);o.onConnect?.();return raw;}};
 const c=await f.context.VoiceProviders.start({provider:'elevenlabs',api:async()=>({token:'test',history:'recent'}),tools:{}});
 c.sendContext('context');c.sendMessage('question');c.setMuted(true);assert.equal(c.inputLevel(),.2);await c.close();assert.deepEqual(calls,['context','question',true,'close']);
});
test('OpenAI keeps app tool selection with the model and deduplicates only identical delegations',async()=>{
 const f=setup(),requests=[];
 const c=await f.context.VoiceProviders.start({provider:'openai',api:async()=>({session:{id:'s'},transport:{sdp:'answer'}}),tools:{delegate_task:async p=>{requests.push(p.request);return JSON.stringify({state:'completed',text:'Verified'});},apple_music_play:()=>assert.fail('No keyword routing'),book_control:()=>assert.fail('No keyword routing')},toolContext:()=>({book:true})});
 f.emit({type:'session.started'});
 f.emit({type:'session.input_transcript.delta',start_ms:0,end_ms:1000,delta:'Play Dreams by Fleetwood Mac.'});f.emit({type:'session.delegation.created',delegation:{id:'one',target:'client'}});await flush();
 f.emit({type:'session.delegation.created',delegation:{id:'one',target:'client'}});f.emit({type:'session.delegation.created',delegation:{id:'duplicate',target:'client'}});await flush();assert.equal(requests.length,2);assert.equal(requests[1],'No new user speech since the preceding handoff.');
 f.emit({type:'session.input_transcript.delta',start_ms:4000,end_ms:5000,delta:'Pick up the story where we left off.'});f.emit({type:'session.delegation.created',delegation:{id:'two',target:'client'}});await flush();assert.equal(requests.length,3);assert.equal(requests[2],'Pick up the story where we left off.');await c.close();
});
test('cancel during microphone acquisition releases a late microphone without opening a session',async()=>{
 const f=setup();let resolve,handle,requests=0;f.context.navigator.mediaDevices.getUserMedia=()=>new Promise(r=>resolve=r);
 const opening=f.context.VoiceProviders.start({provider:'openai',api:async()=>requests++,tools:{},onConversationCreated:c=>handle=c});await handle.close();resolve(f.stream);await assert.rejects(opening,/canceled/);assert.equal(requests,0);assert.equal(f.tracks[0].stopped,true);
});
test('fast command matching is narrow and does not treat albums or quoted chatter as song actions',()=>{
 const f=setup(),match=f.context.resolveVoiceCommand;
 assert.equal(match('Play Dreams by Fleetwood Mac').name,'apple_music_play');assert.equal(match('Maybe someone said play Dreams by Fleetwood Mac'),null);assert.equal(match('Play the album Grace by Jeff Buckley'),null);assert.equal(match('Play Across the Universe').params.title,'Across the Universe');assert.equal(match('Play Across the Universe').params.artist,'');assert.equal(match('Play the song Across the Universe on Apple Music').params.title,'Across the Universe');
 for(const s of ['play that','play it','play something','play the album Grace','play a game','play chess','play the podcast','play Across the Universe on Spotify'])assert.equal(match(s),null,s);
 assert.equal(match('keep playing'),null);assert.equal(match('keep playing',{podcast:true}).name,'podcast_control');
});
test('new task corrections are forwarded while an earlier task is pending',async()=>{
 const f=setup(),pending=[];
 const c=await f.context.VoiceProviders.start({provider:'openai',api:async()=>({session:{id:'s'},transport:{sdp:'answer'}}),tools:{delegate_task:p=>new Promise(resolve=>pending.push({p,resolve}))}});
 f.emit({type:'session.input_transcript.delta',start_ms:0,end_ms:1000,delta:'Find the nearest scooter.'});f.emit({type:'session.delegation.created',delegation:{id:'a',target:'client'}});await flush();
 f.emit({type:'session.input_transcript.delta',start_ms:4000,end_ms:5000,delta:'Forget that, open the camera.'});f.emit({type:'session.delegation.created',delegation:{id:'b',target:'client'}});await flush();
 assert.equal(pending.length,2);assert.match(pending[1].p.request,/Forget that/);
 assert.equal(pending[1].p.voiceContext.previousDelegationId,'a');
 assert.match(pending[1].p.voiceContext.messages[0].text,/nearest scooter/);
 for(const task of pending)task.resolve(JSON.stringify({state:'completed',text:'Test result'}));await flush();await c.close();
});
test('assistant backchannels and long pauses cannot erase an unfinished user request',async()=>{
 const f=setup(),requests=[];
 const c=await f.context.VoiceProviders.start({provider:'openai',api:async()=>({session:{id:'s'},transport:{sdp:'answer'}}),tools:{delegate_task:async p=>{requests.push(p);return '{}';}}});
 f.emit({type:'session.input_transcript.delta',event_id:'input-1',start_ms:0,end_ms:400,delta:'Send the '});
 f.emit({type:'session.input_transcript.delta',event_id:'input-2',start_ms:400,end_ms:800,delta:'tweet'});
 f.emit({type:'session.output_transcript.delta',event_id:'backchannel',delta:'Copy.'});
 f.emit({type:'session.input_transcript.delta',event_id:'input-3',start_ms:6000,end_ms:6400,delta:' to'});
 f.emit({type:'session.input_transcript.delta',event_id:'input-4',start_ms:6400,end_ms:6800,delta:' Nathan'});
 f.emit({type:'session.delegation.created',event_id:'handoff-event',delegation:{id:'send-tweet',target:'client'}});await flush();
 assert.equal(requests[0].request,'to Nathan','latest segment is not flattened with earlier dialogue into a fabricated command');
 assert.equal(JSON.stringify(requests[0].voiceContext.messages),JSON.stringify([{role:'user',text:'Send the tweet'},{role:'assistant',text:'Copy.'},{role:'user',text:' to Nathan'}]));
 await c.close();
});
test('event IDs suppress retransmitted fragments while intentional repeated words survive',async()=>{
 const f=setup(),requests=[];
 const c=await f.context.VoiceProviders.start({provider:'openai',api:async()=>({session:{id:'s'},transport:{sdp:'answer'}}),tools:{delegate_task:async p=>{requests.push(p);return '{}';}}});
 const first={type:'session.input_transcript.delta',event_id:'input',delta:'Play it again'};
 f.emit(first);f.emit(first);
 f.emit({type:'session.delegation.created',event_id:'handoff-1',delegation:{id:'first',target:'client'}});await flush();
 f.emit({type:'session.delegation.created',event_id:'handoff-retransmitted',delegation:{id:'first',target:'client'}});await flush();
 assert.equal(requests.length,1);assert.equal(requests[0].request,'Play it again');
 f.emit({...first,event_id:'different-input'});
 f.emit({type:'session.delegation.created',event_id:'handoff-2',delegation:{id:'second',target:'client'}});await flush();
 assert.equal(requests.length,2);assert.equal(requests[1].request,'Play it again');
 assert.equal(requests[1].voiceContext.hasNewUserSpeech,true);
 assert.equal(requests[1].voiceContext.messages.length,2,'an explicit handoff separates repeated requests without relying on a pause');
 await c.close();
});
test('a distinct handoff with no new speech reaches the model as context, not a replayed command',async()=>{
 const f=setup(),requests=[];
 const c=await f.context.VoiceProviders.start({provider:'openai',api:async()=>({session:{id:'s'},transport:{sdp:'answer'}}),tools:{delegate_task:async p=>{requests.push(p);return '{}';}}});
 f.emit({type:'session.input_transcript.delta',delta:'Send the tweet'});
 f.emit({type:'session.delegation.created',delegation:{id:'first',target:'client'}});await flush();
 f.emit({type:'session.delegation.created',delegation:{id:'second',target:'client'}});await flush();
 assert.equal(requests.length,2);assert.equal(requests[1].voiceContext.hasNewUserSpeech,false);
 assert.equal(requests[1].request,'No new user speech since the preceding handoff.');
 assert.equal(requests[1].voiceContext.previousDelegationId,'first');
 assert.equal(requests[1].voiceContext.messages[0].text,'Send the tweet');
 await c.close();
});
test('earlier conversational questions stay context instead of becoming a combined command',async()=>{
 const f=setup(),requests=[];
 const c=await f.context.VoiceProviders.start({provider:'openai',api:async()=>({session:{id:'s'},transport:{sdp:'answer'}}),tools:{delegate_task:async p=>{requests.push(p);return '{}';}}});
 f.emit({type:'session.input_transcript.delta',delta:'What is two plus two?'});
 f.emit({type:'session.output_transcript.delta',delta:'Four.'});
 f.emit({type:'session.input_transcript.delta',delta:'Open Maps.'});
 f.emit({type:'session.delegation.created',delegation:{id:'maps',target:'client'}});await flush();
 assert.equal(requests[0].request,'Open Maps.');
 assert.equal(JSON.stringify(requests[0].voiceContext.messages),JSON.stringify([{role:'user',text:'What is two plus two?'},{role:'assistant',text:'Four.'},{role:'user',text:'Open Maps.'}]));
 await c.close();
});
test('task progress reaches voice with its own delegation and stops after close',async()=>{
 const f=setup(),pending=[];
 const c=await f.context.VoiceProviders.start({provider:'openai',api:async()=>({session:{id:'s'},transport:{sdp:'answer'}}),tools:{delegate_task:p=>new Promise(resolve=>pending.push({p,resolve}))}});
 f.emit({type:'session.input_transcript.delta',start_ms:0,end_ms:1000,delta:'Open Messages on my Mac.'});
 f.emit({type:'session.delegation.created',delegation:{id:'messages',target:'client'}});await flush();
 pending[0].p.onProgress('Messages is open; finding the conversation.');
 const updates=f.sent.filter(e=>e.type==='session.thinking.append');
 assert.equal(updates.length,1);assert.equal(updates[0].delegation_id,'messages');
 assert.equal(updates[0].content,'Messages is open; finding the conversation.');
 assert.equal(f.sent.filter(e=>e.type==='session.commentary.append').length,0,'routine progress must not request speech');
 await c.close();const count=f.sent.length;
 pending[0].p.onProgress('A late update');pending[0].resolve('{"state":"completed","text":"Opened"}');await flush();
 assert.equal(f.sent.length,count,'closed voice sessions must receive no progress or result');
});
test('phrases never trigger phone work without a model delegation',async()=>{
 const f=setup(),calls=[];const c=await f.context.VoiceProviders.start({provider:'openai',commandFallbackMs:5,api:async()=>({session:{id:'s'},transport:{sdp:'answer'}}),tools:{apple_music_play:()=>assert.fail('No phrase fallback'),delegate_task:async p=>{calls.push(p.request);return '{}';}}});
 f.emit({type:'session.input_transcript.delta',start_ms:0,end_ms:1000,delta:'Play Tangerine by Zeppelin'});await new Promise(r=>setTimeout(r,30));assert.equal(calls.length,0);
 f.emit({type:'session.delegation.created',delegation:{id:'model-choice',target:'client'}});await flush();assert.equal(calls.length,1);await c.close();
});
test('closing before explicit music fallback cancels the pending action',async()=>{
 const f=setup();const c=await f.context.VoiceProviders.start({provider:'openai',commandFallbackMs:5,api:async()=>({session:{id:'s'},transport:{sdp:'answer'}}),tools:{apple_music_play:async()=>assert.fail('Closed session must not play')}});
 f.emit({type:'session.input_transcript.delta',start_ms:0,end_ms:1000,delta:'Play Dreams by Fleetwood Mac'});await c.close();await new Promise(r=>setTimeout(r,20));
});
test('Guardian intercept bypasses Luna and direct music tools for each distinct delegation',async()=>{
 const f=setup(),calls=[];
 const c=await f.context.VoiceProviders.start({provider:'openai',guardianMode:{generation:9},api:async(method,path,body)=>{assert.equal(body.guardianGeneration,9);return {session:{id:'g'},transport:{sdp:'answer'}}},tools:{delegate_task:()=>assert.fail('Luna must not run'),apple_music_play:()=>assert.fail('Local tool must not run')},onVoiceRequest:async request=>{calls.push(request);return {handled:true,result:{text:'Actual Guardian answer'}};}});
 f.emit({type:'session.input_transcript.delta',start_ms:0,end_ms:1000,delta:'Play Dreams by Fleetwood Mac'});
 f.emit({type:'session.delegation.created',delegation:{id:'g1',target:'client'}});await flush();
 f.emit({type:'session.delegation.created',delegation:{id:'g2',target:'client'}});await flush();
 assert.equal(calls.length,2);assert.ok(f.sent.some(e=>e.type==='session.commentary.append'&&e.content.includes('Actual Guardian answer')));await c.close();
});
test('Guardian handoff triggers one transition without speaking a fabricated task result',async()=>{
 const f=setup();let switched=0;
 const c=await f.context.VoiceProviders.start({provider:'openai',api:async()=>({session:{id:'s'},transport:{sdp:'answer'}}),tools:{},onVoiceRequest:async()=>({handled:true,transition:true,mode:{generation:1}}),onGuardianSwitch:()=>switched++});
 f.emit({type:'session.input_transcript.delta',start_ms:0,end_ms:1000,delta:"Talk to Holland's Guardian"});f.emit({type:'session.delegation.created',delegation:{id:'s1',target:'client'}});await flush();assert.equal(switched,1);assert.equal(f.sent.filter(e=>e.type==='session.commentary.append').length,0);await c.close();
});
test('cancel while a server session is being created closes that exact late session',async()=>{
 const f=setup();let resolveCreate,handle;const ended=[];
 const opening=f.context.VoiceProviders.start({provider:'openai',api:async(method,path,data)=>{
  if(path==='/voice/openai')return new Promise(resolve=>resolveCreate=resolve);
  if(path==='/voice-ended'){ended.push(data.sessionId);return {};}
 },tools:{},onConversationCreated:c=>handle=c});
 await flush();await handle.close();resolveCreate({session:{id:'late-old-session'},transport:{sdp:'answer'}});
 await assert.rejects(opening,/canceled/);assert.deepEqual(ended,['late-old-session']);
});
test('a muted handoff disables microphone before connection and preserves server mute',async()=>{
 const f=setup();const c=await f.context.VoiceProviders.start({provider:'openai',initialMuted:true,api:async()=>({session:{id:'s'},transport:{sdp:'answer'}}),tools:{}});
 assert.equal(f.tracks[0].enabled,false);f.emit({type:'session.started'});assert.ok(f.sent.some(e=>e.type==='session.input_audio.mute'));await c.close();
});
test('legacy routing toggle cannot restore phrase matching',async()=>{
 const f=setup(),calls=[];let general=true;const c=await f.context.VoiceProviders.start({provider:'openai',api:async()=>({session:{id:'s'},transport:{sdp:'answer'}}),toolContext:()=>({appRoutinesEnabled:!general}),tools:{delegate_task:async p=>{calls.push(p.request);return '{}';},apple_music_play:()=>assert.fail('No keyword routing')}});
 for(const [i,text] of ['Play Dreams by Fleetwood Mac','Play Tangerine by Led Zeppelin'].entries()){general=!general;f.emit({type:'session.input_transcript.delta',start_ms:i*3000,end_ms:i*3000+1000,delta:text});f.emit({type:'session.delegation.created',delegation:{id:String(i),target:'client'}});await flush();}assert.equal(calls.length,2);await c.close();
});
test('live book read and pause preserve a single output path with voice boost',async()=>{
 const f=setup(),audios=[],gains=[],messages=[];
 f.context.Audio=class {constructor(){audios.push(this);this.muted=false;}play(){return Promise.resolve();}pause(){}};
 const node=()=>({connect(){},disconnect(){}});
 f.context.AudioContext=class {
  constructor(){this.state='running';this.destination=node();}
  async resume(){} async close(){}
  createMediaStreamSource(){return node();}
  createAnalyser(){return {...node(),fftSize:256,getFloatTimeDomainData(a){a.fill(0);}};}
  createGain(){const n={...node(),gain:{value:1}};gains.push(n);return n;}
  createDynamicsCompressor(){return {...node(),...Object.fromEntries(['threshold','knee','ratio','attack','release'].map(k=>[k,{value:0}]))};}
  createWaveShaper(){return node();}
 };
 vm.runInContext(fs.readFileSync(__dirname+'/output-boost.js','utf8'),f.context);
 const c=await f.context.VoiceProviders.start({provider:'openai',outputBoostDb:6,api:async()=>({session:{id:'s'},transport:{sdp:'answer'}}),tools:{},onMessage:m=>messages.push(m)});
 f.pc.ontrack({track:{}});f.emit({type:'session.started'});
 c.readText('The ship entered the harbor.');
 assert.equal(audios[0].muted,true,'raw stream stays muted during boosted reading');
 assert.ok(gains.at(-1).gain.value>1,'only processed output is audible');
 f.emit({type:'session.output_transcript.delta',delta:'The ship entered the harbor.'});
 f.emit({type:'session.input_transcript.delta',delta:'Why?',start_ms:0,end_ms:1000});
 assert.equal(messages[0].kind,'book_narration','read text stays out of conversational memory');
 f.emit({type:'session.output_transcript.delta',delta:'It came to unload cargo.'});
 c.stopReading();assert.equal(audios[0].muted,true);assert.equal(gains.at(-1).gain.value,0,'pause mutes processed output too');
 assert.equal(messages.at(-1).kind,'voice','discussion remains in conversational memory');
 c.setOutputBoost(0);assert.equal(audios[0].muted,true,'changing boost cannot release paused audio');
 await c.close();
});

test('Luna-first submits a settled request without Live delegation and ignores late Live delegation',async()=>{
 const f=setup(),calls=[];const c=await f.context.VoiceProviders.start({provider:'openai',decisionOwner:'luna',inputSettleMs:5,api:async()=>({session:{id:'s'},transport:{sdp:'answer'}}),tools:{delegate_task:async p=>{calls.push(p);return JSON.stringify({state:'completed',text:'Verified answer'});}}});
 f.emit({type:'session.started'});
 f.emit({type:'session.input_transcript.delta',start_ms:0,end_ms:200,delta:'Read this'});
 f.emit({type:'session.input_transcript.delta',start_ms:200,end_ms:500,delta:' paragraph'});
 f.emit({type:'session.output_transcript.delta',delta:'Unrequested reading.'});
 await new Promise(r=>setTimeout(r,30));
 f.emit({type:'session.delegation.created',delegation:{id:'late',target:'client'}});await flush();
 assert.equal(calls.length,1);assert.equal(calls[0].request,'Read this paragraph');assert.equal(calls[0].routingMode,'luna');
 assert.ok(f.sent.some(e=>e.content?.includes('Stop previous speech')));
 await c.close();
});
test('Luna-first drops a stale result and keeps unsolicited speech out of history',async()=>{
 const f=setup(),requests=[],messages=[];const c=await f.context.VoiceProviders.start({provider:'openai',decisionOwner:'luna',inputSettleMs:5,api:async()=>({session:{id:'s'},transport:{sdp:'answer'}}),onMessage:m=>messages.push(m),tools:{delegate_task:p=>new Promise(resolve=>requests.push({p,resolve}))}});
 f.emit({type:'session.started'});f.emit({type:'session.input_transcript.delta',start_ms:0,end_ms:300,delta:'Open YouTube'});await new Promise(r=>setTimeout(r,20));
 f.emit({type:'session.output_transcript.delta',delta:'I opened the wrong thing.'});
 f.emit({type:'session.input_transcript.delta',start_ms:1700,end_ms:2200,delta:'Actually just tell me the time'});await new Promise(r=>setTimeout(r,20));
 requests[0].resolve(JSON.stringify({state:'completed',text:'Old stale answer'}));await flush();
 assert.equal(requests.length,2);assert.ok(!f.sent.some(e=>e.content?.includes('Old stale answer')));assert.ok(!messages.some(m=>m.role==='agent'));
 await c.close();requests[1].resolve('{}');await flush();
});

test('Luna-first output starts muted when the WebRTC track arrives after controller setup',async()=>{
 const f=setup(),audios=[];f.context.Audio=class{constructor(){audios.push(this);this.muted=false;}play(){return Promise.resolve();}pause(){}};
 const c=await f.context.VoiceProviders.start({provider:'openai',decisionOwner:'luna',api:async()=>({session:{id:'s'},transport:{sdp:'answer'}}),tools:{}});
 f.pc.ontrack({track:{}});assert.equal(audios[0].muted,true);await c.close();
});

test('ElevenLabs stopSpeech mutes a stopped task until the agent listens again or resumeSpeech',async()=>{
 const f=setup(),volumes=[];let options;
 f.context.ElevenConversation={startSession:async o=>{options=o;const raw={endSession:async()=>{},setMicMuted(){},sendContextualUpdate(){},sendUserMessage(){},getInputVolume:()=>0,getOutputVolume:()=>.4,setVolume:({volume})=>volumes.push(volume)};o.onConversationCreated(raw);return raw;}};
 const modes=[];const c=await f.context.VoiceProviders.start({provider:'elevenlabs',api:async()=>({token:'t'}),tools:{},onModeChange:m=>modes.push(m.mode)});
 c.stopSpeech();assert.deepEqual(volumes,[0]);
 options.onModeChange({mode:'speaking'});assert.deepEqual(volumes,[0],'still muted while the stopped reply plays');
 options.onModeChange({mode:'listening'});assert.deepEqual(volumes,[0,1]);assert.deepEqual(modes,['speaking','listening']);
 c.stopSpeech();c.resumeSpeech();c.resumeSpeech();assert.deepEqual(volumes,[0,1,0,1]);
 await c.close();
});

test('OpenAI stopSpeech mutes playback and resumeSpeech unmutes it for the Stop acknowledgment',async()=>{
 const f=setup(),audios=[];f.context.Audio=class{constructor(){audios.push(this);}play(){return Promise.resolve();}pause(){}};
 const c=await f.context.VoiceProviders.start({provider:'openai',api:async()=>({session:{id:'s'},transport:{sdp:'answer'}}),tools:{}});
 f.emit({type:'session.started'});
 c.stopSpeech();assert.equal(audios[0].muted,true);
 assert.ok(f.sent.some(e=>e.type==='session.instructions.append'&&/Stop speaking/.test(e.content)));
 c.resumeSpeech();assert.equal(audios[0].muted,false);
 await c.close();
});
