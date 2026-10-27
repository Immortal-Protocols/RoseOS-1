import {createAppGenerator} from './generator.mjs';

export class AppGenerationDriver {
 constructor({store,phone,client,route,currentRequest=()=>'',canStart,knownPackages,log=()=>{},now=Date.now}){
  Object.assign(this,{store,phone,route,canStart,knownPackages,log,now});this.generator=createAppGenerator({client,phone,route,currentRequest,checkpoint:pack=>store.publish(pack),log});this.active=null;this.scanning=false;this.notBefore=0;
 }
 status(){return {enabled:this.store.state.enabled,initialized:this.store.state.initialized,running:this.active?.packageName||null,jobs:Object.values(this.store.state.jobs).map(({packageName,label,status,reason,attempts,updatedAt})=>({packageName,label,status,reason,attempts,updatedAt}))};}
 async tick(){
  if(this.scanning||this.active||!this.canStart())return;
  this.scanning=true;
  try{
   const apps=await this.phone({action:'list_apps'});if(apps.status!=='ok')return;
   this.store.scan(apps.apps||[],this.knownPackages());
   if(!this.store.state.enabled||this.now()<this.notBefore||!this.canStart())return;
   const job=this.store.pending();if(job)this.start(job);
  }catch(error){this.log({stage:'generator_scan_error',reason:error.message});}finally{this.scanning=false;}
 }
 start(job){
  if(this.active)throw Error('Another app is being generated');
  const resumePack=job.attempts>0&&job.reason!=='Explicitly retried'?this.store.pack(job.packageName):null;const controller=new AbortController();this.store.update(job.packageName,{status:'running',reason:'Exploring and verifying exposed app controls',attempts:job.attempts+1});
  const active={packageName:job.packageName,controller,promise:null};this.active=active;
  active.promise=(async()=>{
   try{
    let result;
    const capabilities=await this.phone({action:'app_command',packageName:job.packageName,operation:'capabilities'});
    if(controller.signal.aborted)throw Error('Interrupted');
    if(capabilities.status==='ok'&&Array.isArray(capabilities.tools)&&capabilities.tools.length&&capabilities.tools.every(t=>/^[a-z][a-z0-9_]{0,63}$/.test(t.name)&&/^[a-z][a-z0-9_]{0,63}$/.test(t.operation)&&t.inputSchema?.type==='object'&&typeof t.description==='string')){
     result={status:'ready',reason:'Discovered the installed app’s signature-verified native tool catalog',pack:{packageName:job.packageName,label:job.label,createdAt:this.now(),source:'native',tools:capabilities.tools}};
    }else result=await this.generator.generate(job,controller.signal,resumePack);
    if(controller.signal.aborted)throw Error('Interrupted');
    if(result.status==='ready'){this.store.publish(result.pack);this.store.update(job.packageName,{status:'ready',reason:result.reason,toolCount:result.pack.tools.length});}
    else this.store.update(job.packageName,{status:result.status,reason:result.reason});
    this.log({stage:'generator_finished',packageName:job.packageName,status:result.status,toolCount:result.pack.tools.length});
   }catch(error){
    this.store.update(job.packageName,{status:controller.signal.aborted?'queued':error.code==='needs_input'?'needs_input':'failed',reason:controller.signal.aborted?'Interrupted for a user request':error.message});
    this.log({stage:'generator_finished',packageName:job.packageName,status:controller.signal.aborted?'interrupted':error.code==='needs_input'?'needs_input':'failed',reason:error.message});
   }finally{if(this.active===active)this.active=null;}
  })();
 }
 async interrupt(){this.notBefore=this.now()+15000;const active=this.active;if(active){active.controller.abort();await active.promise;}}
 async control({action,packageName}){
  if(action==='pause'){this.store.state.enabled=false;this.store.save();await this.interrupt();}
  else if(action==='resume'){this.store.state.enabled=true;this.store.save();this.notBefore=0;}
  else if(action==='retry'){
   const apps=await this.phone({action:'list_apps'});const app=apps.apps?.find(a=>a.packageName===packageName);if(!app)throw Error('App is not installed');
   const old=this.store.state.jobs[packageName];if(old?.status==='running')throw Error('App generation is already running');
   if(old)this.store.update(packageName,{status:'queued',reason:'Explicitly retried'});else this.store.enqueue(app);
  }else if(action!=='status')throw Error('Unknown generation control');
  return {status:'ok',...this.status()};
 }
}

export function generationAdapter(store,getDriver){
 const published=store.packs().filter(p=>p.source==='verified-ui-workflows').flatMap(pack=>pack.tools.filter(t=>!t.currentScreenOnly&&(t.operation||t.testedAt||t.name==='state')).map(t=>({pack,tool:t,name:('app_'+pack.packageName.replace(/[^a-z0-9]/gi,'_')+'_'+t.name).slice(0,64)})));
 const direct=new Map(published.map(p=>[p.name,p]));
 const spec=(name,description,properties={},required=[])=>({name,description,foregroundRequired:false,inputSchema:{type:'object',properties,required,additionalProperties:false}});
 return {packageName:'dev.pixel.voice',tools:[
  spec('generated_app_tools','List complete verified app operations. Unverified individual controls are private discovery data and are not callable tools. Call with a packageName to get that app’s operation names, descriptions and inputs; omit it to list available apps. New apps may still be queued or require sign-in.',{packageName:{type:'string'}}),
  spec('generated_app_run','Execute an explicitly user-requested generated app operation using its returned tool name and JSON arguments. Opens the app and verifies the result. Do not invent operations. Unsupported states return an error and use the normal visual fallback.',{packageName:{type:'string'},toolName:{type:'string'},argumentsJson:{type:'string'}},['packageName','toolName']),
  spec('app_generation_control','Read, pause or resume automatic fast-tool generation for new app installs. Retry one installed app after sign-in or an exploration blocker. Pause when the user asks to stop generating/exploring apps.',{action:{type:'string',enum:['status','pause','resume','retry']},packageName:{type:'string'}},['action'])
 ,...published.map(({pack,tool,name})=>spec(name,pack.label+': '+tool.description,tool.inputSchema?.properties||(tool.steps?.some(s=>s.kind==='search')?{query:{type:'string'}}:{}),tool.inputSchema?.required||(tool.steps?.some(s=>s.kind==='search')?['query']:[])))],async execute(name,args={}){
  const driver=getDriver();
  if(direct.has(name)){const {pack,tool}=direct.get(name);try{return await driver.generator.runtime.execute(store.pack(pack.packageName),tool.name,args);}catch(error){return {status:'needs_assistance',reason:error.message};}}
  if(name==='app_generation_control')return driver.control(args);
  if(name==='generated_app_tools'){
   if(!args.packageName)return {status:'ok',apps:store.packs().map(p=>({packageName:p.packageName,label:p.label,toolCount:p.tools.length})),generation:driver.status()};
   const pack=store.pack(args.packageName);if(!pack||store.state.jobs[args.packageName]?.status!=='ready')return {status:'unavailable',reason:'No generated pack',generation:store.state.jobs[args.packageName]||null};
   return {status:'ok',packageName:pack.packageName,label:pack.label,tools:pack.tools.filter(t=>!t.currentScreenOnly).map(t=>({name:t.name,description:t.description,currentScreenOnly:!!t.currentScreenOnly,verification:t.verification||(t.testedAt?'Replay and final-result verified':'App-provided or read-only state'),inputs:t.inputSchema||{type:'object',properties:t.steps.some(s=>s.kind==='search')?{query:{type:'string'}}:{}}}))};
  }
  if(name==='generated_app_run'){
   const pack=store.pack(args.packageName);if(!pack||store.state.jobs[args.packageName]?.status!=='ready')return {status:'unavailable',reason:'No generated tools for that installed app'};
   try{
    const params=JSON.parse(args.argumentsJson||'{}');if(!params||Array.isArray(params)||typeof params!=='object')throw Error('Arguments must be a JSON object');
    if(pack.tools.find(t=>t.name===args.toolName)?.currentScreenOnly)throw Error('This is an unverified control, not a complete app operation');
    return await driver.generator.runtime.execute(pack,args.toolName,params);
   }catch(error){return {status:'needs_assistance',reason:error.message};}
  }
  throw Error('Unknown generated-app tool');
 }};
}
