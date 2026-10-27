import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {TranscriptHistory} from '../server/transcripts.mjs';
import {restoredConversation,voiceSession} from '../server/reconnect.mjs';
const dir=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-reconnect-'));
try {
 const file=path.join(dir,'history.json');let h=new TranscriptHistory(file);
 h.start('before-loss');h.append('user','My project is called ');h.append('user','Marigold.');h.append('assistant','We are discussing Marigold.');
 h=new TranscriptHistory(file);h.start('empty-prepared-session');
 const session=voiceSession('Test',h.read());
 assert.equal(session.store,false);assert.equal(session.input.length,2);
 assert.equal(session.input[0].content[0].text,'My project is called Marigold.');
 assert.equal(session.input[1].role,'assistant');
 h.append('user','What is its name?');
 assert.equal(restoredConversation(h.read()).length,3);
 assert.equal(restoredConversation([{messages:[{role:'developer',text:'bad'},{role:'user',text:'ok'}]}]).length,1);
 assert.deepEqual(restoredConversation(null),[]);
 const bounded=restoredConversation([{messages:Array.from({length:80},(_,i)=>({role:'user',text:`${i}:`+'x'.repeat(1000)}))}]);
 assert.ok(bounded.length<=40);assert.ok(bounded.reduce((n,m)=>n+m.text.length,0)<=16000);
 assert.ok(bounded.at(-1).text.startsWith('79:'));
 assert.ok(session.instructions.includes('Do not repeat completed actions'));
 console.log('Reconnect tests passed: restart, empty sessions, roles, bounds, no replay.');
}finally{fs.rmSync(dir,{recursive:true,force:true});}
