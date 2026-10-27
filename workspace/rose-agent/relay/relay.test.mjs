import {spawn} from 'node:child_process';import crypto from 'node:crypto';import assert from 'node:assert/strict';
const phone=crypto.randomBytes(32).toString('hex'),mac=crypto.randomBytes(32).toString('hex');
const child=spawn(process.execPath,[new URL('./relay.mjs',import.meta.url).pathname],{env:{...process.env,PORT:'18787',PHONE_TOKEN:phone,MAC_TOKEN:mac},stdio:['ignore','pipe','pipe']});
await new Promise((resolve,reject)=>{child.stdout.once('data',resolve);child.once('error',reject);child.once('exit',()=>reject(Error('Relay exited before startup')))});
const call=(route,token,data={})=>fetch('http://127.0.0.1:18787'+route,{method:'POST',headers:{authorization:'Bearer '+token,'content-type':'application/json'},body:JSON.stringify(data)});
try{
 assert.equal((await call('/phone/request','wrong')).status,401);
 assert.equal((await call('/phone/request',phone,{method:'GET',route:'/health'})).status,503);
 const poll=call('/mac/poll',mac);
 await new Promise(r=>setTimeout(r,100));
 const request=call('/phone/request',phone,{method:'GET',route:'/health'});
 const jobs=(await (await poll).json()).jobs;assert.equal(jobs.length,1);assert.equal(jobs[0].route,'/health');
 assert.equal((await call('/mac/reply',mac,{id:jobs[0].id,status:200,body:{ready:true}})).status,200);
 assert.deepEqual(await (await request).json(),{status:200,body:{ready:true}});
 assert.equal((await call('/phone/request',phone,{method:'GET',route:'//attacker.invalid/health'})).status,400);
 assert.equal((await call('/phone/request',phone,{method:'POST',route:'/admin'})).status,400);
 assert.equal((await call('/mac/reply',phone,{})).status,401);
 console.log('PASS: role authentication, offline reporting, request/reply roundtrip, route restrictions');
}finally{child.kill('SIGTERM');}
