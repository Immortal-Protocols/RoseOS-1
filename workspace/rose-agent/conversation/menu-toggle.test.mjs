import {test} from 'node:test';import assert from 'node:assert/strict';import vm from 'node:vm';import fs from 'node:fs';
const html=fs.readFileSync(new URL('../app/assets/index.html',import.meta.url),'utf8');
const code=html.slice(html.indexOf('let modeSwitching=false;'),html.indexOf('async function syncConversationMode'));
test('menu switch waits for close, changes mode once, then opens fresh voice',async()=>{
 const calls=[];let release;const closed=new Promise(r=>release=r);const context={window:{},voiceEndPromise:closed,endConversation:()=>calls.push('end'),api:async(method,url,data)=>{calls.push([method,url,data]);return method==='GET'?{active:true}:{active:false};},Native:{conversationModeState:v=>calls.push(v)},start:async()=>calls.push('start'),guardianMode:{},$:()=>({})};vm.runInNewContext(code,context);
 const first=context.window.toggleConversationMode();await context.window.toggleConversationMode();assert.deepEqual(calls,['end']);release();await first;
 assert.deepEqual(calls.map(x=>Array.isArray(x)?x[0]:x),['end','GET','POST',false,'start']);assert.equal(calls[2][2].action,'end');assert.ok(!JSON.stringify(calls).includes('start_work'));
});
test('failed mode change keeps voice closed and surfaces error',async()=>{
 let starts=0,notice={};const context={window:{},voiceEndPromise:Promise.resolve(),endConversation(){},api:async()=>{throw Error('offline');},Native:{},start:async()=>starts++,guardianMode:null,$:()=>notice};vm.runInNewContext(code,context);await context.window.toggleConversationMode();assert.equal(starts,0);assert.match(notice.textContent,/offline/);
});
