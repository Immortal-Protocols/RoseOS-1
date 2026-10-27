const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const html=fs.readFileSync(__dirname+'/../app/assets/index.html','utf8');
const source=html.slice(html.indexOf('async function runTask(params)'),html.indexOf('\nfunction showGuardian()',html.indexOf('async function runTask(params)')));
test('task polling forwards changed progress once and returns the final result separately',async()=>{
 const responses=[{state:'running',progress:'Opened Messages'},{state:'running',progress:'Opened Messages'},
  {state:'running',progress:'Found the conversation'},{state:'completed',progress:'Found the conversation',text:'Scrolled successfully'}];
 const sent=[],timing=[],requests=[];
 const context={crypto:{randomUUID:()=> 'test-task'},waitingTasks:new Set(),completedTasks:new Set(),
  phoneAppContext:{captureRequest:()=>({status:'unavailable',observedAt:1})},
  voiceTiming:{record:s=>timing.push(s)},$:()=>({textContent:''}),Date,JSON,Promise,
  setTimeout:resolve=>resolve(),api:async(method,path,body)=>{requests.push({method,path,body});return responses.shift();}};
 vm.createContext(context);vm.runInContext(source,context);
 const result=JSON.parse(await context.runTask({request:'Open Messages',onProgress:text=>sent.push(text)}));
 assert.deepEqual(sent,['Opened Messages','Found the conversation']);assert.equal(result.text,'Scrolled successfully');
 assert.equal(timing.filter(s=>s==='task_progress_forwarded').length,2);
 assert.equal(context.waitingTasks.size,0);assert.ok(context.completedTasks.has('test-task'));
 assert.deepEqual(JSON.parse(JSON.stringify(requests[0].body)),{id:'test-task',request:'Open Messages',appContext:{status:'unavailable',observedAt:1}});
});
test('task handoff carries ordered voice context and app snapshot without waiting for transcript relay',async()=>{
 const requests=[],voiceContext={delegationId:'handoff',previousDelegationId:null,hasNewUserSpeech:true,messages:[{role:'user',text:'Send the tweet'},{role:'assistant',text:'Copy'},{role:'user',text:' to Nathan'}]};
 const appContext={status:'ok',packageName:'app.current'};
 const context={crypto:{randomUUID:()=> 'context-task'},waitingTasks:new Set(),completedTasks:new Set(),phoneAppContext:{captureRequest:()=>assert.fail('must preserve the supplied app snapshot')},voiceTiming:null,$:()=>({textContent:''}),Date,JSON,Promise,setTimeout:resolve=>resolve(),api:async(method,path,body)=>{requests.push({method,path,body});return {state:'completed',text:'Test result'};}};
 vm.createContext(context);vm.runInContext(source,context);
 await context.runTask({request:'to Nathan',voiceContext,appContext});
 assert.equal(requests[0].body.voiceContext,voiceContext);
 assert.equal(requests[0].body.appContext,appContext);
 assert.equal(requests[0].body.request,'to Nathan');
});
