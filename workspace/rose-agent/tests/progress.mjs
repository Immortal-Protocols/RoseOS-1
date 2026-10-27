import assert from 'node:assert/strict';
import {SpokenProgress} from '../server/progress.mjs';
let time=0,out=[];const p=new SpokenProgress(x=>out.push(x),{now:()=>time});
p.start();p.update('Building');assert.equal(out.length,0);
time=15000;p.tick();assert.match(out[0],/Building/);
p.update('Installing');p.finish();time=90000;p.tick();assert.equal(out.length,1);
p.start();time+=30000;p.tick();assert.match(out[1],/no new result/);
p.finish();time+=60000;p.tick();assert.equal(out.length,2);
console.log('Progress coalescing, heartbeat, and terminal cleanup passed');
