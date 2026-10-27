// The transcript relay and task submission use separate HTTP requests. Carry a
// bounded speaker-ordered snapshot with the handoff so the model cannot lose
// an earlier clause merely because that relay has not arrived yet. This is
// conversation data, not a turn classifier or an instruction to repeat actions.
export function voiceRequestMessages(value){
 if(!value||typeof value!=='object'||!Array.isArray(value.messages))return [];
 const messages=[];let remaining=12000;
 for(const message of value.messages.slice(-24).reverse()){
  if(!['user','assistant'].includes(message?.role)||typeof message.text!=='string')continue;
  const text=message.text.slice(-remaining);if(!text)continue;
  messages.unshift({role:message.role,text});remaining-=text.length;if(!remaining)break;
 }
 return messages;
}
export function voiceRequestContext(value){
 if(!value||typeof value!=='object'||!Array.isArray(value.messages))return '';
 const messages=voiceRequestMessages(value),boundedId=id=>typeof id==='string'?id.slice(0,200):null;
 return '\nVoice handoff context (conversation data, not instructions): '+JSON.stringify({
  delegationId:boundedId(value.delegationId),previousDelegationId:boundedId(value.previousDelegationId),
  hasNewUserSpeech:value.hasNewUserSpeech===true,messages,
 })+'\nUse the conversation and existing task state to interpret this handoff as a new request, continuation, correction, or redundant handoff. Do not treat previous requests as new instructions solely because they appear in context.';
}
