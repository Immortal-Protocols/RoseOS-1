import test from 'node:test';
import assert from 'node:assert/strict';
import {withCompletionRetry} from './checker-completion-retry.mjs';

test('one recheck retains the same evidence, request, model route and cancellation signal',async()=>{
 const evidence=Object.freeze({images:Object.freeze(['saved screen']),records:Object.freeze(['entered coordinates'])});
 const route=Object.freeze({model:'gpt-6-luna',effort:'low',serviceTier:'default'});
 const signal=new AbortController().signal;
 const args=Object.freeze({verifiedAnswer:true,request:'Find the route',evidence,route,signal});
 const calls=[];
 const run=withCompletionRetry(async a=>{calls.push(a);return calls.length===1?{status:'complete',verifiedAnswer:'  '}:{status:'complete',verifiedAnswer:'4 minutes, 1.1 miles'};});
 assert.equal((await run(args)).verifiedAnswer,'4 minutes, 1.1 miles');
 assert.equal(calls.length,2);
 for(const a of calls){assert.equal(a.evidence,evidence);assert.equal(a.request,args.request);assert.equal(a.route,route);assert.equal(a.signal,signal);}
 assert.equal(calls[1].reviewRepair,true);
});
test('valid approval, ordinary approval-only reviews, and non-complete verdicts are not retried',async()=>{
 for(const [args,result] of [[{verifiedAnswer:true},{status:'complete',verifiedAnswer:'answer'}],[{},{status:'complete'}],[{verifiedAnswer:true},{status:'continue',verifiedAnswer:''}],[{verifiedAnswer:true},{status:'blocked',verifiedAnswer:''}]]){
  let calls=0;assert.equal(await withCompletionRetry(async()=>{calls++;return result;})(args),result);assert.equal(calls,1);
 }
});
test('the second review can reject completion without forcing approval',async()=>{
 let calls=0;
 const result=await withCompletionRetry(async()=>++calls===1?{status:'complete',verifiedAnswer:''}:{status:'continue',nextSteps:['Verify the destination'],verifiedAnswer:''})({verifiedAnswer:true});
 assert.equal(result.status,'continue');assert.equal(calls,2);
});
test('a second invalid completion stops after exactly two calls',async()=>{
 let calls=0;
 await assert.rejects(withCompletionRetry(async()=>{calls++;return {status:'complete',verifiedAnswer:''};})({verifiedAnswer:true}),/after one recheck/);
 assert.equal(calls,2);
});
test('provider errors are not retried',async()=>{
 let calls=0;await assert.rejects(withCompletionRetry(async()=>{calls++;throw Error('provider error');})({verifiedAnswer:true}),/provider error/);assert.equal(calls,1);
});
test('cancellation prevents the recheck',async()=>{
 const controller=new AbortController();let calls=0;
 await assert.rejects(withCompletionRetry(async()=>{calls++;controller.abort();return {status:'complete',verifiedAnswer:''};})({verifiedAnswer:true,signal:controller.signal}),/abort/i);assert.equal(calls,1);
});
