// Uses the existing ten-second client diagnostics; no new request loop.
// Reclaim only when the user asks to start a call, never while an idle call runs.
export class SessionLease {
 constructor(now=Date.now,timeoutMs=45000){this.now=now;this.timeoutMs=timeoutMs;this.id=null;this.seenAt=0;}
 start(id){this.id=id;this.seenAt=this.now();}
 touch(id){if(id!==this.id)return false;this.seenAt=this.now();return true;}
 observe(id,event){
  if(event?.ready!==true||event.connection!=='connected')return false;
  if(event.sessionId&&event.sessionId!==id)return false;
  return this.touch(id);
 }
 expired(id){return id===this.id&&this.now()-this.seenAt>=this.timeoutMs;}
 clear(id){if(id===this.id){this.id=null;this.seenAt=0;}}
}
