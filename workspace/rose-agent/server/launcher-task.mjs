import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

export function validTaskTitle(value){
 if(typeof value!=='string'||value.length>80||/[\r\n]|\.{3}|…/.test(value))return false;
 const words=value.trim().split(/\s+/);return words.length>=3&&words.length<=5;
}

// A read-only projection of the single worker's lifecycle. Never starts,
// cancels, retries, grades or otherwise controls work.
export class LauncherTask {
 constructor(file,{now=Date.now,onChange=()=>{}}={}){
  this.file=file;this.now=now;this.onChange=onChange;this.current=null;this.writing=null;this.dirty=false;
  try{const saved=JSON.parse(fs.readFileSync(file,'utf8'));if(saved.version===1&&saved.current?.id)this.current=saved.current;}catch{}
  if(this.current&&!validTaskTitle(this.current.title))this.current.title='Working on your request';
  if(this.active())this.finish('interrupted','The backend restarted. The previous outcome is unknown.');
  if(this.current?.state==='completed'&&!this.current.widget&&!this.current.visualization&&this.current.widgetStatus!=='unavailable'){this.current.widgetStatus='unavailable';this.save();}
 }
 active(){return !!this.current&&['running','checking','stopping'].includes(this.current.state);}
 // Accepts worker output only before an owner Stop; afterwards the card shows Stopping.
 accepting(){return this.active()&&this.current.state!=='stopping';}
 snapshot(){return {version:1,current:this.current?{...this.current}:null};}
 start(request,presentation='native'){
  const text=String(request||'').trim().slice(0,12000);
  this.current={id:crypto.randomUUID(),request:text,presentation,title:'Working on your request',state:'running',progress:'',activity:'',result:'',startedAt:this.now(),updatedAt:this.now(),finishedAt:null};
  this.save();
 }
 setTitle(id,title){
  if(this.current?.id!==id)return {status:'not_applied',reason:'That task is no longer displayed'};
  if(!validTaskTitle(title))return {status:'not_applied',reason:'Title must be 3 to 5 words, at most 80 characters, without line breaks or ellipses'};
  this.current.title=title.trim().replace(/\s+/g,' ');this.current.titleReadyAt=this.now();this.touch();
  return {status:'updated',title:this.current.title};
 }
 setWidget(id,sourceHash,widget){
  const task=this.current;
  if(task?.id!==id||task.state!=='completed'||crypto.createHash('sha256').update(task.result).digest('hex')!==sourceHash)return {status:'not_applied'};
  task.widget=widget;task.widgetStatus='ready';task.widgetReadyAt=this.now();this.touch();return {status:'updated'};
 }
 setWidgetStatus(id,sourceHash,status){
  const task=this.current;
  if(!['preparing','unavailable'].includes(status)||task?.id!==id||task.state!=='completed'||task.widget||task.visualization||crypto.createHash('sha256').update(task.result).digest('hex')!==sourceHash)return {status:'not_applied'};
  task.widgetStatus=status;this.touch();return {status:'updated'};
 }
 setVisualization(id,html){
  if(this.current?.id!==id||!this.accepting()||this.current.presentation!=='html')return {status:'not_applied',reason:'No matching active HTML task'};
  this.current.visualization={type:'html',html};this.touch();return {status:'prepared',shown:'after task completion'};
 }
 followup(text){if(this.active()){this.current.latestInstruction=String(text).trim().slice(0,12000);this.touch();}}
 progress(text){if(this.accepting()){this.current.progress=String(text||'').trim().slice(0,3000);this.touch();}}
 activity(text){if(this.accepting()){this.current.activity=String(text||'').trim().slice(0,300);this.touch();}}
 result(text){if(this.accepting()){this.current.result=String(text||'').trim().slice(0,12000);this.touch();}}
 checking(){if(this.accepting()){this.current.state='checking';this.touch();}}
 stopping({at=this.now(),source='unknown'}={}){if(this.active()){this.current.state='stopping';this.current.activity='';this.current.stopRequestedAt=at;this.current.stopSource=source;this.touch();}}
 running(){if(this.accepting()){delete this.current.visualization;this.current.state='running';this.touch();}}
 finish(state,result){
  if(!this.active())return;
  if(!['completed','failed','interrupted'].includes(state))throw Error('Invalid terminal task state');
  this.current.state=state;this.current.activity='';if(result)this.current.result=String(result).slice(0,12000);
  this.current.finishedAt=this.now();this.touch();
 }
 touch(){this.current.updatedAt=this.now();this.save();}
 save(){
  try{this.onChange(this.snapshot());}catch{/* Display transport cannot affect work. */}
  this.dirty=true;if(this.writing)return;
  this.writing=Promise.resolve().then(async()=>{
   while(this.dirty){
    this.dirty=false;const text=JSON.stringify(this.snapshot());
    await fs.promises.mkdir(path.dirname(this.file),{recursive:true,mode:0o700});
    await fs.promises.writeFile(this.file+'.tmp',text,{mode:0o600});
    await fs.promises.rename(this.file+'.tmp',this.file);
   }
  }).catch(()=>{/* Display persistence must never fail or retry the user's task. */}).finally(()=>{this.writing=null;});
 }
 async flush(){while(this.writing)await this.writing;}
}
