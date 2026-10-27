import path from 'node:path';
import os from 'node:os';
import WebSocket from '../server/node_modules/ws/index.js';

export class RemoteWork {
 constructor({socketPath=path.join(process.env.CODEX_HOME||path.join(os.homedir(),'.codex'),'app-server-control/app-server-control.sock')}={}){this.socketPath=socketPath;this.sequence=0;this.pending=new Map();this.listeners=new Set();}
 async connect(){
  this.socket=new WebSocket('ws+unix://'+this.socketPath+':/',{perMessageDeflate:false});
  this.socket.on('error',error=>this.fail(error));
  this.socket.on('close',()=>this.fail(Error('Remote Codex connection closed')));
  this.socket.on('message',line=>{
   let message;try{message=JSON.parse(line);}catch{return;}
   if(message.id!==undefined&&!message.method){const waiting=this.pending.get(message.id);if(waiting){this.pending.delete(message.id);clearTimeout(waiting.timer);message.error?waiting.reject(Error(message.error.message)):waiting.resolve(message.result);}}
   for(const listener of this.listeners)listener(message);
  });
  await new Promise((resolve,reject)=>{this.socket.once('open',resolve);this.socket.once('error',reject);});
  await this.rpc('initialize',{clientInfo:{name:'pixel_conversation_work',title:'Pixel Conversation Work',version:'1'},capabilities:{experimentalApi:true}});
  this.socket.send(JSON.stringify({method:'initialized',params:{}}));
  return this;
 }
 fail(error){for(const waiting of this.pending.values()){clearTimeout(waiting.timer);waiting.reject(error);}this.pending.clear();}
 rpc(method,params){return new Promise((resolve,reject)=>{const id=++this.sequence;const timer=setTimeout(()=>{this.pending.delete(id);reject(Error('Remote Codex request timed out: '+method));},30000);this.pending.set(id,{resolve,reject,timer});this.socket.send(JSON.stringify({id,method,params}));});}
 async start({cwd,prompt,onThread=async()=>{}}){
  const created=await this.rpc('thread/start',{cwd,approvalPolicy:'never',sandbox:'danger-full-access',developerInstructions:'Perform only the user-authorized task. Record the result, verification and remaining blockers for each action item in the original conversation notebook using the supplied progress tool before declaring completion. Do not publish, delete user data or change credentials without specific authorization.'});
  const threadId=created.thread?.id;if(!threadId)throw Error('Remote Codex returned no thread receipt');
  await onThread(threadId);
  const result=await this.rpc('turn/start',{threadId,input:[{type:'text',text:prompt}]});
  if(!result.turn?.id)throw Error('Remote Codex returned no turn receipt');
  return {threadId,turnId:result.turn.id,destination:'cloud VM'};
 }
 close(){this.socket?.close();}
}
