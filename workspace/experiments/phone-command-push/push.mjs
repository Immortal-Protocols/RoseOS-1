import fs from 'node:fs';
import path from 'node:path';

// Notification transport only. The existing PhoneCommands queue owns execution
// identity, expiry and results. Reconnecting offers its still-pending commands.
export class PhonePush {
 constructor({file,epoch,pending,offered=()=>{},taskStatus=null}){this.file=file;this.epoch=epoch;this.pending=pending;this.offered=offered;this.taskStatus=taskStatus;this.clients=new Set();this.commandClients=new Set();this.peers=new Map();}
 enabled(){try{return JSON.parse(fs.readFileSync(this.file,'utf8')).enabled===true;}catch{return false;}}
 status(){return {enabled:this.enabled(),connections:this.clients.size,commandConnections:this.commandClients.size,observerConnections:this.clients.size-this.commandClients.size,epoch:this.epoch};}
 set(enabled){
  if(typeof enabled!=='boolean')throw Error('enabled must be boolean');
  fs.mkdirSync(path.dirname(this.file),{recursive:true,mode:0o700});
  fs.writeFileSync(this.file+'.tmp',JSON.stringify({enabled})+'\n',{mode:0o600});fs.renameSync(this.file+'.tmp',this.file);
  if(!enabled)for(const c of [...this.clients])c.end();
  return this.status();
 }
 send(res,event){
  if(res.destroyed||res.writableEnded)return false;
  // A slow consumer must reconnect, not hold an unbounded queue of commands.
  if(res.writableLength>262144){res.destroy();return false;}
  res.write('data: '+JSON.stringify({...event,epoch:this.epoch})+'\n\n');return true;
 }
 connect(req,res,{receiveCommands=true,peerId=null}={}){
  if(!this.enabled()){res.writeHead(503,{'content-type':'application/json'});res.end('{"error":"Push disabled; use polling"}');return;}
  // Reconnect only replaces this phone's stream, never the other phone's stream.
  if(peerId)for(const client of [...this.clients])if(this.peers.get(client)===peerId)client.end();
  while(this.clients.size>=8)this.clients.values().next().value.end();
  res.writeHead(200,{'content-type':'text/event-stream','cache-control':'no-store','connection':'keep-alive','x-accel-buffering':'no'});res.flushHeaders();
  this.clients.add(res);if(receiveCommands)this.commandClients.add(res);if(peerId)this.peers.set(res,peerId);
  this.send(res,{type:'hello',commands:[],...(this.taskStatus?{taskStatus:this.taskStatus()}:{})});
  if(receiveCommands)for(const c of this.pending())if(this.send(res,{type:'commands',commands:[c]}))this.offered(c,'push');
  const timer=setInterval(()=>{
   if(!this.enabled()){res.end();return;}
   if(res.writableLength>262144){res.destroy();return;}
   res.write(': keepalive\n\n');
  },10000);timer.unref?.();
  res.on('close',()=>{clearInterval(timer);this.clients.delete(res);this.commandClients.delete(res);this.peers.delete(res);});
 }
 publish(command){
  if(!this.enabled())return;
  let sent=false;for(const res of this.commandClients)sent=this.send(res,{type:'commands',commands:[command]})||sent;
  if(sent)this.offered(command,'push');
 }
 publishTaskStatus(taskStatus){
  if(!this.enabled())return;
  for(const res of this.clients)this.send(res,{type:'task_status',taskStatus});
 }
 close(){for(const res of [...this.clients])res.end();}
}
