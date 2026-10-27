// Isolated hosted-agent test. No phone tools or production memory writes.
import assert from 'node:assert/strict';
import {TextConversation} from '../server/node_modules/@elevenlabs/client/dist/index.js';
import {elevenRequest,agentId} from '../server/elevenlabs.mjs';
import {historyWithMemory,MEMORY_RULES} from './context.mjs';
const history=[{role:'user',text:'This is an isolated test. Our fictional garden project is called Marigold.'},{role:'assistant',text:'The fictional garden project is Marigold.'}];
const snapshot={enabled:true,records:[{id:'synthetic-only',text:'For this fictional test, the user’s favorite instrument is the nyckelharpa.'}],text:MEMORY_RULES+'\n'+JSON.stringify({personal_memories:[{id:'synthetic-only',text:'For this fictional test, the user’s favorite instrument is the nyckelharpa.'}]})};
const {signed_url}=await elevenRequest('/convai/conversation/get-signed-url?agent_id='+agentId);
let pending=null;let timer;
const conversation=await TextConversation.startSession({signedUrl:signed_url,connectionType:'websocket',textOnly:true,dynamicVariables:{recent_history:historyWithMemory(history,snapshot)},clientTools:{delegate_task:async()=>JSON.stringify({state:'error',message:'This is an isolated memory test. No tools are available; answer from the supplied fictional context.'})},onMessage:m=>{if(m.role==='agent'&&pending){clearTimeout(timer);const resolve=pending;pending=null;resolve(m.message);}},onError:()=>{if(pending){clearTimeout(timer);const resolve=pending;pending=null;resolve('');}}});
const ask=text=>new Promise(resolve=>{pending=resolve;timer=setTimeout(()=>{pending=null;resolve('');},30000);conversation.sendUserMessage(text);});
try {
 const first=await ask('In our fictional test, what is my favorite instrument and what is our garden project called?');
 assert.match(first,/nyckelharpa/i);assert.match(first,/Marigold/i);
 conversation.sendContextualUpdate(MEMORY_RULES+'\n'+JSON.stringify({personal_memories:[{id:'synthetic-revised',text:'For this fictional test, the user’s favorite instrument is now the glass harmonica, replacing nyckelharpa.'}]}));
 const revised=await ask('The fictional preference was updated. What is my favorite instrument now?');assert.match(revised,/glass harmonica/i);
 console.log(JSON.stringify({passed:true,syntheticOnly:true,initialMemoryAndRecentHistory:first,updatedMemory:revised}));
}finally{clearTimeout(timer);await conversation.endSession();}
