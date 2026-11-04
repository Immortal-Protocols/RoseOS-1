#!/usr/bin/env node
// Simulated app-server protocol only. No network, credentials, inference or phone.
import {createInterface} from 'node:readline';
const send=x=>process.stdout.write(JSON.stringify(x)+'\n');
const threadId='fixture-'+process.pid;
let turn=0, current;
createInterface({input:process.stdin}).on('line',line=>{
 const m=JSON.parse(line),p=m.params||{};
 const reply=result=>send({id:m.id,result});
 const event=(method,params)=>send({method,params:{threadId,...params}});
 if(m.method==='initialize')return reply({});
 if(m.method==='account/read')return reply({account:{type:'chatgpt'}});
 if(m.method==='model/list')return reply({data:[{id:'gpt-6.1-terra',model:'gpt-6.1-terra',serviceTiers:[{id:'fast'}]}]});
 if(m.method==='thread/start'){
  if(!p.ephemeral||p.model!=='gpt-6.1-terra'||p.serviceTier!=='fast'||p.dynamicTools.some(t=>t.type!=='function'||!t.inputSchema))throw Error('wrong thread configuration');
  return reply({thread:{id:threadId},model:p.model,serviceTier:p.serviceTier});
 }
 if(m.method==='turn/start'){
  const id=current=String(++turn),text=p.input[0].text;
  event('turn/started',{turn:{id}});
  reply({turn:{id}});
  if(text==='hang')return;
  if(text==='tool')return send({id:1000,method:'item/tool/call',params:{threadId,turnId:id,callId:'c1',tool:'observe',arguments:{}}});
  finish(id,text==='fail'?'failed':'completed');
 }
 if(m.method==='turn/interrupt'){
  finish(current,'interrupted');current=null;return reply({});
 }
 if(m.method==='turn/steer')return reply({turnId:current});
 if(m.id===1000&&m.result)finish(current,'completed');
});
function finish(id,status){
 if(status==='completed'){
  send({method:'item/reasoning/textDelta',params:{threadId,turnId:id,delta:'PRIVATE_REASONING_FIXTURE'}});
  send({method:'item/agentMessage/delta',params:{threadId,turnId:id,itemId:'answer',delta:'Fixture answer.'}});
  send({method:'item/completed',params:{threadId,turnId:id,item:{type:'agentMessage',text:'Fixture answer.',phase:'final_answer'}}});
 }
 send({method:'turn/completed',params:{threadId,turn:{id,status,error:status==='failed'?{message:'Fixture failure'}:null}}});
}
