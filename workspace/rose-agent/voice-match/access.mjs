const allowed=new Set(['/health','/voice/config','/voice/openai','/eleven/session','/voice/event','/eleven/event','/voice-ended','/memory/delivered']);
export function restrictedReply(route,hasGrant,scope,{ready,epoch}){
 if(!hasGrant||scope==='owner')return null;
 if(route==='/health')return {status:200,body:{ready,busy:false}};
 if(route==='/conversation')return {status:200,body:{revision:0,messages:[],personalMemory:null}};
 if(route==='/events')return {status:200,body:{events:[],busy:false,ready,epoch}};
 if(route==='/phone/commands')return {status:200,body:{commands:[]}};
 if(route==='/apps')return {status:200,body:{apps:[]}};
 return allowed.has(route)?null:{status:403,body:{error:'Guest sessions cannot access personal information or control the phone'}};
}
export const guestSessionInstructions='You are Pixel, a concise friendly voice assistant in a guest conversation. No personal history or memories have been provided. Do not claim to know the guest or the owner personally. Personal information, phone tools, email and calendar are unavailable in this guest session. Answer conversational questions; explain that the owner can end this session and approve owner access on the unlocked phone for protected actions.';
export function sessionContext(scope,history,memory,instructions){return scope==='guest'?{history:[],memory:{text:'',revision:null},instructions:guestSessionInstructions}:{history,memory,instructions:instructions+'\n'+memory.text};}
