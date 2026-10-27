import {researchBookmark} from './research.mjs';

export class BookmarkResearch {
 constructor({store,client,sendText,notify=()=>{},research=researchBookmark}){Object.assign(this,{store,client,sendText,notify,research});this.active=null;}
 start(){this.timer=setInterval(()=>this.tick(),1500);this.timer.unref();this.tick();}
 stop(){clearInterval(this.timer);}
 accept(data,deviceId){const r=this.store.accept(data,deviceId);queueMicrotask(()=>this.tick());return r;}
 async tick(){if(this.active||!this.store.state.enabled)return;const job=this.store.next();if(!job)return;this.active=job;const started=Date.now();
  try{
   this.store.update(job,{status:'researching',attempts:job.attempts+1});
   const report=await this.research(this.client,job);this.store.report(job,report);this.store.update(job,{status:'researched',elapsedMs:Date.now()-started});
   if(!this.store.state.chatId)throw Error('Research complete; text recipient is not configured');
   // Commit before attempting the external send. A timeout or process crash
   // must not silently send the same long report twice.
   this.store.update(job,{status:'sending'});
   const delivery=await this.sendText({chatId:this.store.state.chatId,text:'Bookmark research\n\n'+report.text,requestId:job.id});
   if(delivery.status!=='sent'&&delivery.status!=='ok')throw Object.assign(Error(delivery.error||'Text delivery could not be confirmed'),{uncertain:true});
   this.store.update(job,{status:'complete',delivery,elapsedMs:Date.now()-started});this.notify({type:'bookmark_research_done',id:job.id});
  }catch(e){const status=job.status==='sending'?'delivery_uncertain':'failed';this.store.update(job,{status,error:String(e.message).slice(0,400),elapsedMs:Date.now()-started});this.notify({type:'bookmark_research_failed',id:job.id,text:'Bookmark research: '+String(e.message).slice(0,200)});}
  finally{this.active=null;queueMicrotask(()=>this.tick());}
 }
}
