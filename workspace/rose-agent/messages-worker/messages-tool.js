(function(root){
 function createMessagesTool({api}){return async input=>{
  try{return JSON.stringify(await api('POST','/messages-worker/read',input));}
  catch{return JSON.stringify({status:'unavailable',reason:'Message reading could not be completed. Do not invent a message or a reply.'});}
 };}
 root.createMessagesTool=createMessagesTool;
 if(typeof module!=='undefined')module.exports=createMessagesTool;
})(typeof window!=='undefined'?window:globalThis);
