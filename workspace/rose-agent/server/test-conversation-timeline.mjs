import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {ConversationTimeline} from './conversation-timeline.mjs';
const dir=fs.mkdtempSync(path.join(os.tmpdir(),'pixel-conversation-'));
try{
 const file=path.join(dir,'timeline.json');
 const t=new ConversationTimeline(file,[{role:'user',text:'My spoken clue is blue.'},{role:'assistant',text:'Blue.'}]);
 const prompt=t.input('What was my spoken clue?','typed-one');
 assert.match(prompt,/spoken clue is blue/);assert.equal(t.messages.at(-1).source,'text');
 t.add('assistant','Your clue was blue.','text','reply-one');
 const voiceHistory=t.recent();assert.equal(voiceHistory.at(-1).text,'Your clue was blue.');
 assert.equal(voiceHistory.at(-2).text,'What was my spoken clue?');
 const count=t.messages.length;t.add('assistant','Duplicate','text','reply-one');assert.equal(t.messages.length,count);
 assert.equal(t.snapshot(t.revision).unchanged,true);assert.equal(t.snapshot(-1).messages.length,count);
 t.add('assistant','A long passage read from the book.','book_narration','narration-one');
 assert.equal(t.recent().some(m=>m.text==='A long passage read from the book.'),false,'narration is not restored as conversational behavior');
 assert.equal(t.snapshot(-1).messages.some(m=>m.id==='narration-one'),true,'the transcript still retains narration');
 const restored=new ConversationTimeline(file);assert.deepEqual(restored.messages,t.messages);
 for(let i=0;i<120;i++)t.add('user','x'.repeat(2000),'text');
 assert.ok(t.messages.length<=100);assert.ok(t.messages.reduce((n,m)=>n+m.text.length,0)<=80000);
 assert.ok(t.recent().reduce((n,m)=>n+m.text.length,0)<=16000);
 assert.equal(fs.statSync(file).mode&0o777,0o600);
 console.log('Shared text/voice context, persistence, duplicate handling, bounds, and file privacy passed.');
}finally{fs.rmSync(dir,{recursive:true,force:true});}
