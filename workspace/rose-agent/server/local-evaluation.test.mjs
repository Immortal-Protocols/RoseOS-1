import test from 'node:test';
import assert from 'node:assert/strict';
import {localChatRequest,localChatChunk,LocalEvaluationGateway} from './local-evaluation.mjs';

test('local model selection and endpoint stay restricted to approved loopback tests',()=>{
 assert.doesNotThrow(()=>new LocalEvaluationGateway({config:{model:'local/qwen38-27b-splash',upstreamUrl:'http://127.0.0.1:19454/v1/chat/completions'}}));
 const config={model:'local/qwen38-27b-nvfp4',upstreamUrl:'http://127.0.0.1:19451/v1/chat/completions'};
 assert.doesNotThrow(()=>new LocalEvaluationGateway({config}));
 assert.throws(()=>new LocalEvaluationGateway({config:{...config,model:'unapproved'}}));
 assert.throws(()=>new LocalEvaluationGateway({config:{...config,upstreamUrl:'https://example.com/v1/chat/completions'}}));
});

test('Splash constrains custom tools to their exact input envelope',()=>{
 const source={model:'local/qwen38-27b-splash',input:[{role:'user',content:'Read the screen.'}],
  tools:[{type:'custom',name:'exec',description:'Run the supplied program.'}],reasoning:{effort:'low'}};
 const result=localChatRequest(source,{model:source.model,upstreamModel:'qwen'});
 assert.equal(result.body.tools[0].function.strict,true);
 assert.deepEqual(result.body.tools[0].function.parameters.required,['input']);
 assert.equal(result.body.tools[0].function.parameters.additionalProperties,false);
 assert.equal(result.body.reasoning_effort,'low');
 assert.equal(source.tools[0].strict,undefined);
});

test('evaluation inputs preserve text, tool programs and screenshot pixels',()=>{
 const source={model:'local/qwen38-27b-nvfp4',input:[{role:'user',content:'Read the current screen.'}],
  tools:[{type:'custom',name:'exec',description:'Run the supplied program.'}],reasoning:{effort:'low'}};
 source.input.push({role:'user',content:[{type:'input_image',image_url:'data:image/png;base64,AQID',detail:'original'}]});
 const before=JSON.stringify(source);
 const result=localChatRequest(source,{model:source.model,upstreamModel:'qwen-local'});
 assert.equal(JSON.stringify(source),before,'source payload must not be mutated');
 assert.equal(result.body.model,'qwen-local');
 assert.equal(result.imageCount,1);
 assert.equal(result.body.messages.at(-1).content[0].image_url.url,'data:image/png;base64,AQID');
 assert.equal(result.body.chat_template_kwargs.reasoning_effort,source.reasoning.effort);
 assert.ok(result.body.tools.length>0);
 assert.ok(result.names.size>0);
});


test('vLLM reasoning deltas survive the existing transcript conversion',()=>{
 const chunk={choices:[{index:0,delta:{reasoning:'Check the red image.'}}]};
 assert.equal(localChatChunk(chunk).choices[0].delta.reasoning_content,'Check the red image.');
 assert.equal(chunk.choices[0].delta.reasoning_content,undefined);
 const legacy={choices:[{index:0,delta:{reasoning_content:'Already compatible.'}}]};
 assert.equal(localChatChunk(legacy).choices[0].delta.reasoning_content,'Already compatible.');
});


test('vLLM receives prior reasoning under its current request field',()=>{
 const source={model:'local/qwen38-27b-nvfp4',input:[
  {type:'reasoning',content:[{type:'reasoning_text',text:'Observed the route.'}]},
  {type:'function_call',call_id:'call_one',name:'lookup',arguments:'{}'}
 ],tools:[]};
 const result=localChatRequest(source,{model:source.model,upstreamModel:'qwen-local'});
 assert.equal(result.body.messages[0].reasoning,'Observed the route.');
 assert.equal(result.body.messages[0].reasoning_content,undefined);
});


test('Qwen receives one opening instruction block in the original order',()=>{
 const source={model:'local/qwen38-27b-nvfp4',input:[
 {role:'developer',content:'First instruction.'},{role:'developer',content:'Second instruction.'},
 {role:'user',content:'Current request.'}],tools:[]};
 const result=localChatRequest(source,{model:source.model,upstreamModel:'qwen-local'});
 assert.deepEqual(result.body.messages,[{role:'system',content:'First instruction.\n\nSecond instruction.'},{role:'user',content:'Current request.'}]);
 assert.equal(source.input.length,3);
});
