import test from 'node:test';
import assert from 'node:assert/strict';
import {webLookup} from './web-lookup.mjs';

test('web lookup keeps existing no-storage Luna route and returns actual source evidence',async()=>{
 let sent,opts;
 const result=await webLookup({responses:{create:async(body,options)=>{sent=body;opts=options;return {status:'completed',output_text:'Grounded finding',output:[{type:'web_search_call',action:{sources:[{url:'https://example.com/source'}]}}],usage:{total_tokens:100}};}}},{request:'a focused public lookup'});
 assert.equal(sent.model,'gpt-6-luna');assert.equal(sent.store,false);assert.equal(sent.tool_choice.type,'web_search');assert.equal(opts.maxRetries,0);assert.equal(result.text,'Grounded finding');assert.equal(result.sources[0].url,'https://example.com/source');
});

test('invalid requests and incomplete searches cannot be reported as success',async()=>{
 const client={responses:{create:async()=>({status:'incomplete'})}};
 await assert.rejects(webLookup(client,{request:''}),/focused/);
 await assert.rejects(webLookup(client,{request:'valid lookup'}),/did not complete/);
});
