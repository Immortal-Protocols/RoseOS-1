import net from 'node:net';
import crypto from 'node:crypto';

// Only an explicitly created, expiring loopback listener can create a channel.
// The phone dials out through the existing pinned, client-authenticated TLS link.
export class DebugBridge {
 constructor({dispatch,isTrustedPeer,now=Date.now}){this.dispatch=dispatch;this.isTrustedPeer=isTrustedPeer;this.now=now;this.sessions=new Map();this.channels=new Map();}
 async open({host,port,peerId,seconds=1800,localPort=0}){
  if(!/^(127\.0\.0\.1|(?:\d{1,3}\.){3}\d{1,3})$/.test(host)||!Number.isInteger(port)||port<1024||port>65535||! /^[a-f0-9]{64}$/.test(peerId))throw Error('Invalid debugging endpoint');
  if(!Number.isInteger(seconds)||seconds<30||seconds>1800)throw Error('Invalid bridge lifetime');
  if(!Number.isInteger(localPort)||localPort!==0&&(localPort<1024||localPort>65535))throw Error('Invalid loopback port');
  if(this.sessions.size>=2)throw Error('Close the existing debugging bridges first');
  const id=crypto.randomUUID(),session={id,host,port,peerId,expiresAt:this.now()+seconds*1000,channels:new Set()};
  const listener=net.createServer(socket=>this.connect(session,socket));session.listener=listener;
  await new Promise((resolve,reject)=>{listener.once('error',reject);listener.listen(localPort,'127.0.0.1',resolve);});
  session.localPort=listener.address().port;session.timer=setTimeout(()=>this.close(id),seconds*1000);session.timer.unref();
  this.sessions.set(id,session);return {id,localHost:'127.0.0.1',localPort:session.localPort,expiresAt:session.expiresAt};
 }
 connect(session,socket){
  if(session.expiresAt<=this.now()||session.channels.size>=4){socket.destroy();return;}
  socket.pause();
  const id=crypto.randomUUID(),channel={id,session,socket,peer:null,claimed:false};
  this.channels.set(id,channel);session.channels.add(id);
  const close=()=>this.closeChannel(id);
  socket.on('error',close);socket.on('close',close);
  channel.timer=setTimeout(close,15000);channel.timer.unref();
  Promise.resolve().then(()=>this.dispatch({action:'debug_bridge_connect',channelId:id,targetHost:session.host,targetPort:session.port,expiresAt:Math.min(session.expiresAt,this.now()+12000)})).catch(close);
 }
 upgrade(req,socket,head){
  let url;try{url=new URL(req.url,'https://localhost');}catch{return this.reject(socket);}
  const id=url.searchParams.get('channel'),channel=this.channels.get(id);
  if(url.pathname!=='/debug-bridge/connect'||req.method!=='GET'||String(req.headers.upgrade).toLowerCase()!=='pixel-debug-v1'||!channel||channel.claimed||channel.session.expiresAt<=this.now()||!this.isTrustedPeer(req,channel.session.peerId))return this.reject(socket);
  channel.claimed=true;channel.peer=socket;clearTimeout(channel.timer);
  socket.write('HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: pixel-debug-v1\r\n\r\n');
  const close=()=>this.closeChannel(id);socket.on('error',close);socket.on('close',close);
  if(head.length)channel.socket.write(head);
  channel.socket.pipe(socket);socket.pipe(channel.socket);channel.socket.resume();
 }
 reject(socket){socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n');}
 closeChannel(id){const channel=this.channels.get(id);if(!channel)return;this.channels.delete(id);channel.session.channels.delete(id);clearTimeout(channel.timer);channel.socket.destroy();channel.peer?.destroy();}
 close(id){const session=this.sessions.get(id);if(!session)return false;this.sessions.delete(id);clearTimeout(session.timer);for(const channel of [...session.channels])this.closeChannel(channel);session.listener.close();return true;}
 closeAll(){for(const id of [...this.sessions.keys()])this.close(id);}
 status(){return [...this.sessions.values()].map(s=>({id:s.id,localHost:'127.0.0.1',localPort:s.localPort,expiresAt:s.expiresAt,connections:s.channels.size}));}
}
