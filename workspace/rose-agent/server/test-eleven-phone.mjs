// Opt-in integration check. Opens Vanadium on the paired Pixel via Bro 1's real tool.
import {TextConversation} from '@elevenlabs/client';
import {elevenRequest,agentId} from './elevenlabs.mjs';
import https from 'node:https';import fs from 'node:fs';
const token=fs.readFileSync((process.env.HOME + "/.config/pixel-voice/pair-token"),'utf8').trim();
function api(method,route,body){return new Promise((resolve,reject)=>{const r=https.request('https://127.0.0.1:9443'+route,{method,rejectUnauthorized:false,headers:{Authorization:'Bearer '+token,'content-type':'application/json'}},r=>{let b='';r.on('data',x=>b+=x);r.on('end',()=>{try{const data=JSON.parse(b);r.statusCode<300?resolve(data):reject(new Error(data.error));}catch(e){reject(e)}})});r.on('error',reject);r.end(body?JSON.stringify(body):undefined);});}
const {signed_url}=await elevenRequest('/convai/conversation/get-signed-url?agent_id='+agentId);
let task,finished;const done=new Promise(r=>finished=r);
const conversation=await TextConversation.startSession({signedUrl:signed_url,connectionType:'websocket',textOnly:true,dynamicVariables:{recent_history:'This is an authorized integration test. No earlier actions.'},clientTools:{delegate_task:async p=>{console.log('Tool request:',p.request);task=await api('POST','/eleven/task',{id:crypto.randomUUID(),request:p.request});while(['running','starting'].includes(task.state)){await new Promise(r=>setTimeout(r,800));task=await api('GET','/eleven/task/'+task.id);}console.log('Task result:',task.state,task.text);return JSON.stringify(task);}},onMessage:e=>{console.log(e.role+': '+e.message);if(task&&['completed','failed'].includes(task.state)&&e.role==='agent')finished();},onError:e=>{console.error(e);finished();}});
conversation.sendUserMessage('Open Vanadium on my Pixel. Verify that it is the foreground app.');
let timeout;await Promise.race([done,new Promise(r=>{timeout=setTimeout(r,110000);})]);clearTimeout(timeout);await conversation.endSession();if(!task||task.state!=='completed')process.exitCode=1;
