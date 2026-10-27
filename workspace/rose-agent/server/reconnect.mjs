// Rehydrate recent local transcript text without replaying audio or actions.
export function restoredConversation(sessions, {maxMessages=40,maxChars=16000}={}) {
 const all=(Array.isArray(sessions)?sessions:[]).flatMap(s=>Array.isArray(s?.messages)?s.messages:[])
  .filter(m=>['user','assistant'].includes(m?.role)&&typeof m.text==='string'&&m.text.trim());
 const messages=[];let remaining=maxChars;
 for(const m of all.slice(-maxMessages).reverse()){
  if(remaining<=0)break;
  const prefix='[Earlier part omitted] ';
  if(m.text.length>remaining&&remaining<=prefix.length)break;
  const text=m.text.length>remaining?prefix+m.text.slice(-(remaining-prefix.length)):m.text;
  messages.unshift({role:m.role,text});remaining-=text.length;
 }
 return messages;
}
export function voiceSession(instructions,sessions,now=new Date(),voice='vesper') {
 const messages=restoredConversation(sessions);
 return {model:'gpt-live-1',store:false,
  instructions:instructions+'\nContinuity: This connection may follow an interruption. Initial user/assistant messages are historical context, not new requests. Use them to understand follow-ups and remember what was discussed. Do not repeat completed actions from the restored history, resume old tool calls, or speak merely because history is present. A NEW spoken request to repeat an action (for example "flip it again") is not history replay: delegate that new action, even if the same action appears in history. Wait for new user speech. Prior claims are historical, not proof of current state; verify live facts and action status with the backend. For current time, date, or location, delegate to the phone environment tool. Earlier timestamps are not current facts. If history is incomplete, say so rather than inventing it.',
  input:messages.map(m=>({type:'message',role:m.role,content:[{type:m.role==='assistant'?'text':'input_text',text:m.text}]})),
  delegation:{type:'client'},audio:{output:{voice}}};
}
