export function verifiedTextSender(messages,{recipient,timeout=45000}={}){
 return async input=>{
  // Recipient is configured by the user, never chosen by model output.
  const before=await messages.read({action:'messages',chatId:input.chatId});
  if(before.chat?.identifier!==recipient)throw Error('Research recipient no longer matches the configured self conversation');
  const ids=new Set((before.messages||[]).map(m=>m.id));
  const result=await messages.send(input);
  if(!['send_requested','sent','ok'].includes(result.status))return result;
  const deadline=Date.now()+timeout;
  do{
   const state=await messages.read({action:'messages',chatId:input.chatId});
   const match=(state.messages||[]).find(m=>!ids.has(m.id)&&m.text===input.text&&m.fromMe);
   if(match?.error)throw Error('Messages reported a send error');
   if(match?.sent||match?.delivered)return {status:'sent',messageId:match.id,guid:match.guid,delivered:!!match.delivered};
   await new Promise(r=>setTimeout(r,1000));
  }while(Date.now()<deadline);
  return {status:'uncertain',error:'Messages accepted the report but sending could not be verified; not automatically resent'};
 };
}
