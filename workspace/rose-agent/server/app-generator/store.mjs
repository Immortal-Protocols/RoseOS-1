import fs from 'node:fs';
import path from 'node:path';

const packagePattern=/^(?:[A-Za-z][\w]*\.)+[\w]+$/;
export class AppGenerationStore {
 constructor(directory,{now=Date.now}={}){this.directory=directory;this.now=now;fs.mkdirSync(directory,{recursive:true,mode:0o700});this.file=path.join(directory,'queue.json');try{this.state=JSON.parse(fs.readFileSync(this.file,'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;this.state={initialized:false,enabled:true,installed:[],jobs:{}};}for(const job of Object.values(this.state.jobs))if(job.status==='running')job.status='queued';}
 save(){fs.writeFileSync(this.file+'.tmp',JSON.stringify(this.state,null,2),{mode:0o600});fs.renameSync(this.file+'.tmp',this.file);}
 scan(apps,known=new Set()){
  const clean=apps.filter(a=>packagePattern.test(a.packageName)&&typeof a.label==='string');
  const current=new Set(clean.map(a=>a.packageName)),previous=new Set(this.state.installed);
  if(this.state.initialized)for(const app of clean)if(!previous.has(app.packageName)&&!known.has(app.packageName))this.enqueue(app);
  for(const job of Object.values(this.state.jobs))if(!current.has(job.packageName)){job.status='removed';job.updatedAt=this.now();}
  this.state.installed=[...current];this.state.initialized=true;this.save();
 }
 enqueue(app){
  if(!packagePattern.test(app.packageName))throw Error('Invalid app package');
  const old=this.state.jobs[app.packageName];if(old&&['queued','running','ready'].includes(old.status))return old;
  const job={packageName:app.packageName,label:app.label||app.packageName,status:'queued',queuedAt:this.now(),updatedAt:this.now(),attempts:0};this.state.jobs[app.packageName]=job;this.save();return job;
 }
 update(pkg,patch){const job=this.state.jobs[pkg];if(!job)throw Error('Unknown generation job');Object.assign(job,patch,{updatedAt:this.now()});this.save();return job;}
 pending(){return Object.values(this.state.jobs).filter(j=>j.status==='queued').sort((a,b)=>a.queuedAt-b.queuedAt)[0];}
 packFile(pkg){if(!packagePattern.test(pkg))throw Error('Invalid app package');return path.join(this.directory,pkg+'.json');}
 pack(pkg){try{return JSON.parse(fs.readFileSync(this.packFile(pkg),'utf8'));}catch(e){if(e.code==='ENOENT')return null;throw e;}}
 publish(pack){fs.writeFileSync(this.packFile(pack.packageName)+'.tmp',JSON.stringify(pack,null,2),{mode:0o600});fs.renameSync(this.packFile(pack.packageName)+'.tmp',this.packFile(pack.packageName));}
 packs(){return Object.values(this.state.jobs).filter(j=>j.status==='ready').map(j=>this.pack(j.packageName)).filter(Boolean);}
}
