/* Same verified worker for direct voice requests and the general task agent. */
(function(root){
 function createMediaTool({api,delay=ms=>new Promise(r=>setTimeout(r,ms)),now=()=>Date.now()}){
  return async params=>{
   if(!params||!['get_state','pause','resume'].includes(params.operation))return JSON.stringify({status:'needs_clarification',reason:'Choose playback state, pause, or resume.'});
   try{
    let c=await api('POST','/phone/commands',{action:'media_control',app:'apple_music',operation:params.operation});
    const deadline=now()+15000;
    while(c.status==='pending'&&now()<deadline){await delay(200);c=await api('GET','/phone/commands/'+c.id);}
    if(c.status==='completed')return JSON.stringify(c.result);
    return JSON.stringify({status:'outcome_unknown',reason:'Playback state was not confirmed. Do not repeat automatically.'});
   }catch{return JSON.stringify({status:'outcome_unknown',reason:'The media shortcut is disabled or unavailable. No confirmed result; do not replay a possibly completed action.'});}
  };
 }
 root.createMediaTool=createMediaTool;
 if(typeof module!=='undefined')module.exports=createMediaTool;
})(typeof window!=='undefined'?window:globalThis);
