import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

test('model requests use model delegation without phrase matching, synthetic calls or a special delay',async()=>{
 let peer;const requests=[];
 class Peer{constructor(){peer=this;this.iceGatheringState='complete';}createDataChannel(){return this.channel={readyState:'open',send(){},close(){}};}addTrack(){}async createOffer(){return{sdp:'test'};}async setLocalDescription(x){this.localDescription=x;}async setRemoteDescription(){}close(){}}
 const ctx={console:{log(){}},setTimeout,clearTimeout,setInterval,clearInterval,Float32Array,Map,Set,Promise,Date,JSON,RTCPeerConnection:Peer,Audio:class{play(){return Promise.resolve();}pause(){}},MediaStream:class{},navigator:{mediaDevices:{getUserMedia:async()=>({getTracks:()=>[],getAudioTracks:()=>[]})}}};ctx.window=ctx;vm.createContext(ctx);
 for(const file of ['voice-commands.js','voice-providers.js'])vm.runInContext(fs.readFileSync(new URL(file,import.meta.url),'utf8'),ctx);
 const handle=await ctx.VoiceProviders.start({provider:'openai',api:async()=>({session:{id:'test'},transport:{sdp:'answer'}}),toolContext:()=>({appRoutinesEnabled:false}),tools:{delegate_task:async p=>{requests.push(p.request);return JSON.stringify({state:'completed',text:'Verified controller result.'});}}});
 try{
 const event=x=>peer.channel.onmessage({data:JSON.stringify(x)});
 event({type:'session.input_transcript.delta',delta:'Pick so, switch to GPT six sol now',start_ms:0,end_ms:1000});
 await new Promise(r=>setTimeout(r,10));assert.equal(requests.length,0);
 event({type:'session.delegation.created',delegation:{id:'chosen-by-model',target:'client'}});
 await new Promise(r=>setImmediate(r));assert.deepEqual(requests,['Pick so, switch to GPT six sol now']);
 event({type:'session.delegation.created',delegation:{id:'chosen-by-model',target:'client'}});
 await new Promise(r=>setImmediate(r));assert.equal(requests.length,1);
 }finally{await handle.close();}
});

test('hearing a request captures app state without appending anything into Live',async()=>{
 let peer,handle;const sent=[],requests=[];
 class Peer{constructor(){peer=this;this.iceGatheringState='complete';}createDataChannel(){return this.channel={readyState:'open',send:s=>sent.push(JSON.parse(s)),close(){}};}addTrack(){}async createOffer(){return{sdp:'test'};}async setLocalDescription(x){this.localDescription=x;}async setRemoteDescription(){}close(){}}
 const ctx={console:{log(){}},setTimeout,clearTimeout,setInterval,clearInterval,Float32Array,Map,Set,Promise,Date,JSON,RTCPeerConnection:Peer,Audio:class{play(){return Promise.resolve();}pause(){}},MediaStream:class{},navigator:{mediaDevices:{getUserMedia:async()=>({getTracks:()=>[],getAudioTracks:()=>[]})}}};ctx.window=ctx;vm.createContext(ctx);
 for(const file of ['../app/assets/phone-app-context.js','voice-providers.js'])vm.runInContext(fs.readFileSync(new URL(file,import.meta.url),'utf8'),ctx);
 const app=ctx.PhoneAppContext.create({read:()=>({status:'ok',observedAt:Date.now(),foregroundApp:{packageName:'dev.pixel.voice',label:'Rose Agent'},environment:{clock:{localTime:'21:10:00'},location:{city:'Secret test city'}}}),isActive:()=>true,sendContext:text=>handle.sendContext(text)});
 handle=await ctx.VoiceProviders.start({provider:'openai',api:async()=>({session:{id:'test'},transport:{sdp:'answer'}}),captureAppContext:()=>app.captureRequest(),sampleAppContext:()=>app.sample(),tools:{delegate_task:async p=>{requests.push(p);return JSON.stringify({state:'completed',text:'Verified result.'});}}});
 try{
  const event=x=>peer.channel.onmessage({data:JSON.stringify(x)});
  for(const delta of ['Open',' Apple',' Music'])event({type:'session.input_transcript.delta',delta});
  assert.equal(sent.length,0,'first transcript must not inject context while the user speaks');
  event({type:'session.delegation.created',delegation:{id:'open-music',target:'client'}});
  await new Promise(r=>setImmediate(r));assert.equal(requests.length,1);assert.equal(requests[0].request,'Open Apple Music');assert.equal(requests[0].appContext.foregroundApp.packageName,'dev.pixel.voice');assert.equal(requests[0].appContext.environment,undefined);
 }finally{await handle.close();}
});
