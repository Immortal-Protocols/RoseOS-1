// Coalesce progress; terminal results always supersede queued updates.
export class SpokenProgress {
 constructor(send,{now=Date.now,gap=15000,quiet=30000}={}){this.send=send;this.now=now;this.gap=gap;this.quiet=quiet;this.active=false;this.pending=null;this.last=0;}
 start(){this.active=true;this.pending=null;this.last=this.now();}
 update(text){if(!this.active)return;this.pending=String(text).slice(0,1000);this.tick(false);}
 tick(heartbeat=true){if(!this.active)return;const elapsed=this.now()-this.last;if(this.pending&&elapsed>=this.gap){this.send('Brief progress update; work is not finished: '+this.pending);this.pending=null;this.last=this.now();}else if(heartbeat&&elapsed>=this.quiet){this.send('Brief status update: the backend task is still running, but no new result is available yet. Do not claim a particular stage or completion.');this.last=this.now();}}
 finish(){this.active=false;this.pending=null;}
}
