import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawn,execFile} from 'node:child_process';
import {promisify} from 'node:util';

const exec=promisify(execFile);
export const artemisToolDefinitions=[{
 type:'function',name:'artemis_phone_task',
 description:'Carry out a complete task on the paired Pixel using ARTEMIS screenshots, Android screen labels, and visual navigation. Supply the user’s intended outcome and relevant context. Returns the actual observed result and any incomplete work. Do not repeat a mutation after an uncertain outcome.',
 inputSchema:{type:'object',properties:{goal:{type:'string',description:'The complete authorized phone task, with relevant conversation context.'}},required:['goal'],additionalProperties:false}
}];

export class ArtemisTrial {
 constructor({root,privateDirectory,spawnProcess=spawn,execute=exec}){
  this.root=root;this.directory=privateDirectory;this.spawnProcess=spawnProcess;this.execute=execute;
  this.configFile=path.join(privateDirectory,'trial.json');this.active=null;this.admitting=false;this.calls=new Map();
 }
 read(){
  try{return JSON.parse(fs.readFileSync(this.configFile,'utf8'));}
  catch(error){if(error.code!=='ENOENT')throw error;return {enabled:false,ready:false};}
 }
 status(){const c=this.read();return {enabled:c.enabled===true,ready:c.ready===true,userTrialApproved:c.userTrialApproved===true,knownIssue:c.acceptanceIssue||null,running:!!this.active};}
 async checkDevice(){
  const c=this.read();
  if(!c.serial)throw Error('ARTEMIS has no verified debugging connection');
  if(!/^127\.0\.0\.1:\d{4,5}$/.test(c.serial))throw Error('Expected the private debugging bridge');
  const adb=path.join(this.root,'tools/android-sdk/platform-tools/adb');
  await this.execute(adb,['connect',c.serial],{timeout:10000});
  const {stdout}=await this.execute(adb,['-s',c.serial,'shell','getprop','ro.serialno'],{timeout:5000});
  if(stdout.trim()!=='YOUR_PIXEL_SERIAL')throw Error('ARTEMIS connection is not the paired Pixel');
  return c.serial;
 }
 async setEnabled(enabled){
  if(typeof enabled!=='boolean')throw Error('Expected enabled boolean');
  const c=this.read();
  if(enabled){if(!c.ready&&!c.userTrialApproved)throw Error('ARTEMIS trial has not passed acceptance checks');await this.checkDevice();}
  if(!enabled)this.cancel();
  fs.mkdirSync(this.directory,{recursive:true,mode:0o700});
  fs.writeFileSync(this.configFile+'.tmp',JSON.stringify({...c,enabled}),{mode:0o600});
  fs.renameSync(this.configFile+'.tmp',this.configFile);
  return this.status();
 }
 cancel(){
  const active=this.active;if(!active)return;
  active.cancelled=true;
  active.child.kill('SIGTERM');
  active.killTimer=setTimeout(()=>active.child.kill('SIGKILL'),2000);active.killTimer.unref();
 }
 async handleToolCall(request){
  if(request.tool!=='artemis_phone_task')throw Error('Unknown ARTEMIS tool');
  const key=request.threadId+':'+request.callId;
  if(this.calls.has(key))return this.calls.get(key);
  const pending=this.run(request.arguments);this.calls.set(key,pending);
  return pending;
 }
 async run(args){
  const output=value=>({success:value.status==='ok',contentItems:[{type:'inputText',text:JSON.stringify(value)}]});
  if(!this.read().enabled)return output({status:'not_executed',reason:'ARTEMIS trial is off'});
  if(!args||typeof args.goal!=='string'||!args.goal.trim()||args.goal.length>6000||Object.keys(args).some(k=>k!=='goal'))return output({status:'not_executed',reason:'Invalid goal'});
  if(this.active||this.admitting)return output({status:'not_executed',reason:'Another ARTEMIS task is running'});
  let serial;
  this.admitting=true;
  try{serial=await this.checkDevice();}catch{return output({status:'not_executed',reason:'The paired Pixel debugging connection is unavailable'});}finally{this.admitting=false;}
  if(!this.read().enabled)return output({status:'not_executed',reason:'ARTEMIS trial was switched off'});
  const id=crypto.randomUUID(),runDirectory=path.join(this.directory,'runs',id);
  fs.mkdirSync(runDirectory,{recursive:true,mode:0o700});
  const outputPath=path.join(runDirectory,'result.json');
  const log=fs.openSync(path.join(runDirectory,'runner.log'),'a',0o600);
  const python=path.join(this.root,'experiments/artemis-trial-20261019/vendor/.venv/bin/python');
  const script=path.join(this.root,'rose-agent/artemis-trial/runner.py');
  const child=this.spawnProcess(python,[script],{cwd:this.directory,stdio:['pipe',log,log],env:{...process.env,PATH:path.join(this.root,'tools/android-sdk/platform-tools')+path.delimiter+process.env.PATH}});
  fs.closeSync(log);
  const active={child,cancelled:false,timedOut:false};this.active=active;
  const timer=setTimeout(()=>{active.timedOut=true;this.cancel();},125000);timer.unref();
  try{
   child.stdin.on('error',()=>{});
   child.stdin.end(JSON.stringify({id,serial,goal:args.goal,outputPath}));
   await new Promise((resolve,reject)=>{child.once('error',reject);child.once('exit',resolve);});
   if(active.cancelled)return output({status:'not_completed',reason:active.timedOut?'ARTEMIS time limit reached':'ARTEMIS cancelled',effects:'Some actions may already have happened. Inspect before retrying.',traceId:id});
   const result=JSON.parse(fs.readFileSync(outputPath,'utf8'));
   return output({...result,traceId:id});
  }catch{return output({status:'not_completed',reason:'ARTEMIS did not return a verified result',effects:'Inspect before retrying.',traceId:id});}
  finally{clearTimeout(timer);clearTimeout(active.killTimer);if(this.active===active)this.active=null;}
 }
}
