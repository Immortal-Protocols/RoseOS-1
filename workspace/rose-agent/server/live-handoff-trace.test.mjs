import test from 'node:test';import assert from 'node:assert/strict';import {EventEmitter} from 'node:events';
import {LiveHandoffTrace} from './live-handoff-trace.mjs';
test('passive handoff trace records boundaries without content or outgoing messages',()=>{
 const sockets=[],rows=[];let sent=0;
 const trace=new LiveHandoffTrace({client:{},write:r=>rows.push(r),now:()=>5,connect:()=>{
  const s=new EventEmitter();s.socket=new EventEmitter();s.close=()=>s.closed=true;s.send=()=>sent++;sockets.push(s);return s;
 }});
 trace.attach('first');const s=sockets[0];s.socket.emit('open');
 s.emit('event',{type:'session.input_audio.append',audio:'PRIVATE_AUDIO'});
 s.emit('event',{type:'session.input_transcript.delta',delta:'PRIVATE_TEXT',start_ms:1,end_ms:2});
 s.emit('event',{type:'session.delegation.created',delegation:{id:'handoff',target:'client'},offset_ms:3});
 assert.equal(rows.length,3);assert.equal(rows[1].characters,12);assert.equal(rows[2].target,'client');
 assert.ok(!JSON.stringify(rows).includes('PRIVATE'));assert.equal(sent,0);
 trace.attach('second');assert.equal(s.closed,true);trace.close();assert.equal(sockets[1].closed,true);
});
test('observer failures and logging failures do not break the live session',()=>{
 const trace=new LiveHandoffTrace({client:{},write:()=>{throw Error('disk unavailable');},connect:()=>{throw Error('offline');}});
 assert.doesNotThrow(()=>trace.attach('session'));
});
