/* One native routine request. No LLM, retries, or automatic fallback here. */
(function(root){
 function createAppleMusicVoiceTool({api,delay=ms=>new Promise(r=>setTimeout(r,ms)),now=()=>Date.now()}){
  return async function(params){
   if(!params||typeof params.title!=='string'||!params.title.trim()||params.title.length>150||(params.artist!==undefined&&(typeof params.artist!=='string'||params.artist.length>150)))return JSON.stringify({status:'needs_clarification',reason:'A song title is required; artist is optional.'});
   try{
    let c=await api('POST','/phone/commands',{action:'apple_music_run',title:params.title.trim(),artist:(params.artist||'').trim(),pauseAfter:false});
    const deadline=now()+30000;
    while(c.status==='pending'&&now()<deadline){await delay(200);c=await api('GET','/phone/commands/'+c.id);}
    if(c.status==='completed')return JSON.stringify(c.result);
    return JSON.stringify({status:'outcome_unknown',reason:'Playback was not confirmed. Do not repeat automatically; inspect the player first.'});
   }catch{return JSON.stringify({status:'outcome_unknown',reason:'Connection interrupted. Playback may have started. Do not repeat automatically.'});}
  };
 }
 root.createAppleMusicVoiceTool=createAppleMusicVoiceTool;
 if(typeof module!=='undefined'&&module.exports)module.exports=createAppleMusicVoiceTool;
})(typeof window!=='undefined'?window:globalThis);
