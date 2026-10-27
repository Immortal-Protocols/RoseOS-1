(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.PhoneCommandDelivery=factory();})(typeof globalThis==='object'?globalThis:this,function(){
 'use strict';
 class Delivery {
  constructor({api,execute,now=Date.now,sleep=ms=>new Promise(r=>setTimeout(r,ms)),schedule=(fn,ms)=>setTimeout(fn,ms),cancel=id=>clearTimeout(id),report=()=>{}}){
   Object.assign(this,{api,execute,now,sleep,schedule,cancel,report});this.queue=[];this.known=new Map();this.epoch=null;this.connected=false;this.running=false;this.started=false;this.pollTimer=null;
  }
  start(){if(this.started)return;this.started=true;this.poll();}
  stop(){this.started=false;this.cancel(this.pollTimer);this.pollTimer=null;}
  status(){return {connected:this.connected,epoch:this.epoch,queued:this.queue.length,running:this.running};}
  receive(event){
   if(event.type==='transport'){
    this.connected=event.connected===true;
    if(!this.connected&&this.started){this.cancel(this.pollTimer);this.pollTimer=this.schedule(()=>this.poll(),0);}
    return;
   }
   if(!['hello','commands'].includes(event.type))return;
   this.connected=true;this.accept(event,'push');
  }
  accept(event,channel){
   if(typeof event.epoch!=='string'||!Array.isArray(event.commands))return;
   // Ignore a stale poll that finishes after a new stream has connected.
   if(channel==='poll'&&this.connected&&this.epoch&&event.epoch!==this.epoch)return;
   if(this.epoch!==event.epoch){this.epoch=event.epoch;this.queue=[];this.known.clear();}
   for(const command of event.commands){
    if(!command||typeof command.id!=='string'||this.known.has(command.id))continue;
    const item={command,epoch:this.epoch,channel,receivedAt:this.now()};this.known.set(command.id,item);this.queue.push(item);
   }
   this.drain();
  }
  async poll(){
   if(!this.started)return;
   // Only one HTTP poll at a time, including when the stream drops mid-poll.
   if(this.polling)return;
   this.polling=true;
   try{this.accept(await this.api('GET','/phone/commands'),'poll');}catch{}finally{
    this.polling=false;if(this.started){this.cancel(this.pollTimer);this.pollTimer=this.schedule(()=>this.poll(),this.connected?5000:800);}
   }
  }
  async drain(){
   if(this.running)return;this.running=true;
   try{while(this.queue.length){
    const item=this.queue.shift(),c=item.command;if(item.epoch!==this.epoch)continue;
    const startedAt=this.now();let result;
    try{
     // Native also keeps a durable result cache. 'running' means query that
     // operation again, not start a second action or advance to another one.
     do{result=await this.execute(c);if(result.status==='running')await this.sleep(200);}
     while(result.status==='running'&&item.epoch===this.epoch&&this.now()<c.expires+5000);
     if(result.status==='running')result={status:'error',reason:'Command completion timed out; not repeated'};
    }catch(e){result={status:'error',reason:String(e.message||e)};}
    const finishedAt=this.now();let delay=200;
    // Retry delivery of the same result; never rerun a tap after a lost reply.
    while(item.epoch===this.epoch){
     try{await this.api('POST','/phone/commands/'+c.id,{...result,_delivery:{channel:item.channel,receivedToStartMs:startedAt-item.receivedAt,executionMs:finishedAt-startedAt}});break;}
     catch(e){if(e.status===400||e.status===404||this.now()>c.expires+5000)break;await this.sleep(delay);delay=Math.min(2000,delay*2);}
    }
    item.done=true;this.report({id:c.id,channel:item.channel,executionMs:finishedAt-startedAt});
    if(this.known.size>300)for(const [id,entry]of this.known){if(entry.done)this.known.delete(id);if(this.known.size<=200)break;}
   }}finally{this.running=false;}
  }
 }
 return Delivery;
});
