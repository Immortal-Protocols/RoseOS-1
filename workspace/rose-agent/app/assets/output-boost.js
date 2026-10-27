/* Optional received-voice processing only; microphone and music never enter this graph. */
(function(root){
 function limiterCurve(size=4097){const a=new Float32Array(size);for(let i=0;i<size;i++){const x=i*2/(size-1)-1,v=Math.abs(x);a[i]=Math.sign(x)*(v<=.8?v:.8+.18*(1-Math.exp(-(v-.8)/.18)));}return a;}
 function create(ctx,stream,audio,onState=()=>{}){
  let source,gain,compressor,guard,active=false,requested=0,playbackMuted=false;
  function disconnect(){for(const n of [source,gain,compressor,guard])try{n?.disconnect();}catch{}source=gain=compressor=guard=null;}
  function applyMute(){if(active){audio.muted=true;if(gain)gain.gain.value=playbackMuted?0:Math.pow(10,6/20);}else audio.muted=playbackMuted;}
  function setMuted(value){playbackMuted=!!value;applyMute();onState({requestedDb:requested,active,status:active?'on':requested?'unavailable':'off',playbackMuted,rawAudioMuted:audio.muted});}
  function setDb(value){
   requested=value===6?6:0;
   if(!requested){disconnect();active=false;applyMute();onState({requestedDb:0,active:false,status:'off'});return;}
   try{
    if(!ctx||ctx.state!=='running')throw Error('Audio context unavailable');
    if(!source){source=ctx.createMediaStreamSource(stream);gain=ctx.createGain();compressor=ctx.createDynamicsCompressor();guard=ctx.createWaveShaper();
     gain.gain.value=Math.pow(10,6/20);compressor.threshold.value=-3;compressor.knee.value=3;compressor.ratio.value=20;compressor.attack.value=.003;compressor.release.value=.12;
     guard.curve=limiterCurve();guard.oversample='2x';
     source.connect(gain);gain.connect(compressor);compressor.connect(guard);
    }
    audio.muted=true;if(!active)guard.connect(ctx.destination);active=true;applyMute();onState({requestedDb:6,active:true,status:'on'});
   }catch{disconnect();active=false;applyMute();onState({requestedDb:6,active:false,status:'unavailable'});}
  }
  function close(){disconnect();active=false;applyMute();onState({requestedDb:requested,active:false,status:"not_playing"});}
  return {setDb,setMuted,close,state:()=>({requestedDb:requested,active})};
 }
 root.VoiceOutputBoost={create,limiterCurve};if(typeof module!=='undefined')module.exports=root.VoiceOutputBoost;
})(typeof window!=='undefined'?window:globalThis);
