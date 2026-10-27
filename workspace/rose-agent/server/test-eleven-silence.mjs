import {TextConversation} from '@elevenlabs/client';
import {elevenRequest,agentId} from './elevenlabs.mjs';
const {signed_url}=await elevenRequest('/convai/conversation/get-signed-url?agent_id='+agentId);
let started=false,replies=0,skips=0;
const c=await TextConversation.startSession({signedUrl:signed_url,textOnly:true,connectionType:'websocket',dynamicVariables:{recent_history:'No prior conversation.'},onMessage:e=>{if(e.role==='agent'){if(started)replies++;console.log('agent:',e.message);}},onIncomingEvent:e=>{if(JSON.stringify(e).includes('skip_turn'))skips++;}});
await new Promise(r=>setTimeout(r,2000));started=true;c.sendUserMessage('...');
await new Promise(r=>setTimeout(r,35000));await c.endSession();console.log(JSON.stringify({repliesAfterSilence:replies,skipEvents:skips}));if(replies)process.exitCode=1;
