(function(root){
 function createClockTool({api,delay=ms=>new Promise(r=>setTimeout(r,ms)),now=()=>Date.now()}){
  return async params=>{
   try{
    let c=await api('POST','/phone/commands',{action:'clock_control',params});
    const deadline=now()+15000;
    while(c.status==='pending'&&now()<deadline){await delay(150);c=await api('GET','/phone/commands/'+c.id);}
    return JSON.stringify(c.status==='completed'?c.result:{status:'uncertain',reason:'Clock did not confirm. Read state before another action.'});
   }catch{return JSON.stringify({status:'unavailable',reason:'Clock controls disabled or invalid request. No confirmed action. Do not repeat a possibly completed action.'});}
  };
 }
 root.createClockTool=createClockTool;
 if(typeof module!=='undefined')module.exports=createClockTool;
})(typeof window!=='undefined'?window:globalThis);
