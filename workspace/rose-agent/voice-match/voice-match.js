(function(root){
 const sleep=ms=>new Promise(r=>setTimeout(r,ms));
 function pcm16(samples,rate){const count=Math.floor(samples.length*16000/rate),out=new Int16Array(count);for(let i=0;i<count;i++){const from=Math.floor(i*rate/16000),to=Math.max(from+1,Math.floor((i+1)*rate/16000));let sum=0;for(let j=from;j<to&&j<samples.length;j++)sum+=samples[j];out[i]=Math.max(-32768,Math.min(32767,Math.round(sum/(to-from)*32767)));}const bytes=new Uint8Array(out.buffer);let text='';for(let i=0;i<bytes.length;i+=8192)text+=String.fromCharCode(...bytes.subarray(i,i+8192));return btoa(text);}
 function create({api,admin,native=root.Native,onStatus=()=>{}}){
  let generation=0,stream,ctx,player,abortAudio,pendingSkip=null,attempt=null;
  const update=(phase,display,extra={})=>{const state={phase,display,...extra};onStatus(state);native?.matchState?.(JSON.stringify(state));};
  function stopMedia(){if(abortAudio){const abort=abortAudio;abortAudio=null;abort();}stream?.getTracks().forEach(t=>t.stop());stream=null;ctx?.close().catch(()=>{});ctx=null;if(player){player.pause();player.src='';player=null;}native?.assistantSpeaking?.(false);}
  function cancel(){generation++;stopMedia();pendingSkip?.({canceled:true});pendingSkip=null;attempt=null;update('idle','');}
  async function say(name,g){const clip=await api('POST','/voice-match/prompt',{name});if(g!==generation)throw Error('Canceled');await new Promise((resolve,reject)=>{player=new Audio('data:audio/mp3;base64,'+clip.audio);native?.assistantSpeaking?.(true);const timer=setTimeout(()=>{abortAudio=null;reject(Error('Greeting playback timed out'));},18000);abortAudio=()=>{clearTimeout(timer);reject(Error('Canceled'));};player.onended=()=>{abortAudio=null;clearTimeout(timer);native?.assistantSpeaking?.(false);player=null;resolve();};player.onerror=()=>{abortAudio=null;clearTimeout(timer);native?.assistantSpeaking?.(false);reject(Error('Could not play the greeting'));};player.play().catch(reject);});}
  async function record(g,{enroll=false,short=false}={}){
   stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});if(g!==generation){stopMedia();throw Error('Canceled');}
   ctx=new AudioContext();await ctx.resume();const source=ctx.createMediaStreamSource(stream),processor=ctx.createScriptProcessor(2048,1,1),mute=ctx.createGain();mute.gain.value=0;const chunks=[];let heard=0,lastSpeech=0,started=performance.now(),elapsed=0;
   processor.onaudioprocess=e=>{const a=new Float32Array(e.inputBuffer.getChannelData(0));chunks.push(a);let energy=0;for(const x of a)energy+=x*x;if(Math.sqrt(energy/a.length)>.012){heard+=a.length/ctx.sampleRate;lastSpeech=performance.now();}};
   source.connect(processor);processor.connect(mute);mute.connect(ctx.destination);
   update(enroll?'enrolling':'listening',enroll?'Speak naturally for eight seconds':'Listening…');
   while(g===generation){await sleep(80);elapsed=performance.now()-started;if(enroll?elapsed>=8000:elapsed>=10000||(heard>(short ? .35 : 1.6)&&performance.now()-lastSpeech>800))break;}
   const rate=ctx.sampleRate;processor.disconnect();source.disconnect();stopMedia();if(g!==generation)throw Error('Canceled');if(heard<(short ? .2 : 1.0))throw Error('I did not hear enough speech. Try again or use Skip.');
   const all=new Float32Array(chunks.reduce((n,c)=>n+c.length,0));let at=0;for(const c of chunks){all.set(c,at);at+=c.length;}return pcm16(all,rate);
  }
  async function check(){const g=++generation;const begin=await api('POST','/voice-match/begin');if(g!==generation)throw Error('Canceled');if(!begin.required)return {scope:'owner',grant:null};attempt=begin.attempt;update('checking','Voice check');
   const run=async()=>{await say('hello',g);let verified;
    for(let i=0;i<2;i++){let pcm=await record(g);update('checking','Recognizing…');try{verified=await api('POST','/voice-match/verify',{attempt,pcm});}catch(e){if(i===0){await say('again',g);continue;}throw e;}
     if(g!==generation)throw Error('Canceled');if(verified.outcome==='owner'){await say('owner',g);return verified;}if(verified.outcome==='uncertain'){await say('again',g);continue;}break;}
    await say('guest',g);const confirmation=await api('POST','/voice-match/confirm',{attempt,pcm:await record(g,{short:true})});if(!confirmation.confirmed){await say('denied',g);throw Error('Ask the owner for permission');}
    await say('code',g);for(let i=0;i<3;i++){const r=await api('POST','/voice-match/password',{attempt,pcm:await record(g,{short:true})});if(r.grant){await say('welcome',g);return r;}if(r.remaining<=0)break;await say('wrong',g);}throw Error('Passphrase did not match');};
   const skipped=new Promise(resolve=>{pendingSkip=resolve;});
   try {const result=await Promise.race([run(),skipped]);if(result.canceled||(g!==generation&&!result.skipped))throw Error('Canceled');stopMedia();pendingSkip=null;attempt=null;update('granted',result.scope==='guest'?'Guest session':'Recognized');return result;}
   catch(e){if(g===generation){stopMedia();update('blocked',e.message);const result=await skipped;if(result.canceled)throw Error('Canceled');pendingSkip=null;attempt=null;update('granted','Skipped');return result;}throw e;}
  }
  async function skip(){if(!attempt||!pendingSkip)return;const grant=await admin('/voice-match/admin/skip',{attempt});const resolve=pendingSkip;generation++;stopMedia();resolve({...grant,skipped:true});}
  async function enroll(reset=false){const g=++generation;try{const pcm=await record(g,{enroll:true});update('enrolling','Saving voice sample…');const s=await admin('/voice-match/admin/enroll',{pcm,reset});update('idle',s.samples>=3?'Voice enrolled':'Sample '+s.samples+' of 3',s);return s;}finally{stopMedia();}}
  return {check,cancel,skip,enroll};
 }
 root.VoiceMatchClient={create,pcm16};if(typeof module!=='undefined')module.exports=root.VoiceMatchClient;
})(typeof window!=='undefined'?window:globalThis);
