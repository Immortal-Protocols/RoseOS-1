import https from 'node:https';
import fs from 'node:fs';
import path from 'node:path';
const privateDir=(process.env.HOME + "/.config/pixel-voice");
const conf=JSON.parse(fs.readFileSync(path.join(privateDir,'relay.json'),'utf8'));
const relay=new URL(conf.url);if(relay.protocol!=='https:')throw Error('Relay must use HTTPS');
const token=conf.macToken;
const pair=fs.readFileSync(path.join(privateDir,'pair-token'),'utf8').trim();
const cert=fs.readFileSync(path.join(privateDir,'server-cert.pem'));
const allowed=new Set(['/health','/events','/session','/event','/voice-ended','/task','/conversation']);
function local(job){return new Promise((resolve,reject)=>{
 if(!['GET','POST'].includes(job.method)||!allowed.has(job.route?.split('?')[0])||job.route.startsWith('//'))return reject(Error('Invalid route'));
 const body=JSON.stringify(job.body||{});
 const req=https.request({host:'127.0.0.1',port:9443,path:job.route,method:job.method,ca:cert,checkServerIdentity:()=>undefined,headers:{authorization:'Bearer '+pair,'content-type':'application/json'}},res=>{let data='';res.on('data',x=>data+=x);res.on('end',()=>{try{resolve({status:res.statusCode,body:JSON.parse(data)})}catch{reject(Error('Invalid local response'))}})});
 req.setTimeout(29000,()=>req.destroy(Error('Local request timeout')));req.on('error',reject);if(job.method==='POST')req.write(body);req.end();
});}
async function post(route,data){const res=await fetch(new URL(route,relay),{method:'POST',headers:{authorization:'Bearer '+token,'content-type':'application/json'},body:JSON.stringify(data),signal:AbortSignal.timeout(30000)});if(!res.ok)throw Error('Relay HTTP '+res.status);return res.json();}
async function handle(job){let result;try{result=await local(job)}catch(e){result={status:502,body:{error:'Mac service unavailable'}}}await post('/mac/reply',{id:job.id,...result}).catch(e=>console.error(e.message));}
let stopped=false;process.on('SIGTERM',()=>{stopped=true;process.exit()});
while(!stopped){try{const {jobs}=await post('/mac/poll',{});for(const job of jobs||[])handle(job);}catch(e){console.error(e.message);await new Promise(r=>setTimeout(r,2500));}}
