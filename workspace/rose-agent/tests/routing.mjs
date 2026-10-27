import assert from 'node:assert/strict';
import {chooseRoute,ROUTES} from '../server/routing.mjs';
import {Codex} from '../server/codex.mjs';
const mock=a=>({decisions:{create:async()=>({answers:[a]})}});
assert.equal((await chooseRoute(mock({name:'route',type:'choice',choice:'quick'}),'weather')).model,'gpt-6-luna');
for(const a of [{type:'refusal'}, {name:'route',type:'choice',choice:'unknown'}])assert.equal((await chooseRoute(mock(a),'build app')).model,'gpt-6-luna');
assert.equal((await chooseRoute({decisions:{create:async()=>{throw {status:403}}}},'weather')).source,'fixed-luna');
const c=new Codex('/tmp','/tmp/unused');c.thread='test';let calls=[];c.rpc=async(method,params)=>{calls.push({method,params});return {turn:{id:'turn'}}};
await c.submit('weather',ROUTES.quick);assert.equal(calls[0].params.model,'gpt-6-luna');assert.equal(calls[0].params.effort,'medium');assert.equal(calls[0].params.serviceTierForTurn,'priority');
await c.submit('correction',ROUTES.deep);assert.equal(calls[1].method,'turn/steer');assert.equal(calls[1].params.model,undefined);
c.active=null;await c.submit('build app',ROUTES.deep);assert.equal(calls[2].params.model,'gpt-6-luna');assert.equal(calls[2].params.effort,'medium');
console.log('PASS: quick/deep routing, failure fallback, explicit per-turn model, active-turn continuity');

let classified=false;
await chooseRoute({decisions:{create:async()=>{classified=true;throw Error('must not call');}}},'build complex software');
assert.equal(classified,false);
c.active=null;await c.submit('default');assert.equal(calls.at(-1).params.model,'gpt-6-luna');assert.equal(calls.at(-1).params.serviceTierForTurn,'priority');
console.log('PASS: Luna-only mode skips classifier, covers complex tasks and default submit');

const sol=await chooseRoute({decisions:{create:async()=>{throw Error('Classifier must not run');}}},'Open Clock',{enabled:true,taskAgent:'sol-low-ultrafast'});
assert.equal(sol.model,'gpt-6.1-sol');assert.equal(sol.effort,'low');assert.equal(sol.serviceTier,'ultrafast');assert.equal(sol.route,'deep');assert.equal(sol.routingMs,0);
c.active=null;await c.submit('trial',sol);assert.equal(calls.at(-1).params.model,'gpt-6.1-sol');assert.equal(calls.at(-1).params.effort,'low');assert.equal(calls.at(-1).params.serviceTierForTurn,'ultrafast');
console.log('PASS: Sol trial bypasses classifier and passes low/ultrafast to the task runner');

const directLuna=await chooseRoute({decisions:{create:async()=>{throw Error('Classifier must not run');}}},'Open Clock',{enabled:true,taskAgent:'luna-medium-fast'});
assert.equal(directLuna.model,'gpt-6-luna');assert.equal(directLuna.effort,'medium');assert.equal(directLuna.serviceTier,'priority');assert.equal(directLuna.source,'direct-luna-trial');
c.active=null;await c.submit('Luna screenshot trial',directLuna);assert.equal(calls.at(-1).params.model,'gpt-6-luna');assert.equal(calls.at(-1).params.effort,'medium');assert.equal(calls.at(-1).params.serviceTierForTurn,'priority');
console.log('PASS: direct Luna medium/Fast bypasses classifier');
