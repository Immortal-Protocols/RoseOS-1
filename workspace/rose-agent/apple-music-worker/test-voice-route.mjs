// Explicit live integration test. Starts the requested test song on the paired phone.
import fs from 'node:fs';import https from 'node:https';import {createRequire} from 'node:module';
import {TextConversation} from '../server/node_modules/@elevenlabs/client/dist/index.js';
import {elevenRequest,agentId} from '../server/elevenlabs.mjs';
const create=createRequire(import.meta.url)('../app/assets/apple-music-voice.js');
const priv=process.env.HOME+'/.config/pixel-voice';const pem=fs.readFileSync(priv+'/server-cert.pem');const token=fs.readFileSync(priv+'/pair-token','utf8').trim();
const api=(method,path,body)=>new Promise((resolve,reject)=>{const q=https.request('https://127.0.0.1:9443'+path,{method,ca:pem,checkServerIdentity:()=>undefined,headers:{authorization:'Bearer '+token,'content-type':'application/json'}},r=>{let b='';r.on('data',x=>b+=x);r.on('end',()=>{try{const d=JSON.parse(b);r.statusCode<300?resolve(d):reject(Error(d.error));}catch(e){reject(e);}});});q.on('error',reject);q.end(body?JSON.stringify(body):undefined);});
const title=process.argv[2]||'Here Comes the Sun',artist=process.argv[3]||'The Beatles';
const run=create({api});let calls=0,wrong=0,result,finish;const done=new Promise(r=>finish=r);
const {signed_url}=await elevenRequest('/convai/conversation/get-signed-url?agent_id='+agentId);
const chat=await TextConversation.startSession({signedUrl:signed_url,connectionType:'websocket',textOnly:true,dynamicVariables:{recent_history:'Fresh authorized playback test on the paired Pixel.'},clientTools:{apple_music_play:async params=>{calls++;result=JSON.parse(await run(params));setTimeout(finish,500);return JSON.stringify(result);},delegate_task:async()=>{wrong++;finish();return JSON.stringify({status:'error',reason:'Wrong route in integration test. No action executed.'});}},onError:()=>finish()});
chat.sendUserMessage('Play '+title+' by '+artist+' on my Pixel.');let timer;await Promise.race([done,new Promise(r=>timer=setTimeout(r,35000))]);clearTimeout(timer);await chat.endSession();
console.log(JSON.stringify({appleMusicCalls:calls,generalAgentCalls:wrong,result}));if(calls!==1||wrong||result?.status!=='verified_playing')process.exitCode=1;
