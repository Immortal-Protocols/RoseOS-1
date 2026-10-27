import test from 'node:test';
import assert from 'node:assert/strict';
import {responsesToChat,chatStreamToResponses} from './protocol.mjs';
import {prepareRequest,streamAdapter} from '../baseten/tools.mjs';

test('preserves tool programs, identities, reasoning and image pixels in stateless history',()=>{
  const code='text("hello \\\"world\\\"");\ntext(2);';
  const src={model:'test',reasoning:{effort:'medium'},max_output_tokens:4096,tools:[{type:'custom',name:'exec',description:'code'}],input:[
    {role:'developer',content:[{type:'input_text',text:'instructions'}]},
    {role:'user',content:[{type:'input_text',text:'task'}]},
    {type:'reasoning',content:[{type:'reasoning_text',text:'private reasoning'}]},
    {type:'custom_tool_call',call_id:'one',name:'exec',input:code},
    {type:'custom_tool_call_output',call_id:'one',output:[{type:'input_text',text:'tool result'},{type:'input_image',image_url:'data:image/jpeg;base64,abc',detail:'original'}]},
  ]};
  const {body}=prepareRequest(src),chat=responsesToChat(body);
  assert.equal(chat.reasoning_effort,'medium');
  assert.equal(chat.messages[0].role,'system');
  assert.equal(chat.messages[2].reasoning_content,'private reasoning');
  assert.equal(JSON.parse(chat.messages[2].tool_calls[0].function.arguments).input,code);
  assert.deepEqual(chat.messages[3],{role:'tool',tool_call_id:'one',content:'tool result'});
  assert.equal(chat.messages[4].content[0].image_url.url,'data:image/jpeg;base64,abc');
  assert.equal(chat.messages[4].content[0].image_url.detail,'original');
});
test('streams exact function arguments back to Codex custom calls, with usage and terminal state',()=>{
  const s=chatStreamToResponses('test'),convert=streamAdapter(new Set(['exec']));let all=[];
  const push=v=>all.push(...s.push(v).flatMap(convert));
  const args=JSON.stringify({input:'text("hello");\n'});
  push({choices:[{index:0,delta:{reasoning_content:'private'},finish_reason:null}]});
  push({choices:[{index:0,delta:{tool_calls:[{index:0,id:'one',function:{name:'exec',arguments:args.slice(0,7)}}]}}]});
  push({choices:[{index:0,delta:{tool_calls:[{index:0,function:{arguments:args.slice(7)}}]},finish_reason:'tool_calls'}]});
  push({choices:[],usage:{prompt_tokens:100,completion_tokens:10,total_tokens:110,prompt_tokens_details:{cached_tokens:50}}});
  all.push(...s.end().flatMap(convert));
  const completed=all.at(-1);
  assert.equal(completed.type,'response.completed');
  assert.equal(completed.response.usage.input_tokens_details.cached_tokens,50);
  assert.equal(completed.response.output[1].type,'custom_tool_call');
  assert.equal(completed.response.output[1].input,'text("hello");\n');
  assert.equal(completed.response.output[1].call_id,'one');
  assert.equal(completed.response.output[0].content[0].text,'private');
});
test('does not silently accept truncation, malformed tools, or missing completion',()=>{
  const missing=chatStreamToResponses('test');missing.push({choices:[]});assert.throws(()=>missing.end(),/finish_reason/);
  const short=chatStreamToResponses('test');short.push({choices:[{index:0,delta:{content:'part'},finish_reason:'length'}]});
  assert.equal(short.end().at(-1).type,'response.incomplete');
  const malformed=chatStreamToResponses('test');malformed.push({choices:[{index:0,delta:{tool_calls:[{index:0,id:'bad',function:{name:'exec',arguments:'invalid'}}]},finish_reason:'tool_calls'}]});
  assert.throws(()=>malformed.end());
  assert.throws(()=>responsesToChat({input:[{type:'unknown'}]}),/Unsupported/);
});

test('namespaced tools and their history preserve names, programs and call identity',()=>{
 const source={tools:[{type:'namespace',name:'browser',tools:[{type:'custom',name:'js',description:'Execute code'}]}],input:[{type:'custom_tool_call',namespace:'browser',name:'js',call_id:'same-call',input:'await page.title()'}]};
 const {body,names,aliases}=prepareRequest(source);
 assert.equal(body.tools[0].name,'browser__js');
 assert.equal(body.input[0].name,'browser__js');
 assert.equal(JSON.parse(body.input[0].arguments).input,'await page.title()');
 const restore=streamAdapter(names,aliases);
 const event=restore({type:'response.output_item.done',item:{type:'function_call',id:'item',call_id:'same-call',name:'browser__js',arguments:JSON.stringify({input:'await page.title()'})}})[0];
 assert.equal(event.item.namespace,'browser');assert.equal(event.item.name,'js');assert.equal(event.item.call_id,'same-call');assert.equal(event.item.input,'await page.title()');
 assert.throws(()=>prepareRequest({tools:[...source.tools,{type:'function',name:'browser__js'}]}),/Ambiguous/);
});

test('a tool-turn message is progress and only a stopped completion is final',()=>{
 for(const [finish,phase] of [['tool_calls','commentary'],['stop','final_answer']]){
  const stream=chatStreamToResponses('test');stream.push({choices:[{index:0,delta:{content:'Working'},finish_reason:finish}]});
  const message=stream.end().find(e=>e.type==='response.output_item.done').item;
  assert.equal(message.phase,phase);
 }
});

test('previous native searches retain their metadata without fabricating source content',()=>{
 const old={type:'web_search_call',status:'completed',action:{query:'weather'}};
 const chat=responsesToChat({input:[old,{role:'assistant',content:'Earlier answer'}]});
 assert.match(chat.messages[0].content,/metadata alone is not source evidence/);
 assert.match(chat.messages[0].content,/weather/);assert.equal(chat.messages[1].content,'Earlier answer');
});

test('Cerebras parsed reasoning is preserved separately from the answer',()=>{
 const s=chatStreamToResponses('qwen-3.8-27b');s.push({choices:[{index:0,delta:{reasoning:'private reasoning',content:'answer'},finish_reason:'stop'}]});const done=s.end().at(-1);assert.equal(done.response.output[0].content[0].text,'private reasoning');assert.equal(done.response.output[1].content[0].text,'answer');
});
test('Qwen receives trusted instructions first without elevating observations',()=>{
 const c=responsesToChat({input:[{role:'developer',content:'first rule'},{role:'user',content:'task'},{role:'developer',content:'second rule'},{role:'assistant',content:'prior answer'}]});
 assert.deepEqual(c.messages.map(m=>m.role),['system','user','assistant']);
 assert.equal(c.messages[0].content,'first rule\n\nsecond rule');assert.equal(c.messages[1].content,'task');
});
