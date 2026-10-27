/* Passive timing only. Audio levels are proxies, not speaker identification or
 * proof of physical playback. No microphone, routing, or turn controls here. */
(function(root){
 class VoiceTiming {
  constructor({now=()=>performance.now(),wall=()=>Date.now(),emit=()=>{}}={}){this.now=now;this.wall=wall;this.emit=emit;this.events=[];this.serial=0;this.reset();}
  reset(){this.input=false;this.output=false;this.rise=null;this.lastInput=null;this.lastOutput=null;this.turn=0;this.lastPing=-Infinity;this.sessionStarted=this.now();}
  record(stage,details={},at=this.now()){
   const event={seq:++this.serial,stage,turn:this.turn,monoMs:Math.round(at),wallMs:Math.round(this.wall()-(this.now()-at)),...details};
   this.events.push(event);if(this.events.length>64)this.events.shift();
   try{this.emit(event);}catch{}return event;
  }
  start(){this.events=[];this.reset();this.record('session_started');}
  stop(){this.record('session_ended');this.reset();}
  levels(input,output,muted=false){
   const now=this.now();
   if(!muted&&Number.isFinite(input)&&input>=.015){
    this.lastInput=now;if(this.rise===null)this.rise=now;
    if(!this.input&&now-this.rise>=80){this.input=true;this.turn++;this.record('mic_activity_start',{threshold:.015},this.rise);}
   }else{
    if(!this.input)this.rise=null;
    if(this.input&&(muted||now-this.lastInput>=320)){
     this.input=false;this.rise=null;this.record('mic_activity_end',{detectedMonoMs:Math.round(now),quietWindowMs:320,muted},this.lastInput);
    }
   }
   if(Number.isFinite(output)&&output>=.008){
    this.lastOutput=now;if(!this.output){this.output=true;this.record('output_audio_activity_start',{threshold:.008});}
   }else if(this.output&&now-this.lastOutput>=240){this.output=false;this.record('output_audio_activity_end',{},this.lastOutput);}
  }
  message(role,text){this.record(role==='user'?'user_transcript_arrived':'reply_text_arrived',{characters:typeof text==='string'?text.length:0});}
  mode(mode){if(mode==='speaking'||mode==='listening')this.record(mode==='speaking'?'reply_mode_speaking':'reply_mode_listening');}
  ping(ms){const now=this.now();if(Number.isFinite(ms)&&ms>=0&&ms<=120000&&now-this.lastPing>=10000){this.lastPing=now;this.record('network_ping',{pingMs:Math.round(ms)});}}
  snapshot(){return this.events.slice();}
 }
 root.VoiceTiming=VoiceTiming;
 if(typeof module!=='undefined'&&module.exports)module.exports=VoiceTiming;
})(typeof window!=='undefined'?window:globalThis);
