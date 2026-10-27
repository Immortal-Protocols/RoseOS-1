// Private, single-instance HTTPS relay. TLS is terminated by the deployment platform.
// Run behind HTTPS only. Separate 256-bit tokens authenticate phone and Mac.
import http from 'node:http';
import crypto from 'node:crypto';
const phoneToken=process.env.PHONE_TOKEN,macToken=process.env.MAC_TOKEN;
if(!phoneToken||!macToken||phoneToken.length<32||macToken.length<32||phoneToken===macToken)throw Error('Configure distinct strong PHONE_TOKEN and MAC_TOKEN');
const pending=new Map(),queue=[];let waiter=null,lastMac=0;
const allowed=new Set(['/health','/events','/session','/event','/voice-ended','/task','/conversation']);
function valid(req,token){const value=req.headers.authorization||'',expected='Bearer '+token;return value.length===expected.length&&crypto.timingSafeEqual(Buffer.from(value),Buffer.from(expected));}
function reply(res,status,data){if(res.writableEnded||res.destroyed)return;res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(data));}
async function json(req){let n=0,b=[];for await(const p of req){n+=p.length;if(n>250000)throw Error('Body too large');b.push(p)}return JSON.parse(Buffer.concat(b).toString()||'{}');}
function dispatch(){while(queue.length&&!pending.has(queue[0].id))queue.shift();if(waiter&&queue.length){const w=waiter;waiter=null;clearTimeout(w.timer);reply(w.res,200,{jobs:queue.splice(0,8)});}}
const server=http.createServer(async(req,res)=>{try{
 const url=new URL(req.url,'http://localhost');
 if(url.pathname==='/healthz'&&req.method==='GET')return reply(res,200,{ok:true});
 if(url.pathname.startsWith('/phone/')&&!valid(req,phoneToken))return reply(res,401,{error:'Unauthorized'});
 if(url.pathname.startsWith('/mac/')&&!valid(req,macToken))return reply(res,401,{error:'Unauthorized'});
 if(url.pathname==='/phone/request'&&req.method==='POST'){
  if(pending.size>=48)return reply(res,429,{error:'Relay busy'});
  if(Date.now()-lastMac>45000)return reply(res,503,{error:'Mac is offline'});
  const data=await json(req);if(!['GET','POST'].includes(data.method)||typeof data.route!=='string'||!data.route.startsWith('/')||data.route.startsWith('//')||!allowed.has(data.route.split('?')[0]))return reply(res,400,{error:'Unsupported request'});
  const id=crypto.randomUUID();const job={id,method:data.method,route:data.route,body:data.body||{}};
  const timer=setTimeout(()=>{pending.delete(id);reply(res,504,{error:'Mac request timed out'});},35000);
  pending.set(id,{res,timer});res.on('close',()=>{clearTimeout(timer);pending.delete(id)});queue.push(job);dispatch();return;
 }
 if(url.pathname==='/mac/poll'&&req.method==='POST'){
  lastMac=Date.now();if(waiter){clearTimeout(waiter.timer);reply(waiter.res,409,{error:'Another Mac poll replaced this one'});waiter=null;}
  const timer=setTimeout(()=>{if(waiter?.res===res)waiter=null;reply(res,200,{jobs:[]});},20000);
  waiter={res,timer};res.on('close',()=>{if(waiter?.res===res){clearTimeout(timer);waiter=null;}});dispatch();return;
 }
 if(url.pathname==='/mac/reply'&&req.method==='POST'){
  const data=await json(req),p=pending.get(data.id);if(!p)return reply(res,410,{error:'Request no longer pending'});
  if(!Number.isInteger(data.status)||data.status<200||data.status>599)return reply(res,400,{error:'Invalid status'});
  pending.delete(data.id);clearTimeout(p.timer);reply(p.res,200,{status:data.status,body:data.body});return reply(res,200,{ok:true});
 }
 reply(res,404,{error:'Not found'});
}catch(e){reply(res,400,{error:'Invalid relay request'});}});
server.requestTimeout=45000;server.headersTimeout=10000;
server.listen(Number(process.env.PORT||8787),'0.0.0.0',()=>console.log('Relay listening'));
process.on('SIGTERM',()=>{for(const p of pending.values()){clearTimeout(p.timer);reply(p.res,503,{error:'Relay restarting'})}if(waiter){clearTimeout(waiter.timer);reply(waiter.res,503,{error:'Relay restarting'})}server.close(()=>process.exit());setTimeout(()=>process.exit(),1500).unref();});
