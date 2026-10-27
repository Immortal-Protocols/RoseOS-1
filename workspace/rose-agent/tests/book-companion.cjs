const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
let now=100000,seq=0;const timers=new Map(),states=[],reads=[],events=[];let stops=0,legacyPlays=0;
const c={console,Date:class extends Date{static now(){return now;}},setInterval(){},setTimeout(fn,delay){const id=++seq;timers.set(id,{fn,at:now+delay});return id;},clearTimeout:id=>timers.delete(id),ready:true,starting:false,muted:false,phoneAppContext:{sample:()=>({status:'ok',foregroundApp:{packageName:'dev.pixel.books'}})},conversation:{sendContext(){},readText:(text,options)=>reads.push({text,options}),stopReading:()=>stops++},start(){c.starting=true;},Native:{readerState:s=>states.push(JSON.parse(s)),readerPlay:()=>legacyPlays++,readerStop(){},timing:t=>events.push(t)}};c.window=c;vm.createContext(c);vm.runInContext(fs.readFileSync('rose-agent/app/assets/book-companion.js','utf8'),c);
function tick(ms){now+=ms;for(const [id,t] of [...timers])if(t.at<=now){timers.delete(id);t.fn();}}
const base={app:'Pixel Books',id:'g1184',title:'Monte Cristo',index:0,total:3,visible:true,action:'context',observedAt:now,passage:'The ship came into the harbor. Everyone watched.'};
function context(extra={}){return {...base,observedAt:now,...extra};}
assert.equal(c.receiveBookContext(context({observedAt:1})),false);
c.receiveBookContext(context());assert.equal(reads.length,0,'opening a book never starts narration');
c.receiveBookContext(context({readingRequest:{token:'button-1',kind:'read',origin:'button'},readingToken:'button-1'}));assert.equal(reads.length,1);assert.equal(legacyPlays,0,'the separate TTS player is never used');
c.bookOutputMode('speaking');c.bookMessage('assistant','The ship came');assert.equal(states.at(-1).status,'playing','the assistant does not pause itself');
c.bookOutputText('The ship came into');c.bookOutputMode('listening');tick(1500);assert.equal(states.at(-1).advance,undefined,'partial text cannot advance');
c.bookOutputText(' the harbor. Everyone watched.');tick(1200);assert.equal(states.at(-1).advance,true);const advances=states.filter(s=>s.advance).length;tick(1500);assert.equal(states.filter(s=>s.advance).length,advances,'no duplicate advance');
c.receiveBookContext(context({index:1,passage:'A second passage starts here.',readingToken:'button-1',action:'next_live'}));assert.equal(reads.length,2);
c.bookUserSpeech();assert.equal(states.at(-1).status,'paused');const before=states.filter(s=>s.advance).length;c.bookOutputText('A second passage starts here.');c.bookOutputMode('listening');tick(1500);assert.equal(states.filter(s=>s.advance).length,before,'late speech after interruption cannot advance');
c.receiveBookContext(context({index:1,passage:'A second passage starts here.',readingToken:'tool-1',readingRequest:{token:'tool-1',kind:'read',origin:'tool'}}));assert.equal(reads.length,2,'tool reading waits for task completion');assert.equal(c.bookTaskResult({state:'completed'},'delegation'),true);assert.equal(reads.length,3);assert.equal(reads.at(-1).options.delegationId,'delegation');
c.receiveBookContext(context({index:1,passage:'A second passage starts here.',readingRequest:{token:'pause-1',kind:'pause',origin:'button'}}));assert.equal(states.at(-1).status,'paused');assert.ok(stops>0);
c.receiveBookContext(context({readingRequest:{token:'stale-tool',kind:'read',origin:'tool'}}));assert.equal(c.bookTaskResult({state:'completed'},'old',{stale:true}),true);assert.equal(reads.length,3,'superseded tool never starts reading');
assert.equal(c.bookPassageCovered('On the 24th of February, 1815, the ship arrived.','On the twenty fourth of February eighteen fifteen the ship arrived.'),true);
assert.equal(c.bookPassageCovered('The ship came into the harbor. Everyone watched.','The ship arrived.'),false);
c.bookDisconnected();c.bookConnected();assert.equal(reads.length,3,'reconnecting never restarts reading');
console.log('Live reader passed: no automatic reading, one voice, verified advancement, interrupt/resume position, stale task rejection, and no legacy TTS.');
