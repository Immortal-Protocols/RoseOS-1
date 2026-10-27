(function(root){
 function selection(text){
  const match=String(text).trim().match(/^(?:(?:okay|ok|please)[,\s]+)?(?:(?:let me|i(?:'d| would) like to|i want to)\s+)?(?:talk|speak|switch|connect)(?:\s+me)?\s+(?:to|with)\s+(.+?)(?:['’]s|s)\s+guardian[.!?]*$/i);
  return match?.[1]?.trim()||null;
 }
 function back(text){return /^(?:(?:okay|ok|please)[,\s]+)?(?:go |switch |take me )?back to (?:pixel|pixel voice)[.!?]*$/i.test(String(text).trim());}
 root.GuardianVoice={selection,back};
 if(typeof module!=='undefined')module.exports=root.GuardianVoice;
})(typeof window!=='undefined'?window:globalThis);
